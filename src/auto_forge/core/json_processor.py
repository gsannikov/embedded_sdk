#!/usr/bin/env python3
"""
Script:     pre_processor.py
Version:    0.1

Description:
    Preprocessor is a core module that allows to convincingly read JSONs, which were
    enhanced with comments.
"""

import json
import logging
import os
import re
from typing import Optional, Any, Dict

AUTO_FORGE_MODULE_NAME = "Processor"
AUTO_FORGE_MODULE_DESCRIPTION = "JSON preprocessor core service"


class JSONProcessorLib:
    """
    About...
    """

    def __init__(self):

        self._service_name: str = self.__class__.__name__

        # Initialize a logger instance
        self._logger: logging.Logger = logging.getLogger(AUTO_FORGE_MODULE_NAME)
        self._logger.setLevel(level=logging.DEBUG)
        self._initialized = True

    def preprocess(self, file_name: str) -> Optional[Dict[str, Any]]:
        """
         Preprocess a JSON file to remove embedded comments.
         Args:
             file_name (str): Path to the JSON file.
         Returns:
             dict or None: Parsed JSON object with placeholders resolved, or None if an error occurs.
         """
        clean_json: Optional[str] = None

        try:
            # Preform expansion as needed
            expanded_file = os.path.expanduser(os.path.expandvars(file_name))
            file_name = os.path.abspath(expanded_file)  # Resolve relative paths to absolute paths

            # Load the file as text
            with open(file_name, "r") as text_file:
                json_with_comments = text_file.read()

            # Remove comments
            clean_json = self._strip_comments(json_with_comments)

            # Load and return as JSON dictionary
            data = json.loads(clean_json)
            return data

        except (FileNotFoundError, json.JSONDecodeError, ValueError) as json_parsing_error:
            if clean_json is not None:
                error_line = self._get_line_number_from_error(str(json_parsing_error))
                if error_line is not None:
                    self._show_debug_message(file_name, clean_json, error_line, json_parsing_error)
            raise RuntimeError(json_parsing_error)

    @staticmethod
    def _get_line_number_from_error(error_message: str) -> Optional[int]:

        # This function needs to be tailored to the specific format of the error message
        # Common JSONDecodeError message format: 'Expecting ',' delimiter: line 4 column 25 char 76'
        import re
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
                # Highlight the error line in red
                print(f"{index:3}: {current_line} // {error_message}" if error_message else "")
            else:
                # Print other lines in normal color
                print(f"{index:3}: {current_line}")
        print("\n")

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
        pattern = re.compile(
            r'"(?:\\.|[^"\\])*"'  # Match double-quoted strings
            r"|'(?:\\.|[^'\\])*'"  # Match single-quoted strings (if unofficially used in JSON-like structures)
            r"|//.*?$"  # Match single-line comments
            r"|/\*.*?\*/"  # Match multi-line comments
            r"|'''.*?'''"  # Match triple single-quoted Python multi-line strings
            r'|""".*?"""',  # Match triple double-quoted Python multi-line strings
            flags=re.DOTALL | re.MULTILINE
        )

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
