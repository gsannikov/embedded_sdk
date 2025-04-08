#!/usr/bin/env python3
"""

Script:         bootstrap.py
Version:        1.0.0
Author:         Intel AutoForge team

SDK environment installation toolbox.

"""
import json
import logging
import os
import platform
import re
import select
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from contextlib import suppress
from datetime import datetime
from enum import Enum
from typing import Optional, Union, Any, List, Tuple, Match
from urllib.parse import urlparse, unquote

from auto_forge import (JSONProcessorLib, NullLogger)

AUTO_FORGE_MODULE_NAME = "SetupTools"
AUTO_FORGE_MODULE_DESCRIPTION = "Environment setup tools"


class ValidationMethod(Enum):
    """
    Enumeration for system validation methods.

    Attributes:
        EXECUTE_PROCESS (int): Run a shell command and validate based on its return code and/or output.
        READ_FILE (int): Read specific lines from a file and validate expected content.
    """
    EXECUTE_PROCESS = 1
    READ_FILE = 2
    SYS_PACKAGE = 3


class StatusTextType(Enum):
    """
    Enum to specify the types of text display for status messages.

    Attributes:
        TITLE: Initial text padded with dots, e.g., "Status ........."
        PROGRESS: In-place text which is updated and cleared with each new progress step, e.g., "Status ......... 90%"
        STATUS: Final operation stats, typically followed by a newline, e.g., "Status ......... OK"
    """
    TITLE = 1
    PROGRESS = 2
    STATUS = 3


class ANSIGuru:
    def __init__(self):
        pass

    @staticmethod
    def set_cursor_visibility(visible: bool):
        if visible:
            sys.stdout.write('\033[?25h')  # ANSI code to show the cursor
        else:
            sys.stdout.write('\033[?25l')  # ANSI code to hide the cursor
        sys.stdout.flush()

    @staticmethod
    def save_cursor_position():
        """Saves the current cursor position."""
        sys.stdout.write("\033[s")
        sys.stdout.flush()

    @staticmethod
    def restore_cursor_position():
        """Restores the cursor to the last saved position."""
        sys.stdout.write("\033[u")
        sys.stdout.flush()

    def move_cursor(self, row: Optional[int] = None, col: Optional[int] = None):
        """
        Moves the cursor to the specified (row, col). If only one parameter is provided,
        it attempts to move to that row or column maintaining the other coordinate unchanged.

        Args:
            row (Optional[int]): The row to move to (1-based indexing).
            col (Optional[int]): The column to move to (1-based indexing).
        """
        current_pos = self.get_cursor_position()

        # Position unknown or nothing to do when both are none.
        if current_pos is None or (row is None and col is None):
            return

        if row is not None and col is not None:
            # Move to exact row and column
            sys.stdout.write(f"\033[{row + 1};{col + 1}H")
        elif row is None:
            # Move to specific column in the current row
            if current_pos != (-1, -1):
                sys.stdout.write(f"\033[{current_pos[0] + 1};{col + 1}H")
        elif col is None:
            # Move to specific row in the current column
            if current_pos != (-1, -1):
                sys.stdout.write(f"\033[{row + 1};{current_pos[1] + 1}H")

        sys.stdout.flush()

    @staticmethod
    def erase_line_to_end():
        """Erases from the current cursor position to the end of the line."""
        sys.stdout.write("\033[K")
        sys.stdout.flush()

    @staticmethod
    def get_cursor_position() -> Optional[Tuple[int, int]]:
        """
        Queries the terminal for the current cursor position and returns it as a tuple (row, col).
        This function might not work in all environments. It requires the terminal to respond to the query.

        Returns:
            Optional[Tuple[int, int]]: The current cursor position (row, col) as zero-based indices,
            or None if the position cannot be determined.
        """
        sys.stdout.write("\033[6n")
        sys.stdout.flush()
        response: str = ""

        while True:
            ch = sys.stdin.read(1)
            response += ch
            if ch == 'R':
                break
        # Expected response format is ESC[row;colR
        match: Match = re.search(r"\033\[(\d+);(\d+)R", response)
        if match:
            row, col = map(int, match.groups())
            return row - 1, col - 1  # Convert from 1-based to 0-based.
        return None  # Return an invalid position if something goes wrong.

    @staticmethod
    def write_color(text: Optional[str] = None, color_code: int = 0):
        """
        Args:
            text (str): The text to display. If None or empty, only sets the color without text output.
            color_code (int): The ANSI color code for setting the text color. If 0, resets all attributes to default.

        Supported ANSI Color Codes:
        - 0: Reset all attributes
        - 30: Black
        - 31: Red
        - 32: Green
        - 33: Yellow
        - 34: Blue
        - 35: Magenta
        - 36: Cyan
        - 37: White
        - 90: Bright Black (Gray)
        - 91: Bright Red
        - 92: Bright Green
        - 93: Bright Yellow
        - 94: Bright Blue
        - 95: Bright Magenta
        - 96: Bright Cyan
        - 97: Bright White
        """
        if color_code == 0:
            # Reset all attributes
            sys.stdout.write("\033[0m")

        elif text is None or len(text) == 0:
            # Only set the color without followed text and auto color reset
            sys.stdout.write(f"\033[{color_code}m")
        else:
            # ANSI color code is prefixed with \033[ and suffixed with m. Default reset code is \033[0m
            sys.stdout.write(f"\033[{color_code}m{text}\033[0m")

        sys.stdout.flush()


