"""
Script:         auto_forge.py
Author:         AutoForge Team

Description:
    This module serves as the core of the AutoForge system.
    Here we initialize all core libraries, parse and load the various configuration files,
    dynamically load CLI commands and start the build system shell.
"""

# Standard library imports
import argparse
import contextlib
import io
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Any

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (
    AddressInfoType, AutoForgeWorkModeType, AutoLogger, BuildTelemetry, CoreEnvironment,
    CoreGUI, CoreLoader, CoreModuleInterface, CoreProcessor, CorePrompt, CoreSolution,
    CoreVariables, ExceptionGuru, LogHandlersTypes, PROJECT_BUILDERS_PATH,
    PROJECT_COMMANDS_PATH, PROJECT_CONFIG_FILE, PROJECT_LOG_FILE, PROJECT_NAME,
    PROJECT_SHARED_PATH, PROJECT_VERSION, QueueLogger, Registry, ShellAliases,
    SystemInfo, ToolBox, Watchdog, XYType
)


class AutoForge(CoreModuleInterface):
    """
    This module serves as the core of the AutoForge system, initialized ising the basd 'CoreModuleInterface' which
    ensures a singleton pattern.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared earlier in `__init__()`
        See 'CoreModuleInterface' usage.
        """

        self._queue_logger: QueueLogger = QueueLogger()  # Early, pre initialization RAM only logger.
        self._registry: Optional[Registry] = None
        self._solution: Optional[CoreSolution] = None
        self._tool_box: Optional[ToolBox] = None
        self._environment: Optional[CoreEnvironment] = None
        self._variables: Optional[CoreVariables] = None
        self._processor: Optional[CoreProcessor] = None
        self._gui: Optional[CoreGUI] = None
        self._loader: Optional[CoreLoader] = None
        self._prompt: Optional[CorePrompt] = None
        self._telemetry: Optional[BuildTelemetry] = None
        self._work_mode: AutoForgeWorkModeType = AutoForgeWorkModeType.UNKNOWN
        self._auto_logger: Optional[AutoLogger] = None
        self._sequence_log_file: Optional[Path] = None
        self._solution_file: Optional[str] = None
        self._solution_name: Optional[str] = None
        self._steps_file: Optional[str] = None
        self._sys_info: Optional[SystemInfo] = None
        self._shell_aliases: Optional[ShellAliases] = None
        self._watchdog: Optional[Watchdog] = None
        self._watchdog_timeout: int = 10  # Default timeout when not specified by configuration

        # Startup arguments
        self._package_configuration_data: Optional[dict[str, Any]] = None
        self._workspace_path: Optional[str] = None
        self._workspace_exist: Optional[bool] = None
        self._run_command_name: Optional[str] = None
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

        self._registry = Registry()  # Must be first—anchors the core system
        self._tool_box = ToolBox()
        self._processor = CoreProcessor()
        self._sys_info = SystemInfo()
        self._shell_aliases = ShellAliases()

        # Reset the terminal, clean its buffer.
        Watchdog.reset_terminal()

        # Load package configuration and several dictionaries we might need later
        self._package_configuration_data = self._processor.preprocess(PROJECT_CONFIG_FILE)
        self.ansi_codes = self._package_configuration_data.get("ansi_codes")

        # Validate startup arguments
        self._validate_arguments(*args, **kwargs)

        # Start remote debugging if enabled.
        if self._remote_debugging is not None:
            self._queue_logger.debug(
                f"Remote debugging enabled using {self._remote_debugging.host}:{self._remote_debugging.port}")

            self._attach_debugger(host=self._remote_debugging.host, port=self._remote_debugging.port)

        # Start variables
        self._variables = CoreVariables(workspace_path=self._workspace_path, solution_name=self._solution_name,
                                        package_configuration_data=self._package_configuration_data,
                                        work_mode=self._work_mode)

        # Initializing the 'real' logger
        self._init_logger()

        # Load all built-in commands
        self._loader = CoreLoader(package_configuration_data=self._package_configuration_data)
        self._loader.probe(paths=[PROJECT_COMMANDS_PATH, PROJECT_BUILDERS_PATH])
        # Start the environment core module
        self._environment = CoreEnvironment(workspace_path=self._workspace_path,
                                            package_configuration_data=self._package_configuration_data)

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

        self._auto_logger: AutoLogger = AutoLogger(log_level=logging.DEBUG,
                                                   configuration_data=self._package_configuration_data)
        self._auto_logger.set_log_file_name(log_file)
        self._auto_logger.set_handlers(LogHandlersTypes.FILE_HANDLER | LogHandlersTypes.CONSOLE_HANDLER)

        # Enable console logger when in continuos integration mode
        if self._work_mode == AutoForgeWorkModeType.CI:
            allow_console_output = True

        self._logger: logging.Logger = self._auto_logger.get_logger(console_stdout=allow_console_output)
        if log_file is None:
            self._sequence_log_file = Path(self._auto_logger.get_log_filename())

        # System initialized, dump all memory stored records in the logger
        self._queue_logger._target_logger = self._logger
        self._queue_logger.flush()

        self._logger.debug(f"AutoForge version: {PROJECT_VERSION} starting in workspace {self._workspace_path}")
        self._logger.info(self._sys_info)

    def _validate_arguments(  # noqa: C901 # Acceptable complexity
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

        # Get all arguments from kwargs
        def _init_arguments():
            self._solution_name = kwargs.get("solution_name")  # Required argument
            self._workspace_path = kwargs.get("workspace_path")  # Required argument

            # Non-interactive operations specifier, could be either a single command or reference to
            # a solution properties which provide the actual OpenSolaris sequence
            self._run_sequence_ref_name = kwargs.get("run_sequence")
            self._run_command_name = kwargs.get("run_command")

            # Expand and check if the workspace exists
            self._workspace_path = self._tool_box.get_expanded_path(self._workspace_path)
            if not ToolBox.looks_like_unix_path(self._workspace_path):
                raise ValueError(f"the specified path '{self._workspace_path}' does not look like a valid Unix path")

            self._workspace_exist = self._tool_box.validate_path(text=self._workspace_path, raise_exception=False)
            # Move to the workspace path of we have it
            if self._workspace_exist:
                os.chdir(self._workspace_path)

            # Set non-interactive mode if we have either --run-command ot --run_sequence
            if self._run_sequence_ref_name or self._run_command_name:
                self._work_mode = AutoForgeWorkModeType.NON_INTERACTIVE
            else:
                self._work_mode = AutoForgeWorkModeType.INTERACTIVE

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
                local_package_files = self._package_configuration_data.get("local_solution_package_files")
                if isinstance(local_package_files, str):
                    solution_package = (
                        local_package_files.strip().replace("$PROJ_WORKSPACE", self._workspace_path).replace(
                            "$SOLUTION_NAME", self._solution_name))
            # By now we should have a valid string
            if not isinstance(solution_package, str):
                return

            # Apply keywords substitution if we have them in configuration
            keywords_mapping: Optional[dict] = self._package_configuration_data.get("keywords_mapping")
            solution_package = self._tool_box.substitute_keywords(text=solution_package, keywords=keywords_mapping)

            if ToolBox.is_url(solution_package):
                _validate_solution_url(solution_url=solution_package)
            else:
                solution_package_path = ToolBox.get_expanded_path(solution_package)
                if ToolBox.looks_like_unix_path(solution_package_path):
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
                is_url_path = ToolBox.is_url_path(solution_url)
                if is_url_path is None:
                    raise RuntimeError(f"the specified URL '{solution_url}' is not a valid Git URL")
                elif not is_url_path:
                    raise RuntimeError(f"the specified URL '{solution_url}' does not point to a valid path")
                else:
                    self._solution_url = solution_url
                    self._git_token = kwargs.get("git_token")
                    if not self._git_token:
                        # If we have 'git_token_environment_var' use to try and get the token from the user environment
                        git_token_var_name = self._package_configuration_data.get("git_token_environment_var")
                        self._git_token = os.environ.get(git_token_var_name) if git_token_var_name else None

        def _validate_network_options():

            remote_debugging: Optional[str] = kwargs.get("remote_debugging", None)
            proxy_server: Optional[str] = kwargs.get("proxy_server", None)

            if isinstance(remote_debugging, str):
                self._remote_debugging = ToolBox.get_address_and_port(remote_debugging)
                if self._remote_debugging is None:
                    raise ValueError(f"the specified remote debugging address '{remote_debugging}' is invalid. "
                                     f"Expected format: <host>:<port> (e.g., localhost:5678)")
            if isinstance(proxy_server, str):
                self._proxy_server = ToolBox.get_address_and_port(proxy_server)
                if self._proxy_server is None:
                    raise ValueError(f"the specified proxy server address '{proxy_server}' is invalid. "
                                     f"Expected format: <host>:<port> (e.g., www.proxy.com:8080)")

        # Orchestrate
        _init_arguments()
        _validate_solution_package()
        _validate_network_options()

    def _attach_debugger(self, host: str = 'localhost', port: int = 5678, abort_execution: bool = True) -> None:
        """
        Attempt to attach to a remote PyCharm debugger.
        Args:
            host (str, optional): The debugger host to connect to. Defaults to 'localhost'.
            port (int, optional): The debugger port to connect to. Defaults to 5678.
            abort_execution (bool, optional): If True, raise the exception on failure. If False, log and continue.
        """
        try:

            # noinspection PyUnresolvedReferences
            import pydevd_pycharm
            # Redirect stderr temporarily to suppress pydevd's traceback
            with contextlib.redirect_stderr(io.StringIO()):
                pydevd_pycharm.settrace(host=host, port=port, stdoutToServer=True, stderrToServer=True, suspend=False,
                                        trace_only_current_thread=True)

        except ImportError:
            self._queue_logger.warning("'pydevd_pycharm' is not installed; skipping remote debugging")

        except Exception as exception:
            if abort_execution:
                raise exception

    def forge(self) -> Optional[int]:
        """
        Load a solution and fire the AutoForge shell.
        """
        return_code = 1

        try:

            # Configure and start watchdog with default or configuration provided timeout.
            self._watchdog_timeout = self._package_configuration_data.get("watchdog_timeout", self._watchdog_timeout)
            self._watchdog = Watchdog(default_timeout=self._watchdog_timeout)

            # Remove anny previously generated autoforge temporary files.
            ToolBox.clear_residual_files()

            if self._solution_url:
                # Download all files in a given remote git path to a local zip file
                self._solution_package_file = (
                    self._environment.git_get_path_from_url(url=self._solution_url, delete_if_exist=True,
                                                            proxy_host=self._proxy_server, token=self._git_token))

            if self._solution_package_file is not None and self._solution_package_path is None:
                self._solution_package_path = ToolBox.uncompress_file(archive_path=self._solution_package_file)

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

            # Start user telemetry
            telemetry_path = self._variables.expand("$AF_SOLUTION_BASE/telemetry.log")
            self._telemetry = BuildTelemetry.load(telemetry_path)

            self._watchdog.stop()  # Stopping Initialization protection watchdog

            if self._work_mode == AutoForgeWorkModeType.INTERACTIVE:

                # ==============================================================
                # User interactive shell.
                # Indefinite loop until user exits the shell using 'quit'
                # ==============================================================

                # Greetings earthlings, we're here!
                self._tool_box.print_logo(clear_screen=True, terminal_title=f"AutoForge: {self._solution_name}",
                                          blink_pixel=XYType(x=1, y=5))

                # Start blocking build system user mode shell
                self._tool_box.set_terminal_input()  # Disable user input until the prompt is active
                self._gui: CoreGUI = CoreGUI()
                self._prompt = CorePrompt()

                # Start user prompt loop
                self._prompt.cmdloop()
                return_code = self._prompt.last_result

            elif self._work_mode == AutoForgeWorkModeType.NON_INTERACTIVE:

                # ==============================================================
                #  Execute a command or sequence of operations in non-
                #  interactive mode and exit.
                # ==============================================================

                if self._run_sequence_ref_name is not None:

                    # ==============================================================
                    #  Running sequence of operations.
                    # ==============================================================

                    # Get the sequence dictionary from the solution
                    sequence_data = self._solution.get_sequence_by_name(sequence_name=self._run_sequence_ref_name)
                    if not isinstance(sequence_data, dict):
                        raise ValueError(
                            f"sequence reference name '{self._run_sequence_ref_name}' was not found in '{self._solution_name}'")

                    return_code = self._environment.run_sequence(sequence_data=sequence_data)
                    if return_code == 0:
                        # Lastly store the solution in the newly created workspace.
                        scripts_path = self._variables.get(key="SCRIPTS_BASE")
                        if scripts_path is not None:
                            solution_destination_path = os.path.join(scripts_path, 'solution')
                            env_starter_file: Path = PROJECT_SHARED_PATH / 'env.sh'

                            # Move all project specific jsons along with any zip files to the destination path.
                            self._tool_box.cp(
                                pattern=f'{self._solution_package_path}/*.json*,{self._solution_package_path}/*.zip',
                                dest_dir=solution_destination_path)

                            # Place the build system default initiator script
                            self._tool_box.cp(pattern=f'{env_starter_file.__str__()}', dest_dir=self._workspace_path)

                            # Place the sequence log to the newly created workspace logs path.
                            if self._sequence_log_file:
                                self._tool_box.cp(pattern=f'{self._sequence_log_file.__str__()}',
                                                  dest_dir=self._variables.get(key="BUILD_LOGS"))

                            # Finally, create a hidden '.config' file in the solution directory with essential metadata.
                            self._environment.create_config_file(solution_name=self._solution_name,
                                                                 create_path=self._workspace_path)
                elif self._run_command_name:

                    # ==============================================================
                    #  Running command in non interactive mode.
                    # ==============================================================

                    raise RuntimeError(f"running command '{self._run_command_name}' is not yet implemented")

            else:
                raise RuntimeError(f"work mode '{self._work_mode}' not supported")

            return return_code

        except Exception:  # Propagate
            raise

    @property
    def package_configuration(self) -> Optional[dict[str, Any]]:
        """ Returns the package configuration processed JSON """
        return self._package_configuration_data

    @property
    def telemetry(self) -> Optional[BuildTelemetry]:
        """ Returns the AutoForge telemetry class instance """
        return self._telemetry

    @property
    def watchdog(self) -> Optional[Watchdog]:
        """ Returns the Package watchdog instance """
        return self._watchdog

    @property
    def root_logger(self) -> Optional[AutoLogger]:
        """ AutoForge root logger instance """
        return self._auto_logger


def auto_forge_main() -> Optional[int]:
    """
    Console entry point for the AutoForge build suite.
    This function handles user arguments and launches AutoForge to execute the required test.
    Returns:
        int: Exit code of the function.
    """

    result: int = 1  # Default to internal error

    # Force single instance
    if ToolBox.is_another_autoforge_running():
        print("\nError: Another instance of AutoForge is already running. Aborting.\n", file=sys.stderr)
        sys.exit(1)

    try:
        # Check early for the version flag before constructing the parser
        if len(sys.argv) == 2 and sys.argv[1] in ("-v", "--version"):
            print(f"\n{PROJECT_NAME} Version: {PROJECT_VERSION}\n")
            sys.exit(0)

        # Normal arguments handling
        parser = argparse.ArgumentParser(prog="autoforge",
                                         description=f"\033c{AutoForge.who_we_are()} BuildSystem Arguments:")

        # Required argument specifying the workspace path. This can point to an existing workspace
        # or a new one to be created by AutoForge, depending on the solution definition.
        parser.add_argument("-w", "--workspace-path", required=True,
                            help="Path to an existing or new workspace to be used by AutoForge.")

        parser.add_argument("-n", "--solution-name", required=True,
                            help="Name of the solution to use. It must exist in the solution file.")

        # AutoForge requires a solution to operate. This can be provided either as a pre-existing local ZIP archive,
        # or as a Git URL pointing to a directory containing the necessary solution JSON files.

        parser.add_argument("-p", "--solution-package", required=False,
                            help=("Path to an AutoForge solution package. This can be either:\n"
                                  "- Path to an existing .zip archive file.\n"
                                  "- Path to an existing directory containing solution files.\n"
                                  "- Github URL pointing to git path which contains the solution files.\n"
                                  "The package path will be validated at runtime, if not specified, the solution will "
                                  "be searched for in the local solution workspace path under 'scripts/solution'"))

        # AutoForge supports two mutually exclusive non-interactive modes:
        # (1) Running step recipe data (typically used to set up a fresh workspace),
        # (2) Running a single command from an existing workspace.
        # Only one of these modes may be used at a time.

        group = parser.add_mutually_exclusive_group()
        group.add_argument("-s", "--run_sequence", type=str, required=False,
                           help="Solution properties name which points to a sequence of operations")
        group.add_argument("-r", "--run-command", type=str, required=False,
                           help="Name of known command which will be executed")

        # Other optional configuration arguments
        parser.add_argument("-d", "--remote-debugging", type=str, required=False,
                            help="Remote debugging endpoint in the format <ip-address>:<port> (e.g., 127.0.0.1:5678)")

        parser.add_argument("--proxy-server", type=str, required=False,
                            help="Optional proxy server endpoint in the format <ip-address>:<port> (e.g., 192.168.1.1:8080).")

        parser.add_argument("--git-token", type=str, required=False,
                            help="Optional GitHub token to use for authenticating HTTP requests.")

        args = parser.parse_args()
        # Instantiate AutoForge, pass all arguments
        auto_forge: AutoForge = AutoForge(**vars(args))
        return auto_forge.forge()

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
        ToolBox.set_terminal_input(state=True)  # Restore terminal input

    return result
