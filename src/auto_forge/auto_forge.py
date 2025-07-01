"""
Script:         auto_forge.py
Author:         AutoForge Team

Description:
    Central package entry point of the AutoForge build system package, responsible for orchestrating the entire
    build system lifecycle, including:
        - Initializing core subsystems and shared services
        - Handling and validating command-line arguments
        - Parsing and loading solution-level configuration files
        - Dynamically discovering and registering CLI commands
        - Launching the interactive build shell or executing one-shot commands

    Simply put, this is the glue layer between user input, system configuration, and the dynamically
    loaded modular components that implement the build system's functionality.
"""

import argparse
import contextlib
import datetime
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
    AddressInfoType, AutoForgeWorkModeType, CoreLogger, CoreDynamicLoader,
    CorePlatform, CoreGUI, CoreJSONCProcessor, CoreModuleInterface, CoreBuildShell, Crypto,
    CoreRegistry, CoreLinuxAliases, CoreSolution, CoreSystemInfo, CoreToolBox, CoreTelemetry, CoreWatchdog,
    CoreVariables, CoreXRayDB, CoreAI, ExceptionGuru, EventManager, LogHandlersType, StatusNotifType,
    PackageGlobals, CoreContext)

AUTO_FORGE_MODULE_NAME = "AutoForge"
AUTO_FORGE_MODULE_DESCRIPTION = "AutoForge Main"