class SetupToolsLib:

    def __init__(self, workspace_path: Optional[str] = None, automated_mode: bool = False):
        """
        Initialize the environment setup toolbox class.
        Args:
            workspace_path(Optional[str]): The workspace path.
            automated_mode(bool): Specify if we're running in automation mode
        """

        self._py_venv_path: Optional[str] = None
        self._package_manager: Optional[str] = None
        self._workspace_path: Optional[str] = workspace_path
        self._default_execution_time: float = 60.0  # Time allowed for executed shell command
        self._procLib = JSONProcessorLib()  # Instantiate JSON processing library
        self._steps_data: Optional[List[str, Any]] = None  # Stores the steps parsed JSON dictionary
        self._local_storage = {}  # Initialize an empty dictionary for stored variables
        self._ansi_term = ANSIGuru()  # Instance the local ANSI trickery gadgets
        self._automated_mode: bool = automated_mode  # Default execution mode

        if automated_mode:
            self._logger = logging.getLogger(AUTO_FORGE_MODULE_NAME)
        else:
            self._logger = NullLogger()  #

        # The following are defaults used when printing user friendly terminal status
        self._status_title_length: int = 80
        self._status_add_time_prefix: bool = True
        self._status_new_line: bool = False

        # Determine which package manager is available on the system.
        if shutil.which("apt"):
            self._package_manager = "apt"
        elif shutil.which("dnf"):
            self._package_manager = "dnf"

        # Get the system type (e.g., 'Linux', 'Windows', 'Darwin')
        self._system_type = platform.system().lower()
        self._is_wsl = True if "wsl" in platform.release().lower() else False

        # Get extended distro info when we're running under Linux
        if self._system_type == "linux":
            self._linux_distro, self._linux_version = self._get_linux_distro()

    def _show_status(self, text: str,
                     text_type: StatusTextType = StatusTextType.TITLE,
                     new_line: bool = False,
                     operation_status_code: Optional[int] = 0):
        """
        Prints the status message to the console in a formatted manner with color codes.

        Args:
            text (str): The text to log.
            text_type (StatusTextType): TITLE, PROGRESS and cSTATUS.
            new_line (bool): If True, the message is written in a new line rather than in place.
            operation_status_code (int): If specified it will be used for colored final status
        """

        # Either logger or terminal user status, not both.
        if self._automated_mode:
            return

        terminal_width: int = shutil.get_terminal_size().columns
        text = self._normalize_text(text=text, allow_empty=True)
        text_length: int = len(text)
        time_string: Optional[str] = None

        # Terminal width too small
        if text_length >= terminal_width:
            return

        if text_type not in StatusTextType or text_length == 0:
            return

        #
        # Print the title, dots and optional time prefix
        #

        if text_type == StatusTextType.TITLE:

            # adjust screen text length to accommodate for the time prefix
            if self._status_add_time_prefix:
                now = datetime.now()
                time_string = now.strftime("%H:%M:%S ")
                text_length = len(time_string) + text_length

            # Crop text the maximum allowed title length
            if text_length > self._status_title_length:
                text = text[:self._status_title_length - 4]
                text_length = self._status_title_length - 4

            # Adjust the number of dots for the alignment and print the text with one space before and after the dots.
            dots_count = self._status_title_length - text_length - 2
            dots = "." * dots_count

            if not new_line:
                self._ansi_term.erase_line_to_end()

            # Prefixed with the colored time
            if time_string is not None:
                self._ansi_term.write_color(text=time_string, color_code=34)

            sys.stdout.write(f"{text} {dots} ")
            self._ansi_term.erase_line_to_end()
            self._ansi_term.save_cursor_position()

        #
        # Print the intermediate progress text or operation final status
        #

        elif text_type == StatusTextType.PROGRESS or text_type == StatusTextType.STATUS:

            self._ansi_term.restore_cursor_position()

            if text_type == StatusTextType.PROGRESS:
                sys.stdout.write(text[:terminal_width - self._status_title_length])
                self._ansi_term.erase_line_to_end()
                self._ansi_term.restore_cursor_position()
            else:
                # Use green / red colors when we know the operation shell status
                if operation_status_code is not None:
                    if operation_status_code == 0:
                        self._ansi_term.write_color(text=text, color_code=32)
                    else:
                        self._ansi_term.write_color(text=text, color_code=31)
                else:
                    sys.stdout.write(text)

                self._ansi_term.erase_line_to_end()
                if new_line:
                    sys.stdout.write('\n')
                else:
                    sys.stdout.write('\r')

    @staticmethod
    def _normalize_text(text: Optional[str], allow_empty: bool = False) -> str:
        """
        Normalize the input string by stripping leading and trailing whitespace.
        Args:
            text (Optional[str]): The string to be normalized.
            allow_empty (Optional[bool]): No exception of the output is an empty string

        Returns:
            str: A normalized string with no leading or trailing whitespace.
        """
        # Check for None or empty string after potential stripping
        if text is None or not isinstance(text, str):
            raise ValueError("Input must be a non-empty string.")

        # Strip whitespace
        normalized_string = text.strip()
        if not allow_empty and not normalized_string:
            raise ValueError("Input string cannot be empty after stripping")

        return normalized_string

    @staticmethod
    def _check_directory_empty(path: str):
        """
        Check if the given directory is empty.
        Args:
            path (str): The directory path to check.

        Returns:
             None, raising exception on error.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"'{path}' does not exist: {path}")

        if not os.path.isdir(path):
            raise ValueError(f"'{path}' is not a directory")

        # List the contents of the directory
        if os.listdir(path):
            raise RuntimeError(f"'{path}' is not empty")

    @staticmethod
    def _get_linux_distro() -> Optional[tuple[str, str]]:
        """
        Extracts the distribution name and version from /etc/os-release.
        Returns:
            tuple: The distribution name and version in lowercase (e.g., ('ubuntu', '20.04')).
                or None on any error.
        """
        distro_info = {'name': 'unknown', 'version': 'unknown'}

        with suppress(Exception):
            with open("/etc/os-release", "r") as file:
                # Read each line and split by '=' to extract key-value pairs
                for line in file:
                    if line.startswith("ID="):
                        # Clean up the ID value and convert to lowercase
                        distro_info['name'] = line.strip().split('=')[1].strip().replace('"', '').lower()
                    elif line.startswith("VERSION_ID="):
                        # Clean up the VERSION_ID value and convert to lowercase
                        distro_info['version'] = line.strip().split('=')[1].strip().replace('"', '').lower()

                return distro_info['name'], distro_info['version']

        return None

    @staticmethod
    def _strip_ansi_chars(text: str) -> str:
        """
        Remove ANSI escape sequences from a string.
        Args:
            text (str): The text from which ANSI escape sequences should be removed.

        Returns:
            str: The cleaned text without ANSI codes.
        """
        # ANSI escape sequences regex pattern
        ansi_escape_pattern = re.compile(r'''
            \x1B  # ESC
            (?:   # 7-bit C1 Fe (except CSI)
                [@-Z\\-_]
            |     # or [ for CSI, followed by a control sequence
                \[
                [0-?]*  # Parameter bytes
                [ -/]*  # Intermediate bytes
                [@-~]   # Final byte
            )
        ''', re.VERBOSE)
        return ansi_escape_pattern.sub('', text)

    @staticmethod
    def _extract_decimal(input_string: str, treat_no_decimal_as_zero: bool = True) -> Union[float, int]:
        """
        Extracts the first decimal or integer number from a given string.
        Args:
            input_string (str): The input string from which to extract the number.
            treat_no_decimal_as_zero (bool): Instead of exception, assume zero when no decimal value was found

        Returns:
            Union[float, int]: Returns the number as an integer if it's whole, float if fractional.
        """
        # Regular expression to find numbers, including decimals
        match = re.search(r"([-+]?\d*\.\d+|\d+)", input_string)
        if not match:
            if not treat_no_decimal_as_zero:
                raise ValueError("no decimal value found in the string")
            else:
                number_str = "0"
        else:
            number_str = match.group(0)  # Extract the matched part of the string

        # Try to convert the string to an integer or a float
        try:
            # First, attempt to convert to float
            number = float(number_str)
            # If the number is a whole number, return it as an int
            if number.is_integer():
                return int(number)
            return number
        except ValueError:
            raise ValueError("found value is not a number")

    @staticmethod
    def _extract_filename_from_url(url: str) -> str:
        """
        Extracts the filename from a given URL.
        Args:
            url (str): The URL from which to extract the filename.

        Returns:
            str: The extracted filename.
        """
        parsed_url = urlparse(url)
        # Extract the path part of the URL
        path = parsed_url.path
        # Unquote URL-encoded characters and extract the base name of the file
        filename = os.path.basename(unquote(path))
        return filename

    @staticmethod
    def _py_extract_package_version(package_info: str) -> Optional[str]:
        """
        Extracts the package version from the given string of 'pip show' output, allowing
        for case insensitivity in the "Version" label.
        Args:
            package_info (str): The output string from 'pip show' command.

        Returns:
            str: The extracted version number.
        """
        # Use regex to find the version line and extract the version number, allowing case insensitivity
        match = re.search(r"^version:\s*(.+)$", package_info, re.MULTILINE | re.IGNORECASE)

        if match:
            return match.group(1).strip()  # Return the captured version number, stripping any extra whitespace

        # If no version is found, raise an error
        raise ValueError("version information not found in the input string")

    def _validate_sys_package(self, package_name: str):
        """
    `   Check if a package is available in the system's package manager (APT or DNF).
        Args:
            package_name (str): The name of the package to check.

        Returns:
            None, raising exception on error.
        """
        try:
            command: Optional[str] = None
            search_pattern: Optional[str] = None

            if self._package_manager is None:
                raise EnvironmentError("no supported package manager found (APT or DNF)")

            # Determine the package manager
            if self._package_manager == "apt":
                command = f"apt list --installed {package_name}"
                search_pattern = "[installed]"
            elif self._package_manager == "dnf":
                command = f"dnf list --available {package_name}"
                search_pattern = package_name

            command_response = self.shell_execute(command=command)
            if command_response is not None or search_pattern not in command_response:
                raise EnvironmentError(f"system package '{package_name}' not validated using {self._package_manager}")

        # Propagate the exception
        except Exception:
            raise

    def py_execute(self, method_name: str, arguments: Optional[Union[str, dict]] = None) -> Optional[Union[str, int]]:
        """
        Dynamically execute an arbitrary method using its name and arguments read from JSON step.
        Args:
            method_name (str): The name of the python method from this class to be invoked.
            arguments (str or dict, optional): JSON string or dictionary with arguments for the method call.
        Returns:
            Union[str, int]: The result of the method call.

        """
        # Convert JSON string to dictionary if necessary
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                raise ValueError("invalid JSON string provided for arguments.")

        # Default to empty dict if no arguments provided
        if arguments is None:
            arguments = {}

        # Retrieve the method from the class based on method_name
        method = getattr(self, method_name, None)
        if not callable(method):
            raise ValueError(f"method '{method_name}' not found in '{self.__class__.__name__}'")

        # Execute the method with the arguments
        try:
            execution_result = method(**arguments)
            return execution_result

        except Exception:
            # Propagate exception
            raise

    def env_append_sys_path(self, path: str):
        """
        Append a directory to the system's PATH environment variable.
        Args:
            path (str): The directory path to append to PATH.
        """
        path = self.env_expand_var(path)
        # Get the current PATH environment variable
        current_path = os.environ.get('PATH', '')

        # Append the directory to the PATH
        new_path = current_path + os.pathsep + path

        # Set the new PATH in the environment
        os.environ['PATH'] = new_path

    @staticmethod
    def env_expand_var(input_string: str, to_absolute: bool = False) -> str:
        """
        Expand environment variables and user shortcuts in the given path.
        This version ignores command substitution patterns like $(...).
        Args:
            input_string (str): The input string that may contain environment variables and user shortcuts.
            to_absolute (bool): If True, the input will be normalized and converted to an absolute path.

        Returns:
            str: The fully expanded path, ignoring special bash constructs.
        """
        # First expand user tilde
        path_with_user = os.path.expanduser(input_string)

        # Normalize a given path
        def _normalize_path(input_path) -> str:
            if to_absolute:
                return os.path.abspath(os.path.normpath(input_path))
            return input_path

        # Ignore $(...) patterns to avoid mistaking them for environment variables
        def _ignore_command_substitution(match):
            return match.group(0)  # Return the original match without expansion

        # Regular expression to match $(...) patterns
        command_substitution_pattern = re.compile(r'\$\([^)]*\)')

        # Temporarily replace $(...) patterns
        temp_replacement = command_substitution_pattern.sub(_ignore_command_substitution, path_with_user)

        # Now expand environment variables in the modified string
        expanded_path = os.path.expandvars(temp_replacement)

        # Restore any $(...) patterns if they were mistakenly expanded
        restored_path = command_substitution_pattern.sub(_ignore_command_substitution, expanded_path)

        # Check if there are any unexpanded variables left (that are not command substitutions)
        if '$' in re.sub(command_substitution_pattern, '', restored_path):
            # Find where the unexpanded variable starts
            start_idx = restored_path.find('$')
            # Try to extract the variable name, avoiding command substitution patterns
            end_idx = restored_path.find('/', start_idx)
            if end_idx == -1:
                end_idx = len(restored_path)
            variable_name = restored_path[start_idx:end_idx]

            raise ValueError(f"environment variable '{variable_name}' could not be expanded")

        restored_path = _normalize_path(restored_path)
        return restored_path

    def set_workspace(self, start_fresh: bool = False, start_empty: bool = False) -> Optional[str]:
        """
        Initialize the workspace path.
        Args:
            start_fresh (bool): If true, the workspace path will be erased.
            start_empty (bool): If true, the workspace path will be checked that it's empty, defaults to False.

        Returns:
            str: The workspace expanded and verified path.
        """
        try:

            if self._workspace_path is None:
                raise RuntimeError(f"workspace path cannot be None")

            # Expand environment variables and user home shortcuts in the path
            self._workspace_path = self.env_expand_var(input_string=self._workspace_path, to_absolute=True)

            # Safeguard against deleting important directories
            if start_fresh:
                self.path_erase(path=self._workspace_path, allow_non_empty=True)
                # Make sure the base path exisit
                os.makedirs(self._workspace_path, exist_ok=True)

            if start_empty:
                self._check_directory_empty(path=self._workspace_path)

            # Move to the workspace path
            os.chdir(self._workspace_path)
            return self._workspace_path

        # Propagate the exception
        except Exception:
            raise

    @staticmethod
    def env_set(name: str, value: str):
        """
        Update or set an environment variable.
        Args:
            name (str): The name of the environment variable.
            value (str): The value of the environment variable.
        """
        # Update environment
        os.environ[name] = value

    def shell_execute(self, command: str, arguments: str = "",
                      timeout: Optional[float] = None,
                      shell: bool = True,
                      sudo: bool = False,
                      expected_return_code: int = 0,
                      cwd: Optional[str] = None,
                      token: Optional[str] = None) -> Optional[str]:
        """
        Executes a shell command with specified arguments and configuration settings.
        Args:
            command (str): The base command to execute (e.g., 'ls', 'ping').
            arguments (str): Additional arguments to pass to the command.
            timeout (Optional[float]): The maximum time in seconds to allow the command to run, 0 for no timeout.
            shell (bool): If True, the command will be executed in a shell environment.
            sudo (bool): If True, the command will be executed with superuser privileges. Defaults to False.
            expected_return_code (int): The expected exit code of the command. A deviation from this
                                        result will be considered an error.
            cwd (Optional[str]): The directory from which the process should be executed.
            token (Optional[str]): A token to search for in the command output.

        Returns:
            str: The executed command output or None on error.
        """
        full_command = f"{'sudo ' if sudo else ''}{command} {arguments}".strip()  # Create a single string
        full_command = self.env_expand_var(input_string=full_command)  # Expand as needed
        base_command = os.path.basename(command)
        command_response: Optional[str] = None
        env = os.environ.copy()

        # Set default timeout when not provided
        if timeout is None:
            timeout = self._default_execution_time

        if not shell:
            # When not using shell we have to use list for the arguments rather than string
            full_command = shlex.split(full_command)

        # Expand and validate working path if specified
        if cwd is not None:
            cwd = self.env_expand_var(input_string=cwd, to_absolute=True)
            if not os.path.exists(cwd):
                raise RuntimeError(f"specified work path '{cwd}' does not exist")
            else:
                self.env_append_sys_path(path=cwd)
                env = os.environ.copy()

        # Execute the external command
        self._logger.debug(f"Executing: {full_command}")
        process = subprocess.Popen(full_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, shell=shell, cwd=cwd, env=env, bufsize=0)
        try:
            output_line = bytearray()
            command_response = ""

            start_time = time.time()  # Initialize start_time here for timeout management
            while True:
                readable, _, _ = select.select([process.stdout], [], [], 0.01)
                if readable:
                    received_byte = process.stdout.read(1)
                    if received_byte == b'':
                        break
                    else:

                        # Aggregate bytes into complete single lines for logging
                        output_line.append(received_byte[0])

                        if received_byte in (b'\n', b'\r'):
                            complete_line = output_line.decode('utf-8').strip()
                            complete_line = self._strip_ansi_chars(complete_line)
                            output_line.clear()

                            # Aggregate all lines into a complete command response string
                            command_response += complete_line + '\n'
                            # Log the command output
                            self._show_status(text_type=StatusTextType.PROGRESS, text=complete_line)

                # Handle execution timeout
                if timeout > 0 and (time.time() - start_time > timeout):
                    process.kill()
                    raise TimeoutError(f"'{command}' timed out after {timeout} seconds")

            process.wait()

            # Done executing
            command_response = self._normalize_text(text=command_response, allow_empty=True)
            self._logger.debug(f"Response: {command_response}")

            return_code = process.returncode

            if token and command_response and token not in command_response:
                raise ValueError(f"token '{token}' not found in response")

            if expected_return_code is not None and return_code != expected_return_code:
                raise ValueError(
                    f"'{base_command}' failed with return code {return_code}: {command_response}")

        except subprocess.TimeoutExpired:
            process.kill()
            raise

        finally:
            return command_response

    def validate_prerequisite(self,
                              command: str,
                              arguments: Optional[str] = None,
                              cwd: Optional[str] = None,
                              validation_method: ValidationMethod = ValidationMethod.EXECUTE_PROCESS,
                              expected_return_code: int = 0,
                              expected_response: Optional[str] = None,
                              allow_greater_decimal: bool = False) -> Optional[Any]:
        """
        Validates that a system-level prerequisite is met using a specified method.
        Args:
            command (str): For EXECUTE_PROCESS: the command to run.
                           For READ_FILE: a string in the form "<path>:<line_number>:<optional_line_count>".
                           For SYS_PACKAGE: the command variable is treated as the system package to be validated.
            arguments (Optional[str]): Arguments to pass to the command (EXECUTE_PROCESS only).
            cwd (Optional[str]): The directory from which the process should be executed.
            validation_method (ValidationMethod): The type of validation (EXECUTE_PROCESS ,READ_FILE and SYS_PACKAGE)
            expected_return_code (int): The expected exit code from the command.
            expected_response (Optional[str]): Expected content in output (for EXECUTE_PROCESS)
                or file content (for READ_FILE).
            allow_greater_decimal (bool): If True, allows the decimal response to be greater than expected repose (as decimal).

        Returns:
            Optional str, the executed command (in case of EXECUTE_PROCESS) ir None, any error will raise an exception.
        """

        command_response: Optional[Any] = None

        try:
            # Execute a process and check its response
            if validation_method == ValidationMethod.EXECUTE_PROCESS:
                command_response = self.shell_execute(
                    command=command,
                    arguments=arguments,
                    expected_return_code=expected_return_code,
                    cwd=cwd
                )

                if expected_response:
                    if command_response is None:
                        raise RuntimeError(
                            f"'{command}' returned no output while expecting '{expected_response}'")

                    if allow_greater_decimal:
                        actual_version = self._extract_decimal(input_string=command_response)
                        expected_version = self._extract_decimal(input_string=expected_response)
                        if actual_version < expected_version:
                            raise Exception(
                                f"required version is {expected_version} or higher, found {actual_version}")
                    else:
                        if expected_response.lower() not in command_response.lower():
                            raise Exception(f"expected response '{expected_response}' not found in output")

            # Read a text line from a file and compare its content
            elif validation_method == ValidationMethod.READ_FILE:
                parts = command.split(':')
                if len(parts) < 2:
                    raise ValueError(
                        "READ_FILE command must be in the form '<file_path>:<line_number>[:<line_count>]'")
                file_path = parts[0]
                line_number = int(parts[1])
                line_count = int(parts[2]) if len(parts) > 2 else 1

                if not expected_response:
                    raise ValueError("expected response must be provided for READ_FILE validation")

                with open(file_path, 'r') as f:
                    lines = f.readlines()
                    start = max(0, line_number - 1)
                    end = start + line_count
                    selected_lines = lines[start:end]
                    found = any(expected_response.lower() in line.lower() for line in selected_lines)
                    if not found:
                        raise Exception(f"expected response '{expected_response}' "
                                        f"not found in {file_path}:{line_number}")

            # Check if a system package is installed
            elif validation_method == ValidationMethod.SYS_PACKAGE:
                self._validate_sys_package(package_name=command)

            else:
                raise ValueError(f"unsupported validation method: {validation_method}")

        # Propagate the exception
        except Exception:
            raise
        finally:
            return command_response

    def path_erase(self, path: str, allow_non_empty: bool = False):
        """
        Safely delete a directory with safeguards to prevent accidental removal of critical system or user directories.
        Enforces the following safety checks before performing deletion:
          - Prevents deletion of high-level directories (e.g., "/", "/home", "/home/user") by requiring a minimum depth.
          - Refuses to delete the user's home directory or common personal folders like Desktop and Documents.
          - Only attempts deletion if the target path exists.
        Args:
            path (str): The absolute path of the directory to be deleted.
            allow_non_empty (bool): If False and the path is a non-empty directory, the operation is canceled.

        Returns:
            None, raising exception on error.
        """
        try:

            # Prevent deletion of very high-level directories, adjust the level as necessary
            if path.count(os.sep) < 2:
                raise RuntimeError(f"refusing to delete a high-level directory: '{path}'")

            # Ensure the path is not home directory or its important subdirectories
            home_path = os.path.expanduser("~")
            important_paths = [
                home_path,  # Never delete home directory
                os.path.join(home_path, "Documents"),
                os.path.join(home_path, "Desktop"),
            ]

            # Normalize the input path before comparing
            normalized_path = self.env_expand_var(input_string=path, to_absolute=True)

            if normalized_path in map(os.path.abspath, important_paths):
                raise RuntimeError(f"refusing to delete important or protected directory: '{normalized_path}'")

            if not allow_non_empty and os.listdir(path):
                raise IsADirectoryError(f"directory '{path}' is not empty, delete canceled")

            # If the directory exists, remove it
            if os.path.exists(path):
                shutil.rmtree(path)

        # Propagate the exception
        except Exception:
            raise

    def path_create(self, path: str, erase_if_exist: bool = False, project_path: bool = True) -> Optional[str]:
        """
        Create a path or folder tree. Optionally erase if it exists.
        If `project_path` is True, the path is assumed to be relative to `self.base_path`.
        Args:
            path (str): The path to create.
            erase_if_exist (bool): Whether to erase the path if it exists.
            project_path (bool): Whether the path is part of the project base path.

        Returns:
            str: The full path to the created directory.
        """
        full_path: Optional[str] = None

        try:
            # Construct the full path
            if project_path:
                path = os.path.join(self._workspace_path, path)
            full_path = os.path.expanduser(os.path.expandvars(path))

            # Delete safely
            if erase_if_exist and os.path.exists(full_path):
                self.path_erase(path=full_path, allow_non_empty=True)

            # Create the directory
            os.makedirs(full_path, exist_ok=True)
            return full_path  # Return the successfully created path

        except Exception as path_create_error:
            raise Exception(f"could not create '{full_path}': {str(path_create_error)}")

    def py_venv_create(self, venv_path: str, python_version: str = "python3",
                       python_command_path: Optional[str] = None):
        """
        Initialize a Python virtual environment using a specified Python interpreter.
        Args:
            venv_path (str): The directory path where the virtual environment will be created.
            python_version (str): The Python interpreter to use (e.g., 'python', 'python3').
            python_command_path (Optional[str]): Optional explicit path to the Python binary.

        Returns:
            None, raising exception on error.
        """
        try:
            expanded_path = self.env_expand_var(input_string=venv_path, to_absolute=True)
            full_py_venv_path = self.path_create(expanded_path, erase_if_exist=True, project_path=True)
            python_command = python_command_path or python_version
            command_arguments = f"-m venv {full_py_venv_path}"
            self.shell_execute(command=python_command, arguments=command_arguments)
            self._py_venv_path = full_py_venv_path

        except Exception as py_venv_error:
            raise Exception(f"could not create virtual environment in '{venv_path}' {str(py_venv_error)}")

    def py_venv_update_pip(self, venv_path: Optional[str] = None):
        """
        Update pip in a virtual environment using the specified Python interpreter within that environment.
        Args:
            venv_path (Optional[str]): The path to the virtual environment, optional.

        Returns:
            None, raising exception on error.
        """
        try:
            # Determine the path to use for the virtual environment
            if venv_path is None:
                if self._py_venv_path is None:
                    raise ValueError("virtual environment path not provided and default is not set")
                venv_path = self._py_venv_path

            # Determine the path to the python executable within the virtual environment
            python_executable = os.path.join(venv_path, 'bin', 'python')

            # Construct the command to update pip as a single string
            command_arguments = "-m pip install --upgrade pip"
            self.shell_execute(command=python_executable, arguments=command_arguments)

        except Exception as py_env_error:
            raise Exception(f"could not update pip {py_env_error}")

    def py_venv_package_add(self, venv_path: Optional[str] = None, package_or_requirements: str = ""):
        """
        Installs a package or a list of packages from a requirements file into a specified virtual environment using pip.
        Args:
            venv_path (Optional[str]): The path to the virtual environment. If None, uses the default virtual environment path.
            package_or_requirements (str): The package name to install or path to a requirements file.

        Returns:
            None, raising exception on error.
        """
        try:
            # Determine the path to use for the virtual environment
            if venv_path is None:
                if self._py_venv_path is None:
                    raise ValueError("virtual environment path not provided and default is not set")
                venv_path = self._py_venv_path

            # Determine the path to the python executable within the virtual environment
            python_executable = os.path.join(venv_path, 'bin', 'python')

            # Normalize inputs
            package_or_requirements = self._normalize_text(package_or_requirements)
            if len(package_or_requirements) == 0:
                raise RuntimeError(f"no package or requirements file specified for pip")

            # Determine if the input is a package name or a path to a requirements file
            if package_or_requirements.endswith('.txt'):
                command_arguments = f"-m pip install -r {package_or_requirements}"
            else:
                command_arguments = f"-m pip install {package_or_requirements}"

            # Execute the command
            self.shell_execute(command=python_executable, arguments=command_arguments, shell=False)

        except Exception as py_pip_error:
            raise Exception(f"could not install pip package(s) '{package_or_requirements}' {py_pip_error}")

    def py_venv_package_get_version(self, venv_path: Optional[str] = None, package_name: str = "") -> Optional[str]:
        """
        Retrieves the version of a specified package installed in the given virtual environment.
        Args:
            venv_path (Optional[str]): The path to the virtual environment. If None, the default virtual
                environment path is used.
            package_name (str): The name of the package to check.

        Returns:
            str: The version of the package if found, otherwise raising exception.
        """

        try:
            # Determine the path to use for the virtual environment
            if venv_path is None:
                if self._py_venv_path is None:
                    raise ValueError("virtual environment path not provided and default is not set")
                venv_path = self._py_venv_path

            # Determine the path to the python executable within the virtual environment
            python_executable = os.path.join(venv_path, 'bin', 'python')

            # Normalize inputs
            package_name = self._normalize_text(package_name)

            # Construct and execute the command
            command_arguments = f"-m pip show {package_name}"
            command_response = (
                self.shell_execute(command=python_executable, arguments=command_arguments))

            if command_response is not None:
                # Attempt to extract the version out of the text
                package_version = self._py_extract_package_version(command_response)
                return package_version

            raise Exception(f"could not read '{package_name}' version, no response from process")

        # Propagate the exception
        except Exception:
            raise

    def git_clone_repo(self, repo_url: str,
                       dest_repo_path: str,
                       timeout: float = 0,
                       clear_destination_path: bool = True):
        """
        Clones a Git repository from a specified URL into a specified destination directory.
        Args:
            repo_url (str): The URL of the Git repository to clone.
            dest_repo_path (str): The local file system path where the repository should be cloned.
            timeout (float): The maximum time in seconds to allow the git command to run.
                A timeout of 0 indicates no timeout. Default is 0.
            clear_destination_path (bool): A flag to specify whether to clear the destination directory if it
                already exists. Default is True.
.
        Returns:
             None, raising exception on error.
        """
        try:
            # Normalize inputs
            repo_url = self._normalize_text(repo_url)

            # Normalize and prepare the destination path
            dest_repo_path = self.env_expand_var(input_string=dest_repo_path, to_absolute=True)

            # Optionally clear the destination path
            self.path_erase(path=dest_repo_path, allow_non_empty=clear_destination_path)

            # Construct and execute the git clone command
            command_arguments = f"clone --progress {repo_url} {dest_repo_path}"
            self.shell_execute(command="git", arguments=command_arguments,
                               timeout=timeout)

        except Exception as py_git_error:
            raise Exception(f"git operation failure {str(py_git_error)}")

    def git_checkout_revision(self, dest_repo_path: str, revision: str,
                              timeout: float = 0,
                              pull_latest: bool = True):
        """
        Checks out a specific revision in a Git repository.
        Args:
            dest_repo_path (str): The local file system path to the Git repository.
            revision (str): The branch name, tag, or commit hash to checkout.
            timeout (float): The maximum time in seconds to allow the git command to run.
                A timeout of 0 indicates no timeout. Default is 0.
            pull_latest (bool): Whether to perform a git pull to update the repository with the latest changes from
                the remote before checking out.

        Returns:
            None, raising exception on error.
        """
        try:
            # Validate and prepare the repository path
            normalized_repo_path = self._normalize_text(dest_repo_path)
            dest_repo_path = self.env_expand_var(input_string=normalized_repo_path, to_absolute=True)

            if not os.path.exists(dest_repo_path):
                raise FileNotFoundError(f"repo path '{dest_repo_path}' does not exist")

            if pull_latest:
                # Perform a git pull to update the repository
                pull_command = "pull"
                self.shell_execute(command="git", arguments=pull_command,
                                   cwd=dest_repo_path, timeout=timeout)

            # Construct and execute the git checkout command
            command_arguments = f"checkout {revision}"
            self.shell_execute(command="git", arguments=command_arguments,
                               cwd=dest_repo_path, timeout=timeout)

        except Exception as py_git_error:
            raise Exception(f"git operation failure {str(py_git_error)}")

    def download_file(self, url: str, local_path: str,
                      delete_local: bool = False,
                      proxy: Optional[str] = None,
                      token: Optional[str] = None,
                      timeout: Optional[float] = None,
                      extra_headers: Optional[dict] = None):
        """
        Downloads a file from a specified URL to a specified local path, with optional authentication, proxy support,
        and additional HTTP headers. When verbosity is on the download progress is shown.
        Args:
            url (str): The URL from which to download the file.
            local_path (str): The local path / file where the downloaded file should be saved.
            delete_local (bool): Delete local copy of the file if exists.
            proxy (Optional[str]): The proxy server URL to use for the download.
            token (Optional[str]): An authorization token for accessing the file.
            timeout (Optional[float]): The timeout for the download operation, in seconds.
            extra_headers (Optional[dict]): Additional headers to include in the download request.

        Returns:
            None, raising exception on error.
        """
        remote_file: Optional[str] = None

        try:
            # Normalize URL and output name
            url = self._normalize_text(url)
            remote_file = self._extract_filename_from_url(url=url)
            local_path = self.env_expand_var(input_string=local_path, to_absolute=True)

            # If we got just a plain path use the remote file to create a path that point to file name
            if os.path.isdir(local_path):
                local_path = os.path.join(local_path, remote_file)

            if os.path.exists(local_path):
                if not delete_local:
                    raise FileExistsError(f"destination file '{os.path.basename(local_path)}' already exists")
                else:
                    os.remove(local_path)

            # Create the directory if it does not exist
            local_dir = os.path.dirname(local_path)
            if not os.path.exists(local_dir):
                self.path_create(path=local_dir, erase_if_exist=False)

            # Set up the HTTP request
            request = urllib.request.Request(url)

            # Add authorization token to the request headers if provided
            if token:
                request.add_header('Authorization', f'Bearer {token}')

            # Include any extra headers specified
            if extra_headers:
                for header, value in extra_headers.items():
                    request.add_header(header, value)

            # Configure proxy settings if a proxy URL is provided
            if proxy:
                proxy_handler = urllib.request.ProxyHandler({
                    'http': proxy,
                    'https': proxy
                })
                opener = urllib.request.build_opener(proxy_handler)
                urllib.request.install_opener(opener)

            # Perform the download operation
            with urllib.request.urlopen(request, timeout=timeout) as response:
                total_size = int(response.getheader('Content-Length').strip())
                downloaded_size = 0
                chunk_size = 1024 * 10  # 10KB chunk size

                with open(local_path, 'wb') as out_file:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded_size += len(chunk)

                        progress_percentage = (downloaded_size / total_size) * 100
                        self._show_status(text=f"{progress_percentage:.2f}%", text_type=StatusTextType.PROGRESS)

        except Exception as download_error:
            raise RuntimeError(f"could not download '{remote_file or url}', {download_error}")

    def run_steps(self, steps_file: str):
        """
`       Load the steps JSON file and execute them sequentially, exit loop on any error.
        Args:
            steps_file (str): Path to the steps JSON file.
        """
        step_number: int = 0

        try:

            # Expand, convert to absolute path and verify
            steps_file = SetupToolsLib.env_expand_var(input_string=steps_file, to_absolute=True)
            if not os.path.exists(steps_file):
                raise RuntimeError(f"steps file '{steps_file}' does not exist")

            # Process as JSON
            steps_schema = self._procLib.preprocess(steps_file)
            self._steps_data = steps_schema.get("steps")

            # Attempt to get status view defaults
            self._status_new_line = steps_schema.get("status_new_line", self._status_new_line)
            self._status_title_length = steps_schema.get("status_title_length", self._status_title_length)
            self._status_add_time_prefix = steps_schema.get("status_add_time_prefix", self._status_add_time_prefix)

            # Hide the  cursor
            self._ansi_term.set_cursor_visibility(False)

            for step in self._steps_data:

                # Allow a step to temporary override in place status output behaviour
                status_new_line: bool = step.get("status_new_line", self._status_new_line)

                # Allow to skip a step when 'status_step_disabled' exist and set to True
                status_step_disabled: bool = step.get("status_step_disabled", False)
                if status_step_disabled:
                    continue

                self._show_status(text_type=StatusTextType.TITLE, text=step.get('description'),
                                  new_line=status_new_line)
                response = self.py_execute(method_name=step.get("method"), arguments=step.get("arguments"))

                # Handle command output capture to a variable
                store_key = step.get('response_store_key', None)
                # Store the command response as a value If it's a string and we got the skey name from the JSON
                if isinstance(response, str) and store_key is not None:
                    self._logger.debug(f"Storing value '{response}' in '{store_key}'")
                    self._local_storage[store_key] = response

                self._show_status(text_type=StatusTextType.STATUS, text="OK", new_line=status_new_line)
                step_number = step_number + 1

            print("\n")

        except Exception as steps_error:
            ANSIGuru.write_color(text="Error\n", color_code=31)
            raise RuntimeError(f"'{os.path.basename(steps_file)}' at step {step_number} {steps_error}")
        finally:
            # Restore terminal cursor on exit
            self._ansi_term.set_cursor_visibility(True)
