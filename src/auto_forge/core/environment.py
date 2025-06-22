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
import fcntl
import fnmatch
import inspect
import json
import logging
import os
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
from typing import Any, Callable, Optional, Union, Tuple

from colorama import Fore, Style

# AutoForge imports
from auto_forge import (
    AddressInfoType, AutoForgeModuleType, AutoLogger, CommandResultType,
    CoreDynamicLoader, CoreJSONCProcessor, CoreLinuxAliases, CoreModuleInterface,
    CoreRegistry, CoreSystemInfo, CoreToolBox, CoreVariables,
    ExecutionModeType, ProgressTracker, PROJECT_SHARED_PATH,
    SequenceErrorActionType, TerminalEchoType, ValidationMethodType,
    VersionCompare, Watchdog
)

AUTO_FORGE_MODULE_NAME = "Environment"
AUTO_FORGE_MODULE_DESCRIPTION = "Environment operations"


class CoreEnvironment(CoreModuleInterface):
    """
    a Core class that serves as an environment related operation swissknife.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        self._workspace_path: Optional[str] = None
        self._steps_data: Optional[list[tuple[str, Any]]] = None
        self._status_title_length: int = 80
        self._status_add_time_prefix: bool = True
        self._status_new_line: bool = False
        self._running_sequence: bool = False
        self._tracker: Optional[ProgressTracker] = None
        self._variables: Optional[CoreVariables] = None

        super().__init__(*args, **kwargs)

    def _initialize(self, workspace_path: str, configuration: dict[str, Any]) -> None:
        """
        Initialize the 'Environment' class.
        Args:
            workspace_path: path to the workspace directory to initialize.
            configuration: dictionary with package configuration data.
        Note:
            These core modules may be initialized before the main AutoForge controller is constructed.
            As such, they must receive configuration data directly from the top-level auto_forge bootstrap logic
            to support early startup execution.
        """

        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME, log_level=logging.DEBUG)
        self._package_manager: Optional[str] = None
        self._workspace_path: str = workspace_path
        self._subprocess_execution_timout: float = 60.0  # Time allowed for executed shell command
        self._processor = CoreJSONCProcessor.get_instance()  # Instantiate JSON processing library
        self._tool_box: CoreToolBox = CoreToolBox.get_instance()
        self._sys_info: CoreSystemInfo = CoreSystemInfo.get_instance()
        self._loader: CoreDynamicLoader = CoreDynamicLoader.get_instance()
        self._variables: CoreVariables = CoreVariables.get_instance()
        self._configuration: dict[str, Any] = configuration
        self._linux_aliases: CoreLinuxAliases = CoreLinuxAliases.get_instance()

        # Get the interactive commands from package configuration or use defaults if not available
        self._interactive_commands = self._configuration.get('interactive_commands',
                                                             ["cat", "htop", "top", "vim", "less", "nano",
                                                              "vi", "clear", "pico"])

        # Allow to override default execution opf subprocesses in configuration
        self._subprocess_execution_timout = self._configuration.get("subprocess_execution_timout",
                                                                    self._subprocess_execution_timout)

        # Persist this module instance in the global registry for centralized access
        self._registry = CoreRegistry.get_instance()
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

    def _print(self, text: str):
        """
        Print text taking into consideration automation mode.
        Args:
            text (str): The text to print.
        """
        if not self._running_sequence and isinstance(text, str):
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
    def _get_default_python_info() -> Optional[Tuple[str, str]]:
        """
        Returns the path and version of the default Python 3 interpreter if found.
        Returns:
            Optional[Tuple[str, str]]: (interpreter_path, version_string) or None if not found.
        """
        python_executable = shutil.which("python3")
        if not python_executable:
            return None

        try:
            result = subprocess.run([python_executable, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, check=True)
            # Example output: "Python 3.9.18"
            version_parts = result.stdout.strip().split()
            if len(version_parts) == 2 and version_parts[0].lower() == "python":
                major_minor = ".".join(version_parts[1].split(".")[:2])
                return python_executable, major_minor
        except (subprocess.SubprocessError, OSError):
            pass

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
            venv_path = self._variables.expand(key=venv_path.strip())
            python_executable = os.path.join(venv_path, 'bin', 'python')
        else:
            python_executable = shutil.which("python3")

        if not python_executable or not os.path.exists(python_executable):
            raise RuntimeError(f"Python executable not found at: '{python_executable}'")

        self._logger.debug(f"Python executable found: '{python_executable}'")
        return python_executable

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
                             create_as_needed: bool = False, change_dir: bool = False) -> Optional[str]:
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

            # Expand as needed
            self._workspace_path = self._variables.expand(key=self._workspace_path)
            # Safeguard against deleting important directories
            if delete_existing:
                self.path_erase(path=self._workspace_path, allow_non_empty=True)
                # Make sure the base path exist
                os.makedirs(self._workspace_path, exist_ok=True)
            if create_as_needed:
                os.makedirs(self._workspace_path, exist_ok=True)

            # Enforce empty path
            if must_be_empty:
                self._tool_box.is_directory_empty(path=self._workspace_path, raise_exception=True)

            # Set the workspace as a working directory, may raise an exception if it does not exist
            if change_dir:
                os.chdir(self._workspace_path)

            return self._workspace_path

        # Propagate the exception
        except Exception as exception:
            raise exception

    def create_alias(self, alias: str, command: str, commit_changes: bool = False) -> Optional[CommandResultType]:
        # noinspection SpellCheckingInspection
        """
                Create / update a shell alias using the ShellAliases Core module
                Args:
                    alias (str): The shell alias name.
                    command (str): The shell alias command.
                    commit_changes (bool): If true, the shell alias will be committed to the shell startup script (e.g. '~/.bahsrc')
                Returns:
                    CommandResultType: The result object containing the command output and return code,
                """

        return_code: int = 1

        # It's better to pass the expanded variable, since most of our environment is local
        # and won't be available to the shell after we exit.
        expanded_command = self._variables.expand(key=command)

        # Create and commit, we need both to succeed
        if self._linux_aliases.create(alias=alias, command=expanded_command):
            return_code = 0

            if commit_changes:
                if not self._linux_aliases.commit():
                    return_code = 1

        # Wrap return code the command result type
        return CommandResultType(response=alias, return_code=return_code)

    def environment_append_to_path(self, path: str):
        """
        Append a directory to the system's PATH environment variable.
        Args:
            path (str): The directory path to append to PATH.
        """
        path = self._variables.expand(path)
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
            raise ValueError(f"token '{searched_token}' not found in environment variable '{name}'.")

    def execute_python_method(self, method_name: str, arguments: Optional[Union[str, dict]] = None) -> Optional[
        CommandResultType]:
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
        method_kwargs, extra_kwargs = self._tool_box.filter_kwargs_for_method(kwargs=arguments, sig=method_signature)

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
        return_code = self._loader.execute_command(name=command, arguments=arguments, suppress_output=suppress_output)
        # Get the command output
        command_response = self._loader.get_last_output().strip()

        if return_code != expected_return_code:
            raise RuntimeError(f"'{command}' failed with return code {return_code}, expected {expected_return_code}")

        return CommandResultType(response=command_response, return_code=return_code)

    def execute_with_spinner(self, message: str, command: Union[str, Callable], arguments: Optional[Any] = None,
                             command_type: ExecutionModeType = ExecutionModeType.SHELL, timeout: Optional[float] = None,
                             color: Optional[str] = Fore.CYAN, new_lines: int = 0) -> Optional[int]:
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
        message = self._tool_box.normalize_text(text=message)

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
                        command_and_args=self._flatten_command(command=command, arguments=arguments), timeout=timeout)
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

        self._tool_box.set_cursor(visible=False)
        spin_thread = threading.Thread(target=_show_spinner, name="Spinner")
        exec_thread = threading.Thread(target=_execute_foreign_code, name="SpinnerExecute")

        spin_thread.start()
        exec_thread.start()

        exec_thread.join()
        spinner_running = False
        self._tool_box.set_cursor(visible=True)
        spin_thread.join()

        print(Style.RESET_ALL, end='')  # Ensure styling is reset
        return result_container.get('code', 1)

    def execute_shell_command(  # noqa: C901
            self, command_and_args: Union[str, list[str]], timeout: Optional[float] = None,
            echo_type: TerminalEchoType = TerminalEchoType.NONE, leading_text: Optional[str] = None,
            use_pty: bool = True, searched_token: Optional[str] = None, check: bool = True, shell: bool = True,
            cwd: Optional[str] = None, env: Optional[Mapping[str, str]] = None, max_reda_chunk: Optional[int] = 124) -> \
            Optional[CommandResultType]:
        """
        Executes a shell command with specified arguments and configuration settings.
        Args:
            command_and_args (Union[str, list): a single string for the command along its arguments or a list.
            timeout (Optional[float]): The maximum time in seconds to allow the command to run, 0 for no timeout.
            echo_type (TerminalEchoType): Defines how data is being echoed to the terminal from a forked process.
            leading_text (Optional[str]): Leading text to be printed before each logged line.
            use_pty (bool): If True, the command will be executed in a PTY environment. Defaults to False.
            searched_token (Optional[str]): A token to search for in the command output.
            check (bool): If True, the command will raise CalledProcessError if the return code is non-zero
            shell (bool): If True, the command will be executed in a shell environment.
            cwd (Optional[str]): The directory from which the process should be executed.
            env (Optional[Mapping[str, str]]): Environment variables.
            max_reda_chunk (Optional[int]): The maximum number of bytes we're allowed to acclimate.

        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """

        polling_interval: float = 0.1
        kwargs: Optional[dict[str, Any]] = {}
        line_buffer = bytearray()
        lines_queue = deque(maxlen=100)  # Storing upto the last 100 output lines
        master_fd: Optional[int] = None  # PTY master descriptor
        timeout = self._subprocess_execution_timout if timeout is None else timeout  # Set default timeout when not provided
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        max_reda_chunk = 1024 if max_reda_chunk < 1 else max_reda_chunk  # Normalize bad user input

        # Determine the terminal width
        try:
            term_width = shutil.get_terminal_size().columns
        except OSError:
            term_width = 100  # fallback default if terminal size can't be determined

        # Create merged environment where AutoForge variables override exising
        proc_env: dict[str, str] = os.environ.copy()
        if env:
            proc_env.update(env)  # apply overrides and updates

        # Cleanup
        if isinstance(command_and_args, str):
            command_and_args = CoreToolBox.normalize_text(text=command_and_args)
            command_list = command_and_args.strip().split()
        elif isinstance(command_and_args, list):
            command_list = []
            for item in command_and_args:
                cleaned = CoreToolBox.normalize_text(text=item)
                command_list.append(cleaned)
        else:
            raise TypeError("command_and_args must be a string or a list of strings")

        full_command = command_list[0]  # The command
        command = os.path.basename(full_command)

        # Full TTY handoff for interactive apps
        if any(fnmatch.fnmatch(command, pattern) for pattern in self._interactive_commands):
            self._logger.debug(f"Executing: {command_and_args} (Full TTY)")
            results = self.execute_fullscreen_shell_command(command_and_args=command_and_args, env=proc_env,
                                                            timeout=timeout)
            if check and results.return_code != 0:
                raise subprocess.CalledProcessError(returncode=results.return_code, cmd=command)
            return results

        # Expand current work directory if specified
        cwd = self._variables.expand(key=cwd) if cwd else cwd

        def _safe_quote(arg: str) -> str:
            """ Allow simple expansions or globs, quote all else """
            if re.match(r'^[$~][\w{}@]*$', arg) or '*' in arg or '?' in arg:
                return arg  # allow shell expansion
            return shlex.quote(arg)

        # When not using shell we have to use list for the arguments rather than string
        if not shell:
            if " " in command or any(c in command for c in "|&;<>()"):
                raise ValueError(f"unsupported compound shell expression: {command}")
            _command = command_list
        else:
            _command = " ".join(_safe_quote(arg) for arg in command_list)
            env_shell = os.environ.get("SHELL")
            if env_shell:
                kwargs = dict()
                kwargs['executable'] = env_shell

        def _clean_shell_error_prefix(_error_msg: str) -> str:
            """
            Remove common shell prefixes like 'zsh:1:', 'bash: line 1:', etc.
            Preserves newlines and carriage returns.
            """
            _pattern = r'^\s*(?:[a-zA-Z0-9_\-]+:)?(?:\s*line\s*\d+|[0-9]+)?:?\s*'
            match = re.match(_pattern, _error_msg)
            if match:
                remainder = _error_msg[match.end():]
                # Only strip prefix if there's something meaningful left
                if remainder.strip() != "":
                    return remainder
            return _error_msg

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

                # noinspection PyTypeChecker
                decoded = decoder.decode(byte_data)
                sys.stdout.write(decoded)
                sys.stdout.flush()

            except (UnicodeDecodeError, OSError, TypeError) as decode_exception:
                if not suppress_errors:
                    raise decode_exception

        def _print_line(line: str) -> None:
            """
            Prints a line to stdout, either overwriting the current line or printing a new line.
            Args:
                line (str): The text to print.
            """

            line = _clean_shell_error_prefix(line) if line else line
            if line:

                if leading_text is not None:
                    line = leading_text + line  # Prefix with optional leading text

                if "warning:" in line:
                    line = line.replace("warning:", f"{Fore.YELLOW}\nWarning:{Style.RESET_ALL}") + "\n"
                elif "error:" in line:
                    line = line.replace("error:", f"{Fore.RED}\nError:{Style.RESET_ALL}") + "\n"
                else:
                    max_len = max(10, term_width - 10)
                    line = line[:max_len]

                if echo_type in [TerminalEchoType.CLEAR_LINE, TerminalEchoType.SINGLE_LINE]:
                    sys.stdout.write(f'\033[K{line}\r')
                else:
                    sys.stdout.write(line)

                sys.stdout.flush()

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

            clear_text = self._tool_box.strip_ansi(text=text, bare_text=True)
            if clear_text:
                message_queue.append(clear_text)
                self._logger.debug(f"> {clear_text}")

            input_buffer.clear()
            if echo_type != TerminalEchoType.LINE:
                return clear_text
            else:
                return text

        def _is_readable():
            """
            Wait for readable file descriptors from the process.
            Returns a list of readable streams or file descriptors.
            """
            if use_pty:
                return select.select([master_fd], [], [], polling_interval)[0]
            else:
                return select.select([process.stdout, process.stderr], [], [], polling_interval)[0]

        # Execute the external command
        if use_pty:
            self._logger.debug(f"Executing: {command_and_args} (PTY)")
            master_fd, slave_fd = pty.openpty()
            process = subprocess.Popen(_command, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, bufsize=0,
                                       shell=shell, cwd=cwd, env=proc_env, **kwargs)
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        else:  # Normal flow
            self._logger.debug(f"Executing: {command_and_args}")
            process = subprocess.Popen(_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, bufsize=0, shell=shell, cwd=cwd, env=proc_env,
                                       **kwargs)

        try:
            start_time = time.time()
            output_ready = False
            early_exit_no_output = False

            # Wait for process to start emitting output or terminate
            while not output_ready:
                if timeout > 0 and (time.time() - start_time > timeout):
                    raise TimeoutError(f"'{command}' did not produce output after {timeout} seconds")

                if _is_readable():
                    output_ready = True
                elif process.poll() is not None:
                    # Process exited without producing output
                    early_exit_no_output = True
                    break

            if early_exit_no_output:
                self._logger.debug(f"'{command}' exited before producing output.")
                return CommandResultType(response=None, return_code=process.returncode)

            # Loop and read the spawned process output upto timeout or normal termination
            while True:

                if _is_readable():
                    if use_pty:
                        received_bytes = os.read(master_fd, max_reda_chunk)
                    else:
                        received_bytes = process.stdout.read(max_reda_chunk)

                    if received_bytes:
                        for b in received_bytes:

                            if b == 0:
                                break  # EOF or invalid byte

                            # Convert int back to single-byte bytes object
                            byte = bytes([b])

                            # Immediately echo to the byte to the terminal if set
                            if echo_type == TerminalEchoType.BYTE:
                                _print_bytes_safely(byte)

                            # Aggregate bytes into complete single lines for logging
                            line_buffer.append(b)

                            if b in (ord('\n'), ord('\r')):
                                # Clear the line and aggravate into a queue
                                text_line = _bytes_to_message_queue(line_buffer, lines_queue)

                                if len(text_line) > 0:
                                    if echo_type in [TerminalEchoType.LINE, TerminalEchoType.CLEAR_LINE,
                                                     TerminalEchoType.SINGLE_LINE]:
                                        _print_line(text_line)
                                    # Track it if we have a tracker instate
                                    if self._tracker is not None:
                                        self._tracker.set_body_in_place(text=text_line.strip())

                else:
                    # No data ready to read — check if process exited
                    if process.poll() is not None:
                        break

                    # Handle execution timeout
                    elif timeout > 0 and (time.time() - start_time > timeout):
                        process.kill()
                        raise TimeoutError(f"'{command}' timed out after {timeout} seconds")

            process.wait(timeout=1.0)
            # Add any remaining bytes
            if line_buffer:
                _bytes_to_message_queue(line_buffer, lines_queue)

            # Done executing
            command_response: str = "\n".join(lines_queue)  # Convert to a full string with newlines
            return_code = process.returncode

            # Optionally raise exception non-zero return code
            if check and return_code != 0:
                raise subprocess.CalledProcessError(returncode=process.returncode, cmd=command, output=command_response,
                                                    stderr=process.stderr)

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
    def execute_fullscreen_shell_command(command_and_args: str, env: Optional[Mapping[str, str]] = None,
                                         timeout: Optional[float] = None) -> Optional[
        CommandResultType]:
        """
        Runs a full-screen TUI command like 'htop' or 'vim' by fully attaching to the terminal.
        Args:
            command_and_args (str): a string containing the full command and arguments to execute.
            env (Optional[Mapping[str, str]]): Environment variables.
            timeout (Optional, float): Terminate the subprocess after this many seconds.
        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """
        return_code: int = 0  # Initialize to error code
        if timeout:
            Watchdog().start(timeout=timeout)

        with suppress(KeyboardInterrupt):
            result = subprocess.run(command_and_args, shell=True, check=False, stdin=sys.stdin, stdout=sys.stdout,
                                    stderr=sys.stderr, env=env, )
            return_code = result.returncode

        # Stop the watchdog if we've ised it
        if timeout:
            Watchdog().stop()

        return CommandResultType(response='', return_code=return_code)

    def validate_prerequisite(self, command: str, arguments: Optional[str] = None, cwd: Optional[str] = None,
                              validation_method: ValidationMethodType = ValidationMethodType.EXECUTE_PROCESS,
                              expected_response: Optional[str] = None, version: Optional[str] = None) -> Optional[
        CommandResultType]:
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
            version (Optional[str]): The expected version of the system package or executed binary, would be fixed
                (e.g., "10.76"), or an expression (e.g., ">=10.0", "==1.2.3").

        Returns:
            Optional[CommandResultType]: A result object containing the command output and return code,
            or None if an exception was raised.
        """

        try:
            # Execute a process and check its response
            if validation_method == ValidationMethodType.EXECUTE_PROCESS:

                if expected_response is not None and version is not None:
                    raise ValueError(f"can specify either 'expected_response' or 'version' but not both")

                results = self.execute_shell_command(
                    command_and_args=self._flatten_command(command=command, arguments=arguments), cwd=cwd)

                if results.response is None:
                    raise RuntimeError(f"'{command}' returned no output while expecting '{expected_response}'")

                # If the user specified required version, use the VersionInfo auxiliary class to do the heavy lifting.
                if isinstance(version, str):

                    compare_results = VersionCompare().compare(detected=results.response, expected=version)
                    if compare_results is not None:
                        version_ok, detected_version = compare_results
                        if not version_ok:
                            raise Exception(f"command '{command}' version was not satisfied, "
                                            f"expected '{version}' found {detected_version}")

                elif isinstance(expected_response, str):
                    if expected_response.lower() not in results.response.lower():
                        raise Exception(f"expected response '{expected_response}' not found in output")

            # Read a text line from a file and compare its content
            elif validation_method == ValidationMethodType.READ_FILE:
                parts = command.split(':')
                if len(parts) < 2:
                    raise ValueError("READ_FILE command must be in the form '<file_path>:<line_number>[:<line_count>]'")
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

    def path_erase(self, path: str, allow_non_empty: bool = False, raise_exception_if_not_exist: bool = False):
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
            expanded_path = self._variables.expand(key=path)

            if not os.path.exists(expanded_path):
                if raise_exception_if_not_exist:
                    raise FileNotFoundError(f"'{expanded_path}' does not exist")
                return  # Exit without raising exception

            # Prevent deletion of very high-level directories, adjust the level as necessary
            if path.count(os.sep) < 2:
                raise RuntimeError(f"refusing to delete a high-level directory: '{path}'")

            # Ensure the path is not home directory or its important subdirectories
            home_path = os.path.expanduser("~")
            important_paths = [home_path,  # Never delete home directory
                               os.path.join(home_path, "Documents"), os.path.join(home_path, "Desktop"), ]

            if expanded_path in map(os.path.abspath, important_paths):
                raise RuntimeError(f"refusing to delete important or protected directory: '{expanded_path}'")

            if not allow_non_empty and os.listdir(path):
                raise IsADirectoryError(f"directory '{path}' is not empty, delete canceled")

            # If the directory exists, remove it
            if os.path.exists(path):
                shutil.rmtree(path)

        # Propagate the exception
        except Exception as erase_exception:
            raise erase_exception

    def path_create(self, path: Optional[str] = None, paths: Optional[list[str]] = None, erase_if_exist: bool = False,
                    project_path: bool = True) -> Optional[str]:
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
                    path = self._variables.expand(key=path)

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

    def decompress(self, archive_path: str, destination_path: Optional[str] = None) -> Optional[CommandResultType]:
        """
        Decompresses an archive using the ToolBox utility and returns a CommandResultType.
        Args:
            archive_path (str): Path to the archive file to decompress.
            destination_path (Optional[str]): Optional destination directory for extracted content.
        Returns:
            CommandResultType: A structured result containing the extraction path and status code.
        """
        expanded_archive_path: str = self._variables.expand(key=archive_path)
        expanded_destination_path: Optional[str] = (
            self._variables.expand(key=destination_path) if destination_path else None)

        try:
            extracted_path = self._tool_box.uncompress_file(archive_path=expanded_archive_path,
                                                            destination_path=expanded_destination_path,
                                                            delete_after=True,
                                                            update_progress=self._tracker.set_body_in_place)
            return CommandResultType(response=extracted_path, return_code=0)

        except Exception as decompress_error:
            raise decompress_error from decompress_error

    def python_virtualenv_create(self, venv_path: str, python_version: Optional[str] = None) -> Optional[
        CommandResultType]:
        """
        Create a Python virtual environment using a specified or default Python interpreter.
        Args:
            venv_path (str): Destination directory for the virtual environment.
            python_version (Optional[str]): Desired Python version (e.g., "3.9").
                                            If not specified, the system default Python 3 interpreter is used.
        Returns:
            Optional[CommandResultType]: Result object with command output and return code, or None on failure.
        """
        try:
            venv_expanded_path = self._variables.expand(key=venv_path)

            if python_version is None:
                default_info = self._get_default_python_info()
                if not default_info:
                    raise RuntimeError("Failed to locate default Python interpreter and version.")
                python_binary, python_version = default_info
            else:
                python_binary = shutil.which(f"python{python_version}")
                if not python_binary:
                    raise RuntimeError(f"Python interpreter for version '{python_version}' was not found.")

            self._logger.debug(
                f"Using Python '{python_binary}' (version {python_version}) to create venv at '{venv_expanded_path}'")

            created_venv_path = self.path_create(venv_expanded_path, erase_if_exist=True, project_path=True)
            if created_venv_path is None:
                raise RuntimeError(f"Could not create virtual environment path '{venv_expanded_path}'")

            command_and_args = self._flatten_command(command=python_binary, arguments=f"-m venv {created_venv_path}")

            return self.execute_shell_command(command_and_args=command_and_args)

        except Exception as py_error:
            raise Exception(f"Failed to create virtual environment at '{venv_path}': {py_error}") from py_error

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
            python_binary = self._get_python_binary_path(venv_path=venv_path)

            # Construct the command to update pip
            arguments = "-m pip install --upgrade pip"
            command_and_args = self._flatten_command(command=python_binary, arguments=arguments)

            results = self.execute_shell_command(command_and_args=command_and_args)
            return results

        except Exception as py_env_error:
            raise Exception(f"could not update pip {py_env_error}") from py_env_error

    def python_package_add(self, package_or_requirements: str, venv_path: Optional[str] = None) -> Optional[
        CommandResultType]:
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
            package_or_requirements = self._tool_box.normalize_text(package_or_requirements)
            if len(package_or_requirements) == 0:
                raise RuntimeError("no package or requirements file specified for pip")

            # Determine if the input is a package name or a path to a requirements file
            if package_or_requirements.endswith('.txt'):
                arguments = f"-m pip install -r {package_or_requirements}"
            else:
                arguments = f"-m pip install {package_or_requirements}"

            # Execute the command
            results = (
                self.execute_shell_command(command_and_args=self._flatten_command(command=command, arguments=arguments),
                                           shell=False))
            return results

        except Exception as python_pip_error:
            raise Exception(f"could not install pip package(s) '{package_or_requirements}' {python_pip_error}") \
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
            package = self._tool_box.normalize_text(package)
            if not package:
                raise RuntimeError("no package specified for pip")

            arguments = f"-m pip uninstall -y {package}"

            # Execute the command
            results = self.execute_shell_command(
                command_and_args=self._flatten_command(command=command, arguments=arguments), shell=False)

            return results

        except Exception as python_pip_error:
            raise Exception(f"could not uninstall pip package(s) '{package}' {python_pip_error}") from python_pip_error

    def python_package_get_version(self, package: str, venv_path: Optional[str] = None) -> Optional[str]:
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
            package = self._tool_box.normalize_text(package)
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

    def git_clone_repo(self, repo_url: str, dest_repo_path: str, timeout: float = 0,
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
            repo_url = self._tool_box.normalize_text(repo_url)

            # Normalize and prepare the destination path
            if self._variables is not None:
                dest_repo_path = self._variables.expand(key=dest_repo_path)

            dest_repo_path = self._variables.expand(key=dest_repo_path)

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

    def git_checkout_revision(self, dest_repo_path: str, revision: str, timeout: float = 0, pull_latest: bool = True) -> \
            Optional[CommandResultType]:
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
            normalized_repo_path = self._tool_box.normalize_text(dest_repo_path)
            dest_repo_path = self._variables.expand(key=normalized_repo_path)
            command = "git"

            if not os.path.exists(dest_repo_path):
                raise FileNotFoundError(f"repo path '{dest_repo_path}' does not exist")

            if pull_latest:
                # Perform a git pull to update the repository
                arguments = "pull"
                results = self.execute_shell_command(
                    command_and_args=self._flatten_command(command=command, arguments=arguments), cwd=dest_repo_path,
                    timeout=timeout)
                if results.return_code != 0:
                    raise RuntimeError(f"git 'pull'' failed with exit code {results.return_code}")

            # Construct and execute the git checkout command
            arguments = f"checkout {revision}"
            results = self.execute_shell_command(
                command_and_args=self._flatten_command(command=command, arguments=arguments), cwd=dest_repo_path,
                timeout=timeout)
            return results

        except Exception as py_git_error:
            raise Exception(f"git operation failure {py_git_error!s}") from py_git_error

    def git_get_path_from_url(self, url: str, destination_file_name: Optional[str] = None,
                              allowed_extensions: Optional[list[str]] = None,
                              delete_if_exist: bool = False) -> Optional[str]:
        """
        Downloads a GitHub folder (tree URL or API URL) as a .zip archive.
        Args:
            url (str): The URL from which to download the file:
                This is expected to be a GitHub repository URL which points to path rather than a file.
            destination_file_name (str): The local path/file where the downloaded file should be saved.
            allowed_extensions (Optional[List[str]]): Allowed file extensions. If None, all files are downloaded.
            delete_if_exist (bool): Delete local copy of the file if exists.

        Returns:
            str: Full path to the created .zip archive.
        """
        url = self._tool_box.normalize_text(text=url)
        url = self._tool_box.normalize_to_github_api_url(url=url)

        if url is None:
            raise RuntimeError(f"URL '{url}' is not a valid URL")

        # We're getting a pth so the URL is expected to point to git path
        is_url_path = self._tool_box.is_url_path(url)
        if is_url_path is None or not is_url_path:
            raise RuntimeError(f"URL '{url}' is not a valid URL or not pointing to a path")

        # Use temporary destination file if not specified
        if destination_file_name is None:
            destination_file_name = self._tool_box.get_temp_filename()

        destination_file_name = self._variables.expand(key=destination_file_name)

        # Make sure we got something that look like path that points to a filed name
        is_destination_path = self._tool_box.looks_like_unix_path(destination_file_name)
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
        results = self.url_get(url=url, destination=None)
        if results.return_code != 0 or results.extra_data is None:
            raise RuntimeError("could not get path listing for remote URL")

        files: list = results.extra_data
        if not isinstance(files, list):
            raise RuntimeError("path listing did not return a list")

        # Define temporary paths to work on
        destination_temp_path = self._tool_box.get_temp_pathname()

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
                file_url = self._tool_box.normalize_to_github_api_url(url=file_url)
                results = self.url_get(url=file_url, destination=local_filename)
                if results.return_code != 0:
                    raise RuntimeError(f"HTTP operation failed with exit code {results.return_code}")

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
            self, url: str, destination: Optional[str] = None, delete_if_exist: Optional[bool] = False,
            proxy_server: Optional[str] = None, token: Optional[str] = None, timeout: Optional[float] = None,
            extra_headers: Optional[dict] = None) -> Optional[CommandResultType]:
        """
        Downloads a file / list of files from a specified URL to a specified local path, with optional authentication,
        proxy support, and additional HTTP headers. When verbosity is on the download progress is shown.
        Args:
            url (str): The URL from which to download the file.
            destination (Optional[str]): The local path / file where the downloaded file should be saved.
            delete_if_exist (bool): Delete local copy of the file if exists.
            proxy_server (Optional[str]): The proxy server URL to use for the download,
                if None, default to globally configured proxy.
            token (Optional[str]): An authorization token for accessing the file, if None, default to globally
                configured token.
            timeout (Optional[float]): The timeout for the download operation, in seconds.
            extra_headers (Optional[dict]): Additional headers to include in the download request.

        Returns:
            CommandResultType, optional - or exception on error.
        """
        remote_file: Optional[str] = None
        destination_file: Optional[str] = None
        effective_timeout: Optional[float] = None if timeout == 0 else timeout

        # Use globally configured proxy and token when not explicitly specified.
        package_proxy_server: Optional[str] = None
        if isinstance(self.auto_forge.proxy_server, AddressInfoType):
            package_proxy_server = self.auto_forge.proxy_server.endpoint

        proxy_server: Optional[str] = proxy_server if proxy_server else package_proxy_server
        token: Optional[str] = token if token else self.auto_forge.git_token

        try:
            # Normalize URL and output name
            url = self._tool_box.normalize_text(text=url)
            is_url_path = self._tool_box.is_url_path(url)

            if is_url_path is None:
                raise RuntimeError(f"URL '{url}' is not a valid URL")

            if not is_url_path:

                # When the remote URL points to a path, we will attempt to retrieve the directory listing.
                # Therefore, the following section, which constructs the destination file and path, becomes relevant.
                remote_file = self._tool_box.file_from_url(url=url)

                if destination is None:
                    destination = self._tool_box.get_temp_pathname()
                    is_destination_path = True
                else:
                    # Expand the provided destination string as needed
                    destination = self._variables.expand(key=destination)
                    # Try to detect if destination looks like a path
                    is_destination_path = self._tool_box.looks_like_unix_path(destination)

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
            log_message = [f"HTTP request to {request.full_url}"]

            # Add authorization token to the request headers if provided
            if token:
                request.add_header('Authorization', f'Bearer {token}')
                log_message.append(f"using token: {token[:4]}****...")
            else:
                log_message.append(f"Token not specified")

            # Include any extra headers specified
            if extra_headers:
                for header, value in extra_headers.items():
                    request.add_header(header, value)

            # Configure proxy settings if a proxy URL is provided
            if proxy_server:
                proxy_handler = urllib.request.ProxyHandler({'http': proxy_server, 'https': proxy_server})
                opener = urllib.request.build_opener(proxy_handler)
                urllib.request.install_opener(opener)
                log_message.append(f"via proxy: {proxy_server}")
            else:
                log_message.append(f"Proxy not specified")

            self._logger.debug(" | ".join(log_message))

            # Perform the download operation
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:

                content_length = response.getheader('Content-Length')

                if content_length is None or is_url_path:
                    content = response.read()
                    if is_url_path:
                        # When the URL points to a path, return the file listing
                        files = json.loads(content.decode('utf-8'))
                        return CommandResultType(response=url, return_code=0, extra_data=files,
                                                 extra_value=content_length)
                    else:
                        with open(destination_file, 'wb') as f:
                            f.write(content)
                        written_bytes = len(content)
                        return CommandResultType(response=url, return_code=0, extra_value=written_bytes,
                                                 extra_data=content)

                else:
                    total_size = int(response.getheader('Content-Length').strip())
                    downloaded_size = 0
                    chunk_size = 1024 * 10  # 10KB chunk size
                    self._logger.debug(f"Storing '{destination_file}' download, size: {total_size} bytes")

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
                if total_size > 0:
                    return CommandResultType(response=destination_file, return_code=0, extra_value=downloaded_size)
                else:
                    # Received 0 bytes, this is probably not the desired results
                    return CommandResultType(response=destination_file, return_code=1, extra_value=downloaded_size)

        except Exception as download_error:
            raise RuntimeError(f"download error '{remote_file or url}', {download_error}") from download_error

    def finalize_workspace_creation(self, solution_package_path: str, solution_name: str,
                                    sequence_log_file: Optional[Union[str, Path]] = None):
        """
        Carries the last steps of new workspace creation.
        - Copy that startup shell script (env.sh) to the project workspace
        - Copy jsons and zip files from the source package path to the project workspace.
        - Move any residual log file into the workspace 'logs' path.
        - Create the .config at the workspace root which is required for correctly starting AutoForge later.
        """

        def _create_config_file(_solution_name: str, _create_path: str) -> None:
            """
            Creates a .config file inside the given directory with basic solution properties.
            Args:
                _solution_name (str): The name of the solution to store in the config.
                _create_path (str): Path to the directory where .config should be created.
            """
            try:

                _config_file_path: Path = Path(_create_path) / ".config"
                self._logger.debug(f"Creating config file '{str(_config_file_path)}'")

                _install_date = datetime.now().isoformat(timespec='seconds')

                with _config_file_path.open("w") as _config_file:
                    _config_file.write("# Please do not remove or edit.\n")
                    _config_file.write("# File was auto-generated by AutoForge solution installer.\n")
                    _config_file.write(f"solution_name={_solution_name}\n")
                    _config_file.write(f"install_date={_install_date}\n")

            except Exception as create_config_error:
                raise RuntimeError(
                    f"failed to create .config in {_create_path}: {create_config_error}") from create_config_error

        try:
            # Store the solution files in the newly created workspace.
            scripts_path = self._variables.get(key="SCRIPTS_BASE")
            if scripts_path is not None:
                solution_destination_path = os.path.join(scripts_path, 'solution')
                env_starter_file: Path = PROJECT_SHARED_PATH / 'env.sh'

                # Move all project specific jsons along with any zip files to the destination path.
                self._tool_box.cp(
                    pattern=f'{solution_package_path}/*.json*,{solution_package_path}/*.zip',
                    dest_dir=solution_destination_path)

                # Place the build system default initiator script
                self._tool_box.cp(pattern=f'{env_starter_file.__str__()}', dest_dir=self._workspace_path)

                # Place the sequence log to the newly created workspace logs path.
                if sequence_log_file is not None:
                    sequence_log_file = str(sequence_log_file)
                    self._tool_box.cp(pattern=sequence_log_file, dest_dir=self._variables.get(key="BUILD_LOGS"))

                # Finally, create a hidden '.config' file in the solution directory with essential metadata.
                _create_config_file(_solution_name=solution_name, _create_path=self._workspace_path)

        except Exception as exception:
            raise exception from exception

    def run_sequence(self, sequence_data: dict[str, Any], tracker: Optional[ProgressTracker] = None) -> Optional[int]:
        """
        Load and execute a sequence of steps from a structured dictionary.
        Each step is processed in order, and execution stops or resumes based on error policy.
        Args:
            sequence_data (dict[str, Any]): A dictionary containing the execution sequence definition.
            tracker (Optional[ProgressTracker]): An optional progress tracker. If not provided, a local one will be created.
        Returns:
            Optional[int]: Exit code. 0 on success, 1 on error.
        """
        step_number: int = 0
        warnings_count: int = 0
        last_step_results: Optional[CommandResultType] = None
        original_path = os.path.abspath(os.getcwd())  # Store entry path
        status_on_error: Optional[str] = None

        if not self._tool_box.has_nested_list(sequence_data, require_non_empty_lists=True):
            raise ValueError(
                "Sequence data appears to be invalid — expected a dictionary with a non-empty nested list of steps.")

        def _expand_and_print(msg: Optional[Any]) -> None:
            """Expand and print a string after resolving inner variables."""
            if not isinstance(msg, str) or not msg.strip():
                return
            expanded_msg = self._variables.expand(key=msg)
            if expanded_msg:
                sys.stdout.write('\033[2K')  # Clear current line
                print(expanded_msg)

        try:
            self._running_sequence = True  # Mark our state globally
            self._steps_data = sequence_data.get("steps", [])

            # We should have gotten a non-empty list of steps to execute so
            if not isinstance(self._steps_data, list) or not self._steps_data:
                raise ValueError("No valid steps found in the provided sequence.")

            # Set up status view configuration, use class defaults when not specified
            self._status_new_line = sequence_data.get("status_new_line", self._status_new_line)
            self._status_title_length = sequence_data.get("status_title_length", self._status_title_length)
            self._status_add_time_prefix = sequence_data.get("status_add_time_prefix", self._status_add_time_prefix)

            # Initialize progress tracker
            self._tracker = tracker if tracker else ProgressTracker(title_length=self._status_title_length,
                                                                    add_time_prefix=self._status_add_time_prefix)
            # Optional pre-message
            _expand_and_print(sequence_data.get("status_pre_message"))

            # Switch to workspace directory if available
            if os.path.exists(self._workspace_path):
                os.chdir(self._workspace_path)

            # First line reserved for package version
            self._tracker.set_complete_line(pre_text="AutoForge version", result_text=f"{self.auto_forge.version}")

            # Step-by-step execution loop
            for step in self._steps_data:
                status_new_line: bool = step.get("status_new_line", self._status_new_line)

                if step.get("disabled", False):
                    continue

                action_on_error: SequenceErrorActionType = SequenceErrorActionType.from_label(
                    step.get("action_on_error"))
                status_on_error = step.get("status_on_error")

                self._tracker.set_pre(text=step.get("description"), new_line=status_new_line)

                try:
                    last_step_results = self.execute_python_method(method_name=step.get("method"),
                                                                   arguments=step.get("arguments"))
                except Exception as execution_error:

                    # Default - not specified is treated a break
                    if action_on_error in (SequenceErrorActionType.BREAK, SequenceErrorActionType.DEFAULT):
                        raise execution_error from execution_error

                    elif action_on_error == SequenceErrorActionType.RESUME:
                        self._tracker.set_result(text="WARNING", status_code=2)
                        warning_msg = f"Ignored error during step {step_number + 1}: {execution_error}"
                        warnings_count = warnings_count + 1
                        self._logger.warning(warning_msg)

                if last_step_results and last_step_results.return_code == 0:
                    store_key = step.get("response_store_key")
                    if store_key and last_step_results.response:
                        self._logger.debug(f"Storing value '{last_step_results.response}' in '{store_key}'")
                        self._tool_box.store_value(key=store_key, value=last_step_results.response)

                    self._tracker.set_result(text="OK", status_code=0)

                step_number += 1

            self._tracker.set_end()
            _expand_and_print(sequence_data.get("status_post_message"))

            # Briefly delay when we had warnings during the sequence to allow the user to see
            if warnings_count > 0:
                time.sleep(2)
            return 0

        except Exception as steps_error:
            self._tracker.set_result(text="Error", status_code=1)
            print()
            status_on_error and print(status_on_error)  # Echo custom step message if set
            raise RuntimeError(f"Step {step_number + 1} failed: {steps_error}") from steps_error

        finally:
            self._running_sequence = False
            os.chdir(original_path)  # Restore original path
            self._tracker = None
