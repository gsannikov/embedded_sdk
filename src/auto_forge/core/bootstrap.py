#!/usr/bin/env python3
"""

Script:       bootstrap.py
Version:      1.0.0

SDK initialization toolbox.

"""
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
from enum import Enum
from typing import Optional, Tuple, Union
from urllib.parse import urlparse, unquote


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


class EnvCreator:

    def __init__(self, base_path: str, start_fresh: bool = False):
        """
        Initialize the environment creator with a base path. Expand environment variables and user shortcuts,
        resolve the path, and optionally clear it if start_fresh is True.
        """

        self._py_venv_path: Optional[str] = None
        self._package_manager: Optional[str] = None
        self._default_execution_time: float = 60.0  # Time allowed for executed shell command

        # Determine which package manager is available on the system.
        if shutil.which("apt"):
            self._package_manager = "apt"
        elif shutil.which("dnf"):
            self._package_manager = "dnf"

        # Get the system type (e.g., 'Linux', 'Windows', 'Darwin')
        self._system_type = platform.system().lower()
        self._is_wsl = True if "wsl" in platform.release().lower() else False
        if self._system_type == "linux":
            self._linux_distro, self._linux_version = self._get_linux_distro()

        # Expand environment variables and user home shortcuts in the path
        expanded_path = self._smart_expand(input_string=base_path)

        # Resolve the full path (absolute path)
        self._base_path = os.path.abspath(expanded_path)

        # Safeguard against deleting important directories
        if start_fresh:
            self.path_erase(path=self._base_path, allow_non_empty=True)
            # Make sure the base path exisit
            os.makedirs(self._base_path, exist_ok=True)

    @staticmethod
    def _log_in_place(log_line: str):
        """
        Log text to the terminal, updating the same line.
        Args:
            log_line (str): The text to log.
        """
        log_line = log_line.strip()
        if log_line:
            sys.stdout.write('\x1b[K')  # Move to the beginning and clear the line.
            sys.stdout.write(f"{log_line}\r")
            sys.stdout.flush()

    @staticmethod
    def _normalize_text(input_string: Optional[str], allow_empty: bool = False) -> str:
        """
        Normalize the input string by stripping leading and trailing whitespace.
        Args:
            input_string (Optional[str]): The string to be normalized.
            allow_empty (Optional[bool]): No exception of the output is an empty string

        Returns:
            str: A normalized string with no leading or trailing whitespace.
        """
        # Check for None or empty string after potential stripping
        if input_string is None or not isinstance(input_string, str):
            raise ValueError("Input must be a non-empty string.")

        # Strip whitespace
        normalized_string = input_string.strip()
        if not allow_empty and not normalized_string:
            raise ValueError("Input string cannot be empty after stripping")

        return normalized_string

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
    def _extract_package_version(package_info: str) -> Optional[str]:
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

    def _env_append_sys_path(self, path: str):
        """
        Append a directory to the system's PATH environment variable.
        Args:
            path (str): The directory path to append to PATH.
        """
        path = self._smart_expand(path)
        # Get the current PATH environment variable
        current_path = os.environ.get('PATH', '')

        # Append the directory to the PATH
        new_path = current_path + os.pathsep + path

        # Set the new PATH in the environment
        os.environ['PATH'] = new_path

    @staticmethod
    def _smart_expand(input_string: str) -> str:
        """
        Expand environment variables and user shortcuts in the given path.
        This version ignores command substitution patterns like $(...).
        Args:
            input_string (str): The input string that may contain environment variables and user shortcuts.

        Returns:
            str: The fully expanded path, ignoring special bash constructs.
        """
        # First expand user tilde
        path_with_user = os.path.expanduser(input_string)

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

        return restored_path

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

            return_value, command_response = self.shell_execute(command=command)
            if return_value != 0 or search_pattern not in command_response:
                raise EnvironmentError(f"system package '{package_name}' not validated using {self._package_manager}")

        # Propagate the exception
        except Exception:
            raise

    def shell_execute(self, command: str, args: str = "",
                      timeout: Optional[float] = None,
                      shell: bool = True,
                      sudo: bool = False,
                      expected_return_code: int = 0,
                      cwd: Optional[str] = None,
                      token: Optional[str] = None,
                      verbose: bool = False) -> Optional[Tuple[int, Optional[str]]]:
        """
        Executes a shell command with specified arguments and configuration settings.
        Args:
            command (str): The base command to execute (e.g., 'ls', 'ping').
            args (str): Additional arguments to pass to the command.
            timeout (Optional[float]): The maximum time in seconds to allow the command to run, 0 for no timeout.
            shell (bool): If True, the command will be executed in a shell environment.
            sudo (bool): If True, the command will be executed with superuser privileges. Defaults to False.
            expected_return_code (int): The expected exit code of the command. A deviation from this
                                        result will be considered an error.
            cwd (Optional[str]): The directory from which the process should be executed.
            token (Optional[str]): A token to search for in the command output.
            verbose (bool): If True, the command will be executed in verbose mode. Defaults to False.

        Returns:
            Tuple[int, Optional[str]]: The exit code of the command and its output.
        """
        full_command = f"{'sudo ' if sudo else ''}{command} {args}".strip()  # Create a single string
        full_command = self._smart_expand(input_string=full_command)  # Expand as needed
        base_command = os.path.basename(command)

        # Set default timeout when not provided
        if timeout is None:
            timeout = self._default_execution_time

        if not shell:
            # When not using shell we have to use list for the arguments rather than string
            full_command = shlex.split(full_command)

        # Validate working path if specified
        if cwd is not None and not os.path.exists(cwd):
            raise RuntimeError(f"specified work path '{cwd}' does not exist")

        process = subprocess.Popen(full_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, shell=shell, cwd=cwd, bufsize=0)
        try:

            output_line = bytearray()
            command_response: str = ""

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
                            # In verbose mode, echo command output on the same line for immediate feedback.
                            if verbose:
                                self._log_in_place(complete_line)

                # Handle execution timeout
                if timeout > 0 and (time.time() - start_time > timeout):
                    process.kill()
                    raise TimeoutError(f"'{command}' timed out after {timeout} seconds")

            process.wait()

            command_response = self._normalize_text(input_string=command_response, allow_empty=True)
            return_code = process.returncode

            if token and command_response and token not in command_response:
                raise ValueError(f"token '{token}' not found in response")

            if expected_return_code is not None and return_code != expected_return_code:
                raise ValueError(
                    f"'{base_command}' failed with return code {return_code}: {command_response}")

            return return_code, command_response

        except subprocess.TimeoutExpired:
            process.kill()
            raise

    def validate_prerequisite(self,
                              validation_method: ValidationMethod,
                              command: str,
                              command_args: Optional[str] = None,
                              expected_return_code: int = 0,
                              expected_response: Optional[str] = None,
                              allow_greater_revision: bool = False):
        """
        Validates that a system-level prerequisite is met using a specified method.
        Args:
            validation_method (ValidationMethod): The type of validation (EXECUTE_PROCESS ,READ_FILE and SYS_PACKAGE).
            command (str): For EXECUTE_PROCESS: the command to run.
                           For READ_FILE: a string in the form "<path>:<line_number>:<optional_line_count>".
                           For SYS_PACKAGE: the command variable is treated as the system package to be validated.
            command_args (Optional[str]): Arguments to pass to the command (EXECUTE_PROCESS only).
            expected_return_code (int): The expected exit code from the command.
            expected_response (Optional[str]): Expected content in output (for EXECUTE_PROCESS)
                or file content (for READ_FILE).
            allow_greater_revision (bool): If True, allows actual version to be greater than expected version.

        Returns:
            None, raising exception on error.
        """
        try:

            # Execute a process and check its response
            if validation_method == ValidationMethod.EXECUTE_PROCESS:
                return_code, output = self.shell_execute(
                    command=command,
                    args=command_args or "",
                    expected_return_code=expected_return_code
                )

                if expected_response:
                    if output is None:
                        raise RuntimeError(
                            f"'{command}' returned no output while expecting '{expected_response}'")

                    if allow_greater_revision:
                        actual_version = self._extract_decimal(input_string=output)
                        expected_version = self._extract_decimal(input_string=expected_response)
                        if actual_version < expected_version:
                            raise Exception(
                                f"required version is {expected_version} or higher, found {actual_version}")
                    else:
                        if expected_response.lower() not in output.lower():
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

        except Exception as validation_error:
            raise RuntimeError(f"error {validation_error}")

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
            expanded_path = self._smart_expand(input_string=path)
            normalized_path = os.path.abspath(os.path.normpath(expanded_path))

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
                path = os.path.join(self._base_path, path)
            full_path = os.path.expanduser(os.path.expandvars(path))

            # Delete safely
            if erase_if_exist and os.path.exists(full_path):
                self.path_erase(path=full_path)

            # Create the directory
            os.makedirs(full_path, exist_ok=True)
            return full_path  # Return the successfully created path

        except Exception as path_create_error:
            raise Exception(f"could not create '{full_path}': {str(path_create_error)}")

    def py_venv_create(self, path: str, python_version: str = "python3",
                       python_command_path: Optional[str] = None):
        """
        Initialize a Python virtual environment using a specified Python interpreter.
        Args:
            path (str): The directory path where the virtual environment will be created.
            python_version (str): The Python interpreter to use (e.g., 'python', 'python3').
            python_command_path (Optional[str]): Optional explicit path to the Python binary.

        Returns:
            None, raising exception on error.
        """
        try:
            full_py_venv_path = self.path_create(path, erase_if_exist=True, project_path=True)
            python_command = python_command_path or python_version
            command_arguments = f"-m venv {full_py_venv_path}"
            return_value, _ = self.shell_execute(command=python_command, args=command_arguments)
            if return_value == 0:
                self._py_venv_path = full_py_venv_path

        except Exception as py_venv_error:
            raise Exception(f"could not create virtual environment in '{path}' {str(py_venv_error)}")

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
            self.shell_execute(command=python_executable, args=command_arguments)

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
            self.shell_execute(command=python_executable, args=command_arguments)

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
            return_value, command_response = (
                self.shell_execute(command=python_executable, args=command_arguments))

            if return_value == 0 and command_response is not None:
                # Attempt to extract the version out of the text
                package_version = self._extract_package_version(command_response)
                return package_version

            raise Exception(f"could not read pip package '{package_name}' version, status: {str(return_value)}")

        # Propagate the exception
        except Exception:
            raise

    def git_clone_repo(self, repo_url: str,
                       dest_repo_path: str,
                       timeout: float = 0,
                       clear_destination_path: bool = True,
                       verbose: bool = False):
        """
        Clones a Git repository from a specified URL into a specified destination directory.
        Args:
            repo_url (str): The URL of the Git repository to clone.
            dest_repo_path (str): The local file system path where the repository should be cloned.
            timeout (float): The maximum time in seconds to allow the git command to run.
                A timeout of 0 indicates no timeout. Default is 0.
            clear_destination_path (bool): A flag to specify whether to clear the destination directory if it
                already exists. Default is True.
            verbose (bool): If True, the command will be executed in verbose mode. Defaults to False.

        Returns:
             None, raising exception on error.
        """
        try:
            # Normalize inputs
            repo_url = self._normalize_text(repo_url)
            dest_repo_path = self._normalize_text(dest_repo_path)

            # Normalize and prepare the destination path
            expanded_path = self._smart_expand(input_string=dest_repo_path)
            dest_repo_path = os.path.abspath(os.path.normpath(expanded_path))

            # Optionally clear the destination path
            self.path_erase(path=dest_repo_path, allow_non_empty=clear_destination_path)

            # Construct and execute the git clone command
            command_arguments = f"clone --progress {repo_url} {dest_repo_path}"
            self.shell_execute(command="git", args=command_arguments,
                               timeout=timeout, verbose=verbose)

        except Exception as py_git_error:
            raise Exception(f"git operation failure {str(py_git_error)}")

    def git_checkout_revision(self, repo_path: str, revision: str,
                              timeout: float = 0,
                              pull_latest: bool = True,
                              verbose: bool = False):
        """
        Checks out a specific revision in a Git repository.
        Args:
            repo_path (str): The local file system path to the Git repository.
            revision (str): The branch name, tag, or commit hash to checkout.
            timeout (float): The maximum time in seconds to allow the git command to run.
                A timeout of 0 indicates no timeout. Default is 0.
            pull_latest (bool): Whether to perform a git pull to update the repository with the latest changes from
                the remote before checking out.
            verbose (bool): If True, the command will be executed in verbose mode. Defaults to False.

        Returns:
            None, raising exception on error.
        """
        try:
            # Validate and prepare the repository path
            repo_path = self._normalize_text(repo_path)
            repo_path = os.path.abspath(os.path.normpath(repo_path))

            if not os.path.exists(repo_path):
                raise FileNotFoundError(f"repo path '{repo_path}' does not exist")

            if pull_latest:
                # Perform a git pull to update the repository
                pull_command = "pull"
                self.shell_execute(command="git", args=pull_command,
                                   cwd=repo_path, timeout=timeout, verbose=verbose)

            # Construct and execute the git checkout command
            command_arguments = f"checkout {revision}"
            self.shell_execute(command="git", args=command_arguments,
                               cwd=repo_path, timeout=timeout, verbose=verbose)

        except Exception as py_git_error:
            raise Exception(f"git operation failure {str(py_git_error)}")

    def download_file(self, url: str, output_name: str,
                      delete_local: bool = False,
                      proxy: Optional[str] = None,
                      token: Optional[str] = None,
                      timeout: Optional[float] = None,
                      extra_headers: Optional[dict] = None,
                      verbose: bool = False):
        """
        Downloads a file from a specified URL to a specified local path, with optional authentication, proxy support,
        and additional HTTP headers. When verbosity is on the download progress is shown.
        Args:
            url (str): The URL from which to download the file.
            output_name (str): The local path where the downloaded file should be saved.
            delete_local (bool): Delete local copy of the file if exists.
            proxy (Optional[str]): The proxy server URL to use for the download.
            token (Optional[str]): An authorization token for accessing the file.
            timeout (Optional[float]): The timeout for the download operation, in seconds.
            extra_headers (Optional[dict]): Additional headers to include in the download request.
            verbose (bool): If True, the command will be executed in verbose mode. Defaults to False.

        Returns:
            None, raising exception on error.
        """
        remote_file: Optional[str] = None

        try:
            # Normalize URL and output name
            url = self._normalize_text(url)
            remote_file = self._extract_filename_from_url(url=url)
            output_name = self._normalize_text(input_string=output_name)

            # Expand and resolve the output path
            expanded_path = self._smart_expand(input_string=output_name)
            output_name = os.path.abspath(os.path.normpath(expanded_path))

            if os.path.exists(output_name):
                if not delete_local:
                    raise FileExistsError(f"destination file '{os.path.basename(output_name)}' already exists")
                else:
                    os.remove(output_name)

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

                with open(output_name, 'wb') as out_file:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded_size += len(chunk)

                        if verbose:
                            progress_percentage = (downloaded_size / total_size) * 100
                            self._log_in_place(f"{progress_percentage:.2f}%")

        except Exception as download_error:
            raise RuntimeError(f"could not download '{remote_file or url}', {download_error}")


