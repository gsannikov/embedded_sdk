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

# Third-party imports
from colorama import Fore, Style

# Local application imports
from auto_forge import (PROJECT_COMMANDS_PATH, PROJECT_BUILDERS_PATH, PROJECT_SHARED_PATH, PROJECT_CONFIG_FILE,
                        PROJECT_NAME, PROJECT_VERSION, AutoForgeWorkModeType, AddressInfoType, AutoLogger,
                        BuildTelemetry, CoreEnvironment, CoreGUI, CoreLoader, CoreModuleInterface, CoreProcessor,
                        CorePrompt, CoreSolution, CoreVariables, ExceptionGuru, LogHandlersTypes, XYType, Registry,
                        ToolBox, )


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
        self._solution: Optional[CoreSolution] = None
        self._solution_file: Optional[str] = None
        self._solution_name: Optional[str] = None
        self._variables: Optional[CoreVariables] = None
        self._gui: Optional[CoreGUI] = None
        self._prompt: Optional[CorePrompt] = None
        self._telemetry: Optional[BuildTelemetry] = None
        self.work_mode: AutoForgeWorkModeType = AutoForgeWorkModeType.UNKNOWN

        # Startup arguments
        self._automated_mode: bool = False
        self._package_configuration_data: Optional[dict[str, Any]] = None
        self._workspace_path: Optional[str] = None
        self._automation_macro: Optional[str] = None
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
        - Run in non-interactive (automation) mode and execute automation macro.
        Args:
            kwargs: Arguments passed from the command line, validated and analyzed internally.
        """

        # Initialize the most basic and essential core modules, registry must come first.
        self._registry: Registry = Registry()
        self._tool_box: Optional[ToolBox] = ToolBox()
        self._processor: Optional[CoreProcessor] = CoreProcessor()

        # Pass all received arguments down to _validate_arguments
        self._validate_arguments(*args, **kwargs)

        # Start remote debugging ASAP if enabled.
        if self._remote_debugging is not None:
            self._attach_debugger(host=self._remote_debugging.host, port=self._remote_debugging.port)

        # Load AutoForge package configuration and several dictionaries we might need later
        self._package_configuration_data = self._processor.preprocess(PROJECT_CONFIG_FILE)
        self.ansi_codes = self._package_configuration_data.get(
            "ansi_codes") if "ansi_codes" in self._package_configuration_data else None

        # Greetings
        print(f"{self.ansi_codes.get('SCREEN_CLS_SB')}\n\n"
              f"{AutoForge.who_we_are()} v{PROJECT_VERSION} starting...\n")

        # Initializes the logger
        self._auto_logger: AutoLogger = AutoLogger(log_level=logging.DEBUG,
                                                   configuration_data=self._package_configuration_data)
        self._auto_logger.set_log_file_name("auto_forge.log")
        self._auto_logger.set_handlers(LogHandlersTypes.FILE_HANDLER | LogHandlersTypes.CONSOLE_HANDLER)
        self._logger: logging.Logger = self._auto_logger.get_logger(output_console_state=self._automated_mode)
        self._logger.debug(f"AutoForge version: {PROJECT_VERSION} starting in workspace {self._workspace_path}")

        # Load all builtin commands
        self._loader: Optional[CoreLoader] = CoreLoader()
        self._loader.probe(paths=[PROJECT_COMMANDS_PATH, PROJECT_BUILDERS_PATH])
        self._environment: CoreEnvironment = CoreEnvironment(workspace_path=self._workspace_path,
                                                             automated_mode=self._automated_mode)

    def _validate_arguments(  # noqa: C901 # Acceptable complexity
            self, *_args, **kwargs) -> None:
        """
        Validate command-line arguments and set the AutoForge session execution mode.
        Depending on the inputs, AutoForge will either:
        - Start an interactive user shell, or
        - Enter automated mode and execute the provided automation script.
        - Any validation error will immediately raise an exception and consequently terminate AutoForge.
        Args:
            kwargs: Arguments passed from the command line, validated and analyzed internally.
        Note:
            The logger is likely not yet initialized at this stage, so all errors must be raised directly
            (no logging or print statements should be used here).
        """

        # Get all arguments from kwargs
        def _init_arguments():
            self._solution_name = kwargs.get("solution_name")
            self._workspace_path = kwargs.get("workspace_path")
            self._automation_macro = kwargs.get("automation_macro")
            self._solution_url = kwargs.get("solution_url")
            self._git_token = kwargs.get("git_token")
            nonlocal solution_package, remote_debugging, proxy_server
            solution_package = kwargs.get("solution_package")
            remote_debugging = kwargs.get("remote_debugging")
            proxy_server = kwargs.get("proxy_server")

            # Determine AutForge work mode
            if kwargs.get("create_workspace", False):
                self.work_mode = AutoForgeWorkModeType.ENV_CREATE
            else:
                self.work_mode = AutoForgeWorkModeType.INTERACTIVE

        def _validate_workspace_path():
            """
            Workspace creation behavior:
            - If the workspace path does not exist and creation is enabled (default), AutoForge will create it
                based on the solution package instructions.
            - If the workspace exists and creation is disabled, AutoForge will load the existing workspace.
            - If the workspace does not exist and creation is disabled, an exception will be raised.
            """

            if self._workspace_path is None:
                raise ValueError("workspace path must be provided.")
            self._workspace_path = ToolBox.get_expanded_path(self._workspace_path)
            if not ToolBox.looks_like_unix_path(self._workspace_path):
                raise ValueError(f"the specified path '{self._workspace_path}' does not look like a valid Unix path")

            if self.work_mode != AutoForgeWorkModeType.ENV_CREATE and not os.path.exists(self._workspace_path):
                raise RuntimeError(f"workspace path '{self._workspace_path}' does not exist and creation is disabled")

            # If we were requested to create a workspace, the destination path must be empty
            if (self.work_mode == AutoForgeWorkModeType.ENV_CREATE and os.path.exists(
                    self._workspace_path) and not ToolBox.is_directory_empty(self._workspace_path)):
                raise RuntimeError(f"path '{self._workspace_path}' is not empty while workspace creation is enabled")

        def _validate_macro():
            if self._automation_macro is not None:
                self._automation_macro = ToolBox.get_expanded_path(self._automation_macro)
                if not os.path.isfile(self._automation_macro):
                    raise ValueError(
                        f"automation macro path '{self._automation_macro}' does not exist or is not a file")
                self._automated_mode = True

        def _validate_solution_package():
            """
            Solution package validation:
            AutoForge allows flexible input for the 'solution_package' argument:
            - The user can specify a path to a solution archive (.zip file), or
            - A path to an existing directory containing the solution files.
            - A GitHub URL pointing to git path which contains the solution files.
            Validation ensures that the provided path exists and matches one of the acc
            """
            if not isinstance(solution_package, str):
                return

            if ToolBox.is_url(solution_package):
                self._solution_url = solution_package
                return
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

        def _validate_solution_url():
            """
            Solution URL validation:
            AutoForge allows optionally specifying a Git URL, which will later be used to retrieve solution files.
            The URL must have a valid structure and must point to a path (not to a single file)
            """

            if not self._solution_url:
                return
            is_url_path = ToolBox.is_url_path(self._solution_url)
            if is_url_path is None:
                raise RuntimeError(f"the specified URL '{self._solution_url}' is not a valid Git URL")
            if not is_url_path:
                raise RuntimeError(f"the specified URL '{self._solution_url}' does not point to a valid path")

        def _validate_network_options():
            if remote_debugging:
                self._remote_debugging = ToolBox.get_address_and_port(remote_debugging)
                if self._remote_debugging is None:
                    raise ValueError(f"the specified remote debugging address '{remote_debugging}' is invalid. "
                                     f"Expected format: <ip-address>:<port> (e.g., 127.0.0.1:5678)")
            if proxy_server:
                self._proxy_server = ToolBox.get_address_and_port(proxy_server)
                if self._proxy_server is None:
                    raise ValueError(f"the specified proxy server address '{proxy_server}' is invalid. "
                                     f"Expected format: <ip-address>:<port> (e.g., 127.0.0.1:5678)")

        # Orchestrate
        solution_package = None
        remote_debugging = None
        proxy_server = None
        _init_arguments()
        _validate_workspace_path()
        _validate_macro()
        _validate_solution_package()
        _validate_solution_url()
        _validate_network_options()

    @staticmethod
    def _attach_debugger(host: str = '127.0.0.1', port: int = 5678, abort_execution: bool = False) -> None:
        """
        Attempt to attach to a remote PyCharm debugger.
        Args:
            host (str, optional): The debugger host to connect to. Defaults to '127.0.0.1'.
            port (int, optional): The debugger port to connect to. Defaults to 5678.
            abort_execution (bool, optional): If True, raise the exception on failure. If False, log and continue.
        """
        try:

            # noinspection PyUnresolvedReferences
            import pydevd_pycharm
            # Redirect stderr temporarily to suppress pydevd's traceback
            with contextlib.redirect_stderr(io.StringIO()):
                pydevd_pycharm.settrace(host=host, port=port, stdoutToServer=False, stderrToServer=False, suspend=False)

        except Exception as exception:
            if abort_execution:
                raise exception

    def get_package_configuration(self) -> Optional[dict[str, Any]]:
        """ Returns the package configuration processed JSON """
        return self._package_configuration_data

    def get_telemetry(self) -> Optional[BuildTelemetry]:
        """ Returns the AutoForge telemetry class instance """
        return self._telemetry

    def get_root_logger(self) -> Optional[AutoLogger]:
        """ AutoForge root logger instance """
        return self._auto_logger

    def forge(self) -> Optional[int]:
        """
        Load a solution and fire the AutoForge shell.
        """
        try:
            # Remove anny previously generated autoforge temporary files.
            ToolBox.clear_residual_files()

            if self._solution_url:
                # Download all files in a given remote git path to a local zip file
                self._solution_package_file = (
                    self._environment.git_get_path_from_url(url=self._solution_url, delete_if_exist=True,
                                                            proxy_host=self._proxy_server, token=self._git_token))

            if self._solution_package_file is not None and self._solution_package_path is None:
                self._solution_package_path = ToolBox.unzip_file(self._solution_package_file)

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

            self._variables = CoreVariables.get_instance()  # Get an instanced of the singleton variables class
            self._environment.refresh_variables()  # Update the variables instance in the environment module.

            self._logger.debug(f"Solution: '{self._solution_name}' loaded and expanded")

            if self.work_mode == AutoForgeWorkModeType.INTERACTIVE:

                # Start user telemetry
                telemetry_path = self._tool_box.get_expanded_path("~/.auto_forge.telemetry")
                self._telemetry = BuildTelemetry.load(telemetry_path)

                # ==============================================================
                # User interactive shell.
                # Indefinite loop until user exits the shell using 'quit'
                # ==============================================================

                # Greetings earthlings, we're here!
                self._tool_box.print_logo(clear_screen=True, terminal_title=f"AutoForge: {self._solution_name}",
                                          blink_pixel=XYType(x=6, y=2))

                # Start blocking build system user mode shell
                self._tool_box.set_terminal_input(state=False)  # Disable user input until the prompt is active
                self._gui: CoreGUI = CoreGUI()
                self._prompt = CorePrompt()

                # Start user prompt loop
                # noinspection SpellCheckingInspection
                prompt_intro: str = (
                    f"🛠️  Welcome to the \033[1m'{self._solution_name.capitalize()}'\033[0m solution!\n"
                    f"👉 Type \033[1mhelp\033[0m or \033[1m?\033[0m to list available commands.\n")

                self._prompt.cmdloop(intro=prompt_intro)
                ret_val = self._prompt.last_result

            elif self.work_mode == AutoForgeWorkModeType.ENV_CREATE:

                # ==============================================================
                # Execute workspace creation script
                # Follow workspace setup steps as defined by the solution.
                # ==============================================================

                env_steps_file: Optional[str] = self._solution.get_arbitrary_item('environment', deep_search=True)
                if env_steps_file is None:
                    raise RuntimeError("an environment steps file was not specified in the solution")

                # Execute suction creation steps
                ret_val = self._environment.follow_steps(steps_file=env_steps_file)
                if ret_val == 0:
                    # Lastly store the solution in the newly created workspace
                    scripts_path = self._variables.get(key="SCRIPTS_BASE")
                    if scripts_path is not None:
                        solution_destination_path = os.path.join(scripts_path, 'solution')
                        env_starter_file: Path = PROJECT_SHARED_PATH / 'env.sh'

                        self._tool_box.cp(pattern=f'{self._solution_package_path}/*.jsonc',
                                          dest_dir=f'{solution_destination_path}')

                        # Place the build system default initiator script
                        self._tool_box.cp(pattern=f'{env_starter_file.__str__()}', dest_dir=f'{self._workspace_path}')

                        # Finally, create a hidden '.config' file in the solution directory with essential metadata.
                        self._environment.create_config_file(solution_name=self._solution_name,
                                                             create_path=self._workspace_path)

            else:
                raise RuntimeError(f"work mode '{self.work_mode}' not supported")

            return ret_val

        except Exception:  # Propagate
            raise


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
        group = parser.add_mutually_exclusive_group(required=True)

        group.add_argument("-p", "--solution-package", required=False,
                           help=("Path to a local AutoForge solution. This can be either:\n"
                                 "- A path to an existing .zip archive file.\n"
                                 "- A path to an existing directory containing solution files.\n"
                                 "- A Github URL pointing to git path which contains the solution files.\n"
                                 "The provided path will be validated at runtime."))

        # Optional arguments and flags
        parser.add_argument("--create-workspace", dest="create_workspace", action="store_true", default=True,
                            help="Create the workspace if it does not exist (default: True).")
        parser.add_argument("--no-create-workspace", dest="create_workspace", action="store_false",
                            help="Do not create the workspace if it does not exist (raises an error instead).")

        parser.add_argument("--automation-macro", type=str, required=False,
                            help="Path to a JSON file defining an automatic flow to execute after loading the workspace.")

        parser.add_argument("--remote-debugging", type=str, required=False,
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
        print(f"\n\n{Fore.RED}Exception:{Style.RESET_ALL} {runtime_error}.\nFile: {file_name}\nLine: {line_number}\n")

    finally:
        ToolBox.set_terminal_input(state=True, flush=True)  # Restore terminal input

    return result
