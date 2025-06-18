"""
Script:         auto_forge.py
Author:         AutoForge Team

Description:
    This module serves as the central entry point of the AutoForge build system package.
    It is responsible for orchestrating the entire build system lifecycle, including:
        - Initializing core subsystems and shared services
        - Handling and validating command-line arguments
        - Parsing and loading solution-level configuration files
        - Dynamically discovering and registering CLI commands
        - Launching the interactive build shell or executing one-shot commands

    This module acts as the glue layer between user input, system configuration, and the dynamically
    loaded modular components that implement the build system's functionality.
"""

import argparse
import contextlib
import io
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Any

import psutil
# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (
    AddressInfoType, AutoForgeWorkModeType, AutoLogger, BuildTelemetry, CoreDynamicLoader,
    CoreEnvironment, CoreGUI, CoreJSONCProcessor, CoreModuleInterface, CorePrompt,
    CoreRegistry, CoreLinuxAliases, CoreSolution, CoreSystemInfo, CoreToolBox,
    CoreVariables, CoreXRayDB, ExceptionGuru, EventManager, LogHandlersTypes,
    PROJECT_BUILDERS_PATH, PROJECT_COMMANDS_PATH, PROJECT_CONFIG_FILE,
    PROJECT_LOG_FILE, PROJECT_VERSION, QueueLogger, StatusNotifType, Watchdog,
)


