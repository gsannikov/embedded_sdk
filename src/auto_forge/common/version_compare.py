"""
Script:         version_compare.py
Author:         AutoForge Team

Description:
    Provides the VersionCompare class, which offers an API to determine whether a given binary satisfies a required
    version. It supports extracting version information from arbitrary text and comparing it against a required
    version, either as a fixed value (e.g., '4.16') or using a constraint expression with an operator (e.g., '>= 3.7').
"""

import re
from typing import Optional, Union

# AutoForge imports
from auto_forge import (ExpectedVersionInfoType)

AUTO_FORGE_MODULE_NAME = "VersionCompare"
AUTO_FORGE_MODULE_DESCRIPTION = "Version detection and comparison utilities"


class VersionCompare:
    """
    Parses and validates version strings with comparison operators.
    """

    ACCEPTED_OPERATORS = ['>=', '>', '==', '<', '<=']

    def __init__(self):
        pass

        # noinspection SpellCheckingInspection

    @staticmethod
    def _to_tuple(text: str, max_parts: int = 3) -> Optional[tuple[int, ...]]:
        """
        Convert a version string (e.g., '1.2.3') into a tuple of integers, up to `max_parts` components.
        Non-numeric parts are ignored.
        Args:
            text (str): A well-formed version string.
            max_parts (int): Maximum number of version components to return.
        Returns:
            tuple[int, ...]: Parsed version tuple, e.g., (1, 2, 3) or None on error.
        """
        if max_parts > 3:
            return None

        parts = re.findall(r"\d+", text)
        if len(parts) > 0:
            return tuple(map(int, parts[:max_parts]))

        return None

    @staticmethod
    def _parse_version_info(input_string: Optional[str], operators: list) -> Optional[ExpectedVersionInfoType]:
        """
        Parses an input version string, validates its operator, and cleans the version number.
        Args:
            input_string: The input string, e.g., '>=3.2', '>= 3.7.0', '== 9.A5.6.B'.
            operators (list): List of operators to check.
        Returns:
            A namedtuple (operator, version) if successful, otherwise None.
        """
        if not isinstance(input_string, str):
            return None

        input_string = input_string.strip()
        version_part = ""

        # Validate version limiter symbols (operators)
        operator = None

        # If the input starts with a decimal, treat it as a version without an explicit operator.
        # In this case, default to using the '==' operator.
        if re.match(r'^\d+', input_string):
            input_string = "==   " + input_string

        for op in operators:
            if input_string.startswith(op):
                operator = op
                version_part = input_string[len(op):].strip()
                break

        if operator is None:
            raise ValueError(f"unsupported version comparison operator: '{input_string}'")

        # Clean the actual version
        # Remove non-digit and non-dot characters, and handle trailing non-digits
        cleaned_version_parts = []
        for part in version_part.split('.'):
            # Use regex to keep only digits
            cleaned_part = re.sub(r'[^0-9]', '', part)
            if cleaned_part:  # Only add if there are digits left
                cleaned_version_parts.append(cleaned_part)

        cleaned_version = ".".join(cleaned_version_parts)

        # Ensure the cleaned version isn't empty after cleaning
        if not cleaned_version:
            raise ValueError(f"version could not be parsed from: '{input_string}'")

        return ExpectedVersionInfoType(operator=operator, version=cleaned_version)

    # noinspection SpellCheckingInspection
    @staticmethod
    def extract_version(text: Optional[Union[str, bytes]]) -> Optional[str]:
        """
        General purpose the best effort version extractor and identifier from a given text blob.
        Attempts to find version numbers using a series of regular expressions
        designed to match common versioning patterns. It returns the first match found.
        Args:
            text (str, bytes): A string, typically the output of a command, typically in response to
                something like 'binary --version'.
        Returns:
            A string containing the extracted version number if found, otherwise None.
        """
        # Handle bytes input by decoding to string
        if isinstance(text, bytes):
            try:
                text = text.decode(errors='ignore')
            except UnicodeDecodeError:
                # Error: Input bytes could not be decoded with UTF-8.
                return None

        # Ensure that after potential decoding, we have a string
        elif not isinstance(text, str):
            # Error: Input must be a string or bytes.
            return None

        # Regex patterns to match various version formats.
        # Ordered from more specific to more general to try and get the best match first.
        patterns = [  # Examples: 5.2.37(1)-release, 1.2.3-alpha, 2.0.0-rc1
            # Catches semantic versioning with potential build/release info
            r'(\d+\.\d+\.\d+[\w.-]*)',  # Most common: X.Y.Z with optional suffixes

            # Examples: version 5.2.37, v5.2.37, Version: 5.2.37
            r'(?:[Vv]ersion[:\s]?|v)(\d+\.\d+\.\d+[\w.-]*)',

            # Examples: 5.2.37 (without (1)-release part if the above missed it)
            r'(\d+\.\d+\.\d+)',

            # Examples: 1.23, v1.23
            r'(?:[Vv]ersion[:\s]?|v)(\d+\.\d+[\w.-]*)',  # X.Y with optional suffixes

            # Examples: 1.23 (without prefix)
            r'(\d+\.\d+)',

            # Examples: version 5, v5 (less common but possible for major versions)
            r'(?:[Vv]ersion[:\s]?|v)(\d+[\w.-]*)',

            # Example: 12 (single number, could be a build number or simple version)
            # This is very broad, so it's last.
            # It looks for a number that is likely part of a version string,
            # often preceded by "version", "release", or similar keywords, or punctuation.
            r'(?:[Vv]ersion\s*|release\s*|[Rr]evision\s*|[Bb]uild\s*|[\(\s,])(\d+)(?:[\)\s,]|$)', ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                # Prioritize group 1 if it exists (it usually captures just the version part)
                # Otherwise, take group 0 (the whole match)
                if len(match.groups()) > 0 and match.group(1):
                    version = match.group(1)
                    # Further clean-up: sometimes a trailing dot or hyphen might be caught
                    return version.strip('.-')
                elif match.group():
                    # If it's group 0, we might need to clean it up more.
                    # For example, if pattern was r'(?:Version:\s)(\d+\.\d+)'
                    # match.group(0) would be "Version: 1.2", we want "1.2"
                    # However, most of our specific captures are in group(1)
                    # This is a fallback.
                    cleaned_version = re.sub(r'^(?:[Vv]ersion[:\s]?|v)', '', match.group())
                    return cleaned_version.strip('.-')

        return None

    def compare(self, detected: str, expected: str, operators: Optional[list] = None) -> Optional[
        tuple[bool, Optional[str]]]:
        """
        Checks whether the detected version matches the expected which could be a version or an expression.
        Args:
            detected (str):  The version we have, could be binary output or ant string.
            expected (str): the version we expected, could be fixed (e.g., 10.5) or an expression (e.g., ">=10.0", "==1.2.3").
            operators (list): List of operators to check.
        Returns:
            Tuple[bool, Optional[str]]: A tuple of (is_satisfied, detected_version_str).
        """
        try:
            expected_version_tuple: Optional[tuple[int, ...]] = None
            detected_version_tuple: Optional[tuple[int, ...]] = None

            # Use default operators list when not provided.
            if operators is None:
                operators = self.ACCEPTED_OPERATORS

            # Extract detected version tuple from output
            extracted_detected = self.extract_version(text=detected)
            if extracted_detected:
                detected_version_tuple = self._to_tuple(text=extracted_detected)

                if not detected_version_tuple:
                    raise ValueError("input version string could not be determined")

            # Extract operator and version string from version expression
            version_info: Optional[ExpectedVersionInfoType] = self._parse_version_info(input_string=expected,
                                                                                       operators=operators)
            if not version_info:
                raise ValueError(f"invalid expected version: '{expected}'")

            # Convert expected version string to version tuple
            extracted_version = self.extract_version(text=version_info.version)
            if extracted_version:
                expected_version_tuple = self._to_tuple(text=extracted_version)
                if not expected_version_tuple:
                    raise ValueError("input expected version string could not be determined")

            # Perform comparison
            op = version_info.operator
            if op == ">=":
                result = detected_version_tuple >= expected_version_tuple
            elif op == ">":
                result = detected_version_tuple > expected_version_tuple
            elif op == "==":
                result = detected_version_tuple == expected_version_tuple
            elif op == "<":
                result = detected_version_tuple < expected_version_tuple
            elif op == "<=":
                result = detected_version_tuple <= expected_version_tuple
            else:
                raise ValueError(f"unsupported version comparison operator: '{op}'")

            # Return result and printable version string
            return result, ".".join(map(str, detected_version_tuple))

        except Exception as version_detection_error:
            raise version_detection_error from version_detection_error
