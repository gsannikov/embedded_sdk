#!/usr/bin/env python3
"""
Script:     auto_forge.py
Author:     Intel AutoForge team

Description:
    This module serves as the core of the AutoForge system. It is responsible for initializing all core libraries
    and loading various configuration files. The main objective is to provide a fully loaded and validated build
    environment based on the specified solution configurations.
"""

import argparse
import logging
import os
import sys
from typing import Optional

# Internal AutoForge imports
from auto_forge import (logger_setup, VariablesLib, SolutionProcessorLib, SetupToolsLib, PROJECT_NAME,
                        PROJECT_RESOURCES_PATH, PROJECT_VERSION)


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
            workspace_path (str): Path to the workspace folder.
            automated_mode (bool): Set to run in automated mode (CI).
        """

        if not self._is_initialized:

            self._workspace_path: Optional[str] = workspace_path
            self._logger: Optional[logging.Logger] = logger_setup(level=logging.DEBUG, no_colors=False)
            self._solution_file: Optional[str] = None
            self._solution_name: Optional[str] = None
            self._varLib: Optional[VariablesLib] = None
            self._solutionLib: Optional[SolutionProcessorLib] = None

            try:
                self.tools: SetupToolsLib = SetupToolsLib(workspace_path=workspace_path, automated_mode=automated_mode)
                self._workspace_path = self.tools.set_workspace()
                self._is_initialized = True

            # Propagate
            except Exception:
                raise

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

            self._solutionLib = SolutionProcessorLib(solution_config_file_name=solution_file)
            self._varLib = VariablesLib()  # Get an instanced of the singleton variables class

            # Store the primary solution name
            self._solution_name = self._solutionLib.get_primary_solution_name()
            self._logger.debug(f"Primary solution: '{self._solution_name}'")
            return 0

        # Propagate
        except Exception:
            raise

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
        parser = argparse.ArgumentParser(description=PROJECT_NAME)
        parser = argparse.ArgumentParser(prog="auto_forge", description="AutoForge Package Help")
        parser.add_argument("-w", "--workspace_path", required=True,
                            help="Project workspace path")

        parser.add_argument("-s", "--solution_file", required=False,
                            help="Manage a solution by executing a solution file.")

        parser.add_argument("-st", "--steps_file", required=False,
                            help="Create environment by execution steps file and exit")
        parser.add_argument("-am", "--automated_mode", action="store_true", help="Set to enable automation mode")
        parser.add_argument("-sd", "--demo_solution", action="store_true", help="Set to execute a demo solution")
        parser.add_argument("-std", "--demo_steps", action="store_true", help="Set to execute demo steps")
        parser.add_argument("-v", "--version", action="store_true", help="Show version and exit")
        args = parser.parse_args()

        # Show apackage version and exit
        if args.version:
            print(f"Version: {PROJECT_VERSION}")
            return 0

        # Instantiate AutoForge with a given workspace
        auto_forge: AutoForge = AutoForge(workspace_path=args.workspace_path, automated_mode=args.automated_mode)

        # Normal flow excepting a solution
        if args.solution_file is not None:
            # Expand as needed
            args.solution_file = auto_forge.tools.env_expand_var(input_string=args.solution_file, to_absolute=True)
            if os.path.exists(args.solution_file):
                return auto_forge.load_solution(solution_file=args.solution_file)
            RuntimeError(f"could not located provided solution file '{args.solution_file}")

        else:
            # Executing the packge builtin demo solution
            if args.demo_solution:
                demo_solution_file = os.path.join(PROJECT_RESOURCES_PATH.__str__(), "demo_project", "solution.jsonc")
                if os.path.exists(demo_solution_file):
                    return auto_forge.load_solution(solution_file=demo_solution_file, is_demo=True)
                RuntimeError(f"could not located demo solution file '{demo_solution_file}")

        # Execute steps file
        if args.steps_file is not None:
            # Expand as needed
            args.steps_file = auto_forge.tools.env_expand_var(input_string=args.steps_file)
            if os.path.exists(args.steps_file):
                auto_forge.tools.run_steps(steps_file=args.steps_file)
            RuntimeError(f"could not located provided steps file '{args.steps_file}")
        else:
            # Executing the packge builtin demo steps
            if args.demo_steps:
                demo_steps_file = os.path.join(PROJECT_RESOURCES_PATH.__str__(), "demo_project", "setup.jsonc")
                if os.path.exists(demo_steps_file):
                    auto_forge.tools.run_steps(steps_file=demo_steps_file)
                RuntimeError(f"could not located demo steps file '{demo_steps_file}")

        return 0

    except KeyboardInterrupt:
        print("033[A\r", end='')
        print("Interrupted by user, shutting down..")

    except Exception as runtime_error:
        # Should produce 'friendlier' error message than the typical Python backtrace.
        exc_type, exc_obj, exc_tb = sys.exc_info()  # Get exception info
        file_name = os.path.basename(exc_tb.tb_frame.f_code.co_filename)  # Get the file where the exception occurred
        line_number = exc_tb.tb_lineno  # Get the line number where the exception occurred
        print(f"Exception {runtime_error}\nFile: {file_name}\nLine: {line_number}\n")

    return result