class AutoForge(CoreModuleInterface):
    """
    This module serves as the core of the AutoForge system, initialized ising the basd 'CoreModuleInterface' which
    ensures a singleton pattern.
    """

    def __init__(self, *args, **kwargs):
        """
        Early optional class initialization.
        """

        # This is an early, pre-initialization RAM-only logger. Once the main logger is initialized,
        # all records stored in RAM will be flushed to the package logger.

        self._queue_logger: QueueLogger = QueueLogger()
        self._initial_path: Path = Path.cwd().resolve()  # Store our initial works path
        self._exit_code: int = 0

        self._registry: Optional[CoreRegistry] = None
        self._solution: Optional[CoreSolution] = None
        self._tool_box: Optional[CoreToolBox] = None
        self._environment: Optional[CoreEnvironment] = None
        self._variables: Optional[CoreVariables] = None
        self._processor: Optional[CoreJSONCProcessor] = None
        self._gui: Optional[CoreGUI] = None
        self._loader: Optional[CoreDynamicLoader] = None
        self._prompt: Optional[CorePrompt] = None
        self._telemetry: Optional[BuildTelemetry] = None
        self._xray: Optional[CoreXRayDB] = None
        self._work_mode: AutoForgeWorkModeType = AutoForgeWorkModeType.UNKNOWN
        self._auto_logger: Optional[AutoLogger] = None
        self._sequence_log_file: Optional[Path] = None
        self._solution_file: Optional[str] = None
        self._solution_name: Optional[str] = None
        self._steps_file: Optional[str] = None
        self._sys_info: Optional[CoreSystemInfo] = None
        self._linux_aliases: Optional[CoreLinuxAliases] = None
        self._watchdog_watchdog: Optional[Watchdog] = None
        self._watchdog_timeout: int = 10  # Default timeout when not specified by configuration
        self._periodic_timer: Optional[threading.Timer] = None
        self._events_sync_thread: Optional[threading.Thread] = None
        self._periodic_timer_interval: float = 5.0  # 5-seconds timer

        # Startup arguments
        self._configuration: Optional[dict[str, Any]] = None
        self._workspace_path: Optional[str] = None
        self._workspace_exist: Optional[bool] = None
        self._run_command_name: Optional[str] = None
        self._run_command_args: Optional[list] = None
        self._run_sequence_ref_name: Optional[str] = None
        self._solution_package_path: Optional[str] = None
        self._solution_package_file: Optional[str] = None
        self._solution_url: Optional[str] = None
        self._git_token: Optional[str] = None
        self._remote_debugging: Optional[AddressInfoType] = None
        self._proxy_server: Optional[AddressInfoType] = None

        super().__init__(*args, **kwargs)

    def _initialize(self, *args, **kwargs) -> None:
        """
        Initialize the AutoForge core system and prepare the workspace environment.
        Depending on the context, this may involve:
        - Creating a new workspace, or loading an existing one.
        - Load the solution file from either a local path, local file or a git URL.
        - Exec command and exit  in non-interactive mode.
        Args:
            kwargs: Arguments passed from the command line, validated and analyzed internally.
        """

        #
        # Initialize the most fundamental and essential core modules FIRST.
        # These must be constructed before anything else—including the logger or any plugin infrastructure.
        # Order matters: they form the foundation upon which the rest of the system depends.
        #

        self._queue_logger.debug("System initializing..")
        self._queue_logger.debug(f"Started from {os.getcwd()}")

        # Instantiate core modules
        self._events = EventManager(StatusNotifType)
        self._registry = CoreRegistry()  # Must be first—anchors the core system
        self._tool_box = CoreToolBox()
        self._processor = CoreJSONCProcessor()
        self._sys_info = CoreSystemInfo()
        self._linux_aliases = CoreLinuxAliases()

        # Load package configuration and several dictionaries we might need later
        self._configuration = self._processor.render(PROJECT_CONFIG_FILE)
        self.ansi_codes = self._configuration.get("ansi_codes")

        # Configure and start watchdog with default or configuration provided timeout.
        self._watchdog_timeout = self._configuration.get("watchdog_timeout", self._watchdog_timeout)
        self._watchdog = Watchdog(default_timeout=self._watchdog_timeout)
        self._watchdog.stop()

        # Handle arguments
        self._init_arguments(*args, **kwargs)

        # Reset terminal and clean it's buffer.
        if self._work_mode == AutoForgeWorkModeType.INTERACTIVE:
            Watchdog.reset_terminal()

        if self._remote_debugging is not None:
            self._init_debugger(host=self._remote_debugging.host, port=self._remote_debugging.port)

        # Instantiate variables
        self._variables = CoreVariables(workspace_path=self._workspace_path, solution_name=self._solution_name,
                                        configuration=self._configuration,
                                        work_mode=self._work_mode)
        # Initializing the 'real' logger
        self._init_logger()

        # Load all built-in commands
        self._loader = CoreDynamicLoader(configuration=self._configuration)
        self._loader.probe(paths=[PROJECT_COMMANDS_PATH, PROJECT_BUILDERS_PATH])
        # Start the environment core module
        self._environment = CoreEnvironment(workspace_path=self._workspace_path,
                                            configuration=self._configuration)

        # Set the switch interval to 0.001 seconds (1 millisecond), it may make threads
        # responsiveness slightly better
        sys.setswitchinterval(0.001)

        # Remove anny previously generated autoforge temporary files.
        self._tool_box.clear_residual_files()

        # Instantiate the solution class
        self._init_solution()

        # Get telemetry file from configuration and Instantiate the class
        self._telemetry_file = self._configuration.get("telemetry_file", "telemetry.log")
        self._telemetry_file = self._variables.expand(self._telemetry_file)
        self._telemetry = BuildTelemetry.load(self._telemetry_file)

        # Set the events loop thread, without starting it.
        self._events_sync_thread = threading.Thread(target=self._events_loop, daemon=True, name="EvensSyncThread", )

        #
        # At this point, all required components have either been successfully initialized or have raised an exception,
        # halting the AutoForge boot process. We now have access to all core modules via the registry. The solution has
        # been expanded and validated, command-line arguments have been checked, dynamic commands have been loaded and
        # registered, as well as any dynamically loaded build plugins. The initialization watchdog can now be stopped,
        # and we can proceed in either interactive mode or automated non-interactive mode.
        #

        self._watchdog.stop()  # Stopping Initialization protection watchdog

    def _init_solution(self):
        """ Get the solution package, instantiate its class which will expand and validate its content """

        if self._solution_url:
            # Download all files in a given remote git path to a local zip file
            self._solution_package_file = (
                self._environment.git_get_path_from_url(url=self._solution_url, delete_if_exist=True,
                                                        proxy_host=self._proxy_server, token=self._git_token))

        if self._solution_package_file is not None and self._solution_package_path is None:
            self._solution_package_path = self._tool_box.uncompress_file(archive_path=self._solution_package_file)

        self._logger.debug(f"Solution files path: '{self._solution_package_path}'")

        # At this point we expect that self._solution_package_path still point to valid path
        # where all the solution files could be found
        if self._solution_package_path is None:
            raise RuntimeError("Package path is invalid or could not be created")

        solution_file = os.path.join(self._solution_package_path, "solution.jsonc")
        if not os.path.isfile(solution_file):
            raise RuntimeError(f"The main solution file '{solution_file}' was not found")

        # Loads the solution file with multiple parsing passes and comprehensive structural validation.
        # Also initializes the core variables module as part of the process.
        self._solution = CoreSolution(solution_config_file_name=solution_file, solution_name=self._solution_name,
                                      workspace_path=self._workspace_path)

        self._logger.debug(f"Solution: '{self._solution_name}' loaded and expanded")

    def _init_logger(self):
        """ Construct the logger file name, initialize and start logging"""

        allow_console_output = False
        log_file = None

        # Determine if we have a workspace which could she log file
        logs_workspace_path = self._variables.expand(f'$BUILD_LOGS')
        if logs_workspace_path is not None and self._tool_box.validate_path(logs_workspace_path, raise_exception=False):
            log_file = os.path.join(logs_workspace_path, PROJECT_LOG_FILE)
            # Patch it with timestamp so we will have dedicated log for each build system run.
            log_file = self._tool_box.append_timestamp_to_path(log_file)

        # Use different log file name when in one command mode
        if self._work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_ONE_COMMAND:
            log_file = str(self._initial_path / f"{self._solution_name}.log")

        self._auto_logger: AutoLogger = AutoLogger(log_level=logging.DEBUG,
                                                   configuration_data=self._configuration)
        self._auto_logger.set_log_file_name(log_file)
        self._auto_logger.set_handlers(LogHandlersTypes.FILE_HANDLER | LogHandlersTypes.CONSOLE_HANDLER)

        self._logger: logging.Logger = self._auto_logger.get_logger(console_stdout=allow_console_output)
        if log_file is None:
            self._sequence_log_file = Path(self._auto_logger.get_log_filename())

        # System initialized, dump all memory stored records in the logger
        self._queue_logger._target_logger = self._logger
        self._queue_logger.flush()

        self._logger.debug(f"AutoForge version: {PROJECT_VERSION} starting in workspace {self._workspace_path}")
        self._logger.info(str(self._sys_info))

    def _init_arguments(  # noqa: C901 # Acceptable complexity
            self, *_args, **kwargs) -> None:
        """
        Validate command-line arguments and set the AutoForge session execution mode.
        Depending on the inputs, AutoForge will either:
        - Start an interactive user shell, or non-interactive command runner mode.
        - Any validation error will immediately raise an exception and consequently terminate AutoForge.
        Args:
            kwargs: Arguments passed from the command line, validated and analyzed internally.
        Note:
            The logger is likely not yet initialized at this stage, so all errors must be raised directly
            (no logging or print statements should be used here).
        """

        def _validate_solution_package():
            """
            Solution package validation:
            AutoForge allows flexible input for the 'solution_package' argument:
            - The user can specify a path to a solution archive (.zip file), or
            - A path to an existing directory containing the solution files.
            - A GitHub URL pointing to git path which contains the solution files.
            """

            solution_package: Optional[str] = kwargs.get("solution_package", None)
            if not isinstance(solution_package, str):

                # If the solution package isn't provided explicitly, attempt to resolve it
                # from the package configuration, which should define its default install path.
                # Note: Variable resolution isn't fully available at this early stage.
                local_package_files = self._configuration.get("local_solution_package_files")
                if isinstance(local_package_files, str):
                    solution_package = (
                        local_package_files.strip().replace("$PROJ_WORKSPACE", self._workspace_path).replace(
                            "$SOLUTION_NAME", self._solution_name))
            # By now we should have a valid string
            if not isinstance(solution_package, str):
                return

            # Apply keywords substitution if we have them in configuration
            keywords_mapping: Optional[dict] = self._configuration.get("keywords_mapping")
            solution_package = self._tool_box.substitute_keywords(text=solution_package, keywords=keywords_mapping)

            if self._tool_box.is_url(solution_package):
                _validate_solution_url(solution_url=solution_package)
            else:
                solution_package_path = self._tool_box.get_expanded_path(solution_package)
                if self._tool_box.looks_like_unix_path(solution_package_path):
                    if os.path.isdir(solution_package_path):
                        self._solution_package_path = solution_package_path
                        self._solution_package_file = None
                    elif os.path.isfile(solution_package_path) and solution_package_path.lower().endswith(".zip"):
                        self._solution_package_file = solution_package_path
                        self._solution_package_path = None
                    else:
                        raise ValueError(f"package '{solution_package_path}' must be a directory or a .zip file")
                elif os.path.isfile(solution_package_path) and solution_package_path.lower().endswith(".zip"):
                    self._solution_package_file = solution_package_path
                    self._solution_package_path = None
                else:
                    raise ValueError(f"package '{solution_package_path}' is a directory or a .zip file")

        def _validate_solution_url(solution_url: str):
            """
            Solution URL validation:
            AutoForge allows optionally specifying a Git URL, which will later be used to retrieve solution files.
            The URL must have a valid structure and must point to a path (not to a single file)
            """

            if isinstance(solution_url, str):
                is_url_path = self._tool_box.is_url_path(solution_url)
                if is_url_path is None:
                    raise RuntimeError(f"the specified URL '{solution_url}' is not a valid Git URL")
                elif not is_url_path:
                    raise RuntimeError(f"the specified URL '{solution_url}' does not point to a valid path")
                else:
                    self._solution_url = solution_url
                    self._git_token = kwargs.get("git_token")
                    if not self._git_token:
                        # If we have 'git_token_environment_var' use to try and get the token from the user environment
                        git_token_var_name = self._configuration.get("git_token_environment_var")
                        self._git_token = os.environ.get(git_token_var_name) if git_token_var_name else None

        def _validate_network_options():

            remote_debugging: Optional[str] = kwargs.get("remote_debugging", None)
            proxy_server: Optional[str] = kwargs.get("proxy_server", None)

            if isinstance(remote_debugging, str):
                self._remote_debugging = self._tool_box.get_address_and_port(remote_debugging)
                if self._remote_debugging is None:
                    raise ValueError(f"the specified remote debugging address '{remote_debugging}' is invalid. "
                                     f"Expected format: <host>:<port> (e.g., localhost:5678)")
            if isinstance(proxy_server, str):
                self._proxy_server = self._tool_box.get_address_and_port(proxy_server)
                if self._proxy_server is None:
                    raise ValueError(f"the specified proxy server address '{proxy_server}' is invalid. "
                                     f"Expected format: <host>:<port> (e.g., www.proxy.com:8080)")
                self._queue_logger.info(
                    f"Proxy host name set to '{self._proxy_server.host} : {self._proxy_server.port}'")

        # Retrieve all arguments from kwargs
        self._solution_name = kwargs.get("solution_name")  # Required argument
        self._workspace_path = kwargs.get("workspace_path")  # Required argument

        # ==============================================================
        # Interactive vs. non-interactive mode selection.
        # If no non-interactive mode is specified, the interactive
        # prompt starts.
        # ==============================================================

        self._run_sequence_ref_name = kwargs.get("run_sequence")
        if self._run_sequence_ref_name is not None:
            self._work_mode = AutoForgeWorkModeType.NON_INTERACTIVE_SEQUENCE
        else:
            self._run_command_name = kwargs.get("run_command")
            if self._run_command_name is not None:

                self._work_mode = AutoForgeWorkModeType.NON_INTERACTIVE_ONE_COMMAND

                # Handle 'single-command' arguments
                self._run_command_args = kwargs.get("run_command_args", [])
                # Drop the leading '--' left by 'argparse' when using 'REMAINDER' to consume all reminder args
                if self._run_command_args and self._run_command_args[0] == '--':
                    self._run_command_args = self._run_command_args[1:]

        # If none of non-interactive modes was detected we falldown to interactive.
        if self._work_mode == AutoForgeWorkModeType.UNKNOWN:
            self._work_mode = AutoForgeWorkModeType.INTERACTIVE

        # Expand and check if the workspace exists
        self._workspace_path = self._tool_box.get_expanded_path(self._workspace_path)
        if not CoreToolBox.looks_like_unix_path(self._workspace_path):
            raise ValueError(f"the specified path '{self._workspace_path}' does not look like a valid Unix path")

        self._workspace_exist = self._tool_box.validate_path(text=self._workspace_path, raise_exception=False)
        # Move to the workspace path of we have it
        if self._workspace_exist:
            os.chdir(self._workspace_path)

        # Orchestrate)
        _validate_solution_package()
        _validate_network_options()

    def _init_debugger(self, host: str = 'localhost', port: int = 5678, abort_execution: bool = True) -> None:
        """
        Attempt to attach to a remote PyCharm debugger.
        Args:
            host (str, optional): The debugger host to connect to. Defaults to 'localhost'.
            port (int, optional): The debugger port to connect to. Defaults to 5678.
            abort_execution (bool, optional): If True, raise the exception on failure. If False, log and continue.
        """
        try:

            # Start remote debugging if enabled.
            self._queue_logger.debug(
                f"Remote debugging enabled using {host}:{port}")

            # noinspection PyUnresolvedReferences
            import pydevd_pycharm
            # Redirect stderr temporarily to suppress pydevd's traceback
            with contextlib.redirect_stderr(io.StringIO()):
                pydevd_pycharm.settrace(host=host, port=port, suspend=False,
                                        trace_only_current_thread=False)
                # No watch in debug mode
                self._watchdog.stop()

        except ImportError:
            self._queue_logger.warning("'pydevd_pycharm' is not installed; skipping remote debugging")

        except Exception as exception:
            if abort_execution:
                raise exception

    def _timer_expired(self, timer_name: str):
        """
        Called when any of our timers expires.
        Args:
            timer_name (str): The name of the timer that expired:
        """

        if timer_name == "PeriodicDurationTimer":

            # Get CPU utilization percentage for all cores averaged over 1 second
            cpu_percent = psutil.cpu_percent(interval=0.1)
            if cpu_percent > 50:
                self._logger.warning(f"High CPU utilization: {cpu_percent}%")

            # Restart timer
            self._periodic_timer = self._tool_box.set_timer(timer=self._periodic_timer,
                                                            interval=self._periodic_timer_interval,
                                                            expiration_routine=self._timer_expired,
                                                            timer_name="PeriodicDurationTimer")

    def _events_loop(self):
        """
        AutoForge events handler main loop.
        """

        # Start periodic background timer
        self._periodic_timer = self._tool_box.set_timer(timer=self._periodic_timer,
                                                        interval=self._periodic_timer_interval,
                                                        expiration_routine=self._timer_expired,
                                                        timer_name="PeriodicDurationTimer",
                                                        auto_start=True)

        self._logger.debug("Starting the events loop thread")
        while True:

            # --------------------------------------------------------------------------------------------------
            #                                    AutoForge Event Sync
            # --------------------------------------------------------------------------------------------------

            self._events.wait_any()

            # Iterate over possible events and handle them
            for notification in StatusNotifType:
                if self._events.is_set(notification):
                    self._events.clear(notification)

                    # ------------------------------------------------------------------------------------------
                    # Event: ERROR
                    #   Description:
                    #   Centralized error events handler
                    # ------------------------------------------------------------------------------------------

                    if notification == StatusNotifType.ERROR:
                        print(notification.name)
                        pass

                    # ------------------------------------------------------------------------------------------
                    # Events: OPERATION_START, OPERATION_END
                    #   Description:
                    #   Operation started or ended.
                    # ------------------------------------------------------------------------------------------

                    elif notification in (StatusNotifType.OPERATION_START, StatusNotifType.OPERATION_END):
                        print(notification.name)
                        pass

                    # ------------------------------------------------------------------------------------------
                    # Event: TERM
                    #   Description:
                    #   Centralized error events handler
                    # ------------------------------------------------------------------------------------------

                    elif notification == StatusNotifType.TERM:
                        print(notification.name)
                        pass

    def forge(self) -> Optional[int]:

        """
        Load a solution and fire the AutoForge shell.
        """

        try:

            if self._work_mode == AutoForgeWorkModeType.INTERACTIVE:

                # Start events loop thread
                self._events_sync_thread.start()

                # ==============================================================
                # User interactive shell.
                # Indefinite loop until user exits the shell using 'quit'
                # ==============================================================

                self._logger.debug("Running in interactive user shell mode")

                self._gui: CoreGUI = CoreGUI()
                self._prompt = CorePrompt()

                # Initializes XRay SQLite background indexing
                self._xray = CoreXRayDB(no_index_rebuild=True)
                self._xray.start()

                # Start user prompt loop
                self._prompt.cmdloop()
                self._exit_code = self._prompt.last_result

            else:

                # ==============================================================
                #  Execute a command or sequence of operations in non-
                #  interactive mode and exit.
                # ==============================================================

                if self._work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_ONE_COMMAND:

                    # ==============================================================
                    #  Running single command from an exiting workspace in
                    #  non-interactive mode.
                    # ==============================================================

                    self._logger.debug("Running in single command automatic non-interactive mode")

                    # Prepare the prompt instance
                    self._prompt = CorePrompt()

                    # Compose the full command string (with arguments, if any)
                    command_line = " ".join([self._run_command_name.strip()] + self._run_command_args)
                    self._logger.debug("Executing command: %s", command_line)

                    # Execute the command (same way cmdloop does internally)
                    self._prompt.onecmd_plus_hooks(command_line)
                    self._exit_code = self._prompt.last_result if self._prompt.last_result is not None else 0

                elif self._work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_SEQUENCE:

                    # ==============================================================
                    #  Running sequence of operations in non interactive-mode,
                    #  typically for creating a new workspace.
                    # ==============================================================

                    self._logger.debug("Running in sequence execution non-interactive mode")

                    # Get the sequence dictionary from the solution
                    sequence_data = self._solution.get_sequence_by_name(sequence_name=self._run_sequence_ref_name)
                    if not isinstance(sequence_data, dict):
                        raise ValueError(
                            f"sequence reference name '{self._run_sequence_ref_name}' was not found in '{self._solution_name}'")

                    # Execute sequence
                    self._exit_code = self._environment.run_sequence(sequence_data=sequence_data)
                    if self._exit_code == 0:
                        # Finalize workspace creation
                        self._environment.finalize_workspace_creation(solution_name=self._solution_name,
                                                                      solution_package_path=self._solution_package_path,
                                                                      sequence_log_file=self._sequence_log_file)
                else:
                    raise RuntimeError(f"work mode '{self._work_mode}' not supported")

                return self._exit_code

        except Exception:  # Propagate
            raise

    @property
    def version(self) -> str:
        """ Return package version string """
        return PROJECT_VERSION

    @property
    def configuration(self) -> Optional[dict[str, Any]]:
        """ Returns the package configuration processed JSON """
        return self._configuration

    @property
    def telemetry(self) -> Optional[BuildTelemetry]:
        """ Returns the AutoForge telemetry class instance """
        return self._telemetry

    @property
    def watchdog(self) -> Optional[Watchdog]:
        """ Returns the Package watchdog instance """
        return self._watchdog

    @property
    def work_mode(self) -> Optional[AutoForgeWorkModeType]:
        """Return whether the application was started in interactive or non-interactive mode."""
        return self._work_mode

    @property
    def root_logger(self) -> Optional[AutoLogger]:
        """ AutoForge root logger instance """
        return self._auto_logger


def auto_forge_start(args: argparse.Namespace) -> Optional[int]:
    """
    Instantiates AutoForge with the provided arguments and execute its main 'forge()' method.
    Args:
        args (argparse): Parsed command line arguments.
    Returns:
        int: Exit status of the AutoForge execution.
    """

    result: int = 1  # Default to error

    try:
        # Instantiate AutoForge, pass all arguments
        auto_forge: AutoForge = AutoForge(**vars(args))
        result = auto_forge.forge()

    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}Interrupted by user, shutting down.{Style.RESET_ALL}\n")

    except Exception as runtime_error:
        # Retrieve information about the original exception that triggered this handler.
        file_name, line_number = ExceptionGuru().get_context()
        # If we can get a logger, use it to log the error.
        logger_instance = AutoLogger.get_base_logger()
        if logger_instance is not None:
            logger_instance.error(f"Exception: {runtime_error}.File: {file_name}, Line: {line_number}")
        print(f"\n{Fore.RED}Exception:{Style.RESET_ALL} {runtime_error}.\nFile: {file_name}\nLine: {line_number}\n")
    finally:
        CoreToolBox.set_terminal_input(state=True)  # Restore terminal input

    return result
