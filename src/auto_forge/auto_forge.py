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
from auto_forge import (ToolBox, Variables, Solution, Environment, CommandsLoader,
                        PROJECT_RESOURCES_PATH, PROJECT_VERSION, PROJECT_NAME, AutoLogger, Prompt, AutoHandlers)


class AutoForge:
    _instance = None
    _is_initialized = False

    def __new__(cls, workspace_path: Optional[str] = None, automated_mode: bool = False):
        """
        Basic class initialization in a singleton mode
        """

        if cls._instance is None:
            cls._instance = super(AutoForge, cls).__new__(cls)

        return cls._instance

    def __init__(self, workspace_path: Optional[str] = None, automated_mode: bool = False):
        """
        Initializes AutoForge main class
        Args:
            workspace_path (str, Optional): Path to the workspace folder.
            automated_mode (bool): Set to run in automated mode (CI).
        """

        if not self._is_initialized:

            # Initializes the logger
            self._auto_logger: AutoLogger = AutoLogger(log_level=logging.DEBUG)
            self._auto_logger.set_log_file_name("auto_forge.log")
            self._auto_logger.set_handlers(handlers=AutoHandlers.FILE_HANDLER)

            if automated_mode:
                self._auto_logger.set_handlers(AutoHandlers.FILE_HANDLER | AutoHandlers.CONSOLE_HANDLER)

            self._logger: logging.Logger = self._auto_logger.get_logger()
            self._logger.debug("Initializing...")

            self._toolbox: Optional[ToolBox] = ToolBox(parent=self)
            self._solution_file: Optional[str] = None
            self._solution_name: Optional[str] = None
            self._varLib: Optional[Variables] = None
            self._solutionLib: Optional[Solution] = None

            if not workspace_path:
                raise RuntimeError("'workspace_path' is required when initializing AutoForge")

            self._workspace_path = Environment.environment_variable_expand(text=workspace_path, to_absolute_path=True)

            try:
                self.commands: Optional[CommandsLoader] = CommandsLoader()  # Probe for commands and load them
                self.tools: Environment = Environment(workspace_path=self._workspace_path,
                                                      automated_mode=automated_mode)

                self.prompt = Prompt(commands_loader=self.commands)

                self._toolbox.print_logo(clear_screen=True)  # Show logo
                self._is_initialized = True  # Done initializing

            except Exception:
                raise  # Propagate

    @staticmethod
    def get_instance() -> "AutoForge":
        """
        Returns the singleton instance of the AutoForge class.
        Returns:
            AutoForge: The global AutoForge instance.
        """
        return AutoForge._instance

    @staticmethod
    def get_logger() -> Optional[logging.Logger]:
        """
        Returns the logger instance of the AutoForge class.
        Returns:
            Logger: q logger instance.
        """
        return AutoForge.get_instance()._logger

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

            if self._solutionLib is not None:
                raise RuntimeError(f"solution already loaded.")

            if is_demo:
                self._logger.warning("Running is demo mode")

            self._logger.debug(f"Workspace path: {self._workspace_path}")

            self._solutionLib = Solution(solution_config_file_name=solution_file)
            self._varLib = Variables()  # Get an instanced of the singleton variables class

            # Store the primary solution name
            self._solution_name = self._solutionLib.get_primary_solution_name()
            self._logger.debug(f"Primary solution: '{self._solution_name}'")

            # Enter build system prompt loop
            return self.prompt.cmdloop()

        # Propagate
        except Exception as solution_exception:
            raise solution_exception

    def get_workspace_path(self) -> Optional[str]:
        """
        Returns the full path to the workspace folder.
        """
        if self._workspace_path is None:
            raise RuntimeError("workspace folder not set")

        return self._workspace_path


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

        # Show apackage version
        if args.version:
            auto_forge.show_version()

        # Normal flow excepting a solution
        if args.solution_file is not None:
            # Expand as needed
            args.solution_file = auto_forge.tools.environment_variable_expand(text=args.solution_file,
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
            args.steps_file = auto_forge.tools.environment_variable_expand(text=args.steps_file, to_absolute_path=True)
            if os.path.exists(args.steps_file):
                return auto_forge.tools.follow_steps(steps_file=args.steps_file)
            raise RuntimeError(f"could not located provided steps file '{args.steps_file}")
        else:
            # Executing this packge builtin demo steps script
            if args.demo_steps:
                demo_steps_file = os.path.join(PROJECT_RESOURCES_PATH.__str__(), "demo_project", "setup.jsonc")
                if os.path.exists(demo_steps_file):
                    return auto_forge.tools.follow_steps(steps_file=demo_steps_file)
                raise RuntimeError(f"could not located demo steps file '{demo_steps_file}")

        return 0

    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted by user, shutting down.{Style.RESET_ALL}\n")

    except Exception as runtime_error:
        # Should produce 'friendlier' error message than the typical Python backtrace.
        exc_type, exc_obj, exc_tb = sys.exc_info()  # Get exception info
        file_name = os.path.basename(exc_tb.tb_frame.f_code.co_filename)  # Get the file where the exception occurred
        line_number = exc_tb.tb_lineno  # Get the line number where the exception occurred

        # Attempt to log the error
        logger_instance = AutoForge.get_logger()
        if logger_instance is not None:
            logger_instance.error(f"Exception: {runtime_error}.File: {file_name}, Line: {line_number}")

        print(f"\n{Fore.RED}Exception:{Style.RESET_ALL} {runtime_error}.\nFile: {file_name}\nLine: {line_number}\n")

    return result
