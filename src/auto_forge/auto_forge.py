#!/usr/bin/env python3
"""
Script:         auto_forge.py
Author:         AutoForge Team

Description:
    This module serves as the core of the AutoForge system.
    Here we initialize all core libraries, parse and load the various configuration files,
    dynamically load CLI commands and start the build system shell.
"""

import argparse
import logging
import os
import sys
from typing import Optional

# Colorama
from colorama import Fore, Style

# Internal AutoForge imports
from auto_forge import (ToolBox, CoreModuleInterface, CoreProcessor, CoreVariables, CoreGUI,
                        CoreSolution, CoreEnvironment, CoreLoader, CorePrompt, Registry, AutoLogger, LogHandlersTypes,
                        ExceptionGuru, PROJECT_RESOURCES_PATH, PROJECT_VERSION, PROJECT_NAME, PROJECT_COMMANDS_PATH)


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

        super().__init__(*args, **kwargs)

    def _initialize(self, workspace_path: str, automated_mode: Optional[bool] = False) -> None:
        """
        Initialize the AutoForge core system and prepare the workspace environment.
        Depending on the context, this may involve creating a new workspace or reusing
        an existing one. Also configures the system for automated (CI) mode if enabled.

        Args:
            workspace_path (str): Absolute path to the workspace directory.
            automated_mode (bool, optional): If True, enables CI-safe, non-interactive behavior.
        """

        self._registry: Registry = Registry()

        if not isinstance(workspace_path, str):
            raise RuntimeError("argument 'workspace' must be a string")

        # Initializes the logger
        self._auto_logger: AutoLogger = AutoLogger(log_level=logging.DEBUG)
        self._auto_logger.set_log_file_name("auto_forge.log")
        self._auto_logger.set_handlers(LogHandlersTypes.FILE_HANDLER | LogHandlersTypes.CONSOLE_HANDLER)
        self._logger: logging.Logger = self._auto_logger.get_logger(output_console_state=automated_mode)
        self._logger.debug("AutoForge starting...")

        # Initialize core modules
        self._toolbox: Optional[ToolBox] = ToolBox()
        self._processor: Optional[CoreProcessor] = CoreProcessor()

        # Load all the builtin commands
        self._loader: Optional[CoreLoader] = CoreLoader()
        self._loader.probe(path=PROJECT_COMMANDS_PATH)

        self._environment: CoreEnvironment = CoreEnvironment(workspace_path=workspace_path,
                                                             automated_mode=automated_mode)

        self._gui: CoreGUI = CoreGUI()

        # Show startup branding
        self._toolbox.print_logo(clear_screen=True)

    @staticmethod
    def show_version(exit_code: Optional[int] = None) -> None:
        """
        Prints the current AutoForge version.
        Args:
            exit_code (Optional[int]): If provided, exits the program with the given code after printing.
        """
        print(f"\n{PROJECT_NAME} Version: {PROJECT_VERSION}")
        if exit_code is not None:
            sys.exit(exit_code)

    def load_solution(self, solution_file: Optional[str] = None, is_demo: bool = False) -> Optional[int]:
        """
        Load the solution file, preprocess it and make it ready for execution
         Args:
            solution_file (str): Path to the solution file.
            is_demo (bool): Is this a demo solution?
        """
        try:
            if self._solution is not None:
                raise RuntimeError(f"solution already loaded.")

            if is_demo:
                self._logger.warning("Running is demo mode")

            workspace_path = CoreEnvironment.get_workspace_path()
            self._logger.debug(f"Workspace path: {workspace_path}")

            self._solution = CoreSolution(solution_config_file_name=solution_file)
            self._variables = CoreVariables.get_instance()  # Get an instanced of the singleton variables class

            # Store the primary solution name
            self._solution_name = self._solution.get_primary_solution_name()
            self._logger.debug(f"Primary solution: '{self._solution_name}'")

            # Enter build system prompt loop
            self._prompt = CorePrompt()
            return self._prompt.cmdloop()

        # Propagate
        except Exception:
            raise


