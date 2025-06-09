"""
Script:         processor.py
Author:         AutoForge Team

Description:
    Core module for preprocessing JSON files that may contain comments. It strips comments,
    validates the content, and returns a standard JSON-compatible dictionary to the caller.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Optional, Union

# AutoForge imports
from auto_forge import AutoForgeModuleType, CoreModuleInterface
from auto_forge.common.registry import Registry  # Runtime import to prevent circular import

AUTO_FORGE_MODULE_NAME = "Processor"
AUTO_FORGE_MODULE_DESCRIPTION = "JSON preprocessor"


class CoreProcessor(CoreModuleInterface):
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

    def _remove_formatter_hints(self, obj):
        """

        """
        if isinstance(obj, dict):
            return {k: self._remove_formatter_hints(v) for k, v in obj.items() if
                    not (isinstance(v, str) and "# @formatter:" in v)}
        elif isinstance(obj, list):
            return [self._remove_formatter_hints(i) for i in obj]
        else:
            return obj

    @staticmethod
    def _strip_comments(json_like_str: str) -> str:
        """
        Remove various types of comments from a string containing JSON-like content.
        Args:
            json_like_str (str): A string containing JSON data with embedded comments.
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
        def _replace_func(match):
            s = match.group(0)
            if s[0] in '"\'':  # If it's a string, return it unchanged
                return s
            else:
                return ''  # Otherwise, it's a comment, replace it with nothing

        # Remove comments using the custom replace function
        cleaned_str = re.sub(pattern, _replace_func, json_like_str)

        # Post-processing to fix trailing commas left by removed comments
        cleaned_str = re.sub(r',\s*([]}])', r'\1',
                             cleaned_str)  # Remove trailing commas before a closing brace or bracket

        # Clean up residual whitespaces and new lines if necessary
        cleaned_str = re.sub(r'\n\s*\n', '\n', cleaned_str)  # Collapse multiple new lines
        return cleaned_str.strip()

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
        clean_json: Optional[str] = None
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
                json_with_comments = text_file.read()

            # Perform comments cleanup
            clean_json = self._strip_comments(json_with_comments)

            # Load and return as JSON dictionary
            data = json.loads(clean_json)

            # Remove keys with formatter strings in values
            cleaned_data = self._remove_formatter_hints(data)

            return cleaned_data

        except (FileNotFoundError, json.JSONDecodeError, ValueError) as json_parsing_error:
            if clean_json is not None:
                error_line = self._get_line_number_from_error(str(json_parsing_error))
                if error_line is not None:
                    self._show_debug_message(file_name, clean_json, error_line, json_parsing_error)
            raise
