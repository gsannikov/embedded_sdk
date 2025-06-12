"""
Script:         jsonc_processor.py
Author:         AutoForge Team

Description:
    Core module for preprocessing JSON files that may contain comments. It strips comments,
    validates the content, and returns a standard JSON-compatible dictionary to the caller.
"""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional, Union

from rich.console import Console
from rich.text import Text

# AutoForge imports
from auto_forge import AutoForgeModuleType, CoreModuleInterface
from auto_forge.common.registry import Registry  # Runtime import to prevent circular import

AUTO_FORGE_MODULE_NAME = "Processor"
AUTO_FORGE_MODULE_DESCRIPTION = "JSONC preprocessor"


class _JOSNPrettyPrinter:
    """
    A Rich-based utility class for pretty-printing JSON data with syntax highlighting,
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


class CoreJSONCProcessor(CoreModuleInterface):
    """
    JSON pre-processing dedicated class.
    """

    def _initialize(self):
        """
        Initializes the 'Processor' class instance which provide extended functionality around JSON files.
        """
        # Persist this module instance in the global registry for centralized access
        registry = Registry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

    @staticmethod
    def _get_line_number_from_error(error_message: str) -> Optional[int]:

        # This function needs to be tailored to the specific format of the error message
        # Common JSONDecodeError message format: 'Expecting ',' delimiter: line 4 column 25 char 76'
        match = re.search(r"line (\d+)", error_message)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _show_debug_message(file_name: str, json_string: str, line_number: Optional[int] = None,
                            error_message: Optional[str] = None):
        """
        JSON debugging helper: prints each line of a JSON string with line numbers, focusing on the error line
        by printing five lines before and after the erroneous line. Lines that contain errors are highlighted in red.
        Args:
            file_name (str): The JSON file name
            json_string (str): The faulty JSON as a clear text string.
            line_number (int, optional): The specific line number where the error occurred.
            error_message (str, optional): A description of the error.
        """

        lines = json_string.splitlines()
        print(f"Syntax Error:\nA JSON error was detected in '{os.path.basename(file_name)}' "
              f"at line {line_number}.\n")

        # Calculate the range of lines to print around the error
        start_line = max(1, line_number - 5) if line_number else 1
        end_line = min(len(lines), line_number + 5) if line_number else len(lines)

        for index in range(start_line, end_line + 1):
            current_line = lines[index - 2]  # Adjust for zero-based index
            if index == line_number:
                highlight = f"\033[43;97m{index:3}: {current_line}\033[0m"
                if error_message:
                    print(f"{highlight} \033[91m// {error_message}\033[0m")
                else:
                    print(highlight)
            else:
                print(f"{index:3}: {current_line}")

        print()

    def _remove_pycharm_formatter_hints(self, _json_obj: Optional[Any]) -> Optional[Any]:
        """
        Remove PyCharm formatting directives: @formatter:off or  @formatter:on
        """
        if isinstance(_json_obj, dict):
            return {k: self._remove_pycharm_formatter_hints(v) for k, v in _json_obj.items() if
                    not (isinstance(v, str) and "# @formatter:" in v)}
        elif isinstance(_json_obj, list):
            return [self._remove_pycharm_formatter_hints(i) for i in _json_obj]
        else:
            return _json_obj

    @staticmethod
    def _strip_comments(_text: str) -> str:
        """
        Remove various types of comments from a string containing JSON-like content.
        Args:
            _text (str): A string containing JSON data with embedded comments.
        Returns:
            str: The JSON string with all comments removed, ready to be parsed by json.loads()
        """
        # Pattern to find JSON strings, comments, or any relevant content
        pattern = re.compile(r'"(?:\\.|[^"\\])*"'  # Match double-quoted strings
                             r"|'(?:\\.|[^'\\])*'"  # Match single-quoted strings (if unofficially used in JSON-like structures)
                             r"|//.*?$"  # Match single-line comments
                             r"|/\*.*?\*/"  # Match multi-line comments
                             r"|'''.*?'''"  # Match triple single-quoted Python multi-line strings
                             r'|""".*?"""',  # Match triple double-quoted Python multi-line strings
                             flags=re.DOTALL | re.MULTILINE)

        # Use a function to decide what to replace with
        def _replace_func(_match):
            _s = _match.group(0)
            if _s[0] in '"\'':  # If it's a string, return it unchanged
                return _s
            else:
                return ''  # Otherwise, it's a comment, replace it with nothing

        # Remove comments using the custom replace function
        _cleaned = re.sub(pattern, _replace_func, _text)

        # Post-processing to fix trailing commas left by removed comments
        _cleaned = re.sub(r',\s*([]}])', r'\1',
                             _cleaned)  # Remove trailing commas before a closing brace or bracket

        # Clean up residual whitespaces and new lines if necessary
        _cleaned = re.sub(r'\n\s*\n', '\n', _cleaned)  # Collapse multiple new lines
        return _cleaned.strip()

    @staticmethod
    def _normalize_multiline_strings(_text: str) -> str:
        """ Convert multiline double-quoted strings into valid JSON """
        def _replacer(_match):
            _content = _match.group(1)
            _escaped = _content.replace('\n', '\\n')
            return f'"{_escaped}"'

        # Match content inside "..." which spans multiple lines
        _pattern = r'"((?:[^"\\]|\\.)*?)"(?=\s*[:,}])'  # Note: Crude, improve as needed
        return re.sub(_pattern, _replacer, _text, flags=re.DOTALL)

    def preprocess(self, file_name: Union[str, Path]) -> Optional[dict[str, Any]]:
        """
        Preprocess a JSON or JSONC file to remove embedded comments.
        If the specified file does not exist but a file with the alternate extension
        exists (.json ↔ .jsonc), the alternate will be used.
        Args:
            file_name (str | Path): Path to the JSON or JSONC file.
        Returns:
            dict or None: Parsed JSON object, or None if an error occurs.
        """
        clean_text: Optional[str] = None
        path_obj = Path(file_name)
        base = path_obj.with_suffix('')  # Remove .json or .jsonc if present

        # Determine the actual file to use
        if (base.with_suffix('.json')).is_file():
            resolved_path = base.with_suffix('.json')
        elif (base.with_suffix('.jsonc')).is_file():
            resolved_path = base.with_suffix('.jsonc')
        else:
            raise FileNotFoundError("Neither .json nor .jsonc file could be found.")

        # Optional: normalize early to str for downstream APIs
        file_name: str = str(resolved_path)

        try:

            # Expand and normalize
            config_file = os.path.expanduser(os.path.expandvars(file_name))
            if not config_file.endswith(os.sep + '.'):
                config_file = os.path.abspath(config_file)

            if not os.path.exists(config_file):
                raise FileNotFoundError(f"JSONC file '{config_file}' does not exist.")

            # Load the file as text
            with open(config_file) as text_file:
                dirty_json = text_file.read()

            # Handle potential strings spanning across several lines
            clean_text = self._normalize_multiline_strings(dirty_json)

            # Perform comments cleanup
            clean_text = self._strip_comments(clean_text)

            # Load and return as JSON dictionary
            data = json.loads(clean_text)

            # Remove PyCharm embedded formatting directives
            cleaned_data = self._remove_pycharm_formatter_hints(data)

            return cleaned_data

        except (FileNotFoundError, json.JSONDecodeError, ValueError) as json_parsing_error:
            if clean_text is not None:
                error_line = self._get_line_number_from_error(str(json_parsing_error))
                if error_line is not None:
                    self._show_debug_message(file_name, clean_text, error_line, json_parsing_error)
            raise


    @staticmethod
    def pretty_print(obj:Any, indent: int = 4, sort_keys: bool = False, console: Optional[Console] = None,
                 numbering_width: int = 4, highlight_keys: Optional[list[str]] = None, auto_width: bool = True):
        """
        Uses rich to pretty print JSON data with syntax highlighting, line numbers, and optional key highlighting
        for enhanced terminal readability.
        """
        try:
            pretty_printer = _JOSNPrettyPrinter(indent, sort_keys, console, numbering_width, highlight_keys, auto_width)
            pretty_printer.render(obj)

        except Exception as printer_error:
            raise printer_error from printer_error
