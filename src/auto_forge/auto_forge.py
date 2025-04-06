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

from colorama import init

# Internal AutoForge imports
from auto_forge import (logger_setup, VariablesLib, SolutionProcessorLib, PROJECT_CONFIG_PATH)


class AutoForge:
    def __init__(self, solution_file: str, logger: logging.Logger):
        """
        Initializes AutoForge main class - placeholder.
        Args:
            logger (object): an instance of a configured logger object.
        """
        init(autoreset=True, strip=False)  # Required by 'colorama'

        self._logger = logger
        self._logger.propagate = True
        self.varLib: Optional[VariablesLib] = None
        self.solutionLib: Optional[SolutionProcessorLib] = None

        try:
            # Load the solution file, preprocess it and make it ready for execution
            self.solutionLib = SolutionProcessorLib(solution_file_name=solution_file)
            self.varLib = VariablesLib()  # Get an instanced of the singleton variables class

            self._logger.debug(f"Initialized")

        except Exception as init_error:
            raise RuntimeError(f"initialization error: {init_error}.")


def auto_forge_main() -> Optional[int]:
    """
    Console entry point for the AutoForge build suite.
    This function handles user arguments and launches AutoForge to execute the required test.

    Returns:
        int: Exit code of the function.
    """

    result: int = 1  # Default to internal error
    logger  = logger_setup(level=logging.DEBUG, no_colors=False)

    try:

        # For now, we assume that the solution is in the library 'config' path
        solution_file: Path = PROJECT_CONFIG_PATH / "solution.jsonc"

        # Instantiate AutoForge
        auto_forge: AutoForge = AutoForge(solution_file=solution_file.__str__(), logger=logger)

        # Example: iterating through a searched list of configurations
        config_list = auto_forge.solutionLib.get_configurations_list(solution_name="imcv2", project_name="zephyr")
        for config in config_list:
            config_data = auto_forge.solutionLib.query_configurations(solution_name="imcv2",
                                                                      project_name="zephyr", configuration_name=config)
            print(f"- {config}, board: {config_data.get('board')}")
        return 0

    except KeyboardInterrupt:
        print("033[A\r", end='')
        if logger is not None:
            logger.error("Interrupted by user, shutting down..")

    except Exception as runtime_error:
        # Should produce 'friendlier' error message than the typical Python backtrace.
        exc_type, exc_obj, exc_tb = sys.exc_info()  # Get exception info
        file_name = os.path.basename(exc_tb.tb_frame.f_code.co_filename)  # Get the file where the exception occurred
        line_number = exc_tb.tb_lineno  # Get the line number where the exception occurred
        print(f"Runtime error:{runtime_error}\nFile: {file_name}\nLine: {line_number}\n")

    return result
