"""
Script:         hello_command.py
Author:         AutoForge Team

Description:
    Sample 'hello world' command demonstrating how to construct
    and register a new command with AutoForge.

"""

import argparse
from typing import Any, Optional

# AutoForge imports
from auto_forge import (CommandInterface, AutoForgCommandType)


# noinspection PyMethodMayBeStatic
class HelloCommand(CommandInterface):
    """
    A simple 'hello world' command example for registering dynamically command.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the HelloCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments.
        """
        super().__init__(command_name="hello", command_type=AutoForgCommandType.MISCELLANEOUS)

    def initialize(self, **_kwargs: Any) -> Optional[bool]:
        """
        Command specific initialization, will be executed lastly by the interface class
        after all other initializers.
        """
        self._logger.info("Initializing 'HelloCommand'..")
        return True

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("-m", "--message", type=str,
                            help="Optional message to print.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """
        return_code: int = 0

        if args.message:
            print(f"You wrote: '{args.message}' 😎")
        else:
            # Error: no arguments
            return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
