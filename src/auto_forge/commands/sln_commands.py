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
from auto_forge import (AutoForgFolderType, CommandInterface, CoreEnvironment, CoreSolution, CoreVariables,
                        CoreJSONCProcessor, CoreToolBox, FieldColorType
                        )

AUTO_FORGE_MODULE_NAME = "sln"
AUTO_FORGE_MODULE_DESCRIPTION = "Solution utilities"
AUTO_FORGE_MODULE_VERSION = "1.0"


class SolutionCommand(CommandInterface):
    """
    A simple 'hello world' command example for AutoForge CLI.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the SolutionCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments.
        """

        self._solution: Optional[CoreSolution] = None
        self._variables: Optional[CoreVariables] = None
        self._environment: Optional[CoreEnvironment] = None
        self._tool_box: Optional[CoreToolBox] = CoreToolBox.get_instance()
        self._preprocessor: Optional[CoreJSONCProcessor] = CoreJSONCProcessor.get_instance()

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME)

    def _show_environment_variables(self) -> Optional[int]:
        """
        Display the list of managed variables in a styled table using Rich.
        This method is fully compatible with cmd2 and does not rely on self.console.
        """

        console = Console(force_terminal=True)

        def _bool_emoji(bool_value: Optional[bool]) -> str:
            if bool_value:
                return "[green]✔[/]"
            elif bool_value is False:
                return "[red]✘[/]"
            return "[dim]-[/]"

        var_list: list = self._variables.export()
        project_workspace: Optional[str] = self._variables.get('PROJ_WORKSPACE')
        solution_name: Optional[str] = self._variables.get('SOLUTION_NAME')
        home_directory = self._tool_box.get_expanded_path(path="$HOME")

        if not isinstance(project_workspace, str) or not isinstance(solution_name, str):
            raise RuntimeError("could not get our solution name or project workspace or both")

        if not isinstance(var_list, list) or not var_list:
            raise RuntimeError("no variables to display")

        try:
            table = Table(title=f"{solution_name.capitalize()}: Managed Variables", box=box.ROUNDED)

            # Define columns based on VariableFieldType
            table.add_column("Key", style="bold cyan", no_wrap=True)
            table.add_column("Value", style="green")
            table.add_column("Description", style="dim")
            table.add_column("Path?", style="yellow", justify="center")
            table.add_column("Auto Create", justify="center")
            table.add_column("Type", justify="center")

            for var in var_list:
                key = str(var.get("key", "") or "")
                description = str(var.get("description", "") or "")
                is_path = var.get("is_path", False)
                value = var.get("value", "")
                folder_type = var.get("folder_type", AutoForgFolderType.UNKNOWN)

                # Get and format folder type
                folder_type_str = folder_type.name if isinstance(folder_type, AutoForgFolderType) else ""
                folder_type_str = folder_type_str.replace("UNKNOWN", "-")

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
                              _bool_emoji(var.get("create_path_if_not_exist")), folder_type_str)

            console.print('\n', table, '\n')
            return 0

        except Exception as variables_error:
            raise variables_error from variables_error

    def _show_log(self, cheerful: bool) -> Optional[int]:
        """
        Display the AutoForge logger output with color-coded fields.
        Args:
            cheerful (bool): If True, display the log with enhanced formatting or emotive tone.
                             Otherwise, use a more standard presentation.
        """
        field_colors = [FieldColorType(field_name="AutoForge", color=Fore.GREEN),
                        FieldColorType(field_name="Variables", color=Fore.LIGHTBLUE_EX),
                        FieldColorType(field_name="Loader", color=Fore.MAGENTA),
                        FieldColorType(field_name="XRayDB", color=Fore.CYAN),
                        FieldColorType(field_name="Environment", color=Fore.BLUE),
                        FieldColorType(field_name="Prompt", color=Fore.LIGHTCYAN_EX),
                        FieldColorType(field_name="Solution", color=Fore.LIGHTYELLOW_EX),
                        FieldColorType(field_name="Signatures", color=Fore.LIGHTRED_EX), ]
        try:
            self._solution.auto_forge.root_logger.show(cheerful=cheerful, field_colors=field_colors)
            return 0

        except Exception as log_error:
            raise log_error from log_error

    def _show_solution(self) -> Optional[int]:
        """ Use Textual to show the solution viewer with a clean, structured single-solution summary."""

        try:
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

            # Encode as base64 to safely pass as argument
            json_text = json.dumps(solution_summary)
            panel_arg = base64.b64encode(json_text.encode("utf-8")).decode("ascii")

            # Show in Textual viewer
            return self._tool_box.show_json_file(
                json_path_or_data=solution_data,
                title="Solution Viewer",
                panel_content=panel_arg, )

        except Exception as solution_error:
            raise solution_error from solution_error

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """

        # This command allow only one option at a time
        group = parser.add_mutually_exclusive_group(required=False)

        # 'Show' commands
        group.add_argument('-s', '--show-solution', action='store_true',
                           help='Print the processed solution JSON file to the terminal.')
        group.add_argument('-e', '--show-environment-variables', action='store_true',
                           help='Show session environment variables.')
        group.add_argument("-l", "--show-log", action="store_true", help="Show the log output")
        group.add_argument('-g', '--show-guide', action='store_true', help='Show the solution creation guide.')

        # General purpose tools
        group.add_argument('-j', '--show-json', help='JSON file Viewer.')

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """

        self._solution: CoreSolution = CoreSolution.get_instance()
        self._variables: CoreVariables = CoreVariables.get_instance()

        if args.show_solution:
            # Show the expanded solution using the JSON viewer
            return self._show_solution()

        elif args.show_environment_variables:
            # Show a table with all the project environment variables
            return self._show_environment_variables()

        elif args.show_log:
            # Show system log
            return self._show_log(cheerful=True)

        elif args.show_guide:
            # Show tutorials for the solution JSON file structure
            return self._tool_box.show_help_file(relative_path='solution/guide.md')

        elif args.show_json:
            # View JSON/C file.
            return self._tool_box.show_json_file(json_path_or_data=args.show_json, title=f"File: {args.show_json}")

        else:
            # Error: no arguments
            return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
