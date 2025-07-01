"""
Script:         lsd_command.py
Author:         AutoForge Team

Description:
    Display richly formatted directory listings, inspired by https://github.com/lsd-rs/lsd.
    This enhanced listing tool, 'lsd', provides color-coded output, icon-based file identification,
    and timestamped views.

    This module defines file and directory icon mappings using the Nerd Fonts glyph set.
    It requires the terminal or code editor to use a compatible Nerd Font, or icons will appear
    as blank boxes or question marks.
    Nerd Fonts: https://www.nerdfonts.com/font-downloads

    To use these icons correctly, install one of the patched fonts (e.g. FiraCode Nerd Font, Hack Nerd Font)
    and configure your terminal emulator or IDE to use it.

    Recommended fonts:
      - FiraCode Nerd Font: https://www.nerdfonts.com/font-downloads#firacode
      - Hack Nerd Font: https://www.nerdfonts.com/font-downloads#hack
      - JetBrains Mono Nerd Font: https://www.nerdfonts.com/font-downloads#jetbrainsmono
"""

import argparse
import datetime
import locale
import math
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# AutoForge imports
from auto_forge import (CommandInterface, CoreToolBox)

AUTO_FORGE_MODULE_NAME = "lsd"
AUTO_FORGE_MODULE_DESCRIPTION = "ls - reimagined"
AUTO_FORGE_MODULE_VERSION = "1.0"


@dataclass(frozen=True)
class _LSDIconInfo:
    """
    Utility class to help oin storing file visual properties
    """
    icon: str
    description: str
    color: str


