"""
Module: xray_command.py
Author: AutoForge Team

Description:
    Provides functionality for running queries on the build system SQLite DB.
"""

import argparse
import re
from logging import Logger
from typing import Any, Optional

from rich import box, panel
from rich.console import Console
from rich.table import Table

# AutoForge imports
from auto_forge import (CoreVariables, CommandInterface, AutoLogger, CoreXRayDB, XRayStateType)

AUTO_FORGE_MODULE_NAME = "xray"
AUTO_FORGE_MODULE_DESCRIPTION = "XRayDB Play Ground"
AUTO_FORGE_MODULE_VERSION = "1.0"


# noinspection SqlNoDataSourceInspection
class XRayCommand(CommandInterface):
    """
    Implements a command cross-platform command similar to Windows 'start'.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the EditCommand class.
        Args:
            **_kwargs (Any): Optional keyword arguments, such as:
        """

        self._variables: CoreVariables = CoreVariables.get_instance()
        self._xray_db: Optional[CoreXRayDB] = None
        self._console = Console(force_terminal=True)

        # Get a logger instance
        self._logger: Logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME.capitalize())

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=True)

    def initialize(self, **_kwargs: Any) -> Optional[bool]:
        """
        Command specific initialization, will be executed lastly by the interface class
        after all other initializers.
        """

        # Detect installed editors
        if self._configuration is None:
            raise RuntimeError("Package configuration was missing during initialization")

        return True

    def _find_all_duplicates(self, limit: int = 500) -> Optional[int]:
        """
        Print sets of files that have identical purified content, grouped by checksum.
        """

        xray_db = CoreXRayDB.get_instance()
        if xray_db is None or xray_db.state != XRayStateType.RUNNING:
            raise RuntimeError("XRay is not initialized or not running")

        try:
            rows = xray_db.query_raw(f"""
                SELECT checksum, GROUP_CONCAT(path, '|') FROM file_meta
                WHERE checksum IS NOT NULL
                GROUP BY checksum
                HAVING COUNT(*) > 1
                LIMIT {limit};
            """)

            if not rows:
                print("No duplicate files found.")
                return 1

            table = Table(show_lines=True)
            table.add_column("#", style="dim", justify="right", width=4)
            table.add_column("Checksum", style="bold yellow", width=20)
            table.add_column("Files (clickable)", style="green")

            for idx, (checksum, paths_concat) in enumerate(rows, 1):
                paths = paths_concat.split('|')
                file_links = "\n".join(f"[link=file://{p}]{p}[/link]" for p in paths)
                table.add_row(str(idx), checksum, file_links)

            self._console.print(table)
            return 0

        except Exception as e:
            print(f"[!] Error while finding duplicates: {e}")

    def _find_all_mains(self, limit: int = 500) -> Optional[int]:
        """
        Print all files that implement a likely C-style `main()` function, with line numbers.
        This function queries the XRay content index for files containing the word 'main',
        then applies a regular expression to filter only those files that include a valid
        C/C++-style main function signature (e.g., `int main()`, `void main(int argc, char** argv)`).

        For each matching file, the full path and the line number of the match are printed.

        Args:
            limit (int): Maximum number of candidate files to scan. Default is 500.
        """

        xray = CoreXRayDB.get_instance()
        if not xray or xray.state != XRayStateType.RUNNING:
            raise RuntimeError("XRay is not running or unavailable")

        try:
            rows = xray.query_raw(f"""
                                                   SELECT path, content
                                                   FROM files
                                                   WHERE content MATCH 'main' LIMIT {limit}
                                                   """)

            main_regex = re.compile(
                r'\b(?:int|void)\s+main\s*\(\s*(?:void|int\s+\w+\s*,\s*char\s*\*+\s*\w+.*)?\s*\)',
                re.IGNORECASE,
            )

            matches = []
            for path, content in rows:
                for lineno, line in enumerate(content.splitlines(), start=1):
                    if main_regex.search(line):
                        matches.append((path, lineno, line.strip()))
                        break  # Only first match per file

            if not matches:
                print("No valid 'main' implementations found.")
                return 1

            # Render with Rich
            table = Table(title="Detected C-style main() Implementations", box=box.ROUNDED)
            table.add_column("Path", style="white", overflow="fold")
            table.add_column("Line", justify="right", style="cyan")
            table.add_column("Code Snippet", style="bright_yellow", overflow="fold")

            for path, lineno, line in matches:
                file_link = f"[link=file://{path}]{path}[/link]"
                table.add_row(file_link, str(lineno), line)

            self._console.print(panel)
            return 0

        except Exception as xray_error:
            print(f"Error while searching for main functions: {xray_error}")

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument(
            "-m", "--find-mains",
            nargs="?",
            const=500,
            type=int,
            metavar="LIMIT",
            help="Find files with main() implementations (optional LIMIT, default: 500)"
        )
        parser.add_argument(
            "-d", "--find-duplicates",
            nargs="?",
            const=500,
            type=int,
            metavar="LIMIT",
            help="Find duplicated files (optional LIMIT, default: 500)"
        )

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the 'xray' command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """

        if args.find_mains:
            limit = args.find_mains or 500
            return_code = self._find_all_mains(limit)

        elif args.find_duplicates:
            limit = args.find_duplicates or 500
            return_code = self._find_all_duplicates(limit)

        else:
            # Error: no arguments
            return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