class AutoForge(CoreModuleInterface):
    """
    This module serves as the core of the AutoForge system, initialized ising the basd 'CoreModuleInterface' which
    ensures a singleton pattern.
    """

    def __init__(self, *args, **kwargs):
        """
        Early class initialization.
        """

        self._initial_path: Path = Path.cwd().resolve()  # Store our initial works path
        self._exit_code: int = 0
        self._registry: Optional[CoreRegistry] = None
        self._telemetry: Optional[CoreTelemetry] = None
        self._solution: Optional[CoreSolution] = None
        self._tool_box: Optional[CoreToolBox] = None
        self._platform: Optional[CorePlatform] = None
        self._variables: Optional[CoreVariables] = None
        self._processor: Optional[CoreJSONCProcessor] = None
        self._gui: Optional[CoreGUI] = None
        self._loader: Optional[CoreDynamicLoader] = None
        self._build_shell: Optional[CoreBuildShell] = None
        self._xray: Optional[CoreXRayDB] = None
        self._ai_bridge: Optional[CoreAI] = None
        self._work_mode: AutoForgeWorkModeType = AutoForgeWorkModeType.UNKNOWN
        self._core_logger: Optional[CoreLogger] = None
        self._log_file_name: Optional[str] = None
        self._solution_file: Optional[str] = None
        self._solution_name: Optional[str] = None
        self._steps_file: Optional[str] = None
        self._sys_info: Optional[CoreSystemInfo] = None
        self._linux_aliases: Optional[CoreLinuxAliases] = None
        self._watchdog: Optional[CoreWatchdog] = None
        self._watchdog_timeout: int = 10  # Default timeout when not specified by configuration
        self._periodic_timer: Optional[threading.Timer] = None
        self._events_sync_thread: Optional[threading.Thread] = None
        self._periodic_timer_interval: float = 5.0  # 5-seconds timer
        self._secrets: Optional[dict] = None

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

        # Set how often Python checks for thread switches (every 1 millisecond).
        # This can improve responsiveness in multithreaded programs.
        sys.setswitchinterval(0.001)

        super().__init__(*args, **kwargs)

    def _initialize(self, *args, **kwargs) -> None:
        """
        Initializes the AutoForge system: instantiates all core modules, validates command-line arguments,
        expands the solution, and finally either starts the interactive user shell or performs an automated task.
        """

        # ----------------------------------------------------------------------
        # Core Module Instantiation Notes:
        #
        # Initialize the most fundamental and essential core modules first.
        # The instantiation order is critical: independent modules must be initialized
        # before dependent ones to avoid circular dependency issues.
        #
        # This order must align with the import sequence defined in the package's __init__.py.
        # Modifying it will almost certainly result in import-time dependency errors.
        #
        # All core modules derive from the abstract base class `CoreModuleInterface`,
        # which enforces a singleton pattern to ensure each module is only instantiated once.
        # ----------------------------------------------------------------------

        # Instantiate core modules
        self._registry = CoreRegistry()  # Must be first—anchors the core system
        self._telemetry = CoreTelemetry()

        # Obtain a logger instance as early as possible, configured to support memory-based logging.
        # Later, once we determine whether to use file and/or console output, all buffered memory logs
        # will be flushed to the appropriate active handlers.
        self._core_logger = CoreLogger(log_level=logging.DEBUG, configuration_data=self._configuration)
        self._logger: logging.Logger = self._core_logger.get_logger(console_stdout=False)
        self._logger.debug("System initializing..")
        self._logger.debug(f"Started from '{os.getcwd()}', editable package '{PackageGlobals.EDITABLE}'")

        # Instantiate the JSONC processor. This module cleans and processes .jsonc files,
        # returning a validated JSON object. Since nearly all configuration files in this system
        # are either JSON or mostly JSONC, this module must be loaded as early as possible.
        self._processor = CoreJSONCProcessor()

        # Load the package configuration from 'auto_forge.jsonc', which is part of the package itself
        # and is used extensively at runtime. This is not a user configuration file,
        # but rather an internal AutoForge configuration and metadata store.
        self._configuration = self._processor.render(PackageGlobals.CONFIG_FILE)

        # Allow the configuration to be accessed from any module via the context,
        # without requiring those modules to instantiate this class.
        CoreContext.set_config_provider(self)

        # Configure start the watchdog which will auto-terminate the application if start time goes beyond the defined interval
        self._watchdog_timeout = self._configuration.get("watchdog_timeout", self._watchdog_timeout)
        self._watchdog = CoreWatchdog(default_timeout=self._watchdog_timeout)
        self._watchdog.stop()

        # Reset of the core modules
        self._sys_info = CoreSystemInfo()
        self._tool_box = CoreToolBox()
        self._linux_aliases = CoreLinuxAliases()

        # Handle command-line arguments to determine the work mode — for example,
        # whether we're running in automation mode, interactive shell mode, or using
        # other user-defined startup flags.
        self._init_arguments(*args, **kwargs)

        # If debug mode was enabled via the command line, attempt to safely import and start
        # `pydevd_pycharm`, which tries to connect to a remote PyCharm debug server.
        # Since this build system heavily relies on terminal-focused libraries like cmd2 and prompt_toolkit,
        # traditional in-terminal debugging may not work as expected. This remote debug mode is currently
        # the most reliable way to perform step-by-step debugging.
        if self._remote_debugging is not None:
            self._init_debugger(host=self._remote_debugging.host, port=self._remote_debugging.port)

        # Reset terminal and clean it's buffer.
        if self._work_mode == AutoForgeWorkModeType.INTERACTIVE:
            self._tool_box.reset_terminal()

        # Instantiate the variables module, which replaces the traditional shell environment
        # with a more powerful and extensible core-based system.
        self._variables = CoreVariables(workspace_path=self._workspace_path, solution_name=self._solution_name,
                                        work_mode=self._work_mode)

        # Initialize secrets storage and load current data
        self._secrets = self._init_secrets(demo_mode=True)

        # At this point, we have enough information to finalize logger initialization.
        # This step flushes all temporarily buffered logs into the finalized logger instance,
        # which will be used from this point onward.
        self._init_logger()
        self._ai_bridge = CoreAI()

        # Load all supported dynamic modules — currently includes: command handlers and build plugins
        self._loader = CoreDynamicLoader()

        # Instantiate the platform module, which provides key utilities for interacting with the user's platform.
        # This includes methods for executing processes (individually or in sequence), performing essential Git operations,
        # working with the file system, and more.
        self._platform = CorePlatform(workspace_path=self._workspace_path)

        # Remove any previously generated autoforge temporary files.
        self._tool_box.clear_residual_files()

        # The last core module to be instantiated is the solution module. It comes last because it depends
        # on most of the other core modules to function correctly. Its task is to load the project’s solution file(s),
        # preprocess them, and resolve all references, pointers, and variables into a clean, validated JSON.
        # This JSON acts as the "DNA" that defines how the entire build system will behave.
        self._init_solution()

        # Start SQLite based background indexing service
        self._xray = CoreXRayDB()

        # Enumerate paths tagged as potentially containing modules that can be dynamically discovered and loaded.
        # This call should be the last step in the early initialization sequence.
        self._loader.probe()

        # Set the events-loop thread, without starting it yet.
        self._events_sync_thread = threading.Thread(target=self._events_loop, daemon=True, name="EvensSyncThread", )

        #
        # At this point, all required components have either been successfully initialized or have raised an exception,
        # halting the AutoForge boot process. We now have access to all core modules via the registry. The solution has
        # been expanded and validated, command-line arguments have been checked, dynamic commands have been loaded and
        # registered, as well as any dynamically loaded build plugins. The initialization watchdog can now be stopped,
        # and we can proceed in either interactive mode or automated non-interactive mode.
        #

        self._watchdog.stop()  # Stopping Initialization protection watchdog

        # Inform telemetry that the module is up & running.
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

    def _init_solution(self):
        """ Get the solution package, instantiate its class which will expand and validate its content """

        if self._solution_url:
            # Download all files in a given remote git path to a local zip file
            self._solution_package_file = (
                self._platform.git_get_path_from_url(url=self._solution_url, delete_if_exist=True))

        if self._solution_package_file is not None and self._solution_package_path is None:
            self._solution_package_path = self._tool_box.decompress_archive(archive_path=self._solution_package_file)

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
        default_log_file = f"{PackageGlobals.PROJ_NAME}.log"

        # Determine if we have a workspace which could she log file
        logs_workspace_path = self._variables.expand(f'$BUILD_LOGS')

        # Determine the log file name to use (when not specified through arguments)
        if self._log_file_name is None:

            # Use different log file name when in one command mode
            if self._work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_ONE_COMMAND:
                self._log_file_name = str(self._initial_path / f"{self._solution_name}.{self._run_command_name}.log")

            # Use different log file name when in one command mode
            elif self._work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_SEQUENCE:
                self._log_file_name = str(
                    self._initial_path / f"{self._solution_name}.{self._run_sequence_ref_name}.log")

            # Normal flow
            elif logs_workspace_path is not None and self._tool_box.validate_path(logs_workspace_path,
                                                                                  raise_exception=False):
                self._log_file_name = os.path.join(logs_workspace_path, default_log_file)
                # Patch it with timestamp so we will have dedicated log for each build system run.
                self._log_file_name = self._tool_box.append_timestamp_to_path(self._log_file_name)

        # Initialize logger, do not disable memory logger, it will be auto disabled by flush_memory_logs()
        self._core_logger.set_log_file_name(self._log_file_name)
        self._core_logger.set_handlers(
            LogHandlersType.FILE_HANDLER | LogHandlersType.CONSOLE_HANDLER | LogHandlersType.MEMORY_HANDLER)

        self._logger: logging.Logger = self._core_logger.get_logger(console_stdout=allow_console_output)

        # Flush memory logs and disable memory logger
        self._core_logger.flush_memory_logs(LogHandlersType.FILE_HANDLER)
        self._logger.info(f"AutoForge version: {PackageGlobals.VERSION} starting")

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
                    self._logger.debug(f"Using solution from URL '{solution_url}'")

                    self._git_token = kwargs.get("git_token")
                    if not self._git_token:
                        # If we have 'git_token_environment_var' use to try and get the token from the user environment
                        git_token_var_name = self._configuration.get("git_token_environment_var")
                        self._git_token = os.environ.get(git_token_var_name) if git_token_var_name else None

                    if self._git_token:
                        self._logger.debug("GitHub token '{self._git_token[:4]'...")

        def _validate_network_options():

            remote_debugging: Optional[str] = kwargs.get("remote_debugging", None)
            if isinstance(remote_debugging, str):
                self._remote_debugging = self._tool_box.get_address_and_port(remote_debugging)
                if self._remote_debugging is None:
                    raise ValueError(f"the specified remote debugging address '{remote_debugging}' is invalid. "
                                     f"Expected format: <host>:<port> (e.g., localhost:5678)")

            # Process proxy server argument
            self.set_proxy_server(proxy_server=kwargs.get("proxy_server", None), silent=False)

        # Retrieve all arguments from kwargs
        self._solution_name = kwargs.get("solution_name")  # Required argument
        self._workspace_path = kwargs.get("workspace_path")  # Required argument
        self._log_file_name = kwargs.get("log_file")  # Optional set specific log file name

        # ==============================================================
        # Interactive vs. non-interactive mode selection.
        # If no non-interactive mode is specified, the interactive
        # prompt starts.
        # ==============================================================

        self._run_sequence_ref_name = kwargs.get("run_sequence")
        if self._run_sequence_ref_name is not None:
            self._work_mode = AutoForgeWorkModeType.NON_INTERACTIVE_SEQUENCE
            self._logger.debug(f"Sequence ref name '{self._run_sequence_ref_name}'")
        else:
            self._run_command_name = kwargs.get("run_command")
            if self._run_command_name is not None:
                self._work_mode = AutoForgeWorkModeType.NON_INTERACTIVE_ONE_COMMAND
                self._logger.debug(f"Run command name '{self._run_command_name}'")

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

        self._logger.debug(f"Workspace path '{self._workspace_path}'")
        self._workspace_exist = self._tool_box.validate_path(text=self._workspace_path, raise_exception=False)
        # Move to the workspace path of we have it
        if self._workspace_exist:
            os.chdir(self._workspace_path)

        # Process other arguments
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
            self._logger.debug(f"Remote debugging enabled using {host}:{port}")

            # noinspection PyUnresolvedReferences
            import pydevd_pycharm
            # Redirect stderr temporarily to suppress pydevd's traceback
            with contextlib.redirect_stderr(io.StringIO()):
                pydevd_pycharm.settrace(host=host, port=port, suspend=False,
                                        trace_only_current_thread=False)
                # Dogs not allowed in debug
                self._watchdog.stop()

        except ImportError:
            self._logger.warning("'pydevd_pycharm' is not installed; skipping remote debugging")

        except Exception as exception:
            if abort_execution:
                raise exception

    def _init_secrets(self, demo_mode: bool = True) -> Optional[dict]:
        """
        Initialize the secrets container using the 'Crypto' module, update the metadata date field,
        and get a fresh copy of the stored secrets.
        """
        try:

            if not demo_mode:
                key_file_path = self._variables.get("AF_SOLUTION_KEY")
                secrets_file_path = self._variables.get("AF_SOLUTION_SECRETS")
            else:
                key_file_path = str(PackageGlobals.SAMPLES_PATH / "demo" / "res_1.bin")
                secrets_file_path = str(PackageGlobals.SAMPLES_PATH / "demo" / "res_2.bin")

            secrets_schema_data = self.configuration.get("secrets_schema")
            if secrets_schema_data is None:
                raise ValueError("'secrets_schema' is missing from configuration. Cannot initialize secrets.")

            if key_file_path is None or secrets_file_path is None:
                raise ValueError(
                    "'AF_SOLUTION_KEY' or 'AF_SOLUTION_SECRETS' are not defined in variables. Cannot initialize Crypto.")

            # Initialize Crypto with the key file path, creating it if needed
            crypto = Crypto(key_file=key_file_path, create_as_needed=True)

            # Read secrets or create a fresh secrets data using the schema if the file doesn't exist
            secrets = crypto.create_or_load_encrypted_dict(
                filename=secrets_file_path,
                default_data=secrets_schema_data
            )

            secrets_metadata = secrets.get('metadata')
            if secrets_metadata is None:
                raise RuntimeError("'metadata' section is missing from the encrypted secrets file. "
                                   "Ensure the schema includes it.")

            # Update metadata timestamp (already correct for Python 3.9+ compatibility)
            secrets_metadata['last_updated'] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            # Save the updated metadata back to the encrypted secrets file
            crypto.modify_encrypted_dict(
                filename=secrets_file_path,
                key="metadata",
                value=secrets_metadata
            )

            # Return the updated secrets dictionary for further use
            return secrets

        except (ValueError, FileNotFoundError, RuntimeError) as e:
            # Catch specific errors from Crypto methods or custom checks
            raise RuntimeError(f"failed to initialize secrets container: {e}") from e
        except Exception as e:
            # Catch any other unexpected errors during the process
            raise RuntimeError(f"unexpected error occurred during secrets initialization: {e}") from e

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

        # Setup events manager
        self._events = EventManager(StatusNotifType)

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

    def set_proxy_server(self, proxy_server: Optional[str], update_environment: bool = True,
                         silent: bool = True) -> Optional[bool]:
        # noinspection HttpUrlsUsage
        """
        Set the proxy server to use.
        Args:
            proxy_server (str, optional): Proxy server in either:
                - host:port format (e.g., proxy.example.com:8080)
                - full URL format (e.g., http://proxy.example.com:8080 or https://user:pass@proxy:8080)
            update_environment (bool): If True, sets HTTP_PROXY and HTTPS_PROXY in system environment.
            silent (bool): If False, raises ValueError on failure. If True, returns False instead.
        Returns:
            Optional[bool]: True if successful, False if failed silently, None if input was invalid.
        """
        if not isinstance(proxy_server, str):
            # Fall back to environment variables
            if proxy_server is None:
                proxy_server = os.environ.get("https_proxy") or os.environ.get("http_proxy")

        if isinstance(proxy_server, str):
            self._proxy_server = self._tool_box.get_address_and_port(proxy_server)
            if self._proxy_server is None:
                if not silent:
                    raise ValueError(f"Invalid proxy server address or URL '{proxy_server}'. "
                                     f"Expected format: 'host:port' or full proxy URL.")
                return False

            # Construct normalized proxy URL
            # noinspection HttpUrlsUsage
            if self._variables is not None:
                self._variables.add(key="HTTP_PROXY", value=self._proxy_server.url, is_path=False,
                                    description="Proxy Server")

            if update_environment:
                os.environ["HTTP_PROXY"] = self._proxy_server.url
                os.environ["HTTPS_PROXY"] = self._proxy_server.url.replace("http", "https")

            if hasattr(self, "_queue_logger"):
                self._queue_logger.debug(f"Proxy set to: {self._proxy_server.url}")

            return True

        return False

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
                self._build_shell = CoreBuildShell()

                # Start XRay SQLite background indexing service.
                self._xray.start()

                # Start user prompt loop
                self._build_shell.cmdloop()
                self._exit_code = self._build_shell.last_result
                return self._exit_code

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
                    self._build_shell = CoreBuildShell()

                    # Compose the full command string (with arguments, if any)
                    command_line = " ".join([self._run_command_name.strip()] + self._run_command_args)
                    self._logger.debug("Executing command: %s", command_line)

                    # Execute the command (same way cmdloop does internally)
                    self._build_shell.onecmd_plus_hooks(command_line)
                    self._exit_code = self._build_shell.last_result if self._build_shell.last_result is not None else 0

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
                    self._exit_code = self._platform.run_sequence(sequence_data=sequence_data)
                    if self._exit_code == 0:
                        # Finalize workspace creation
                        self._platform.finalize_workspace_creation(solution_name=self._solution_name,
                                                                   solution_package_path=self._solution_package_path,
                                                                   sequence_log_file=self._log_file_name)
                else:
                    raise RuntimeError(f"work mode '{self._work_mode}' not supported")

                return self._exit_code

        except Exception:  # Propagate
            raise

    @property
    def logger(self) -> Optional[CoreLogger]:
        """ Return the core logger class instance """
        return self._core_logger

    @property
    def version(self) -> str:
        """ Return package version string """
        return PackageGlobals.VERSION

    @property
    def proxy_server(self) -> Optional[AddressInfoType]:
        """ Return configured proxy server string """
        return self._proxy_server

    @property
    def git_token(self) -> Optional[str]:
        """Get or set the configured web access token string."""
        return self._git_token

    @git_token.setter
    def git_token(self, token: str) -> None:
        self._git_token = token

    @property
    def configuration(self) -> Optional[dict[str, Any]]:
        """ Returns the package configuration processed JSON """
        return self._configuration

    @property
    def watchdog(self) -> Optional[CoreWatchdog]:
        """ Returns the Package watchdog instance """
        return self._watchdog

    @property
    def secrets(self) -> Optional[dict]:
        """ Returns stored secrets """
        return self._secrets

    @property
    def work_mode(self) -> Optional[AutoForgeWorkModeType]:
        """Return whether the application was started in interactive or non-interactive mode."""
        return self._work_mode


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
        logger_instance = CoreLogger.get_base_logger()
        if logger_instance is not None:
            logger_instance.error(f"Exception: {runtime_error}.File: {file_name}, Line: {line_number}")
        print(f"\n{Fore.RED}Exception:{Style.RESET_ALL} {runtime_error}.\nFile: {file_name}\nLine: {line_number}\n")
    finally:
        CoreToolBox.set_terminal_input(state=True)  # Restore terminal input

    return result