class LSDCommand(CommandInterface):

    def __init__(self, **_kwargs: Any):
        """
        Initializes the MiniWestCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments.
        """

        # Variables fwe should populate from configuration
        self._ansi_codes: Optional[dict[str, Any]] = None
        self._terminal_icons: Optional[dict[str, Any]] = None

        # Helps to get the date formatted to the specific system local settings
        locale.setlocale(locale.LC_TIME, '')
        self._default_date_format = '%m-%d %H:%M'

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=True)

    def initialize(self, **_kwargs: Any) -> bool:

        self._tool_box = CoreToolBox.get_instance()

        # Dependencies check
        if None in (self._tool_box, self._configuration):
            raise RuntimeError("failed to instantiate critical dependencies")

        # Gets resources  from configuration
        self._ansi_codes: Optional[dict[str, Any]] = self._configuration.get("ansi_codes", {})
        self._terminal_icons: Optional[dict[str, Any]] = self._configuration.get("terminal_icons")

        if None in (self._terminal_icons, self._ansi_codes):
            raise RuntimeError("'lsd' can't run, essential terminal resources (icons and ANSI codes) are unavailable")

        return True

    def _get_icon_info(self, ext_or_name: Path) -> _LSDIconInfo:
        """
        Return a symbolic icon for the given file or directory.
        Args:
            ext_or_name (Path): The file or directory path.
        Returns:
            TerminalFileIconInfo: Icon info associated with the given file or directory.
        """
        if self._terminal_icons is None or self._ansi_codes is None:
            return _LSDIconInfo("?", "Unknown", "")  # Fallback if maps not loaded

        icon_map = self._terminal_icons
        ansi_map = self._ansi_codes

        # Determine icon entry by name or extension
        if ext_or_name.is_dir():
            entry = icon_map.get("default_dir")
        else:
            name = ext_or_name.name
            entry = icon_map.get(name)
            if not entry:
                ext = ext_or_name.suffix.lower()
                entry = icon_map.get(ext, icon_map["default_file"])

        icon = entry.get("icon", "?")
        description = entry.get("description", "Unknown file")
        color_key = entry.get("color", "")
        color_code = ansi_map.get(color_key, "")  # resolve colorama style color name to the ANSI code.

        return _LSDIconInfo(icon=icon, description=description, color=color_code)

    def _format_entry_name_with_icon(self, name: str, is_dir: bool, icon_info: Optional[_LSDIconInfo]) -> str:
        """
        Format a file or directory name with an icon and color.
        Args:
            name (str): The filename to format.
            is_dir (bool): Whether this entry is a directory.
            icon_info (Optional[_LSDIconInfo]): Metadata about the icon.
        Returns:
            str: Colored and formatted name string.
        """
        if icon_info:
            icon = (icon_info.icon.strip() + " ").ljust(3)
            color = icon_info.color
        elif is_dir:
            icon = "  "  # fallback folder glyph
            color = self._ansi_codes.get('FORE.LIGHTBLUE_EX')
        else:
            icon = "   "  # no icon
            color = self._ansi_codes.get('FORE_WHITE') + self._ansi_codes.get('STYLE_BRIGHT')

        return f"{color}{icon}{name}{self._ansi_codes.get('STYLE_RESET_ALL')}"

    def _color_size(self, size_bytes: int) -> str:
        """
        Convert a size in bytes to a human-readable, colorized string.
        Args:
            size_bytes (int): The size in bytes.
        Returns:
            str: A colorized and human-readable size string.
        """
        if size_bytes == 0:
            return "0"

        if size_bytes == -1:
            # noinspection SpellCheckingInspection
            return f"{self._ansi_codes.get('FORE_LIGHTRED_EX')}?"

        if size_bytes < 1024:
            return (f"{self._ansi_codes.get('FORE_LIGHTGREEN_EX')}"
                    f"{size_bytes}{self._ansi_codes.get('FORE_GREEN')}b")

        size_name = ("", "k", "M", "G", "T")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = size_bytes / p

        if i == 0:
            return f"{int(s)}"

        num_str = f"{s:.1f}" if s < 10 else f"{int(s)}"
        num_part = num_str.rstrip("0").rstrip(".") if '.' in num_str else num_str
        unit = size_name[i]

        return (f"{self._ansi_codes.get('FORE_LIGHTGREEN_EX')}"
                f"{num_part}{self._ansi_codes.get('FORE_GREEN')}{unit}")

    def _get_max_date_width(self, field_name: str) -> int:
        """
        Calculate the maximum visible width needed for the date column.
        Args:
            field_name (str): The column header text, e.g. "Date Modified".
        Returns:
            int: The maximum width needed to fit either the date or the header.
        """
        now = datetime.datetime.now()
        max_date_width = max(len(now.strftime(self._default_date_format)), len(field_name))
        return max_date_width

    def _get_max_size_width(self, entry: Path, filed_name: str) -> int:
        """
        Quickly iterate through a path and return the maximum visible width of size strings as returned
        by _color_size(), excluding ANSI codes.
        Args:
            entry (Path): A directory or file path.
            filed_name (str): The name of the 'size' filed.
        Returns:
            int: Maximum width of the size field in visible characters.
        """
        max_width = len(filed_name)

        if not entry.exists():
            return max_width

        entries = [entry]
        if entry.is_dir():
            try:
                entries = list(entry.iterdir())
            except PermissionError:
                return max_width

        for entry in entries:
            if entry.is_file():
                with suppress(Exception):
                    size_bytes = entry.stat().st_size
                    colored = self._color_size(size_bytes)
                    visible = self._tool_box.strip_ansi(colored)
                    max_width = max(max_width, len(visible))

        return max_width

    def _get_formated_summary(self, total_files: int = 0, total_dirs: int = 0) -> Optional[str]:
        """
        Gets a colorized summary of listed files and directories only when counts that are greater than zero.
        Args:
            total_files (int): Number of files listed.
            total_dirs (int): Number of directories listed.
        """
        summary_parts = []
        summary_text = ""

        if not total_files and not total_dirs:
            return None

        if total_files > 0:
            summary_parts.append(
                f"{self._ansi_codes.get('FORE_GREEN')}{total_files} file{'s' if total_files != 1 else ''}"
                f"{self._ansi_codes.get('STYLE_RESET_ALL')}")

        if total_dirs > 0:
            summary_parts.append(
                f"{self._ansi_codes.get('FORE_CYAN')}{total_dirs} director{'ies' if total_dirs != 1 else 'y'}"
                f"{self._ansi_codes.get('STYLE_RESET_ALL')}")

        if summary_parts:
            summary_text = (f"Summary: {self._ansi_codes.get('STYLE_BRIGHT')}" + " • ".join(
                summary_parts) + self._ansi_codes.get('STYLE_RESET_ALL'))

        return summary_text

    def _lsd(self,  # noqa: C901 # Method is indeed too long, noted, thanks.
             destination_paths: list[Path], show_all: bool = False, group_directories_first: bool = False,
             disable_icons: bool = False, immediate_echo: bool = True, _show_long: Optional[bool] = False) -> Optional[
        str]:
        """
        The beating heart of the LSD Command.
        Args:
            destination_paths (list[Path]): List of paths to list.
            show_all (bool): Show hidden files (starting with '.').
            group_directories_first (bool): Group directories before files.
            disable_icons (bool): Disable icons column.
            immediate_echo (bool): Immediately echo output to stdout.
            _show_long (bool, optional): Show long description column.
        Returns:
            str: Formatted directory listing or None when immediate_echo is enabled.
        """

        output_lines = []
        show_header = len(destination_paths) > 1
        total_files = 0
        total_dirs = 0

        # Fields names
        size_header_text: str = 'Size'
        date_header_text: str = 'Date Modified'
        name_header_text: str = 'Name'

        # Get the projected max date field width
        max_date_width = self._get_max_date_width(date_header_text)
        date_padded_text = date_header_text.ljust(max_date_width)

        for dest in destination_paths:
            """ Loop on all files and folders in the specified path """
            dest = self._tool_box.get_expanded_path(str(dest))
            path = Path(dest)
            max_size_width = len(size_header_text)

            if not path.exists():
                output_lines.append(f"{self._ansi_codes.get('FORE_RED')}{self._module_info.name}: "
                                    f"{dest}: no such file or directory{self._ansi_codes.get('STYLE_RESET_ALL')}")
                continue

            if path.is_dir():
                max_size_width = self._get_max_size_width(path, size_header_text)
                entries = list(path.iterdir())
                if group_directories_first:
                    entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
                else:
                    entries.sort(key=lambda e: e.name.lower())
            else:
                entries = [path]

            if show_header:
                if not immediate_echo:
                    output_lines.append(f"{path}:")
                else:
                    sys.stdout.write(f"{path}:\n")

            size_padded_text = size_header_text.ljust(max_size_width)
            header = (f"{self._ansi_codes.get('STYLE_UNDERLINE')}{size_padded_text}"
                      f"{self._ansi_codes.get('STYLE_RESET_ALL')}{size_padded_text[len(size_header_text):]} "
                      f"{self._ansi_codes.get('STYLE_UNDERLINE')}{date_header_text}"
                      f"{self._ansi_codes.get('STYLE_RESET_ALL')}{date_padded_text[len(date_header_text):]} "
                      f"{self._ansi_codes.get('STYLE_UNDERLINE')}{name_header_text}"
                      f"{self._ansi_codes.get('STYLE_RESET_ALL')}")

            if not immediate_echo:
                output_lines.append(header)
            else:
                sys.stdout.write(f"{header}\n")

            for entry in entries:
                is_dir = False
                assert isinstance(entry, Path)
                if not show_all and entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        is_dir = True
                        size_str = (f"{self._ansi_codes.get('FORE_CYAN')}{'-':<{max_size_width}}"
                                    f"{self._ansi_codes.get('STYLE_RESET_ALL')}")
                        total_dirs += 1
                    else:
                        size_bytes = entry.stat().st_size
                        colored = self._color_size(size_bytes)
                        plain = self._tool_box.strip_ansi(colored)
                        padding = max_size_width - len(plain)
                        size_str = colored + " " * padding if padding > 0 else colored
                        total_files += 1

                except (FileNotFoundError, PermissionError):
                    if is_dir:
                        # noinspection SpellCheckingInspection
                        size_str = (f"{self._ansi_codes.get('FORE_LIGHTRED_EX')}?{'-':<{max_size_width}}"
                                    f"{self._ansi_codes.get('STYLE_RESET_ALL')}")
                    else:
                        colored = self._color_size(-1)  # Passing -1 will return "?" in red
                        plain = self._tool_box.strip_ansi(colored)
                        padding = max_size_width - len(plain)
                        size_str = colored + " " * padding if padding > 0 else colored

                # Add the data and time
                mtime = datetime.datetime.fromtimestamp(entry.lstat().st_mtime)
                date_raw = mtime.strftime(self._default_date_format)
                date_str = (f"{self._ansi_codes.get('FORE_BLUE')}{date_raw:<{max_date_width}}"
                            f"{self._ansi_codes.get('STYLE_RESET_ALL')}")

                # Find a suitable icon for the path / file
                icon_info: Optional[_LSDIconInfo] = None
                if not disable_icons:
                    icon_info = self._get_icon_info(entry)

                # File name + symlinks
                name = entry.name
                if entry.is_symlink():
                    try:
                        target = os.readlink(entry)
                        name += f" -> {target}"
                    except OSError:
                        name += " -> ?"

                # Construct a line with the appropriate icon
                name_str = self._format_entry_name_with_icon(name, entry.is_dir(), icon_info)

                # Final line composition
                formatted_line = f"{size_str} {date_str}  {name_str}"

                if not immediate_echo:
                    output_lines.append(formatted_line)
                    if show_header:
                        output_lines.append("")
                else:
                    sys.stdout.write(formatted_line + '\n')
                    sys.stdout.flush()

        formatted_summary = self._get_formated_summary(total_files=total_files, total_dirs=total_dirs)
        if formatted_summary is not None:
            if not immediate_echo:
                return "\n".join(output_lines) + '\n\n' + formatted_summary
            else:
                print('\n' + formatted_summary)

        return None

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        # Add options (optional arguments)
        parser.add_argument("-a", "--all", action="store_true", help="Show all files, including hidden ones.")
        parser.add_argument("-l", "--long", action="store_true", help="Long listing (currently ignored)")
        parser.add_argument("-g", "--group-directories-first", action="store_true",
                            help="Group directories before files.")
        parser.add_argument("-ni", "--no-icons", action="store_true", help="Disable icons.")

        # Add file/dir arguments: accept 0 or more
        parser.add_argument("paths", nargs="*", help="Files or directories to list")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:
            args (argparse.Namespace): The parsed arguments.
        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """

        target_paths = args.paths if args.paths else [os.getcwd()]

        # Gets the directory listing and print
        results = self._lsd(destination_paths=target_paths, show_all=args.all,
                            group_directories_first=args.group_directories_first, disable_icons=args.no_icons,
                            immediate_echo=False)

        if results is not None:
            print(f"\n{results}")
        print()
        return 0
