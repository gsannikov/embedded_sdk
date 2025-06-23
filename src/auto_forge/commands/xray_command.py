"""
Module: xray_command.py
Author: AutoForge Team

Description:
    XRay CLI commands allows for source analysis and duplicate detection.
    It leverages the CoreXRayDB backend to index files under solution 'Source' paths into a
    SQLite database. Users can perform structured or wildcard-based file searches, detect
    duplicate content, or execute arbitrary SQL-style queries on the indexed metadata.
"""

import argparse
import json
import os
import re
from logging import Logger
from pathlib import Path
from typing import Any, Optional

from rich import box
from rich.console import Console
from rich.table import Table

# AutoForge imports
from auto_forge import (CoreVariables, CommandInterface, AutoLogger, CoreXRayDB, CoreSolution, XRayStateType)

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
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=False)

    def initialize(self, **_kwargs: Any) -> Optional[bool]:
        """
        Command specific initialization, will be executed lastly by the interface class
        after all other initializers.
        """

        # Detect installed editors
        if self._configuration is None:
            raise RuntimeError("Package configuration was missing during initialization")

        return True

    @staticmethod
    def _shell_to_sql_like(pattern: str) -> str:
        return (
            pattern
            .replace("\\", "\\\\")  # escape backslashes
            .replace("_", "\\_")  # escape SQL _ wildcard
            .replace("%", "\\%")  # escape SQL % wildcard
            .replace("?", "_")  # shell ? → SQL _
            .replace("*", "%")  # shell * → SQL %
        )

    def _resolve_search_pattern_and_extensions(
            self, file_name_pattern: str, extensions: Optional[list[str]]) -> tuple[str, list[str]]:
        """
        Analyze the file name pattern and extensions.
        - Auto-wildcard if no wildcards present.
        - Infer extension from pattern like 'foo.c' if needed.
        - Reconcile or validate against explicitly passed extensions.
        Returns:
            (sql_like_pattern, final_extensions)
        """
        pattern = file_name_pattern.strip()
        has_wildcards = any(ch in pattern for ch in "*?")

        if "." in pattern and not has_wildcards:
            base_part, ext_part = pattern.rsplit(".", 1)
            inferred_ext = ext_part.lower()

            if extensions is not None:
                lowered = [e.lower() for e in extensions]
                if inferred_ext not in lowered:
                    raise ValueError(
                        f"Pattern '{file_name_pattern}' implies extension '.{inferred_ext}', "
                        f"which is not in allowed extensions: {extensions}"
                    )
                extensions = [inferred_ext]
            else:
                extensions = [inferred_ext]

            pattern = base_part + "*"

        elif not has_wildcards:
            pattern += "*"

        sql_pattern = self._shell_to_sql_like(pattern)
        return sql_pattern, extensions

    @staticmethod
    def _get_build_path(project_name: str, configuration_name: str,
                        appended_path: Optional[str] = None) -> Optional[Path]:

        """ Helper method to get the build path of specific  project and configuration """
        _solution = CoreSolution.get_instance()
        if _solution is None:
            return None

        config_data = _solution.query_configurations(project_name=project_name,
                                                     configuration_name=configuration_name)
        if isinstance(config_data, dict):
            build_path = config_data.get("build_path")
            if isinstance(build_path, str):
                build_path = Path(build_path)
                if appended_path is not None:
                    return build_path / appended_path
                else:
                    return build_path

        return None

    def _locate_files(self, file_name_pattern: str, extensions: Optional[list[str]] = None, limit: int = 500) -> \
            Optional[int]:
        """
        Locate files by name pattern with optional extension filter and result limit.
        Args:
            file_name_pattern (str): SQL LIKE-style pattern (use '%' or '_' wildcards).
            extensions (Optional[list]): List of extensions to filter by (e.g., ['c', 'h']).
            limit (int): Max number of results to return.

        Returns:
            Optional[int]: 0 if files were found, 1 otherwise.
        """
        xray_db = CoreXRayDB.get_instance()
        if xray_db is None or xray_db.state != XRayStateType.RUNNING:
            raise RuntimeError("XRay is not initialized or not running")

        # Try to locate and CMake registered paths which is auto generated during the userspace CMake build
        cmake_json_loaded: bool = False
        cmake_json_path: Optional[Path] = self._get_build_path(project_name="zephyr_build", configuration_name="debug",
                                                               appended_path="registered_paths.json")
        if not isinstance(cmake_json_path, Path):
            cmake_json_path = self._get_build_path(project_name="zephyr_build", configuration_name="release",
                                                   appended_path="registered_paths.json")

        if cmake_json_path and cmake_json_path.is_file():
            with cmake_json_path.open("r") as jf:
                cmake_path_data = json.load(jf)
                # Canonicalize and sort CMake paths by length (longest match first)
                cmake_prefixes = sorted(
                    [(var, str(Path(base_path).resolve())) for var, base_path in cmake_path_data.items()],
                    key=lambda kv: -len(kv[1])
                )
                cmake_json_loaded = True

        try:
            query = (f"""
                    SELECT path, ext
                    FROM file_meta
                    WHERE base LIKE ? \
                    LIMIT {limit}
                    """)

            # Normalize user input
            sql_pattern, extensions = self._resolve_search_pattern_and_extensions(file_name_pattern, extensions)
            params = [sql_pattern]
            query += " ESCAPE '\\'"

            if extensions:
                ext_placeholders = ",".join("?" for _ in extensions)
                query += f" AND ext IN ({ext_placeholders})"
                params.extend(extensions)

            query += f" ORDER BY path LIMIT {limit}"
            rows = xray_db.query_raw(query, tuple(params))

            if not rows:
                print("No matching files found.")
                return 1

            table = Table(title=f"Search results for '{file_name_pattern}'", box=box.ROUNDED)
            table.add_column("#", style="dim", justify="right", width=4)
            table.add_column("Type", style="cyan", width=4)
            table.add_column("Path", style="white")

            if not cmake_json_loaded:
                for idx, (path, ext) in enumerate(rows, 1):
                    table.add_row(str(idx), ext or "", f"[link=file://{path}]{path}[/link]")
            else:
                table.add_column("CMake Include", style="magenta")
                # Canonicalize each found path before comparison
                for idx, (path, ext) in enumerate(rows, 1):
                    resolved_path = str(Path(path).resolve())
                    cmake_hint = ""
                    for var, base_path in cmake_prefixes:
                        if resolved_path.startswith(base_path):
                            relative = os.path.relpath(resolved_path, base_path)
                            cmake_hint = f"${{{var}}}/{relative}"
                            break
                    table.add_row(str(idx), ext or "", f"[link=file://{path}]{path}[/link]", cmake_hint)

            self._console.print('\n', table)
            return 0

        except Exception as xray_error:
            raise xray_error from xray_error

    def _find_all_duplicates(self, limit: int = 500) -> Optional[int]:
        """
        Print sets of files that have identical purified content, grouped by checksum.
        """

        xray_db = CoreXRayDB.get_instance()
        if xray_db is None or xray_db.state != XRayStateType.RUNNING:
            raise RuntimeError("XRay is not initialized or not running")

        try:
            rows = xray_db.query_raw(f"""
                SELECT checksum, GROUP_CONCAT(path, '|') 
                FROM file_meta
                WHERE checksum IS NOT NULL
                  AND ext IN ('c', 'h')
                GROUP BY checksum
                HAVING COUNT(*) > 1
                LIMIT {limit};
            """)

            if not rows:
                print("No duplicate files found.")
                return 1

            table = Table(show_lines=True, box=box.ROUNDED)
            table.add_column("#", style="dim", justify="right", width=4)
            table.add_column("Checksum", style="bold yellow", width=20)
            table.add_column("Files", style="green")

            for idx, (checksum, paths_concat) in enumerate(rows, 1):
                paths = paths_concat.split('|')
                file_links = "\n".join(f"[link=file://{p}]{p}[/link]" for p in paths)
                table.add_row(str(idx), checksum, file_links)

            self._console.print('\n', table)
            return 0

        except Exception as xray_error:
            raise xray_error from xray_error

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
                SELECT files.path, files.content
                FROM files
                JOIN file_meta ON files.path = file_meta.path
                WHERE file_meta.ext IN ('c')
                  AND files.content MATCH 'main'
                LIMIT {limit}
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
            table.add_column("Snippet", style="bright_yellow", overflow="fold")

            for path, lineno, line in matches:
                file_link = f"[link=file://{path}]{path}[/link]"
                table.add_row(file_link, str(lineno), line)

            self._console.print('\n', table)
            return 0

        except Exception as xray_error:
            raise xray_error from xray_error

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """

        def _split_extensions(_ext_arg: str) -> list[str]:
            return [e.strip().lower() for e in _ext_arg.split(",") if e.strip()]

        parser.add_argument(
            "-m", "--find-mains", action='store_true', help="Find files with main() implementations")
        parser.add_argument(
            "-d", "--find-duplicates", action='store_true', help="Find duplicated files")
        parser.add_argument(
            "-l", "--locate_files", type=str, help="Locate files (optionally limit number of results")

        parser.add_argument(
            "--limit", type=int, default=500, help="Maximum number of results to return (default: 500)")

        parser.add_argument(
            "--ext",
            type=_split_extensions, default=["c", "h"], help="Comma-separated extensions (e.g. --ext c,h). Default: c,h"
        )

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the 'xray' command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """
        limit: int = args.limit if args.limit else 500
        extensions: list = args.ext if args.ext else ["c", "h"]

        if args.find_mains:
            return_code = self._find_all_mains(limit=limit)

        elif args.find_duplicates:
            return_code = self._find_all_duplicates(limit=limit)

        elif args.locate_files:
            return_code = self._locate_files(file_name_pattern=args.locate_files, limit=limit, extensions=extensions)

        else:
            # Error: no arguments
            return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
