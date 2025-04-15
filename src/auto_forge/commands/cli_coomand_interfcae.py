"""
Script:         cli_command_interface.py
Author:         Intel AutoForge Team

Description:
    This module defines the `CLICommand` abstract base class, which provides a standardized
    interface for implementing modular, pluggable command-line commands within the AutoForge framework.

    Each command subclass is responsible for:
        - Declaring its name and description.
        - Registering its CLI arguments using `argparse`.
        - Implementing execution logic based on parsed arguments.

    The interface supports both programmatic and shell-style invocation, enabling dynamic discovery
    and execution of commands across tools and environments.
"""

import argparse
import shlex
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class CLICommand(ABC):
    """
    Abstract base class for CLI commands that can be dynamically registered and executed.
    Each derived class must define its name, description, argument parser, and run logic.
    """

    def __init__(self):
        """
        Initializes the CLICommand and prepares its argument parser using
        the name and description provided by the subclass.
        """
        self._parser: argparse.ArgumentParser = argparse.ArgumentParser(
            prog=self.get_name(),
            description=self.get_description()
        )
        self.create_parser(self._parser)
        super().__init__()

    def _extract_short_arg_map(self) -> Dict[str, str]:
        """
        Dynamically builds a mapping of short option flags (e.g. '-v') to their
        corresponding argument destination names (e.g. 'verbose').

        Returns:
            Dict[str, str]: Mapping from single-letter short options to argparse dest names.
        """
        short_map = {}
        for action in self._parser._actions:
            if action.option_strings and len(action.option_strings) >= 2:
                for opt in action.option_strings:
                    if opt.startswith('-') and not opt.startswith('--'):
                        short_flag = opt.lstrip('-')
                        short_map[short_flag] = action.dest
        return short_map

    def execute(self, command: Optional[str] = None, **kwargs: Any) -> int:
        """
        Executes the command using either a shell-style string or structured kwargs.

        Args:
            command (Optional[str]): If provided, a raw shell-style string (e.g., "--flag -v").
            **kwargs: Alternatively, keyword-style argument values (e.g., flag=True, count=3).

        Returns:
            int: 0 on success, non-zero on failure (e.g., usage error).
        """
        if command is not None:
            args_list = shlex.split(command.strip())
        else:
            args_list = []
            for key, value in kwargs.items():
                cli_key = f'--{key.replace("_", "-")}'
                if isinstance(value, bool):
                    if value:
                        args_list.append(cli_key)
                else:
                    args_list.extend([cli_key, str(value)])

        try:
            args = self._parser.parse_args(args_list)
            return self.run(args)
        except SystemExit:
            # argparse internally calls sys.exit() on error; trap it and return non-zero
            return 1

    @abstractmethod
    def get_name(self) -> str:
        """
        Returns:
            str: The CLI keyword name for this command (e.g., 'build', 'test').
        """
        pass

    @abstractmethod
    def get_description(self) -> str:
        """
        Returns:
            str: A human-readable description of the command, shown in help.
        """
        pass

    @abstractmethod
    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-specific arguments to the provided parser.

        Args:
            parser (argparse.ArgumentParser): Parser to populate with arguments.
        """
        pass

    @abstractmethod
    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the actual logic of the command after parsing.

        Args:
            args (argparse.Namespace): Parsed arguments.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        pass