def auto_forge_main() -> Optional[int]:
    """
    Console entry point for the AutoForge build suite.
    This function handles user arguments and launches AutoForge to execute the required test.

    Returns:
        int: Exit code of the function.
    """
    result: int = 1  # Default to internal error

    try:

        # Check early for the version flag before constructing the parser
        if len(sys.argv) == 2 and sys.argv[1] in ("-v", "--version"):
            AutoForge.show_version(exit_code=0)

        # Normal arguments handling
        parser = argparse.ArgumentParser(prog="autoforge", description="AutoForge Package Help")
        parser.add_argument("-w", "--workspace_path", required=True,
                            help="Project workspace path")

        parser.add_argument("-s", "--solution_file", required=False,
                            help="Manage a solution by executing a solution file.")

        parser.add_argument("-st", "--steps_file", required=False,
                            help="Create environment by execution steps file and exit")
        parser.add_argument("-am", "--automated_mode", action="store_true", help="Set to enable automation mode")
        parser.add_argument("-sd", "--demo_solution", action="store_true", help="Set to execute a demo solution")
        parser.add_argument("-std", "--demo_steps", action="store_true", help="Set to execute demo steps")
        parser.add_argument("-v", "--version", action="store_true", help="Show version")
        args = parser.parse_args()

        # Instantiate AutoForge with a given workspace
        auto_forge: AutoForge = AutoForge(workspace_path=args.workspace_path, automated_mode=args.automated_mode)
        environment: CoreEnvironment = CoreEnvironment.get_instance()

        # Show apackage version
        if args.version:
            auto_forge.show_version()

        # Normal flow excepting a solution
        if args.solution_file is not None:
            # Expand as needed
            args.solution_file = environment.environment_variable_expand(text=args.solution_file,
                                                                         to_absolute_path=True)
            if os.path.exists(args.solution_file):
                return auto_forge.load_solution(solution_file=args.solution_file)
            raise RuntimeError(f"could not located provided solution file '{args.solution_file}")

        else:
            # Executing the packge builtin demo solution
            if args.demo_solution:
                demo_solution_file = os.path.join(PROJECT_RESOURCES_PATH.__str__(), "demo_project", "solution.jsonc")
                if os.path.exists(demo_solution_file):
                    return auto_forge.load_solution(solution_file=demo_solution_file, is_demo=True)
                raise RuntimeError(f"could not located demo solution file '{demo_solution_file}")

        # Execute a steps script
        if args.steps_file is not None:
            # Expand as needed
            args.steps_file = environment.environment_variable_expand(text=args.steps_file, to_absolute_path=True)
            if os.path.exists(args.steps_file):
                return environment.follow_steps(steps_file=args.steps_file)
            raise RuntimeError(f"could not located provided steps file '{args.steps_file}")
        else:
            # Executing this packge builtin demo steps script
            if args.demo_steps:
                demo_steps_file = os.path.join(PROJECT_RESOURCES_PATH.__str__(), "demo_project", "setup.jsonc")
                if os.path.exists(demo_steps_file):
                    return environment.follow_steps(steps_file=demo_steps_file)
                raise RuntimeError(f"could not located demo steps file '{demo_steps_file}")

        return 0

    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted by user, shutting down.{Style.RESET_ALL}\n")

    except Exception as runtime_error:

        # Retrieve information about the original exception that triggered this handler.
        file_name, line_number = ExceptionGuru().get_context()

        # If we can get a logger, use it to log the error.
        logger_instance = AutoLogger.get_base_logger()
        if logger_instance is not None:
            logger_instance.error(f"Exception: {runtime_error}.File: {file_name}, Line: {line_number}")

        print(f"\n{Fore.RED}Exception:{Style.RESET_ALL} {runtime_error}.\nFile: {file_name}\nLine: {line_number}\n")

    return result
