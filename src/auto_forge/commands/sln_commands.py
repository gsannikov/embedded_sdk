"""
Script:         sln_command.py
Author:         AutoForge Team

Description:
    Solution general purpose management utilities.

"""

import argparse
from pathlib import Path
from typing import Any, Optional

# Third-party
from colorama import Fore
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

# AutoForge imports
from auto_forge import (
    CLICommandInterface,
    CoreEnvironment,
    CoreSolution,
    CoreVariables,
    ToolBox,
    FieldColorType)

AUTO_FORGE_MODULE_NAME = "sln"
AUTO_FORGE_MODULE_DESCRIPTION = "Solution utilities"
AUTO_FORGE_MODULE_VERSION = "1.0"


class SolutionCommand(CLICommandInterface):
    """
    A simple 'hello world' command example for AutoForge CLI.
    """

    def __init__(self, **kwargs: Any):
        """
        Initializes the HelloCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments, such as:
                - raise_exceptions (bool): If True, raises exceptions on error instead of returning error codes.
        """

        self._solution: Optional[CoreSolution] = None
        self._variables: Optional[CoreVariables] = None
        self._environment: Optional[CoreEnvironment] = None
        self._tool_box: Optional[ToolBox] = ToolBox.get_instance()

        # Extract optional parameters
        raise_exceptions: bool = kwargs.get('raise_exceptions', False)

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME,
                         raise_exceptions=raise_exceptions)

    def _print_variables_table(self):
        """
        Display the list of managed variables in a styled table using Rich.
        This method is fully compatible with cmd2 and does not rely on self.console.
        """

        console = Console()

        def _bool_emoji(bool_value: Optional[bool]) -> str:
            if bool_value is True:
                return "[green]✔[/]"
            elif bool_value is False:
                return "[red]✘[/]"
            return "[dim]-[/]"

        var_list: list = self._variables.export()
        project_workspace: Optional[str] = self._variables.get('PROJ_WORKSPACE', quiet=True)

        if not isinstance(var_list, list) or not var_list:
            print("Error: no variables to display.")
            return

        table = Table(title="Managed Variables", box=box.ROUNDED)

        # Define columns based on VariableFieldType
        table.add_column("Name", style="bold cyan", no_wrap=True)
        table.add_column("Value", style="green")
        table.add_column("Description", style="dim")
        table.add_column("Is Path", style="yellow", justify="center")
        table.add_column("Create If Missing", justify="center")

        for var in var_list:
            name = str(var.get("name", "") or "")
            description = str(var.get("description", "") or "")
            is_path = var.get("is_path", False)
            value = var.get("value", "")

            # Build styled value text
            value_text = Text()
            if (
                    project_workspace
                    and is_path
                    and isinstance(value, str)
                    and value.startswith(project_workspace)
            ):
                try:
                    rel_path = Path(value).relative_to(project_workspace)
                    value_text.append("<ws>", style="blue")
                    value_text.append(f"/{rel_path}")
                except ValueError:
                    value_text = Text(str(value))
            else:
                value_text = Text(str(value))

            table.add_row(
                name, value_text, description,
                _bool_emoji(is_path),
                _bool_emoji(var.get("create_path_if_not_exist"))
            )

        console.print('\n', table, '\n')

    def _show_log(self, cheerful: bool) -> None:
        """
        Display the AutoForge logger output with color-coded fields.
        Args:
            cheerful (bool): If True, display the log with enhanced formatting or emotive tone.
                             Otherwise, use a more standard presentation.
        """

        field_colors = [
            FieldColorType(field_name="AutoForge", color=Fore.GREEN),
            FieldColorType(field_name="Variables", color=Fore.LIGHTBLUE_EX),
            FieldColorType(field_name="Loader", color=Fore.MAGENTA),
            FieldColorType(field_name="Prompt", color=Fore.LIGHTCYAN_EX),
            FieldColorType(field_name="Solution", color=Fore.LIGHTYELLOW_EX),
            FieldColorType(field_name="Signatures", color=Fore.LIGHTRED_EX),
        ]

        self._solution.auto_forge.get_root_logger().show(cheerful=cheerful, field_colors=field_colors)

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """

        parser.add_argument('-j', '--print-json', action='store_true',
                            help='Print the processed solution JSON file to the terminal.')

        # Logger printout
        parser.add_argument("-l", "--log", action="store_true", help="Show the log output")
        parser.add_argument('-t', '--tutorial', action='store_true',
                            help='Show the solution creation  tutorial.')

        parser.add_argument("-c", "--cheerful", action="store_true", help="Enable colorful log output (only with -l)")

        parser.add_argument('-e', '--show-environment-variables', action='store_true',
                            help='Show session environment variables.')

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """
        return_code: int = 0
        self._solution: CoreSolution = CoreSolution.get_instance()
        self._variables: CoreVariables = CoreVariables.get_instance()

        if args.print_json:
            self._solution.show(pretty=True)  # Pretty print the solution processed JSON
        elif args.show_environment_variables:
            self._print_variables_table()
        elif args.log:
            self._show_log(args.cheerful)
        elif args.tutorial:
            self._tool_box.show_help_file(help_file_relative_path='solution/guide.md')
        else:
            # Error: no arguments
            return_code = CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
