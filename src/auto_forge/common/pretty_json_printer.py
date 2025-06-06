"""
Script:         pretty_printer.py
Author:         AutoForge Team

Description:
    Provides a terminal-friendly JSON pretty printer using Rich for syntax highlighting,
    line numbering, and key-specific styling. Designed to assist developers in reading
    structured JSON data with enhanced clarity and context.
"""

import json
import re
import shutil
from typing import Any, Optional, Union

# Third-party
from rich.console import Console
from rich.text import Text

AUTO_FORGE_MODULE_NAME = "JSON Pretty Printer"
AUTO_FORGE_MODULE_DESCRIPTION = "Self-explanatory"


class PrettyPrinter:
    """
    A Rich-based utility for pretty-printing JSON data with syntax highlighting,
    line numbers, and optional key highlighting for enhanced terminal readability.
    """

    def __init__(self, indent: int = 4, sort_keys: bool = False, console: Optional[Console] = None,
                 numbering_width: int = 4, highlight_keys: Optional[list[str]] = None, auto_width: bool = True):
        """
        Pretty-prints a JSON-compatible object using native JSON formatting,
        Rich manual styling, and a configurable skin for colors and layout.

        Args:
            indent: Number of spaces for indentation (default: 4).
            sort_keys: If True, sort keys alphabetically (default: False).
            console: Optional Rich Console object. If None, a default is created.
            numbering_width: Width reserved for line numbers.
            highlight_keys: Optional list of JSON key names to highlight using distinctive colors.
            auto_width (bool): Automatically adjust the output to the terminal width
        """
        self.indent = indent
        self.sort_keys = sort_keys
        self.console = console or Console(force_terminal=True, color_system="truecolor")
        self.numbering_width = numbering_width
        self.auto_width = auto_width
        self.highlight_keys = highlight_keys or []

        self._json_skin = {"line_number": "dim", "line_separator": "dim", "key_default": "bold cyan",
                           "value_string": "bold white", "value_number": "cyan", "value_bool": "magenta",
                           "value_null": "dim", "punctuation": "white", "bracket": "bold white", }

        color_pool = ["bold green", "bold blue", "bold magenta", "bold green", "bright_blue", "bright_cyan",
                      "bright_magenta", "bright_green", "bright_white", "bold cyan", "bold white", "bright_black", ]
        self._color_map = {key: color_pool[i % len(color_pool)] for i, key in enumerate(self.highlight_keys)}

        self._key_pattern = re.compile(r'(\s*)"(.*?)":\s*(.*)')
        self._list_item_pattern = re.compile(r'(\s*)(".*?"|true|false|null|\d+)(,?)$')

    def render(self, obj: Union[dict[str, Any], list[Any]]) -> None:
        """
        Pretty-prints a JSON-compatible object using styled Rich output.

        This method displays a dictionary or list using syntax highlighting, line numbers,
        and optional key highlighting. It supports standard JSON primitives and adds visual
        formatting for easier reading in terminal environments.

        Args:
            obj (Union[Dict[str, Any], List[Any]]): A JSON-compatible object (dict or list)
                to render. Must be serializable by `json.dumps()`.
        """
        json_str = json.dumps(obj, indent=self.indent, ensure_ascii=False, sort_keys=self.sort_keys)
        lines = json_str.splitlines()
        line_number = 1

        max_width = None
        if self.auto_width:
            max_width = shutil.get_terminal_size((80, 20)).columns - 10  # Leave room for gutter and " │ "

        print()
        for line in lines:
            if match := self._key_pattern.match(line):
                styled = self._render_key_value_line(*match.groups(), line)
            elif match := self._list_item_pattern.match(line):
                styled = self._render_list_item_line(*match.groups())
            else:
                styled = self._render_raw_json_line(line)

            # Handle line trimming
            if self.auto_width and styled.cell_len > max_width:
                styled.truncate(max_width - 3, overflow="ellipsis")
                styled.append("...", style="yellow")

            num = str(line_number).rjust(self.numbering_width)
            self.console.print(Text(num + " │ ", style=self._json_skin["line_number"]) + styled)
            line_number += 1
        print()

    def _render_value_with_style(self, value: str) -> Text:
        """
        Applies appropriate Rich style to a JSON value string and returns a Text object.
        Args:
            value (str): The JSON value (maybe a string, number, boolean, or null).
        Returns:
            Text: A Rich Text object with the styled value.
        """
        styled = Text()
        if value.startswith('"') and value.endswith('"'):
            styled.append(value, style=self._json_skin["value_string"])
        elif value in ('true', 'false'):
            styled.append(value, style=self._json_skin["value_bool"])
        elif value == 'null':
            styled.append(value, style=self._json_skin["value_null"])
        else:
            styled.append(value, style=self._json_skin["value_number"])
        return styled

    def _render_key_value_line(self, indent_spaces: str, key: str, value: str, original_line: str) -> Text:
        """
        Constructs a styled Rich Text line for a JSON key-value pair.
        """
        styled = Text()
        styled.append(indent_spaces)

        key_style = self._color_map.get(key, self._json_skin["key_default"])
        styled.append(f'"{key}"', style=key_style)
        styled.append(": ", style=self._json_skin["punctuation"])

        value = value.rstrip(',')
        comma = "," if original_line.strip().endswith(',') else ""

        styled += self._render_value_with_style(value)

        if comma:
            styled.append(comma, style=self._json_skin["punctuation"])

        return styled

    def _render_list_item_line(self, indent_spaces: str, val: str, comma: str) -> Text:
        """
        Constructs a styled Rich Text line for a JSON list item.
        """
        styled = Text()
        styled.append(indent_spaces)
        styled += self._render_value_with_style(val)

        if comma:
            styled.append(comma, style=self._json_skin["punctuation"])

        return styled

    def _render_raw_json_line(self, line: str) -> Text:
        """
        Constructs a styled Rich Text line for a generic JSON line.
        Args:
            line (str): The raw JSON line to be styled.
        Returns:
            Text: A Rich Text object with bracket and punctuation highlighting applied.
        """
        styled = Text()
        for char in line:
            if char in ['{', '}', '[', ']']:
                styled.append(char, style=self._json_skin["bracket"])
            elif char == ':' or char == ',':
                styled.append(char, style=self._json_skin["punctuation"])
            else:
                styled.append(char)
        return styled
