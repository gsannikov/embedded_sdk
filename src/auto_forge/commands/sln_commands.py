"""
Script:         sln_command.py
Author:         AutoForge Team

Description:
    Solution general purpose management utilities.

"""

import argparse
import base64
import json
from pathlib import Path
from typing import Any, Optional

# Third-party
from colorama import Fore
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

# AutoForge imports
from auto_forge import (CLICommandInterface, CoreEnvironment, CoreSolution, CoreVariables, CoreProcessor, ToolBox,
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
        self._preprocessor: Optional[CoreProcessor] = CoreProcessor.get_instance()

        # Extract optional parameters
        raise_exceptions: bool = kwargs.get('raise_exceptions', False)

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, raise_exceptions=raise_exceptions)

    def _print_variables_table(self):
        """
        Display the list of managed variables in a styled table using Rich.
        This method is fully compatible with cmd2 and does not rely on self.console.
        """

        console = Console(force_terminal=True)

        def _bool_emoji(bool_value: Optional[bool]) -> str:
            if bool_value is True:
                return "[green]✔[/]"
            elif bool_value is False:
                return "[red]✘[/]"
            return "[dim]-[/]"

        var_list: list = self._variables.export()
        project_workspace: Optional[str] = self._variables.get('PROJ_WORKSPACE')
        solution_name: Optional[str] = self._variables.get('SOLUTION_NAME')
        home_directory = self._tool_box.get_expanded_path(path="$HOME")

        if not isinstance(project_workspace, str) or not isinstance(solution_name, str):
            print("Error: could not get our solution name or project workspace or both")
            return

        if not isinstance(var_list, list) or not var_list:
            print("Error: no variables to display.")
            return

        table = Table(title=f"{solution_name.capitalize()}: Managed Variables", box=box.ROUNDED)

        # Define columns based on VariableFieldType
        table.add_column("Key", style="bold cyan", no_wrap=True)
        table.add_column("Value", style="green")
        table.add_column("Description", style="dim")
        table.add_column("Is Path", style="yellow", justify="center")
        table.add_column("Create If Missing", justify="center")

        for var in var_list:
            key = str(var.get("key", "") or "")
            description = str(var.get("description", "") or "")
            is_path = var.get("is_path", False)
            value = var.get("value", "")

            # Build styled value text
            value_text = Text()
            try:
                if is_path and isinstance(value, str) and value.startswith(project_workspace):
                    rel_path = Path(value).relative_to(project_workspace)
                    value_text.append("$", style="blue")
                    value_text.append(f"/{rel_path}")
                elif is_path and isinstance(value, str) and value.startswith(home_directory):
                    rel_path = Path(value).relative_to(home_directory)
                    value_text.append("~", style="purple")
                    value_text.append(f"/{rel_path}")
                else:
                    value_text = Text(str(value))
            except ValueError:
                value_text = Text(str(value))

            table.add_row(key, value_text, description, _bool_emoji(is_path),
                          _bool_emoji(var.get("create_path_if_not_exist")))

        console.print('\n', table, '\n')

    def _print_log(self, cheerful: bool) -> None:
        """
        Display the AutoForge logger output with color-coded fields.
        Args:
            cheerful (bool): If True, display the log with enhanced formatting or emotive tone.
                             Otherwise, use a more standard presentation.
        """
        field_colors = [FieldColorType(field_name="AutoForge", color=Fore.GREEN),
                        FieldColorType(field_name="Variables", color=Fore.LIGHTBLUE_EX),
                        FieldColorType(field_name="Loader", color=Fore.MAGENTA),
                        FieldColorType(field_name="Prompt", color=Fore.LIGHTCYAN_EX),
                        FieldColorType(field_name="Solution", color=Fore.LIGHTYELLOW_EX),
                        FieldColorType(field_name="Signatures", color=Fore.LIGHTRED_EX), ]

        self._solution.auto_forge.root_logger.show(cheerful=cheerful, field_colors=field_colors)

    def _print_solution(self):
        """Use Textual to show the solution viewer with a clean, structured single-solution summary."""

        # Full solution data for the main JSON tree
        solution_data = self._solution.get_loaded_solution(name_only=False)

        # Just the name for summary
        solution_name = self._solution.get_loaded_solution(name_only=True)

        # Build structured solution summary
        solution_summary = {
            "Solution": solution_name.capitalize(),
            "Projects": []
        }

        for project in self._solution.query_projects():
            project_name = project.get("name")
            toolchain = project.get("tool_chain", {}).get("name", "unknown")

            configurations = self._solution.query_configurations(project_name=project_name)
            config_names = [conf.get("name") for conf in configurations]

            solution_summary["Projects"].append({
                "Name": project_name.capitalize(),
                "Toolchain": toolchain,
                "Configurations": config_names
            })

        # Encode as base64 to safely pass as CLI argument
        json_text = json.dumps(solution_summary)
        panel_arg = base64.b64encode(json_text.encode("utf-8")).decode("ascii")

        # Show in Textual viewer
        self._tool_box.show_json_file(
            json_path_or_data=solution_data,
            title="Solution Viewer",
            panel_content=panel_arg
        )

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument('-p', '--print-solution', action='store_true',
                            help='Print the processed solution JSON file to the terminal.')

        parser.add_argument('-j', '--print-json', help='JSON file Viewer.')

        # Logger printout
        parser.add_argument("-l", "--log", action="store_true", help="Show the log output")
        parser.add_argument('-t', '--tutorial', action='store_true', help='Show the solution creation tutorial.')

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

        if args.print_solution:
            # Show the expanded solution using the JSON viewer
            self._print_solution()

        elif args.print_json:
            # View JSON/C file.
            self._tool_box.show_json_file(json_path_or_data=args.print_json, title=f"File: {args.print_json}")

        elif args.show_environment_variables:
            # Show a table with all the project environment variables
            self._print_variables_table()

        elif args.log:
            # Show system log
            self._print_log(args.cheerful)

        elif args.tutorial:
            # Show tutorial regarding the solution structure
            self._tool_box.show_help_file(help_file_relative_path='solution/guide.md')
        else:
            # Error: no arguments
            return_code = CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
