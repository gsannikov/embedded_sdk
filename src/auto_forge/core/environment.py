"""
Script:         environment.py
Author:         AutoForge Team

Description:
    Core module providing a comprehensive API for simplifying various environment-related operations, including:
    - Execution of shell commands and Python methods.
    - Common Git-related operations.
    - Management of Python virtual environments and PIP packages.
    - Probing the user environment to ensure prerequisites are met.
"""
import codecs
import inspect
import json
import logging
import os
import platform
import pty
import re
import select
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from collections import deque
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Union

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (
    AddressInfoType,
    AutoForgeModuleType,
    AutoLogger,
    CoreLoader,
    CoreModuleInterface,
    CoreProcessor,
    CommandResultType,
    ExecutionModeType,
    ProgressTracker,
    Registry,
    ToolBox,
    ValidationMethodType,
)
from auto_forge.core.variables import CoreVariables  # Runtime import to prevent circular import

AUTO_FORGE_MODULE_NAME = "Environment"
AUTO_FORGE_MODULE_DESCRIPTION = "Environment operations"


class CoreEnvironment(CoreModuleInterface):
    """
    a Core class that serves as an environment related operation swissknife.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared earlier in `__init__()`
        See 'CoreModuleInterface' usage.
        """
        self._workspace_path: Optional[str] = None
        self._steps_data: Optional[list[tuple[str, Any]]] = None
        self._status_title_length: int = 80
        self._status_add_time_prefix: bool = True
        self._status_new_line: bool = False
        self._tracker: Optional[ProgressTracker] = None
        self._variables: Optional[CoreVariables] = None

        super().__init__(*args, **kwargs)

    def _initialize(self, workspace_path: str,
                    automated_mode: Optional[bool] = False,
                    configuration_data: Optional[dict[str, Any]] = None) -> None:
        """
        Initialize the 'Environment' class, collect few system properties and prepare for execution a 'steps' file.
        Args:
            workspace_path(str): The workspace path.
            automated_mode(boo, Optional): Specify if we're running in automation mode.
            configuration_data (dict, optional): Global AutoForge JSON configuration data.
        """

        # Create a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME, log_level=logging.DEBUG)
        self._package_manager: Optional[str] = None
        self._workspace_path: Optional[str] = workspace_path
        self._default_execution_time: float = 60.0  # Time allowed for executed shell command
        self._processor = CoreProcessor.get_instance()  # Instantiate JSON processing library
        self._automated_mode: bool = automated_mode  # Default execution mode
        self._toolbox: ToolBox = ToolBox.get_instance()
        self._loader: CoreLoader = CoreLoader.get_instance()

        # Use project config list for the interactive commands if available, else fallback to default list
        self._interactive_commands = configuration_data.get('interactive_commands') if configuration_data else None
        if not self._interactive_commands:
            self._interactive_commands = ["cat", "htop", "top", "vim", "less", "nano", "vi", "clear"]

        # Determine which package manager is available on the system.
        if shutil.which("apt"):
            self._package_manager = "apt"
        elif shutil.which("dnf"):
            self._package_manager = "dnf"

        # Get the system type (e.g., 'Linux', 'Windows', 'Darwin')
        self._system_type = platform.system().lower()
        self._is_wsl = "wsl" in platform.release().lower()

        # Get extended distro info when we're running under Linux
        if self._system_type == "linux":
            self._linux_distro, self._linux_version = self._get_linux_distro()

        # Normalize workspace path
        if self._workspace_path:
            self._workspace_path = self.environment_variable_expand(text=self._workspace_path,
                                                                    to_absolute_path=True)

        # Persist this module instance in the global registry for centralized access
        registry = Registry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME,
                                 description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

    def _print(self, text: str):
        """
        Print text taking into consideration automation mode.
        Args:
            text (str): The text to print.
        """
        if not self._automated_mode and isinstance(text, str):
            print(text)

    @staticmethod
    def _flatten_command(command: str, arguments: Optional[Union[str, list[Any]]] = None) -> str:
        if arguments is None:
            return command
        if isinstance(arguments, str):
            return f"{command} {arguments}"
        if isinstance(arguments, list):
            return " ".join([command] + [shlex.quote(str(arg)) for arg in arguments])
        raise TypeError("arguments must be None, a string, or a list")

    @staticmethod
    def _get_linux_distro() -> Optional[tuple[str, str]]:
        """
        Extracts the distribution name and version from /etc/os-release.
        Returns:
            tuple: The distribution name and version in lowercase (e.g., ('ubuntu', '20.04')).
                or None on any error.
        """
        distro_info = {'name': 'unknown', 'version': 'unknown'}

        # Attempt to open the file and extract distro info,
        # suppressing any exceptions (e.g., file not found, permission error)
        with suppress(Exception), open("/etc/os-release") as file:
            # Iterate through each line to find OS name and version
            for line in file:
                if line.startswith("ID="):
                    # Extract and clean the distro name
                    distro_info['name'] = (
                        line.strip().split('=')[1].strip().replace('"', '').lower()
                    )
                elif line.startswith("VERSION_ID="):
                    # Extract and clean the distro version
                    distro_info['version'] = (
                        line.strip().split('=')[1].strip().replace('"', '').lower()
                    )

            # Return tuple only if both name and version were found
            return distro_info['name'], distro_info['version']

        # If anything failed (file missing, parsing error), return None
        return None

    def _get_python_binary_path(self, venv_path: Optional[str] = None) -> Optional[str]:
        """
        Determines the path to the Python executable.
        If a virtual environment path is provided, constructs the expected Python binary path
        inside its 'bin' directory. Otherwise, falls back to the system default Python executable.

        Args:
            venv_path (Optional[str]): Path to a Python virtual environment, if applicable.

        Returns:
            Optional[str]: Full path to the resolved Python executable.
        """
        if venv_path:
            venv_path = self.environment_variable_expand(text=venv_path.strip())
            python_executable = os.path.join(venv_path, 'bin', 'python')
        else:
            python_executable = shutil.which("python")

        if not python_executable or not os.path.exists(python_executable):
            raise RuntimeError(f"Python executable not found at: '{python_executable}'")

        return python_executable

    @staticmethod
    def _parse_version(text: str, max_parts: int = 3) -> tuple[int, ...]:
        """
        Extracts up to `max_parts` numerical components of a version string.
        Non-numeric suffixes (like '1A', 'rc1') are ignored.
        """

        if not isinstance(text, str):
            raise ValueError("input is not a string")

        parts = re.findall(r"\d+", text)
        if not parts:
            raise ValueError(f"No version number found in: {text}")
        return tuple(int(p) for p in parts[:max_parts])

    @staticmethod
    def _extract_decimal(text: str, treat_no_decimal_as_zero: bool = True) -> Union[float, int]:
        """
        Extracts the first decimal or integer number from a given string.
        Args:
            text (str): The input string from which to extract the number.
            treat_no_decimal_as_zero (bool): Instead of exception, assume zero when no decimal value was found

        Returns:
            Union[float, int]: Returns the number as an integer if it's whole, float if fractional.
        """

        if not isinstance(text, str):
            raise ValueError("input is not a string")

        # Regular expression to find numbers, including decimals
        match = re.search(r"([-+]?\d*\.\d+|\d+)", text)
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
        except ValueError as value_error:
            raise ValueError("found value is not a number") from value_error

    @staticmethod
    def _extract_python_package_version(package_info: str) -> Optional[str]:
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

    def _validate_sys_package(self, package_name: str) -> Optional[CommandResultType]:
        """
    `   Check if a package is available in the system's package manager (APT or DNF).
        Args:
            package_name (str): The name of the package to check.

        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        try:
            command: Optional[str] = None
            search_pattern: Optional[str] = None

            if self._package_manager is None:
                raise OSError("no supported package manager found (APT or DNF)")

            # Determine the package manager
            if self._package_manager == "apt":
                command = f"apt list --installed {package_name}"
                search_pattern = "[installed]"
            elif self._package_manager == "dnf":
                command = f"dnf list --available {package_name}"
                search_pattern = package_name

            results = self.execute_shell_command(command_and_args=command)
            if not results.response or search_pattern not in results.response:
                raise OSError(f"system package '{package_name}' not validated using {self._package_manager}")

            return results

        except Exception:  # Propagate the exception
            raise

    def initialize_workspace(self, delete_existing: bool = False, must_be_empty: bool = False,
                             create_as_needed: bool = False,
                             change_dir: bool = False) -> Optional[str]:
        """
        Initializes the workspace path.
        Args:
            delete_existing (bool): If true, the workspace path will be erased.
            must_be_empty (bool): If true, the workspace path will be checked that it's empty, defaults to False.
            create_as_needed (bool): If true, the workspace path will be created, defaults to False.
            change_dir (bool): If true, switch to the workspace directory, defaults to False.

        Returns:
            str: The workspace expanded and verified path.
        """
        try:

            if self._workspace_path is None:
                raise RuntimeError("stored 'workspace path' cannot be None")

            # Expand environment variables and user home shortcuts in the path
            self._workspace_path = self.environment_variable_expand(text=self._workspace_path, to_absolute_path=True)

            # Safeguard against deleting important directories
            if delete_existing:
                self.path_erase(path=self._workspace_path, allow_non_empty=True)
                # Make sure the base path exist
                os.makedirs(self._workspace_path, exist_ok=True)
            if create_as_needed:
                os.makedirs(self._workspace_path, exist_ok=True)

            # Enforce empty path
            if must_be_empty:
                self._toolbox.is_directory_empty(path=self._workspace_path, raise_exception=True)

            # Set the workspace as a working directory, may raise an exception if it does not exist
            if change_dir:
                os.chdir(self._workspace_path)

            return self._workspace_path

        # Propagate the exception
        except Exception as exception:
            raise exception

    def environment_append_to_path(self, path: str):
        """
        Append a directory to the system's PATH environment variable.
        Args:
            path (str): The directory path to append to PATH.
        """
        path = self.environment_variable_expand(path)
        # Get the current PATH environment variable
        current_path = os.environ.get('PATH', '')

        # Append the directory to the PATH
        new_path = current_path + os.pathsep + path

        # Set the new PATH in the environment
        os.environ['PATH'] = new_path

    @staticmethod
    def environment_variable_expand(text: str, to_absolute_path: bool = False) -> str:
        """
        Expand environment variables and user shortcuts in the given path.
        This version ignores command substitution patterns like $(...).
        Args:
            text (str): The input string that may contain environment variables and user shortcuts.
            to_absolute_path (bool): If True, the input will be normalized and converted to an absolute path.

        Returns:
            str: The fully expanded variable, ignoring special bash constructs.
        """

        if not text.strip():
            return ""  # do NOT expand empty string

        # First expand user tilde
        path_with_user = os.path.expanduser(text)

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

        # Convert to absolute path if specified
        if to_absolute_path:
            restored_path = os.path.abspath(os.path.normpath(restored_path))

        return restored_path

    @staticmethod
    def environment_variable_set(name: str, value: str, allow_overwrite: bool = True):
        """
        Set an environment variable, optionally preventing overwrite.

        Args:
            name (str): The name of the environment variable.
            value (str): The value to assign to the environment variable.
            allow_overwrite (bool): If False and the variable already exists, raises an exception.
                                    Defaults to False.
        """
        if not allow_overwrite and name in os.environ:
            raise ValueError(f"variable '{name}' already exists and overwriting is not allowed")
        os.environ[name] = value

    @staticmethod
    def environment_variable_expect(name: str, searched_token: str, case_sensitive: bool = False):
        """
        Ensure a specific token is present in the value of an environment variable.

        Args:
            name (str): The name of the environment variable to check.
            searched_token (str): The token to search for in the variable's value.
            case_sensitive (bool): If True, the search is case-sensitive. Defaults to False.
        """
        if name not in os.environ:
            raise KeyError(f"environment variable '{name}' does not exist.")

        env_value = os.environ[name]

        if not case_sensitive:
            env_value = env_value.lower()
            searched_token = searched_token.lower()

        if searched_token not in env_value:
            raise ValueError(
                f"token '{searched_token}' not found in environment variable '{name}'."
            )

    def execute_python_method(self, method_name: str,
                              arguments: Optional[Union[str, dict]] = None) -> Optional[CommandResultType]:
        """
        Dynamically execute an arbitrary method using its name and arguments read from JSON step.
        Args:
            method_name (str): The name of the python method from this class to be invoked.
            arguments (str or dict, optional): JSON string or dictionary with arguments for the method call.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        # Convert JSON string to dictionary if necessary
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as json_error:
                raise ValueError("invalid JSON string provided for arguments") from json_error

        # Default to empty dict if no arguments provided
        if arguments is None:
            arguments = {}

        # Retrieve the method from the class based on method_name
        method = getattr(self, method_name, None)
        if not callable(method):
            raise ValueError(f"method '{method_name}' not found in '{self.__class__.__name__}'")

        # Finetune the arguments to the executed method based on its signature
        method_signature = inspect.signature(method)
        method_kwargs, extra_kwargs = self._toolbox.filter_kwargs_for_method(kwargs=arguments, sig=method_signature)

        self._logger.debug(f"Executing Python method: '{method.__name__}'")

        # Execute the method with the arguments
        try:
            results = method(**method_kwargs)
            # Type check or conversion if needed
            if isinstance(results, CommandResultType):
                return results
            elif isinstance(results, str):
                return CommandResultType(response=results, return_code=0)
            else:
                return None  # Method did not return an expected return value

        except Exception as exception:
            raise exception

    def execute_cli_command(self, command: str, arguments: str, expected_return_code: int = 0,
                            suppress_output: bool = False) -> Optional[CommandResultType]:
        """
        Executes a registered CLI command by name with shell-style arguments.=
        Args:
            command (str): The name of the CLI command to execute.
            arguments (str): A shell-style argument string to pass to the command.
            expected_return_code (int): The return code expected from the command. Defaults to 0.
            suppress_output (bool): If True, suppresses terminal output while still capturing it.

        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """

        self._logger.debug(f"Executing registered command: '{command}'")
        return_code = self._loader.execute_command(name=command, arguments=arguments,
                                                   suppress_output=suppress_output)
        # Get the command output
        command_response = self._loader.get_last_output().strip()

        if return_code != expected_return_code:
            raise RuntimeError(
                f"'{command}' failed with return code {return_code}, expected {expected_return_code}")

        return CommandResultType(response=command_response, return_code=return_code)

    def execute_with_spinner(self,
                             message: str,
                             command: Union[str, Callable],
                             arguments: Optional[Any] = None,
                             command_type: ExecutionModeType = ExecutionModeType.SHELL,
                             timeout: Optional[float] = None,
                             color: Optional[str] = Fore.CYAN,
                             new_lines: int = 0) -> Optional[int]:
        """
        Run a command with a spinning indicator and optional timeout.

        Args:
            message (str): Message to show before the spinner.
            command (str | Callable): Command to execute (shell or Python method).
            arguments (Optional[Any]): Command-line-style arguments or dict for Python calls.
            command_type (ExecutionModeType): Execution mode type.
            timeout (Optional[float]): Timeout in seconds (for shell commands only).
            color (Optional[str]): Colorama color for the spinner text.
            new_lines (int): The number of new lines print before the spinner text.

        Returns:
            Optional[int]: Command result code (0 = success, None = failed).
        """
        spinner_running = True
        result_container = {}
        message = self._toolbox.normalize_text(text=message)

        if new_lines:
            print('\n' * new_lines, end='')

        def _show_spinner():
            symbols = ['|', '/', '-', '\\']
            idx = 0
            while spinner_running:
                spinner = f"{color}{symbols[idx % len(symbols)]}{Style.RESET_ALL}"
                print(f"\r{message} {spinner}", end='', flush=True)
                time.sleep(0.1)
                idx += 1
            # Clean up line when done
            print('\r' + ' ' * (len(message) + 4), end='\r', flush=True)

        def _execute_foreign_code():
            try:
                if command_type == ExecutionModeType.SHELL:
                    self.execute_shell_command(
                        command_and_args=self._flatten_command(command=command, arguments=arguments),
                        shell=True,
                        terminal_echo=True,
                        expand_command=False,
                        timeout=timeout
                    )
                    result_container['code'] = 0

                elif command_type == ExecutionModeType.PYTHON:
                    if not callable(command):
                        raise RuntimeError("Cannot execute non-callable command")

                    if isinstance(arguments, str):
                        kwargs = json.loads(arguments)
                        command(**kwargs)
                    elif isinstance(arguments, dict):
                        command(**arguments)
                    elif arguments is not None:
                        command(arguments)
                    else:
                        command()
                    result_container['code'] = 0

                else:
                    raise RuntimeError("Unrecognized command type")

            except Exception as exception:
                result_container['code'] = -1
                raise exception

        self._toolbox.set_cursor(visible=False)
        spin_thread = threading.Thread(target=_show_spinner, name="Spinner")
        exec_thread = threading.Thread(target=_execute_foreign_code, name="ForeignCodeExecutor")

        spin_thread.start()
        exec_thread.start()

        exec_thread.join()
        spinner_running = False
        self._toolbox.set_cursor(visible=True)
        spin_thread.join()

        print(Style.RESET_ALL, end='')  # Ensure styling is reset
        return result_container.get('code', 1)

    def execute_shell_command(  # noqa: C901
            self,
            command_and_args: Union[str, list[str]],
            timeout: Optional[float] = None,
            terminal_echo: bool = False,
            expand_command: bool = True,
            use_pty: bool = True,
            searched_token: Optional[str] = None,
            check: bool = True,
            shell: bool = True,
            cwd: Optional[str] = None,
            env: Optional[Mapping[str, str]] = None) -> Optional[CommandResultType]:
        """
        Executes a shell command with specified arguments and configuration settings.
        Args:
            command_and_args (Union[str, list): a single string for the command along its arguments or a list.
            timeout (Optional[float]): The maximum time in seconds to allow the command to run, 0 for no timeout.
            terminal_echo (bool): If True, the command response will be immediately echoed to the terminal. Defaults to False.
            expand_command (bool): If True, the command will be expanded to resolve any input similar to '$EXAMPLE'.
            use_pty (bool): If True, the command will be executed in a PTY environment. Defaults to False.
            searched_token (Optional[str]): A token to search for in the command output.
            check (bool): If True, the command will raise CalledProcessError if the return code is non-zero
            shell (bool): If True, the command will be executed in a shell environment.
            cwd (Optional[str]): The directory from which the process should be executed.
            env (Optional[Mapping[str, str]]): Environment variables.

        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """

        polling_interval: float = 0.0001
        kwargs: Optional[dict[str, Any]] = {}
        line_buffer = bytearray()
        lines_queue = deque(maxlen=100)  # Storing upto the last 100 output lines
        master_fd: Optional[int] = None  # PTY master descriptor
        timeout = self._default_execution_time is timeout is None  # Set default timeout when not provided
        env = os.environ if env is None else env
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

        # Cleanup and expansion
        if isinstance(command_and_args, str):
            command_and_args = ToolBox.normalize_text(text=command_and_args)
            if expand_command:
                command_and_args = self.environment_variable_expand(text=command_and_args)
            command_list = command_and_args.strip().split()
        elif isinstance(command_and_args, list):
            command_list = []
            for item in command_and_args:
                cleaned = ToolBox.normalize_text(text=item)
                if expand_command:
                    cleaned = self.environment_variable_expand(text=cleaned)
                command_list.append(cleaned)
        else:
            raise TypeError("command_and_args must be a string or a list of strings")

        full_command = command_list[0]  # The command
        command = os.path.basename(full_command)

        # Full TTY handoff for interactive apps
        if command in self._interactive_commands:
            self._logger.debug(f"Executing: {command_and_args} (Full TTY)")
            results = self.execute_fullscreen_shell_command(command_and_args=command_and_args)
            if check and results.return_code != 0:
                raise subprocess.CalledProcessError(returncode=results.return_code, cmd=command)
            return results

        # Expand current work directory if specified
        if cwd:
            cwd = self.environment_variable_expand(text=cwd)

        # When not using shell we have to use list for the arguments rather than string
        if not shell:
            _command = command_list
        else:
            _command = " ".join(shlex.quote(arg) for arg in command_list)  # Flatten to string
            env_shell = os.environ.get("SHELL")
            if env_shell:
                kwargs = dict()
                kwargs['executable'] = env_shell

        if shutil.which(command) is None:
            raise RuntimeError(f"command not found: {command}")

        def _print_bytes_safely(byte_data: bytes, suppress_errors: bool = True):
            """
            Incrementally decodes a single byte of UTF-8 data and writes the result to stdout.

            This function uses a persistent UTF-8 decoder to correctly handle multibyte characters
            (e.g., box-drawing or Unicode symbols) that may span multiple byte reads. Output is
            flushed immediately to ensure real-time terminal updates.

            Args:
                byte_data (bytes): A single byte read from a terminal or subprocess stream.
                suppress_errors (bool): If True, suppresses decoding or write errors silently.
                                        If False, exceptions will propagate.
            """
            try:
                if not isinstance(byte_data, bytes):
                    raise TypeError("byte_data must be of type 'bytes'")
                decoded = decoder.decode(byte_data)
                if terminal_echo:
                    sys.stdout.write(decoded)
                    sys.stdout.flush()
            except (UnicodeDecodeError, OSError, TypeError) as decode_exception:
                if not suppress_errors:
                    raise decode_exception

        def _bytes_to_message_queue(input_buffer: bytearray, message_queue: deque) -> str:
            """
            Decode a UTF-8 byte buffer, remove ANSI escape codes, and append the result
            to a message queue. Clears the input buffer after processing.
            Args:
                input_buffer (bytearray): Incoming buffer of raw bytes.
                message_queue (deque): Target queue to store cleaned lines.
            Returns:
                str: The cleaned string (or empty string if nothing was added).
            """
            try:
                text = input_buffer.decode('utf-8', errors='replace')
            except Exception as decode_error:
                raise RuntimeError(f"Decode error: {decode_error}") from decode_error

            clear_text = self._toolbox.strip_ansi(text=text, bare_text=True)
            if clear_text:
                message_queue.append(clear_text)
                self._logger.debug(f"> {clear_text}")

            input_buffer.clear()
            return clear_text

        # Execute the external command
        if use_pty:
            self._logger.debug(f"Executing: {command_and_args} (PTY)")
            master_fd, slave_fd = pty.openpty()
            process = subprocess.Popen(_command, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                                       bufsize=0, shell=shell, cwd=cwd, env=env, **kwargs)

        else:  # Normal flow
            self._logger.debug(f"Executing: {command_and_args}")
            process = subprocess.Popen(_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT,
                                       bufsize=0, shell=shell, cwd=cwd, env=env, **kwargs)

        # Loop and read the spawned process output upto timeout or normal termination
        try:
            start_time = time.time()  # Initialize start_time here for timeout management
            while process.poll() is not None:
                time.sleep(polling_interval)
                if timeout > 0 and (time.time() - start_time > timeout):
                    process.kill()
                    raise TimeoutError(f"'{command}' process didn't start after {timeout} seconds")

            start_time = time.time()  # Initialize start_time here for timeout management
            while True:
                if use_pty:
                    readable, _, _ = select.select([master_fd], [], [], polling_interval)
                else:
                    readable, _, _ = select.select([process.stdout], [], [], polling_interval)

                if readable:
                    received_byte = os.read(master_fd, 1) if use_pty else process.stdout.read(1)

                    if not received_byte:
                        break  # EOF: nothing more to read

                    if received_byte == b'':
                        break
                    else:
                        # Immediately echo to the byte to the terminal if set
                        if terminal_echo:
                            _print_bytes_safely(received_byte)

                        # Aggregate bytes into complete single lines for logging
                        line_buffer.append(received_byte[0])

                        if received_byte in (b'\n', b'\r'):
                            # Clear the line and aggravate into a queue
                            text_line = _bytes_to_message_queue(line_buffer, lines_queue)

                            # Track it if we have a tracker instate
                            if text_line and self._tracker is not None:
                                self._tracker.set_body_in_place(text=text_line)
                else:
                    # No data ready to read — check if process exited
                    if process.poll() is not None:
                        break

                # Handle execution timeout
                if timeout > 0 and (time.time() - start_time > timeout):
                    process.kill()
                    raise TimeoutError(f"'{command}' timed out after {timeout} seconds")

            process.wait()

            # Add any remaining bytes
            if line_buffer:
                _bytes_to_message_queue(line_buffer, lines_queue)

            # Done executing
            return_code = process.returncode

            # Add additional clear line at the end
            if terminal_echo:
                print()

            # Optionally raise exception non-zero return code
            if check and return_code != 0:
                raise subprocess.CalledProcessError(
                    returncode=process.returncode,
                    cmd=command, output=process.stdout, stderr=process.stderr)

            command_response: str = "\n".join(lines_queue)  # Convert to a full string with newlines
            if searched_token and command_response and searched_token not in command_response:
                raise ValueError(f"token '{searched_token}' not found in response")

            return CommandResultType(response=command_response, return_code=return_code)

        except subprocess.TimeoutExpired:
            process.kill()
            raise
        except Exception:
            raise
        finally:
            if master_fd is not None:  # Close PTY descriptor
                os.close(master_fd)

    @staticmethod
    def execute_fullscreen_shell_command(command_and_args: str) -> Optional[CommandResultType]:
        """
        Runs a full-screen TUI command like 'htop' or 'vim' by fully attaching to the terminal.
        Args:
            command_and_args (str): a string containing the full command and arguments to execute.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        return_code: int = 0  # Initialize to error code

        with suppress(KeyboardInterrupt):
            result = subprocess.run(
                command_and_args,
                shell=True, check=False, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=os.environ,
            )
            return_code = result.returncode

        return CommandResultType(response='', return_code=return_code)

    def validate_prerequisite(self,
                              command: str,
                              arguments: Optional[str] = None,
                              cwd: Optional[str] = None,
                              validation_method: ValidationMethodType = ValidationMethodType.EXECUTE_PROCESS,
                              expected_response: Optional[str] = None,
                              allow_greater_decimal: bool = False) -> Optional[CommandResultType]:
        """
        Validates that a system-level prerequisite is met using a specified method.
        Args:
            command (str): For EXECUTE_PROCESS: the command to run.
                           For READ_FILE: a string in the form "<path>:<line_number>:<optional_line_count>".
                           For SYS_PACKAGE: the command variable is treated as the system package to be validated.
            arguments (Optional[str]): Arguments to pass to the command (EXECUTE_PROCESS only).
            cwd (Optional[str]): The directory from which the process should be executed.
            validation_method (ValidationMethodType): The type of validation (EXECUTE_PROCESS ,READ_FILE and SYS_PACKAGE)
            expected_response (Optional[str]): Expected content in output (for EXECUTE_PROCESS)
                or file content (for READ_FILE).
            allow_greater_decimal (bool): If True, allows the decimal response to be greater than expected repose (as decimal).

        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """

        try:
            # Execute a process and check its response
            if validation_method == ValidationMethodType.EXECUTE_PROCESS:
                results = self.execute_shell_command(
                    command_and_args=self._flatten_command(command=command, arguments=arguments),
                    cwd=cwd
                )

                if expected_response:
                    if results.response is None:
                        raise RuntimeError(
                            f"'{command}' returned no output while expecting '{expected_response}'")

                    if allow_greater_decimal:
                        actual_version = self._parse_version(text=results.response)
                        expected_version = self._parse_version(text=expected_response)
                        if actual_version < expected_version:
                            raise Exception(
                                f"required {command} version is {expected_version} or higher, found {actual_version}")
                    else:
                        if expected_response.lower() not in results.response.lower():
                            raise Exception(f"expected response '{expected_response}' not found in output")

            # Read a text line from a file and compare its content
            elif validation_method == ValidationMethodType.READ_FILE:
                parts = command.split(':')
                if len(parts) < 2:
                    raise ValueError(
                        "READ_FILE command must be in the form '<file_path>:<line_number>[:<line_count>]'")
                file_path = parts[0]
                line_number = int(parts[1])
                line_count = int(parts[2]) if len(parts) > 2 else 1

                if not expected_response:
                    raise ValueError("expected response must be provided for READ_FILE validation")

                with open(file_path) as f:
                    lines = f.readlines()
                    start = max(0, line_number - 1)
                    end = start + line_count
                    selected_lines = lines[start:end]
                    found = any(expected_response.lower() in line.lower() for line in selected_lines)
                    if not found:
                        raise Exception(f"expected response '{expected_response}' "
                                        f"not found in {file_path}:{line_number}")

                    results = CommandResultType(response=None, return_code=0)

            # Check if a system package is installed
            elif validation_method == ValidationMethodType.SYS_PACKAGE:
                results = self._validate_sys_package(package_name=command)

            else:
                raise ValueError(f"unsupported validation method: {validation_method}")

            return results

        except Exception:  # Propagate the exception
            raise

    def path_erase(self, path: str, allow_non_empty: bool = False,
                   raise_exception_if_not_exist: bool = False):
        """
        Safely delete a directory with safeguards to prevent accidental removal of critical system or user directories.
        Enforces the following safety checks before performing deletion:
          - Prevents deletion of high-level directories (e.g., "/", "/home", "/home/user") by requiring a minimum depth.
          - Refuses to delete the user's home directory or common personal folders like Desktop and Documents.
          - Only attempts deletion if the target path exists.
        Args:
            path (str): The absolute path of the directory to be deleted.
            allow_non_empty (bool): If False and the path is a non-empty directory, the operation is canceled.
            raise_exception_if_not_exist(bool): If True, raises an exception if the path does not exist.

        Returns:
            None, raising exception on error.
        """
        try:

            # Normalize the input path before comparing
            normalized_path = self.environment_variable_expand(text=path, to_absolute_path=True)

            if not os.path.exists(normalized_path):
                if raise_exception_if_not_exist:
                    raise FileNotFoundError(f"'{normalized_path}' does not exist")
                return  # Exit without raising exception

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

            if normalized_path in map(os.path.abspath, important_paths):
                raise RuntimeError(f"refusing to delete important or protected directory: '{normalized_path}'")

            if not allow_non_empty and os.listdir(path):
                raise IsADirectoryError(f"directory '{path}' is not empty, delete canceled")

            # If the directory exists, remove it
            if os.path.exists(path):
                shutil.rmtree(path)

        # Propagate the exception
        except Exception as erase_exception:
            raise erase_exception

    def path_create(self, path: Optional[str] = None, paths: Optional[list[str]] = None,
                    erase_if_exist: bool = False, project_path: bool = True) -> Optional[str]:
        """
        Create a path or folder tree. Optionally erase if it exists.
        If `project_path` is True, the path is assumed to be relative to `self._workspace_path`.
        Args:
            path: (str, optional): A single path to create.
            paths: (List[str], optional): A list of paths to create.
            erase_if_exist (bool): Whether to erase the path if it exists.
            project_path (bool): Whether the path is part of the project base path.

        Returns:
            Optional[str]: The full path to the last created directory, or None if no path was created.
        """
        if path is None and paths is None:
            raise ValueError("must specify either 'path' or 'paths'")

        if path:
            paths = [path]  # If only a single path is given, make it a list

        last_full_path = None
        full_path = None

        for path in paths:
            try:
                if self._variables and path:
                    path = self._variables.expand(text=path, expand_path=False)

                full_path = os.path.join(self._workspace_path, path) if project_path else path
                full_path = os.path.expanduser(os.path.expandvars(full_path))

                if erase_if_exist and os.path.exists(full_path):
                    # Assuming self.path_erase() is correctly implemented to safely delete paths
                    self.path_erase(path=full_path, allow_non_empty=True)

                os.makedirs(full_path, exist_ok=not erase_if_exist)
                last_full_path = full_path  # Update the last path created

            except Exception as path_create_error:
                raise Exception(f"could not create '{full_path}': {path_create_error!s}") from path_create_error

        return last_full_path  # Return the path of the last directory successfully created

    def python_virtualenv_create(self, venv_path: str, python_version: Optional[str],
                                 python_binary_path: Optional[str] = None) -> Optional[CommandResultType]:
        """
        Initialize a Python virtual environment using a specified Python interpreter.
        Args:
            venv_path (str): The directory path where the virtual environment will be created.
            python_version (str): The Python interpreter to use (e.g., '3', '3.9').
            python_binary_path (Optional[str]): Optional explicit path to the Python binary.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        try:
            expanded_path = self.environment_variable_expand(text=venv_path, to_absolute_path=True)

            # Verify inputs
            if python_binary_path is not None:
                expanded_python_binary_path = self.environment_variable_expand(text=python_binary_path,
                                                                               to_absolute_path=True)
                self._toolbox.validate_path(expanded_python_binary_path)
                python_binary = os.path.join(expanded_python_binary_path, f"python{python_version}")
            else:
                expanded_python_binary_path = None
                python_binary = f"python{python_version}"

            if not os.path.exists(python_binary):
                python_binary = shutil.which(python_binary)
                if not python_binary:
                    raise RuntimeError(f"Python binary '{python_binary}' could not be found")

            full_py_venv_path = self.path_create(expanded_path, erase_if_exist=True, project_path=True)

            command = python_binary
            arguments = f"-m venv {full_py_venv_path}"
            results = self.execute_shell_command(
                command_and_args=self._flatten_command(command=command, arguments=arguments),
                cwd=expanded_python_binary_path)

            return results

        except Exception as py_venv_error:
            raise Exception(
                f"could not create virtual environment in '{venv_path}' {py_venv_error!s}") from py_venv_error

    def python_update_pip(self, venv_path: Optional[str] = None) -> Optional[CommandResultType]:
        """
        Update pip in a virtual environment using the specified Python interpreter within that environment.
        Args:
            venv_path (Optional[str]): The path to the virtual environment. If None use system default.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        try:
            # Determines the path to the Python executable.
            command = self._get_python_binary_path(venv_path=venv_path)

            # Construct the command to update pip
            arguments = "-m pip install --upgrade pip"
            results = self.execute_shell_command(
                command_and_args=self._flatten_command(command=command, arguments=arguments))

            return results

        except Exception as py_env_error:
            raise Exception(f"could not update pip {py_env_error}") from py_env_error

    def python_package_add(self, package_or_requirements: str,
                           venv_path: Optional[str] = None) -> Optional[CommandResultType]:
        """
        Installs a package or a list of packages from a requirements file into a specified virtual environment using pip.
        Args:
            package_or_requirements (str): The package name to install or path to a requirements file.
            venv_path (Optional[str]): The path to the virtual environment. If None use system default.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        try:
            # Determines the path to the Python executable.
            command = self._get_python_binary_path(venv_path=venv_path)

            # Normalize inputs
            package_or_requirements = self._toolbox.normalize_text(package_or_requirements)
            if len(package_or_requirements) == 0:
                raise RuntimeError("no package or requirements file specified for pip")

            # Determine if the input is a package name or a path to a requirements file
            if package_or_requirements.endswith('.txt'):
                arguments = f"-m pip install -r {package_or_requirements}"
            else:
                arguments = f"-m pip install {package_or_requirements}"

            # Execute the command
            results = (self.execute_shell_command(command_and_args=
                                                  self._flatten_command(command=command, arguments=arguments),
                                                  shell=False))
            return results

        except Exception as python_pip_error:
            raise Exception(
                f"could not install pip package(s) '{package_or_requirements}' {python_pip_error}") \
                from python_pip_error

    def python_package_uninstall(self, package: str, venv_path: Optional[str] = None) -> Optional[CommandResultType]:
        """
        Uninstall a package using pip.
        Args:
            package (str): The package name to uninstall.
            venv_path (Optional[str]): The path to the virtual environment. If None use system default.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        try:
            # Determines the path to the Python executable.
            command = self._get_python_binary_path(venv_path=venv_path)

            # Normalize inputs
            package = self._toolbox.normalize_text(package)
            if not package:
                raise RuntimeError("no package specified for pip")

            arguments = f"-m pip uninstall -y {package}"

            # Execute the command
            results = self.execute_shell_command(
                command_and_args=self._flatten_command(command=command, arguments=arguments), shell=False)

            return results

        except Exception as python_pip_error:
            raise Exception(f"could not uninstall pip package(s) '{package}' {python_pip_error}") from python_pip_error

    def python_package_get_version(self, package: str,
                                   venv_path: Optional[str] = None) -> Optional[str]:
        """
        Retrieves the version of a specified package installed in the given virtual environment.
        Args:
            package (str): The package name to uninstall.
            venv_path (Optional[str]): The path to the virtual environment. If None use system default.

        Returns:
            str: The version of the package if found, otherwise raising exception.
        """
        try:
            # Determines the path to the Python executable.
            command = self._get_python_binary_path(venv_path=venv_path)

            # Normalize inputs
            package = self._toolbox.normalize_text(package)
            if not package:
                raise RuntimeError("no package specified for pip")

            # Construct and execute the command
            arguments = f"-m pip show {package}"
            results = (self.execute_shell_command(
                command_and_args=self._flatten_command(command=command, arguments=arguments)))

            if results.response is not None:
                # Attempt to extract the version out of the text
                package_version = self._extract_python_package_version(results.response)
                return package_version

            raise Exception(f"could not read '{package}' version, no response from process")

        except Exception:  # Propagate the exception
            raise

    def git_clone_repo(self, repo_url: str,
                       dest_repo_path: str,
                       timeout: float = 0,
                       clear_destination_path: bool = True) -> Optional[CommandResultType]:
        """
        Clones a Git repository from a specified URL into a specified destination directory.
        Args:
            repo_url (str): The URL of the Git repository to clone.
            dest_repo_path (str): The local file system path where the repository should be cloned.
            timeout (float): The maximum time in seconds to allow the git command to run.
                A timeout of 0 indicates no timeout. Default is 0.
            clear_destination_path (bool): A flag to specify whether to clear the destination directory if it
                already exists. Default is True.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        try:
            # Normalize inputs
            repo_url = self._toolbox.normalize_text(repo_url)

            # Normalize and prepare the destination path
            if self._variables is not None:
                dest_repo_path = self._variables.expand(text=dest_repo_path, expand_path=False)

            dest_repo_path = self.environment_variable_expand(text=dest_repo_path, to_absolute_path=True)

            # Optionally clear the destination path
            self.path_erase(path=dest_repo_path, allow_non_empty=clear_destination_path)

            # Construct and execute the git clone command
            command = "git"
            arguments = f"clone --progress {repo_url} {dest_repo_path}"
            command_result = self.execute_shell_command(
                command_and_args=self._flatten_command(command=command, arguments=arguments), timeout=timeout)

            return command_result

        except Exception as py_git_error:
            raise Exception(f"git operation failure {py_git_error!s}") from py_git_error

    def git_checkout_revision(self, dest_repo_path: str, revision: str,
                              timeout: float = 0,
                              pull_latest: bool = True) -> Optional[CommandResultType]:
        """
        Checks out a specific revision in a Git repository.
        Args:
            dest_repo_path (str): The local file system path to the Git repository.
            revision (str): The branch name, tag, or commit hash to check out.
            timeout (float): The maximum time in seconds to allow the git command to run.
                A timeout of 0 indicates no timeout. Default is 0.
            pull_latest (bool): Whether to perform a git pull to update the repository with the latest changes from
                the remote before checking out.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        try:
            # Validate and prepare the repository path
            normalized_repo_path = self._toolbox.normalize_text(dest_repo_path)
            dest_repo_path = self.environment_variable_expand(text=normalized_repo_path, to_absolute_path=True)
            command = "git"

            if not os.path.exists(dest_repo_path):
                raise FileNotFoundError(f"repo path '{dest_repo_path}' does not exist")

            if pull_latest:
                # Perform a git pull to update the repository
                arguments = "pull"
                results = self.execute_shell_command(command_and_args=
                                                     self._flatten_command(command=command, arguments=arguments),
                                                     cwd=dest_repo_path, timeout=timeout)
                if results.return_code != 0:
                    raise RuntimeError(f"git 'pull'' failed with exit code {results.return_code}")

            # Construct and execute the git checkout command
            arguments = f"checkout {revision}"
            results = self.execute_shell_command(command_and_args=
                                                 self._flatten_command(command=command, arguments=arguments),
                                                 cwd=dest_repo_path, timeout=timeout)
            return results

        except Exception as py_git_error:
            raise Exception(f"git operation failure {py_git_error!s}") from py_git_error

    def git_get_path_from_url(self, url: str,
                              destination_file_name: Optional[str] = None,
                              allowed_extensions: Optional[list[str]] = None,
                              delete_if_exist: bool = False,
                              proxy_host: Optional[AddressInfoType] = None,
                              token: Optional[str] = None) -> Optional[str]:
        """
        Downloads a GitHub folder (tree URL or API URL) as a .zip archive.
        Args:
            url (str): The URL from which to download the file:
                This is expected to be a GitHub repository URL which points to path rather than a file.
            destination_file_name (str): The local path/file where the downloaded file should be saved.
            allowed_extensions (Optional[List[str]]): Allowed file extensions. If None, all files are downloaded.
            delete_if_exist (bool): Delete local copy of the file if exists.
            proxy_host (Optional[str]): The proxy server URL to use for the download.
            token (Optional[str]): An authorization token for accessing the file.

        Returns:
            str: Full path to the created .zip archive.
        """
        url = self._toolbox.normalize_text(text=url)
        url = self._toolbox.normalize_to_github_api_url(url=url)
        proxy: Optional[str] = proxy_host.endpoint if proxy_host else None

        if url is None:
            raise RuntimeError(f"URL '{url}' is not a valid URL")

        # We're getting a pth so the URL is expected to point to git path
        is_url_path = self._toolbox.is_url_path(url)
        if is_url_path is None or not is_url_path:
            raise RuntimeError(f"URL '{url}' is not a valid URL or not pointing to a path")

        # Use temporary destination file if not specified
        if destination_file_name is None:
            destination_file_name = self._toolbox.get_temp_filename()

        destination_file_name = CoreEnvironment.environment_variable_expand(text=destination_file_name,
                                                                            to_absolute_path=True)

        # Make sure we got something that look like path that points to a filed name
        is_destination_path = self._toolbox.looks_like_unix_path(destination_file_name)
        if is_destination_path:
            raise RuntimeError(f"destination '{destination_file_name}' must point to a file name")

        # Remove destination file if exists
        if os.path.exists(destination_file_name):
            if not delete_if_exist:
                raise RuntimeError(f"destination '{destination_file_name}' already exist and "
                                   f"we're nit allowed to delete")
            else:
                os.remove(destination_file_name)

        # Gets the files list
        files: list = self.url_get(url=url, destination=None, proxy=proxy, token=token)
        if not isinstance(files, list):
            raise RuntimeError("could not get listing for remote URL")

        # Define temporary paths to work on
        destination_temp_path = self._toolbox.get_temp_pathname()

        # Download files and create ZIP
        try:
            for file_info in files:
                if file_info['type'] != 'file':
                    continue  # Skip subdirectories (for now)

                filename = file_info['name']
                # If allowed_extensions is specified, filter
                if allowed_extensions and not any(filename.lower().endswith(ext.lower()) for ext in allowed_extensions):
                    continue

                file_url = file_info['download_url']
                local_filename = os.path.join(destination_temp_path, file_info['name'])

                # Use the provided download function
                file_url = self._toolbox.normalize_to_github_api_url(url=file_url)
                self.url_get(url=file_url, proxy=proxy, token=token, destination=local_filename)

            # After all files are downloaded, zip them
            with zipfile.ZipFile(destination_file_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, filenames in os.walk(destination_temp_path):
                    for filename in filenames:
                        full_path = os.path.join(root, filename)
                        archive_name = os.path.relpath(full_path, destination_temp_path)
                        zipf.write(full_path, archive_name)

            return destination_file_name

        except Exception:
            raise
        finally:
            shutil.rmtree(destination_temp_path)

    def url_get(  # noqa: C901 # Acceptable complexity
            self, url: str,
            destination: Optional[str] = None,
            delete_if_exist: Optional[bool] = False,
            proxy: Optional[str] = None,
            token: Optional[str] = None,
            timeout: Optional[float] = None,
            extra_headers: Optional[dict] = None) -> Optional[Any]:
        """
        Downloads a file / list of files from a specified URL to a specified local path, with optional authentication,
        proxy support, and additional HTTP headers. When verbosity is on the download progress is shown.
        Args:
            url (str): The URL from which to download the file.
            destination (Optional[str]): The local path / file where the downloaded file should be saved.
            delete_if_exist (bool): Delete local copy of the file if exists.
            proxy (Optional[str]): The proxy server URL to use for the download.
            token (Optional[str]): An authorization token for accessing the file.
            timeout (Optional[float]): The timeout for the download operation, in seconds.
            extra_headers (Optional[dict]): Additional headers to include in the download request.

        Returns:
            Any - based on the context or exception on error.
        """
        remote_file: Optional[str] = None
        destination_file: Optional[str] = None
        effective_timeout: Optional[float] = None if timeout == 0 else timeout

        try:
            # Normalize URL and output name
            url = self._toolbox.normalize_text(text=url)
            is_url_path = self._toolbox.is_url_path(url)

            if is_url_path is None:
                raise RuntimeError(f"URL '{url}' is not a valid URL")

            if not is_url_path:

                # When the remote URL points to a path, we will attempt to retrieve the directory listing.
                # Therefore, the following section, which constructs the destination file and path, becomes relevant.
                remote_file = self._toolbox.file_from_url(url=url)

                if destination is None:
                    destination = self._toolbox.get_temp_pathname()
                    is_destination_path = True
                else:
                    # Expand the provided destination string as needed
                    destination = self.environment_variable_expand(text=destination, to_absolute_path=True)
                    # Try to detect if destination looks like a path
                    is_destination_path = self._toolbox.looks_like_unix_path(destination)

                if is_destination_path:
                    destination_dir = destination
                    destination_file = os.path.join(destination_dir, remote_file)
                else:
                    destination_dir = os.path.dirname(destination)
                    destination_file = destination

                if os.path.exists(destination_file):
                    if not delete_if_exist:
                        raise FileExistsError(f"destination file '{os.path.basename(destination)}' already exists")
                    else:
                        os.remove(destination_file)
                else:  # Create the directory if it does not exist
                    self.path_create(path=destination_dir, erase_if_exist=False)

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
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:

                content_length = response.getheader('Content-Length')

                if content_length is None or is_url_path:
                    content = response.read()
                    if is_url_path:
                        # When the UTL points to a path, return the file listing
                        files = json.loads(content.decode('utf-8'))
                        return files
                    else:
                        with open(destination_file, 'wb') as f:
                            f.write(content)

                        written_bytes = len(content)
                        return written_bytes
                else:
                    total_size = int(response.getheader('Content-Length').strip())
                    downloaded_size = 0
                    chunk_size = 1024 * 10  # 10KB chunk size
                    self._logger.debug(f"Starting '{destination_file}' download, size: {total_size} bytes")

                    with open(destination_file, 'wb') as out_file:
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            out_file.write(chunk)
                            downloaded_size += len(chunk)

                            progress_percentage = (downloaded_size / total_size) * 100
                            percentage_text = f"{progress_percentage:.2f}%"

                            if self._tracker is not None:
                                # Update the tracker if we have it
                                self._tracker.set_body_in_place(text=percentage_text)

                self._logger.debug(f"Total {total_size} bytes downloaded and written")
                return downloaded_size

        except Exception as download_error:
            raise RuntimeError(f"download error '{remote_file or url}', {download_error}") from download_error

    def create_config_file(self, solution_name: str, config_path: str) -> None:
        """
        Creates a .config file inside the given directory with basic solution properties.

        Args:
            solution_name (str): The name of the solution to store in the config.
            config_path (str): Path to the directory where .config should be created.
        """
        try:
            config_dir = Path(config_path)
            config_file = config_dir / ".config"

            self._logger.debug(f"Creating config file '{config_file.__str__()}'")

            config_dir.mkdir(parents=True, exist_ok=True)
            install_date = datetime.now().isoformat(timespec='seconds')

            with config_file.open("w") as f:
                f.write("# Please do not remove or edit.\n")
                f.write("# File was auto-generated by AutoForge solution installer.\n")
                f.write(f"solution_name={solution_name}\n")
                f.write(f"install_date={install_date}\n")

        except Exception as exception:
            raise RuntimeError(f"failed to create .config in {config_path}: {exception}") from exception

    def follow_steps(self, steps_file: str, tracker: Optional[ProgressTracker] = None) -> Optional[int]:
        """
