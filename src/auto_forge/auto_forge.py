#!/usr/bin/env python3
"""
Script:     auto_forge.py
Author:     Intel AutoForge team

Description:
    This module serves as the core of the AutoForge system. It is responsible for initializing all core libraries
    and loading various configuration files. The main objective is to provide a fully loaded and validated build
    environment based on the specified solution configurations.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Internal AutoForge imports
from auto_forge import (logger_setup, VariablesLib, SolutionProcessorLib, SetupToolsLib, PROJECT_RESOURCES_PATH)


class AutoForge:
    _instance = None
    _is_initialized = False

    def __new__(cls, workspace_path: Optional[str] = None, logger: Optional[logging.Logger] = None):
        """
        Basic class initialization in a singleton mode
        """

        if cls._instance is None:
            cls._instance = super(AutoForge, cls).__new__(cls)

        return cls._instance

    def __init__(self, workspace_path: Optional[str] = None):
        """
        Initializes AutoForge main class
        Args:
            workspace_path (str): Path to the workspace folder.
        """

        if not self._is_initialized:

            self._workspace_path: Optional[str] = workspace_path
            self._logger: Optional[logging.Logger] = logger_setup(level=logging.DEBUG, no_colors=False)
            self._solution_file: Optional[str] = None
            self._solution_name: Optional[str] = None
            self._varLib: Optional[VariablesLib] = None
            self._solutionLib: Optional[SolutionProcessorLib] = None

            try:
                self._setupLib: SetupToolsLib = SetupToolsLib(workspace_path=workspace_path, logger=self._logger)
                self._workspace_path = self._setupLib.set_workspace()
                self._is_initialized = True

            # Propagate
            except Exception:
                raise

    def load_solution(self, solution_file: Optional[str] = None):
        """
        Load the solution file, preprocess it and make it ready for execution
         Args:
            solution_file (str): Path to the solution file.
        """
        try:

            if self._solutionLib is not None:
                raise RuntimeError(f"solution already loaded.")

            self._solutionLib = SolutionProcessorLib(solution_config_file_name=solution_file)
            self._varLib = VariablesLib()  # Get an instanced of the singleton variables class

            # Store the primary solution name
            self._solution_name = self._solutionLib.get_primary_solution_name()
            self._logger.debug(f"Primary solution: '{self._solution_name}'")

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
    demo_project_path: Path = PROJECT_RESOURCES_PATH / "demo_project"

    try:

        # For now, we assume that the solution is in the library 'config' path
        solution_file: Path = demo_project_path / "solution.jsonc"

        # Instantiate AutoForge
        auto_forge: AutoForge = AutoForge(workspace_path="~/projects/af_install/ws")
        auto_forge.load_solution(solution_file=solution_file.__str__())

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
