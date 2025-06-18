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

        # Get a logger instance
        self._logger: Logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME.capitalize())

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=True)

    def initialize(self, **_kwargs: Any) -> Optional[bool]:
        """
        Command specific initialization, will be executed lastly by the interface class after all other initializers.
        """

        # Detect installed editors
        if self._configuration is None:
            raise RuntimeError("Package configuration was missing during initialization")

        return True

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

    @staticmethod
    def _find_all_mains(limit: int = 500) -> Optional[int]:
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

            for path, lineno, line in matches:
                print(f"{path}:{lineno} | {line}")

            return 0

        except Exception as xray_error:
            print(f"Error while searching for main functions: {xray_error}")

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

        else:
            # Error: no arguments
            return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