`       Load the steps JSON file and execute them sequentially, exit loop on any error.
        Args:
            steps_file (str): Path to the steps JSON file.
            tracker (Optional[ProgressTracker]): A progress tracker instance to, else a local one will be carted.
        Returns:
            int: Exit code of the function.
        """
        step_number: int = 0
        local_path = os.path.abspath(os.getcwd())  # Store initial path
        self._variables = CoreVariables.get_instance()

        def _expand_and_print(msg: Optional[Any]) -> None:
            """ Expands an input if it's a string, resolve inner variables and finally print thed results."""
            if not isinstance(msg, str) or msg == "":
                return None

            expanded_msg = self._variables.expand(text=msg, expand_path=False)
            if expanded_msg:
                print(expanded_msg)
            return None

        try:
            # Expand, convert to absolute path and verify
            steps_file = self.environment_variable_expand(text=steps_file, to_absolute_path=True)
            if not os.path.exists(steps_file):
                raise RuntimeError(f"steps file '{steps_file}' does not exist")

            # Process as JSON
            steps_schema = self._processor.preprocess(steps_file)
            self._steps_data = steps_schema.get("steps")

            # Attempt to get status view defaults
            self._status_new_line = steps_schema.get("status_new_line", self._status_new_line)
            self._status_title_length = steps_schema.get("status_title_length", self._status_title_length)
            self._status_add_time_prefix = steps_schema.get("status_add_time_prefix", self._status_add_time_prefix)

            # Initialize a track instance
            self._tracker = tracker if tracker is not None else ProgressTracker(title_length=self._status_title_length,
                                                                                add_time_prefix=self._status_add_time_prefix)

            # User optional greetings messages
            _expand_and_print(steps_schema.get("status_pre_message"))

            # Move to the workspace path if exist as early as possible
            if os.path.exists(self._workspace_path):
                os.chdir(self._workspace_path)

            for step in self._steps_data:

                # Allow a step to temporary override in place status output behaviour
                status_new_line: bool = step.get("status_new_line", self._status_new_line)

                # Allow to skip a step when 'status_step_disabled' exist and set to True
                step_disabled: bool = step.get("step_disabled", False)
                if step_disabled:
                    continue

                self._tracker.set_pre(text=step.get('description'), new_line=status_new_line)

                # Execute the method
                results = self.execute_python_method(method_name=step.get("method"), arguments=step.get("arguments"))

                # Handle command output capture to a variable
                store_key = step.get('response_store_key', None)
                # Store the command response as a value If it's a string, and we got the skey name from the JSON
                if results.response and store_key is not None:
                    self._logger.debug(f"Storing value '{results.response}' in '{store_key}'")
                    self._toolbox.store_value(key=store_key, value=results.response)

                self._tracker.set_result(text="OK", status_code=0)
                step_number = step_number + 1

            # User optional signoff messages
            self._tracker.set_end()
            _expand_and_print(steps_schema.get("status_post_message"))
            return 0

        except Exception as steps_error:
            self._tracker.set_result(text="Error", status_code=1)
            raise RuntimeError(f"'{os.path.basename(steps_file)}' at step {step_number} {steps_error}") from steps_error
        finally:
            # Restore terminal cursor on exit
            os.chdir(local_path)  # Restore initial path
            self._tracker = None
