"""
Module: edit_command.py
Author: AutoForge Team

Description:
    Provides functionality for launching text editors to open files or directories
Features:
    - Editor discovery and listing.
    - Wildcard and numeric selection of editor.
    - Safe invocation of terminal and GUI editors.
    - Automatic trust registration for Visual Studio Code workspaces.
"""

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
from contextlib import suppress
from nturl2path import pathname2url
from typing import Optional, Any, Iterable

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# AutoForge imports
from auto_forge import (CoreVariables, CommandInterface, CoreSystemInfo, CoreSolution)

AUTO_FORGE_MODULE_NAME = "edit"
AUTO_FORGE_MODULE_DESCRIPTION = "Invokes the preferred editor to open files or directories"
AUTO_FORGE_MODULE_VERSION = "1.0"


class EditCommand(CommandInterface):
    """
    Implements a command cross-platform command similar to Windows 'start'.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the EditCommand class.
        Args:
            **_kwargs (Any): Optional keyword arguments, such as:
        """

        # Extract optional parameters
        self._detected_editors: Optional[list[dict[str, Any]]] = []
        self._sys_info: CoreSystemInfo = CoreSystemInfo.get_instance()
        self._selected_editor_index: Optional[int] = None
        self._variables: CoreVariables = CoreVariables.get_instance()
        self._solution: Optional[CoreSolution] = None
        self._max_fallback_search_paths: int = 10

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=True)

    def initialize(self, **_kwargs: Any) -> bool:
        """
        Command specific initialization, will be executed lastly by the interface class after all other initializers.
        """
        # Add few WSL specific variables to the project environment
        self._inject_wsl_environment()

        # Detect installed editors
        if self._configuration is None:
            raise RuntimeError("Package configuration was missing during initialization")

        searched_editors_data = self._configuration.get("searched_editors", [])
        fallback_search_path = self._configuration.get("editors_fallback_search_paths", [])

        # Clean bad or missing paths
        fallback_search_path = self._purify_paths(paths=fallback_search_path,
                                                  max_items=self._max_fallback_search_paths)

        self._detected_editors = self._detect_installed_editors(editors=searched_editors_data,
                                                                fallback_search_path=fallback_search_path,
                                                                max_depth=0)
        self._logger.debug(f"Found {len(self._detected_editors)} editors, use 'edit -l' to list them")
        return True

    def _inject_wsl_environment(self) -> None:
        """
        Injects relevant WSL environment variables into the package runtime,
        such as the user's home path and the Windows C: drive mount path.
        """
        wsl_home = self._sys_info.wsl_home
        if isinstance(wsl_home, str):
            # noinspection SpellCheckingInspection
            self._variables.add(key='WSL_HOMEPATH', value=wsl_home, is_path=True, path_must_exist=True,
                                description='WSL user home path')

        wsl_c_mount = self._sys_info.wsl_c_mount
        if isinstance(wsl_c_mount, str):
            # noinspection SpellCheckingInspection
            self._variables.add(key='WSL_C_MOUNT', value=wsl_c_mount, is_path=True, path_must_exist=True,
                                description='WSL C mount path')

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
            path = self._variables.expand(key=raw_path, quiet=True).replace("\\", "/").rstrip("/")

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
            editor_args = editor.get("args")

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
                    "path": str(found_path),
                    "args": editor_args,
                })

        return detected

    def _print_detected_editors(self, editor_index: Optional[int] = None) -> None:
        """
        Pretty-prints the detected editors using a Rich table.
        Uses self._detected_editors, which should be a list of dicts
        containing 'name', 'type', and 'path'.
        Args:
            editor_index (Optional[int]): Index of selected editor (0-based).
        """
        console = Console(force_terminal=True)

        if not self._detected_editors:
            console.print(Panel("[bold red]No editors detected.[/bold red]", title="Detected Editors"))
            return None

        # Invalidate editor_index if we can't use it
        if not isinstance(editor_index, int) or (editor_index > len(self._detected_editors)):
            editor_index = None

        table = Table(title="Detected Editors", box=box.ROUNDED)

        table.add_column("#", style="bold yellow", justify="right", width=4)
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Type", style="magenta", justify="center")
        table.add_column("Path", style="white", overflow="fold")

        for idx, editor in enumerate(self._detected_editors, start=1):
            is_selected = (editor_index is not None and editor_index == idx - 1)
            ordinal = f"[bold cyan]* {idx}[/bold cyan]" if is_selected else str(idx)
            name = str(editor.get("name", "-"))
            typ = str(editor.get("type", "-"))
            path = str(editor.get("path", "-"))

            table.add_row(ordinal, name, typ, path)

        console.print('\n', table, '\n')
        return None

    def _resolve_editor_identifier(self, editor_identifier: Optional[str] = None) -> Optional[int]:
        """
        Resolves the index of the selected editor based on an identifier.
        The identifier may be:
            - None: defaults to the first detected editor.
            - A digit (e.g., "2"): interpreted as a 1-based index.
            - A wildcard string (e.g., "*code*"): matched case-insensitively against editor names.
        Returns:
            int: The index of the resolved editor in self._detected_editors.
            None: If no editors are detected.
        """
        self._logger.debug(f"Resolving editor using '{editor_identifier}'")

        if not self._detected_editors:
            self._logger.warning("No editors detected.")
            return None

        # Numeric selection (1-based index)
        if editor_identifier and editor_identifier.isdigit():
            idx = int(editor_identifier) - 1
            if 0 <= idx < len(self._detected_editors):
                return idx
            self._logger.warning(f"Editor index '{editor_identifier}' is out of range, falling back to default.")
            return 0

        # Wildcard + case-insensitive name match
        elif isinstance(editor_identifier, str):
            pattern = editor_identifier.lower()
            for idx, editor in enumerate(self._detected_editors):
                name = editor.get("name", "").lower()
                if fnmatch.fnmatch(name, pattern):
                    return idx

            self._logger.warning(f"Editor was not resolved using '{editor_identifier}', falling back to default.")
            return 0

        # Fallback to default
        return None

    def _purify_paths(self, paths: Iterable[dict[str, Any]], max_items: Optional[int] = None) -> list[dict[str, Any]]:
        """
        Filters and normalizes a list of dictionaries containing file or directory paths.
        For each dictionary that has a 'path' key:
          - Expands the path to an absolute path.
          - Validates that the path exists.
          - If valid, includes the updated dictionary in the result.
          - If missing or invalid, the item is excluded.
        Args:
            paths: Iterable of dictionaries. Each dict may contain a 'path' key.
            max_items: Maximum number of items to allow in the returned list.
        Returns:
            A new list of dictionaries where 'path' values are absolute and exist on disk.
        """
        purified = []
        for entry in paths:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str):
                abs_path = self._variables.expand(key=path, quiet=True)
                if os.path.exists(abs_path):
                    new_entry = dict(entry)  # shallow copy
                    new_entry["path"] = abs_path
                    purified.append(new_entry)

        # Truncate the list of specified
        if isinstance(max_items, int) and len(purified) > max_items:
            purified[:] = purified[:purified]

        return purified

    def _vscode_trust_workspace_path(self, path: str):
        """
        Best-effort method to mark a workspace as trusted in Visual Studio Code,
        suppressing the "Restricted Mode" popup, accepts either a file path or a directory path.

        Note: On WSL, it automatically finds the Windows-side trust file.
        On native Linux, it uses the standard config path.
        """
        with suppress(Exception):
            abs_path = os.path.abspath(path)
            trusted_path = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
            folder_uri = "file://" + pathname2url(trusted_path)

            # Detect WSL environment
            is_wsl = self._sys_info.is_wsl

            if is_wsl:
                # noinspection SpellCheckingInspection
                user = (
                        os.environ.get("WINUSER")
                        or os.environ.get("USERNAME")
                        or os.environ.get("USER")
                )
                config_path = f"/mnt/c/Users/{user}/AppData/Roaming/Code/User/workspaceTrustState.json"
                if not os.path.exists(config_path):
                    # Try VS Code Insiders
                    config_path = f"/mnt/c/Users/{user}/AppData/Roaming/Code - Insiders/User/workspaceTrustState.json"
            else:
                config_path = os.path.expanduser("~/.config/Code/User/workspaceTrustState.json")

            # Load or initialize trust data
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    trust_data = json.load(f)
            else:
                trust_data = {"trustedFolders": [], "trustedFiles": []}

            trusted = trust_data.setdefault("trustedFolders", [])
            if folder_uri not in trusted:
                trusted.append(folder_uri)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(trust_data, f, indent=4)

    def _edit_file(self, path: str, editor_index: Optional[int] = None) -> Optional[int]:
        """
        Launch the selected editor to open a file or directory.
        Args:
            path (str): The target file or directory to open.
            editor_index (int): The index of the editor to use.
                - A number (e.g., "2"): treated as 1-based index into self._detected_editors.
                - A wildcard string (e.g., "*code*"): matched case-insensitively against editor names.
        Returns:
            Execution return code.
        """

        if not isinstance(editor_index, int) or (editor_index > len(self._detected_editors)):
            raise RuntimeError(
                f"Invalid editor index specified, run 'edit -l' to list available editors.")

        path = self._variables.expand(key=path, quiet=True)
        if os.path.basename(path) == path:
            # Note: When it's just a base name (e.g., "foo.txt"), prepend current working directory
            path = os.path.abspath(os.path.join(os.getcwd(), path))

        if not os.path.exists(path):
            raise FileNotFoundError(f"Path does not exist: {path}")

        selected_editor = self._detected_editors[editor_index]
        editor_path = selected_editor.get("path")
        editor_type = selected_editor.get("type", "terminal")
        editor_args = selected_editor.get("args")

        if os.path.isdir(path):
            if editor_type != "gui":
                raise RuntimeError(f"cannot open directory with terminal editor: '{path}'")
        try:
            # VSCode specific: automatically add the path to the trusted paths
            self._vscode_trust_workspace_path(path)

            self._logger.debug(f"Opening '{path}' in '{editor_path} {editor_args}'")
            if selected_editor.get("type") == "terminal":
                results = subprocess.run([editor_path, *editor_args, os.path.abspath(path)])
            else:
                results = subprocess.Popen(
                    [editor_path, *editor_args, os.path.abspath(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
            return results.returncode

        except Exception as execution_error:
            raise execution_error from execution_error

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("-p", "--path", type=str, help="File name or path to open")
        parser.add_argument("-l", "--list-editors", action="store_true", help="Show the list of detected editors")
        parser.add_argument("-id", "--editor-identifier", type=str,
                            help="Editor identifying text, could be index or string")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes 'edit' command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """

        return_code = 0
        self._solution: CoreSolution = CoreSolution.get_instance()

        # Resolve the editor identifier (from arguments or config) to an index in the detected editors list.
        if self._selected_editor_index is None:
            if args.editor_identifier is not None:
                self._selected_editor_index = self._resolve_editor_identifier(args.editor_identifier)
            else:
                # Get the identifier from the solution
                editor_identifier = self._solution.get_arbitrary_item(key="default_editor")
                if isinstance(editor_identifier, str):
                    self._selected_editor_index = self._resolve_editor_identifier(editor_identifier)

        # Handle arguments

        if args.path is not None:
            # Execute the selected editor to edit a specified file
            return_code = self._edit_file(path=args.path, editor_index=self._selected_editor_index)

        elif args.list_editors:
            # List all detected editors.
            self._print_detected_editors(editor_index=self._selected_editor_index)

        elif args.editor_identifier is not None:
            # Set the session editor
            editor_index = self._resolve_editor_identifier(args.editor_identifier)
            if editor_index is not None:
                self._selected_editor_index = editor_index
                print(f"Editor index changed to '{editor_index + 1}'\n")
            else:
                raise RuntimeError(f"Could not set editor index for '{args.editor_identifier}'")

        else:
            # Error: no arguments
            return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
