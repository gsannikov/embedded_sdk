"""
Module: edit_command.py
Author: AutoForge Team

Description:
    ToDo...

"""

import argparse
import os
import shutil
from typing import Optional, Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# AutoForge imports
from auto_forge import (CoreVariables, CLICommandInterface, SystemInfo)

AUTO_FORGE_MODULE_NAME = "edit"
AUTO_FORGE_MODULE_DESCRIPTION = "Uses the preferred editor"
AUTO_FORGE_MODULE_VERSION = "1.0"


class EditCommand(CLICommandInterface):
    """
    Implements a command cross-platform command similar to Windows 'start'.
    """

    def __init__(self, **kwargs: Any):
        """
        Initializes the StartCommand class.
        """

        # Extract optional parameters
        self._package_configuration_data: Optional[list, Any] = kwargs.get('package_configuration_data', None)
        self._detected_editors: Optional[list[dict[str, Any]]] = []
        self._sys_info: SystemInfo = SystemInfo.get_instance()
        self._variables: CoreVariables = CoreVariables.get_instance()

        wsl_home = self._sys_info.wsl_home()
        if isinstance(wsl_home, str):
            self._variables.add(key='WSL_HOMEPATH', value=wsl_home, is_path=True, path_must_exist=True,
                                description='WSL user home path')

        # Detect installed editors
        if self._package_configuration_data is not None:
            searched_editors_data = self._package_configuration_data.get("searched_editors", [])
            fallback_search_path = self._package_configuration_data.get("editors_fallback_search_paths", [])

            self._detected_editors = self._detect_installed_editors(editors=searched_editors_data,
                                                                    fallback_search_path=fallback_search_path,
                                                                    max_depth=0)

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, raise_exceptions=True, hidden=True)

    def _search_in_fallback_dirs(self, aliases: list, fallback_search_path: list, max_depth: int) -> Optional[str]:
        """
        Search for an executable matching one of the aliases inside fallback search paths.
        - Optional WSL-specific search path entries (skipped if not in WSL or not targeting .exe).
        - Automatic appending of `.exe` for Windows paths when missing.
        - Case-insensitive match for Windows (.exe) paths.
        - Use of os.scandir() for efficient shallow scans (max_depth == 0).
        - Variable expansion via self._variables.expand(key=...).

        Args:
            aliases (list): List of potential executable names.
            fallback_search_path (list): List of dicts with "path" and optional "wsl_path".
            max_depth (int): How deep to search subdirectories (0 = top level only).

        Returns:
            str | None: Absolute path of the first found executable, or None.
        """
        is_windows_target = any(alias.lower().endswith(".exe") for alias in aliases)
        search_dirs = []

        for entry in fallback_search_path:
            raw_path = entry.get("path")
            is_wsl_path = entry.get("wsl_path", False)
            if not raw_path:
                continue

            # Expand variables and normalize slashes
            path = self._variables.expand(key=raw_path,quiet=True).replace("\\", "/").rstrip("/")

            # Skip Windows paths if we're not in WSL or not targeting .exe
            if is_wsl_path and (not self._sys_info.is_wsl or not is_windows_target):
                continue

            if os.path.isdir(path):
                search_dirs.append((path, is_wsl_path))

        for root, is_windows_path in search_dirs:
            # Adjust candidate names: append .exe if needed for WSL paths
            candidate_names = [
                alias if not (is_windows_path and not alias.lower().endswith(".exe"))
                else alias + ".exe"
                for alias in aliases
            ]
            candidate_names_lc = [name.lower() for name in candidate_names] if is_windows_path else None

            if max_depth <= 0:
                try:
                    for entry in os.scandir(root):
                        if not entry.is_file():
                            continue
                        name = entry.name
                        if is_windows_path:
                            if name.lower() in candidate_names_lc and os.access(entry.path, os.X_OK):
                                return entry.path
                        else:
                            if name in candidate_names and os.access(entry.path, os.X_OK):
                                return entry.path
                except PermissionError:
                    continue

            else:
                for dirpath, _, filenames in os.walk(root):
                    depth = dirpath[len(root):].count(os.sep)
                    if depth >= max_depth:
                        continue

                    if is_windows_path:
                        filenames_lc = {f.lower() for f in filenames}
                        for name_lc in candidate_names_lc:
                            if name_lc in filenames_lc:
                                match_name = next(f for f in filenames if f.lower() == name_lc)
                                full_path = os.path.join(dirpath, match_name)
                                if os.access(full_path, os.X_OK):
                                    return str(full_path)
                    else:
                        for name in candidate_names:
                            if name in filenames:
                                full_path = os.path.join(dirpath, name)
                                if os.access(full_path, os.X_OK):
                                    return str(full_path)

        return None

    def _detect_installed_editors(self, editors: list, fallback_search_path: list, max_depth: int = 3) -> list[
        dict[str, str]]:
        """
        Detects which editors from the given list are available on the system.
        Args:
            editors (list): List of editor descriptors, each containing 'name', 'type', and 'aliases'.
            fallback_search_path (list): Paths to use for fallback recursive search.
            max_depth (int): Maximum recursion depth for fallback search.
        Returns:
            List[dict]: A list of detected editors with name, type, and absolute path.
        """
        detected = []

        for editor in editors:
            aliases = editor.get("aliases", [])
            editor_name = editor.get("name")
            editor_type = editor.get("type")

            found_path: Optional[str] = None

            # 1. Try PATH-based search
            for alias in aliases:
                path = shutil.which(alias)
                if path and not path.startswith("/mnt/"):
                    found_path = str(path)
                    break

            # 2. Fallback directory scan
            if not found_path:
                found_path = self._search_in_fallback_dirs(
                    aliases=aliases,
                    fallback_search_path=fallback_search_path,
                    max_depth=max_depth
                )

            if found_path:
                detected.append({
                    "name": editor_name,
                    "type": editor_type,
                    "path": str(found_path)
                })

        return detected

    def _print_detected_editors(self):
        """
        Pretty-prints the detected editors using a Rich table.
        Uses self._detected_editors, which should be a list of dicts
        containing 'name', 'type', and 'path'.
        """
        console = Console(force_terminal=True)

        if not self._detected_editors:
            console.print(Panel("[bold red]No editors detected.[/bold red]", title="Detected Editors"))
            return

        table = Table(title="Detected Editors", box=box.ROUNDED)

        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Type", style="magenta", justify="center")
        table.add_column("Path", style="white", overflow="fold")

        for editor in self._detected_editors:
            name = str(editor.get("name", "-"))
            typ = str(editor.get("type", "-"))
            path = str(editor.get("path", "-"))

            table.add_row(name, typ, path)

        console.print('\n', table, '\n')

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("-f", "--file", type=str, help="File to edit")
        parser.add_argument("-l", "--list_editors", action="store_true", help="Show the list of detected editors")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """

        return_code = 0

        if args.file is not None:
            raise RuntimeError("not yet implemented")
        elif args.list_editors:
            self._print_detected_editors()
        else:
            # Error: no arguments
            return_code = CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