def bootstrap_main() -> int:
    result: int = 1  # Default to internal error

    os.environ["USERSPACE_BASE_PATH"] = "/home/emichael/projects/userspace_sdk"
    # Set proxy environment variables
    # noinspection HttpUrlsUsage
    os.environ['http_proxy'] = 'http://proxy-dmz.intel.com:911'
    # noinspection HttpUrlsUsage
    os.environ['https_proxy'] = 'http://proxy-dmz.intel.com:911'

    try:

        creator = EnvCreator(base_path="$USERSPACE_BASE_PATH/workspace", start_fresh=True)
        _, token = creator.shell_execute(command="$HOME/bin/dt github print-token")

        creator.download_file(token=token,
                              url="https://raw.githubusercontent.com/intel-innersource/firmware.ethernet.imcv2/refs/heads/main/scripts/wsl/imcv2_image_creator.py?token=GHSAT0AAAAAAC564XZR4RMV6HBNIHZNLUBEZ7EFVBA",
                              output_name="~/test.py", delete_local=True, verbose=True)

        creator.validate_prerequisite(validation_method=ValidationMethod.SYS_PACKAGE, command="perl")

        creator.validate_prerequisite(validation_method=ValidationMethod.EXECUTE_PROCESS,
                                      command="$HOME/.pyenv/shims/python", command_args="--version",
                                      expected_response="Python 3.8.0",
                                      allow_greater_revision=True)

        creator.validate_prerequisite(validation_method=ValidationMethod.READ_FILE,
                                      command="/etc/os-release:4:1",
                                      expected_response="24.04")

        repo_path = creator.path_create(path="userspace/fw")
        creator.path_create(path="build")
        creator.path_create(path="userspace/scripts")

        creator.git_clone_repo(
            repo_url="https://github.com/intel-innersource/firmware.ethernet.mountevans.imc.imc-userspace.git",
            dest_repo_path=repo_path, verbose=True, timeout=360)

        creator.git_checkout_revision(repo_path=repo_path, revision="dev/ditah/fix_coverity_gcmds", verbose=True)
        return 0

    except KeyboardInterrupt:
        print("Interrupted by user, shutting down..")
        return result
    except Exception as runtime_error:
        # Should produce 'friendlier' error message than the typical Python backtrace.
        exc_type, exc_obj, exc_tb = sys.exc_info()  # Get exception info
        file_name = os.path.basename(exc_tb.tb_frame.f_code.co_filename)  # Get the file where the exception occurred
        line_number = exc_tb.tb_lineno  # Get the line number where the exception occurred
        print(f"Error: {str(runtime_error).capitalize()}.\nFile: {file_name}\nLine: {line_number}\n")
        return result


if __name__ == "__main__":
    sys.exit(bootstrap_main())
