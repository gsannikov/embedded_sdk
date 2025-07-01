"""
Module: xray_command.py
Author: AutoForge Team

Description:
    The XRay command allows for source analysis and duplicate detection.
    It leverages the CoreXRayDB backend to index files under solution 'Source' paths into a
    SQLite database. Users can perform structured or wildcard-based file searches, detect
    duplicate content, or execute arbitrary SQL-style queries on the indexed metadata.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

# Third-party
from rich import box
from rich.console import Console
from rich.table import Table

# AutoForge imports
from auto_forge import (CommandInterface, CoreVariables, CoreXRayDB, CoreSolution)

AUTO_FORGE_MODULE_NAME = "xray"
AUTO_FORGE_MODULE_DESCRIPTION = "XRayDB Play Ground"
AUTO_FORGE_MODULE_VERSION = "1.1"


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

        self._variables = CoreVariables.get_instance()
        self._xray_db = CoreXRayDB.get_instance()
        self._console = Console(force_terminal=True)

        # Dependencies check
        if None in (self._variables, self._xray_db):
            raise RuntimeError("failed to instantiate critical dependencies")

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=False)

    def initialize(self, **_kwargs: Any) -> Optional[bool]:
        """
        Command specific initialization, will be executed lastly by the interface class
        after all other initializers.
        """
        # Make sure we have access to the package configuration
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
    def _get_build_path(project_name: str, configuration_name: str) -> Optional[Path]:
        """
        Uses the solution class to retrieve the build path of a specific project and configuration
        from the currently loaded solution.
        Args:
            project_name (str): Name of the project (e.g., "zephyr_build").
            configuration_name (str): Build configuration name (e.g., "debug", "release").
        Returns:
            Optional[Path]: Path to the build directory, or None if not found.
        """
        _solution = CoreSolution.get_instance()
        if _solution is None:
            return None

        config_data = _solution.query_configurations(project_name=project_name,
                                                     configuration_name=configuration_name)
        if isinstance(config_data, dict):
            build_path = config_data.get("build_path")
            if isinstance(build_path, str):
                build_path = Path(build_path)
                return build_path

        return None

    def _get_auto_vars(self, path: Path, prefix_str: str) -> Optional[dict[str, str]]:
        """
        Aggregates all anonymous dictionaries from JSON files in the given directory that start with the specified prefix.
        These JSON files are automatically generated during the project's CMake build process. Each file is expected
        to contain a single anonymous dictionary of path variable declarations used during compilation.
        Args:
            path (Path): Path to the build output directory where the JSON files are located.
            prefix_str (str): Prefix string used to identify relevant JSON files (e.g., "_auto_forge_").
        Returns:
            Optional[dict[str, str]]: A unified dictionary of all key-value pairs from matched files.
        """
        json_files = list(path.glob(f"{prefix_str}*.json"))
        if not json_files:
            raise FileNotFoundError(f"No JSON files found with prefix '{prefix_str}' in {path}")

        combined = {}
        for file in json_files:
            self._logger.debug(f"Reading build output file '{file}'")
            try:
                with file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                raise ValueError(f"Failed to parse JSON in '{file.name}': {e}")
            if not isinstance(data, dict):
                raise ValueError(f"File '{file.name}' must contain a single anonymous dictionary")
            if not data:
                raise ValueError(f"File '{file.name}' contains an empty dictionary")
            for key, val in data.items():
                if key in combined:
                    if combined[key] != val:
                        raise ValueError(
                            f"Conflicting value for key '{key}' in file '{file.name}': "
                            f"'{combined[key]}' vs '{val}'"
                        )
                else:
                    combined[key] = val

        return combined

    @staticmethod
    def _strip_if_filename(path_str: Optional[str]) -> Optional[str]:
        """
        If 'path_str' ends in what looks like a file name (has an extension), return the parent path.
        Otherwise, return the string unchanged. If input is None, return None.
        Examples:
            "abcdefg"                           -> "abcdefg"
            None                               -> None
            "${MEV_IMC_MNG_LIB_PATH}/foo.h"    -> "${MEV_IMC_MNG_LIB_PATH}"
            "/test/abcd"                       -> "/test/abcd"
        """
        if path_str is None:
            return None

        tail = os.path.basename(path_str)
        name, ext = os.path.splitext(tail)

        if ext and name:  # likely a file
            return os.path.dirname(path_str)
        else:
            return path_str

    def _locate_files(self, file_name_pattern: str,
                      extensions: Optional[list[str]] = None, limit: int = 500,
                      show_cmake_paths: bool = False) -> Optional[int]:
        """
        Locate files by name pattern with optional extension filter and result limit.
        Also attempts to show how each file could be included in CMake, based on
        automatically generated path mappings collected during the userspace CMake build.

        Args:
            file_name_pattern (str): SQL LIKE-style pattern (use '%' or '_' wildcards).
            extensions (Optional[list[str]]): List of extensions to filter by (e.g., ['c', 'h']).
            limit (int): Maximum number of results to return.
            show_cmake_paths (bool): Show CMake include paths analysis.

        Returns:
            Optional[int]: 0 if files were found, 1 otherwise.

        CMake Path Mapping:
            If the current build directory contains files like '_auto_forge_*.json',
            each of which holds an anonymous dictionary of CMake variable names to paths,
            these will be loaded and matched against each located file.
            For any match, a simplified CMake-style include path like:
                ${MEV_IMC_MNG_LIB_PATH}/include
            will be shown if it resolves to a registered base path.
        """

        def _highlight_var_substitution(text: str) -> str:
            """
            Highlight ${...} variables in a CMake path using rich markup.
            """
            return re.sub(r"(\$\{[^}]+})", r"[green]\1[/green]", text)

        # Try to locate CMake-registered paths auto-generated during userspace build
        cmake_prefixes: list[tuple[str, str]] = []

        if show_cmake_paths:
            cmake_json_path: Optional[Path] = self._get_build_path(project_name="zephyr_build",
                                                                   configuration_name="debug")
            if not isinstance(cmake_json_path, Path):
                cmake_json_path = self._get_build_path(project_name="zephyr_build", configuration_name="release")

            if cmake_json_path:
                cmake_paths_data: Optional[dict] = self._get_auto_vars(path=cmake_json_path, prefix_str="_auto_forge_")
                if isinstance(cmake_paths_data, dict):
                    cmake_prefixes = sorted(
                        [(var, str(Path(base_path).resolve())) for var, base_path in cmake_paths_data.items()],
                        key=lambda kv: -len(kv[1])
                    )

        try:
            sql_pattern, extensions = self._resolve_search_pattern_and_extensions(file_name_pattern, extensions)
            query = """
                    SELECT path, ext
                    FROM file_meta
                    WHERE base LIKE ? ESCAPE '\\' \
                    """
            params = [sql_pattern]

            if extensions:
                ext_placeholders = ",".join("?" for _ in extensions)
                query += f" AND ext IN ({ext_placeholders})"
                params.extend(extensions)

            query += " ORDER BY path LIMIT ?"
            params.append(str(limit or 500))

            rows = self._xray_db.query_raw(query, tuple(params))
            if not rows:
                print("No matching files found.")
                return 1

            # Determine if at least one file can be matched to a CMake path
            table_rows = []
            show_cmake_column = False

            for idx, (path, ext) in enumerate(rows, 1):
                resolved_path = str(Path(path).resolve())
                cmake_hint = ""
                for var, base_path in cmake_prefixes:
                    if resolved_path.startswith(base_path):
                        relative = os.path.relpath(resolved_path, base_path)
                        cmake_hint = f"${{{var}}}/{relative}"
                        cmake_hint = self._strip_if_filename(path_str=cmake_hint)
                        show_cmake_column = True
                        break
                cmake_hint = _highlight_var_substitution(text=cmake_hint)
                table_rows.append((idx, ext or "", path, cmake_hint))

            # Build the display table
            table = Table(title=f"Search results for '{file_name_pattern}'", box=box.ROUNDED)
            table.add_column("#", style="dim", justify="right", width=4)
            table.add_column("Type", style="cyan", width=4)
            table.add_column("Path", style="white")
            if show_cmake_column:
                table.add_column("CMake Include", style="magenta")

            for idx, ext, path, cmake_hint in table_rows:
                row = [str(idx), ext, f"[link=file://{path}]{path}[/link]"]
                if show_cmake_column:
                    row.append(cmake_hint)
                table.add_row(*row)

            self._console.print('\n', table)
            return 0

        except Exception as xray_error:
            raise xray_error from xray_error

    def _find_all_duplicates(self, limit: int = 500) -> Optional[int]:
        """
        Print sets of files that have identical purified content, grouped by checksum.
        """
        try:
            rows = self._xray_db.query_raw(f"""
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
        try:
            rows = self._xray_db.query_raw(f"""
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
                print("No results containing 'main' ware found.")
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
            "-l", "--locate-files", type=str, help="Locate files (optionally limit number of results")

        parser.add_argument("-r", "--refresh-indexes", action="store_true", help="Perform DB indexes refresh")

        parser.add_argument(
            "--limit", type=int, default=500, help="Maximum number of results to return (default: 500)")

        parser.add_argument(
            "--ext",
            type=_split_extensions, default=["c", "h"], help="Comma-separated extensions (e.g. --ext c,h). Default: c,h"
        )

        parser.add_argument(
            "-c", "--cmake-include", action='store_true', help="Show CMake include paths based on build outputs")

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

        if args.refresh_indexes:
            return self._xray_db.refresh()

        elif args.find_mains:
            return_code = self._find_all_mains(limit=limit)

        elif args.find_duplicates:
            return_code = self._find_all_duplicates(limit=limit)

        elif args.locate_files:
            return_code = self._locate_files(file_name_pattern=args.locate_files, limit=limit, extensions=extensions,
                                             show_cmake_paths=args.cmake_include)
        else:
            # Error: no arguments
            return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
