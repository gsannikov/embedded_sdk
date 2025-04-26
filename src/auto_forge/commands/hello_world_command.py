"""
Script:         hello_command.py
Author:         AutoForge Team

Description:
    Sample 'hello world' command demonstrating how to dynamically add CLI commands to AutoForge.
"""

import argparse
from typing import Any

# AutoForge imports
from auto_forge import CLICommandInterface, AutoLogger


class HelloCommand(CLICommandInterface):
    """
    A simple 'hello world' command example for AutoForge CLI.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the HelloCommand class.
        Args:
            **_kwargs (Any): Optional keyword arguments, such as:
                - raise_exceptions (bool): If True, raises exceptions on error instead of returning error codes.
        """
        self._logger = AutoLogger().get_logger(name='hello')

        super().__init__(command_name="hello")

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument(
            "-t", "--text",
            type=str,
            help="Optional text to greet."
        )

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """
        if args.text:
            print(f"Hello '{args.text}' 😎")

            self._logger.info(f"'{self._command_name}' executed successfully")
            return 0

        self._logger.warning("HelloCommand called without required arguments")
        return CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS
