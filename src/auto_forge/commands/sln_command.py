"""
Script:         sln_command.py
Author:         AutoForge Team

Description:
    General-purpose solution management utilities and viewers, including:
    - Logs, JSON and Markdown viewers
    - Environment variables viewer
    - Expanded solution viewer
    - Real-time counters and other dynamically monitored data views
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
from auto_forge import (AutoForgFolderType, CommandInterface, FieldColorType, VariableType)

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

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME)

    @staticmethod
    def _bool_emoji(bool_value: Optional[bool]) -> str:
        """ Helper to print true / false emoji with ease """
        if bool_value:
            return "[green]✔[/]"
        elif bool_value is False:
            return "[red]✘[/]"
        return "[dim]-[/]"

    def _show_environment_variables(self) -> Optional[int]:
        """
        Display the list of managed variables in a styled table using Rich.
        This method is fully compatible with cmd2 and does not rely on self.console.
        """
        console = Console(force_terminal=True)

        var_list: list = self.sdk.variables.export()
        project_workspace: Optional[str] = self.sdk.variables.get('PROJ_WORKSPACE')
        solution_name: Optional[str] = self.sdk.variables.get('SOLUTION_NAME')
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
            table.add_column("Info", style="dim")
            table.add_column("Path?", style="yellow", justify="center")
            table.add_column("Create", justify="center")
            table.add_column("Folder", justify="center")
            table.add_column("Class", style="dim italic", justify="center")

            for var in var_list:
                key = str(var.get("key", "") or "")
                description = str(var.get("description", "") or "")
                is_path = var.get("is_path", False)
                value = var.get("value", "")
                folder_type = var.get("folder_type", AutoForgFolderType.UNKNOWN)
                var_type: VariableType = var.get("type", VariableType.UNKNOWN)

                # Get and format folder type
                folder_type_str = folder_type.name if isinstance(folder_type, AutoForgFolderType) else ""
                folder_type_str = folder_type_str.replace("UNKNOWN", "-")

                # Build styled value text
                value_text = Text()
                try:
                    if is_path and isinstance(value, str):
                        path_obj = Path(value)

                        if value.startswith(project_workspace):
                            rel_path = path_obj.relative_to(project_workspace)
                            value_text.append("$", style="blue")
                            value_text.append(f"/{rel_path}")
                        elif value.startswith(home_directory):
                            rel_path = path_obj.relative_to(home_directory)
                            value_text.append("~", style="purple")
                            value_text.append(f"/{rel_path}")
                        else:
                            value_text.append(str(value))
                        # Highlight missing paths in yellow
                        if not path_obj.exists():
                            value_text.stylize("yellow")

                    else:
                        value_text = Text(str(value))
                except ValueError:
                    value_text = Text(str(value))

                table.add_row(key, value_text, description, self._bool_emoji(is_path),
                              self._bool_emoji(var.get("create_path_if_not_exist")), folder_type_str,
                              var_type.name.capitalize())

            console.print('\n', table)
            return 0

        except Exception as variables_error:
            raise variables_error from variables_error

    def _show_telemetry(self) -> Optional[int]:
        """
        Displays a summary of the current telemetry state, including initialized tracer/meter components,
        counters, and uptime, using Rich formatting.
        """

        console = Console(force_terminal=True)
        print()

        console.rule("[bold white]Telemetry Status", style="cyan")
        print()

        # High-level summary
        elapsed = self.sdk.telemetry.elapsed_since_start()
        console.print(f"[bold]Service:[/bold]            {self.sdk.telemetry.service_name or 'N/A'}")
        console.print(f"[bold]Start Time (UNIX):[/bold]  {self.sdk.telemetry.start_unix:.3f}")
        console.print(f"[bold]Uptime:[/bold]             {self._tool_box.format_duration(elapsed)}")

        # Tracer state
        console.print(f"[bold]Tracer initialized:[/bold] {self._bool_emoji(self.sdk.telemetry.tracer is not None)}")
        console.print(f"[bold]Meter initialized:[/bold]  {self._bool_emoji(self.sdk.telemetry.meter is not None)}")

        # Show boot events (module start time since epoch)
        if self.sdk.telemetry.registered_boot_events:
            boot_table = Table(title="Module Boot Times", box=box.ROUNDED, title_justify="left")
            boot_table.add_column("Module", style="cyan")
            boot_table.add_column("Boot Offset From Epoch", style="green")

            for name, delay in sorted(self.sdk.telemetry.registered_boot_events.items(), key=lambda x: x[1]):
                boot_table.add_row(name, f"{delay:.3f} sec")

            print()
            console.print(boot_table)

        # If counters exist, list them
        if self.sdk.telemetry.meter:
            table = Table(title="Registered Counters", box=box.ROUNDED, title_justify="left")
            table.add_column("Name", style="cyan", no_wrap=True)
            table.add_column("Unit", style="magenta")
            table.add_column("Description", style="white")
            table.add_column("Value", style="green", justify="right")

            try:
                for counter in self.sdk.telemetry.registered_counters:
                    name = getattr(counter, "name", "unknown")
                    unit = getattr(counter, "unit", "-")
                    desc = getattr(counter, "description", "-")
                    value = getattr(counter, "value", "-")
                    table.add_row(name, unit, desc, str(value))

            except Exception as telemetry_error:
                console.print(f"[red]Warning:[/red] Could not enumerate counters: {telemetry_error}")

            console.print(table, '\n')
        else:
            console.print("[dim]No 'meter' available — counters not tracked.[/dim]")

        print()

        return 0

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
                        FieldColorType(field_name="Platform", color=Fore.BLUE),
                        FieldColorType(field_name="Shell", color=Fore.LIGHTCYAN_EX),
                        FieldColorType(field_name="ToolBox", color=Fore.LIGHTYELLOW_EX),
                        FieldColorType(field_name="AIBridge", color=Fore.LIGHTGREEN_EX), ]
        try:
            self._core_logger.show(cheerful=cheerful, field_colors=field_colors)
            return 0

        except Exception as log_error:
            raise log_error from log_error

    def _show_solution(self) -> Optional[int]:
        """ Use Textual to show the solution viewer with a clean, structured single-solution summary."""

        try:
            # Full solution data for the main JSON tree
            solution_data = self.sdk.solution.get_loaded_solution(name_only=False)

            # Just the name for summary
            solution_name = self.sdk.solution.get_loaded_solution(name_only=True)

            # Build structured solution summary
            solution_summary = {
                "Solution": solution_name.capitalize(),
                "Projects": []
            }

            for project in self.sdk.solution.query_projects():
                project_name = project.get("name")
                toolchain = project.get("tool_chain", {}).get("name", "unknown")

                configurations = self.sdk.solution.query_configurations(project_name=project_name)
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
        group.add_argument('-tl', '--show-telemetry', action='store_true', help='Show telemetry status.')
        group.add_argument('-g', '--show-guide', action='store_true', help='Show the solution creation guide.')

        # General purpose viewers
        group.add_argument('-m', '--show-mark-down', help='Markdown File Viewer.')
        group.add_argument('-j', '--show-json', help='JSON file Viewer.')

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """

        if args.show_solution:
            # Show the expanded solution using the JSON viewer
            return self._show_solution()

        elif args.show_environment_variables:
            # Show a table with all the project environment variables
            return self._show_environment_variables()

        elif args.show_log:
            # Show system log
            return self._show_log(cheerful=True)

        elif args.show_telemetry:
            # Show system log
            return self._show_telemetry()

        elif args.show_guide:
            # Show tutorials for the solution JSON file structure
            return self._tool_box.show_markdown_file(path='solution/guide.md')

        elif args.show_mark_down:
            # View Markdown file
            return self._tool_box.show_markdown_file(path=f'{args.show_mark_down}')

        elif args.show_json:
            # View JSON/C file.
            return self._tool_box.show_json_file(json_path_or_data=args.show_json, title=f"File: {args.show_json}")

        else:
            # Error: no arguments
            return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
