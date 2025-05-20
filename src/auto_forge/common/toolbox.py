"""
Script:         toolbox,py
Author:         AutoForge Team

Description:
    Auxiliary module defining the 'ToolBox' class, which provides utility functions
    used throughout the AutoForge library. It contains a collection of general-purpose
    methods for common tasks.
"""

import base64
import glob
import importlib.metadata
import importlib.util
import inspect
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, SupportsInt, Union
from urllib.parse import ParseResult, unquote, urlparse

import psutil
from colorama import Fore

# Retrieve our package base path from settings
from auto_forge import (
    PROJECT_BASE_PATH,
    PROJECT_SHARED_PATH,
    AddressInfoType,
    AutoForgeModuleType,
    AutoLogger,
    CoreModuleInterface,
    TerminalAnsiCodes,
)
from auto_forge.common.registry import Registry  # Runtime import to prevent circular import

AUTO_FORGE_MODULE_NAME = "ToolBox"
AUTO_FORGE_MODULE_DESCRIPTION = "General purpose support routines"
AUTO_FORGE_TEMP_PATTERN = "__AUTO_FORGE_"  # Prefix for temporary path names


class ToolBox(CoreModuleInterface):

    def _initialize(self, *_args, **_kwargs) -> None:
        """
        Initialize the 'ToolBox' class.
        """

        # Create a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._dynamic_vars_storage = {}  # Local static dictionary for managed session variables
        self._test_str: str = "test"

        # Persist this module instance in the global registry for centralized access
        registry = Registry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME,
                                 description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

    @staticmethod
    def print_bytes(byte_array: bytes, bytes_per_line: int = 16):
        """
        Prints a byte array as hex values formatted in specified number of bytes per line.
        Args:
        byte_array (bytes): The byte array to be printed.
        bytes_per_line (int, optional): Number of hex values to print per line. Default is 16.
        """
        if byte_array is None or not isinstance(byte_array, bytes):
            return

        output = []
        hex_values = [f'{byte:02x}' for byte in byte_array]
        for i in range(0, len(hex_values), bytes_per_line):
            output.append(' '.join(hex_values[i:i + bytes_per_line]))
        print("\n".join(output) + "\n")

    @staticmethod
    def set_realtime_priority(priority: int = 10):
        """
        Sets the real-time scheduling priority for the current process using the FIFO scheduling algorithm.
        Args:
            priority (int): Desired priority level. The effective priority set will be clamped to the
                            system's allowable range for real-time priorities.
        Notes:
            - Real-time priorities require elevated privileges (typically root). Running this without
              sufficient privileges will result in a PermissionError.
            - Using real-time scheduling can significantly affect system responsiveness and should be
              used judiciously.
        """
        try:
            # Get max and min real-time priority range for the scheduler FIFO
            max_priority = os.sched_get_priority_max(os.SCHED_FIFO)
            min_priority = os.sched_get_priority_min(os.SCHED_FIFO)

            # Ensure the desired priority is within the valid range
            priority = max(min(priority, max_priority), min_priority)

            # Set the scheduler for the current process
            pid = os.getpid()
            param = os.sched_param(priority)
            os.sched_setscheduler(pid, os.SCHED_FIFO, param)

        # Propagate
        except Exception as exception:
            raise exception

    @staticmethod
    def looks_like_unix_path(might_be_path: str) -> bool:
        """
        Determines if a string looks like a valid Unix-style filesystem path.
        Args:
            might_be_path (str): The string to check.
        Returns:
            bool: True if the string looks like a Unix directory path, False otherwise.
        """
        if not might_be_path or not isinstance(might_be_path, str):
            return False

        # Reject strings with obvious syntax errors (e.g., unmatched or unexpected characters)
        if '<' in might_be_path or '>' in might_be_path:
            return False

        if re.search(r'[<>:"|?*]', might_be_path):  # extra caution — reserved or risky characters
            return False

        path = Path(might_be_path)

        # Reject if it's clearly a file (based on extension)
        if path.suffix:
            return False

        # Require at least one slash and non-empty parts
        parts = might_be_path.strip('/').split('/')
        if len(parts) < 1 or any(not part for part in parts):
            return False

        return '/' in might_be_path

    def store_value(self, key: Any, value: Any) -> bool:
        """
        Provide a simple interface to store values into a RAM-based storage.
        The storage should enforce unique keys (case-insensitive)
        and non-empty or None values.
        Args:
            key: The key under which the value will be stored. Case-insensitive.
            value: The value to store. Must not be empty or None.

        Returns:
            bool: True if the value was stored successfully, False otherwise.
        """
        if value is None or key is None:
            return False

        # Normalize the key to ensure case-insensitivity
        normalized_key = key.lower()

        # Check if we have something to do
        if normalized_key in self._dynamic_vars_storage and self._dynamic_vars_storage[normalized_key] == value:
            self._logger.warning(f"Key '{normalized_key}' is already stored and has the same value '{value}'")
            return True

        # Store the value
        self._dynamic_vars_storage[normalized_key] = value
        return True

    def load_value(self, key: Any, default_value: Any = None) -> Any:
        """
        Provide a simple interface to load values from a RAM-based storage.
        Handles a special format 'load_value:<key>' to allow recursive fetching of the value.

        Args:
            key (str): The key of the value to load, or a special formatted string 'load_value:<key>'.
                       The key is case-insensitive.
                       second case: normal dictionary fetch 'tcp:162'
            default_value (str, Optional): If specified, it will be returned when we could not read the required key.

        Returns:
            Any: The value associated with the key, or an empty string if the key does not exist.
        """
        value = default_value

        if key is None:
            return value

        # Check if the key includes a special prefix indicating a command
        if isinstance(key, str):
            if key.startswith("load_value:"):
                # Extract the actual key after the prefix
                actual_key = key.split(":", 1)[1]
                # Normalize the actual key to ensure case-insensitivity
                key = actual_key.lower()
                # Return the value if the key exists, otherwise return an empty string
                value = self._dynamic_vars_storage.get(key, default_value)
            else:
                # Normalize the key to ensure case-insensitivity
                key = key.lower()
                # Return the value if the key exists, otherwise return an empty string
                value = self._dynamic_vars_storage.get(key, default_value)

        if value is None:
            value = key  # Probably JSON value was passed, return it
        return value

    @staticmethod
    def format_duration(seconds):
        """
        Converts a number of total seconds (which can include fractional seconds) into a human-readable
        string representing the duration in hours, minutes, seconds, and milliseconds.
        Args:
            seconds (float or int): Total duration in seconds, including fractional parts for milliseconds.
        Returns:
            str: A string formatted as 'X hours Y minutes Z seconds W milliseconds', omitting any value
                 that is zero.

        Raises:
            ValueError: If the input cannot be converted to a float.
        """
        try:
            seconds = float(seconds)
        except ValueError as value_error:
            raise ValueError(f"Invalid input: {seconds} cannot be converted to a float") from value_error

        def pluralize(time, unit):
            """ Returns a string with the unit correctly pluralized based on the time. """
            if time == 1:
                return f"{time} {unit}"
            else:
                return f"{time} {unit}s"

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds_int = int(seconds % 60)  # Get the integer part of the remaining seconds
        milliseconds = int((seconds - int(seconds)) * 1000)  # Correct calculation of milliseconds

        parts = []
        if hours:
            parts.append(pluralize(hours, "hour"))
        if minutes:
            parts.append(pluralize(minutes, "minute"))
        if seconds_int:
            parts.append(pluralize(seconds_int, "second"))
        if milliseconds:
            parts.append(pluralize(milliseconds, "millisecond"))

        return ", ".join(parts) if parts else "0 seconds"

    @staticmethod
    def find_class_property(class_name, property_name):
        """
        Determines if given properties exist in a given class name
        """
        cls = globals().get(class_name, None)
        if cls is None:
            # If the class is not in globals, check in sys.modules
            cls = getattr(sys.modules[__name__], class_name, None)

        # Check if the class exists and has the specified property
        return hasattr(cls, property_name) if cls else False

    @staticmethod
    def class_name_in_file(class_name: str, file_path: str) -> bool:
        """
        Check if the specified class name is defined in the given Python file.
        """
        try:
            with open(file_path, encoding='utf-8') as file:
                for line in file:
                    if line.startswith('class ') and class_name in line:
                        return True
        except OSError:
            return False
        return False

    @staticmethod
    def find_class_in_project(class_name: str, root_path: str = PROJECT_BASE_PATH) -> Optional[type]:
        """
        Search for a class by name in all Python files under a specified directory.
        This function dynamically loads each Python file that contains the specified class name,
        attempting to import the module and retrieve the class.

        Args:
            class_name (str): The name of the class to search for.
            root_path (str): The base directory from which to start the search.

        Returns:
            Optional[Type]: The class if found, None otherwise.
        """
        for subdir, _dirs, files in os.walk(root_path):
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(subdir, file)
                    if ToolBox.class_name_in_file(class_name,
                                                  file_path):  # Assuming this method checks the file content for the class name
                        module_name = os.path.splitext(os.path.basename(file_path))[0]
                        try:
                            # Load the module from a given file path
                            sys.path.insert(0, subdir)  # Temporarily prepend the current directory to sys.path
                            spec = importlib.util.spec_from_file_location(module_name, file_path)
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                            # Attempt to fetch the class from the module
                            if hasattr(module, class_name):
                                return getattr(module, class_name)

                        except Exception as exception:
                            raise RuntimeError(
                                f"Failed to import {module_name} from {file_path}: {exception}") from exception
                        finally:
                            # Ensure the modified path is always cleaned up
                            if subdir in sys.path:
                                sys.path.remove(subdir)

        return None

    @staticmethod
    def find_class_in_module(class_name: str, module_name: str) -> Optional[type]:
        """
        Dynamically find and return a class type by name from a given module.
        """
        if module_name:
            try:
                module = importlib.import_module(module_name)
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if name == class_name:
                        return obj
            except ImportError as import_error:
                raise ImportError(f"Module '{module_name}' not found.") from import_error
        return None

    def find_method_name(self, method_name: str, directory: Optional[str] = None) -> (
            tuple)[Optional[str], Optional[str], Optional[str]]:
        """
         Searches for a specified method within Python files in given directory and returns information about
         the method's location including its class (if applicable) and module path.

         Recursively searches through all Python files in the specified directory or, if not specified,
         in a default 'project_path'. It uses regular expressions to identify class and method definitions that match
         the given method name.

         Args:
             method_name (str): The name of the method to find.
             directory (Optional[str]): The directory to search in. If None, use a default directory 'project_path'.

         Returns:
             Tuple[Optional[str], Optional[str], Optional[str]]: A tuple containing the class name (or None if
             the method is not within a class), the method name, and the module path where the method is defined.
             If the method is not found, all tuple elements will be None.
         Notes:
             - The search is case-sensitive and matches the exact method name.
             - The method does not support overloaded methods; it returns the first match found.
             - If file access or regex processing errors occur, they are logged, and the search continues
               with the next file.
         """
        if directory is None:
            directory = PROJECT_BASE_PATH

        base_package_name = os.path.basename(directory)

        # Regular expression to match class and method definitions
        class_regex = re.compile(r'^class\s+(\w+)\s*:', re.MULTILINE)
        method_regex = re.compile(r'^\s*def\s+(' + re.escape(method_name) + r')\s*\(', re.MULTILINE)

        # Walk through all files in the given directory
        for root, _dirs, files in os.walk(directory):
            for file in files:
                if file.endswith('.py'):  # Check only Python files
                    file_path = os.path.join(root, file)
                    try:
                        # Convert a file path to a module path, safeguard against type and path issues
                        module_path = os.path.relpath(str(file_path), directory).replace(os.sep, '.')[:-3]
                        if base_package_name not in module_path:
                            module_path = f"{base_package_name}.{module_path}"
                    except Exception as path_error:
                        self._logger.debug(f"Warning processing path {file_path}: {path_error}")
                        continue

                    try:
                        with open(file_path, encoding='utf-8') as f:
                            content = f.read()
                    except Exception as open_error:
                        self._logger.debug(f"Warning could not read file {file_path}: {open_error}")
                        continue

                    current_class = None
                    last_pos = 0

                    # Iterate over all classes and methods in the file
                    try:
                        for match in class_regex.finditer(content):
                            class_start = match.start()
                            # Check methods in the previous class (or global scope if no class yet)
                            method_match = method_regex.search(content, last_pos, class_start)
                            if method_match:
                                return current_class, method_match.group(1), module_path

                            current_class = match.group(1)
                            last_pos = match.end()

                        # Check for the method in the last class or global scope after the last class
                        method_match = method_regex.search(content, last_pos)
                        if method_match:
                            return current_class, method_match.group(1), module_path
                    except Exception as search_error:
                        self._logger.debug(f"Warning processing content from {file_path}: {search_error}")
                        continue

        # Return None for class, method, and module path if not found
        return None, None, None

    @staticmethod
    def filter_kwargs_for_method(kwargs: dict[str, Any], sig: inspect.Signature) -> (
            tuple)[dict[str, Any], dict[str, Any]]:
        """
        Filters keyword arguments (`kwargs`) according to the signature of a method, and manages nested structures.

        Traverses the given `kwargs` dictionary, including nested dictionaries, and:
        - Assigns the values to `method_kwargs` if the keys match the parameters defined in the method's signature.
        - Any non-matching keys are placed in `extra_kwargs`.
        - Handles nested dictionaries by flattening their keys and checks for name conflicts across different levels.

        Parameters:
            kwargs (Dict[str, Any]): The keyword arguments to filter.
            sig (inspect.Signature): The signature of the method for which `kwargs` are being prepared.

        Returns:
            Tuple[Dict[str, Any], Dict[str, Any]]: A tuple containing two dictionaries:
                - `method_kwargs`: Keyword arguments that match the signature.
                - `extra_kwargs`: Extra keyword arguments that do not match the signature.
        """
        method_kwargs = {}
        extra_kwargs = {}
        used_keys = set()

        def _search_and_assign(current_dict, path=''):
            """
            Recursively searches and assigns values from nested dictionaries to the appropriate keyword
            argument dictionary.
            Args:
                current_dict (Dict[str, Any]): The current dictionary being traversed.
                path (str): The nested path used to build full key names that reflect the structure of the original
                `kwargs`.
            """
            for key, value in current_dict.items():
                current_path = f"{path}.{key}" if path else key  # Construct a full nested key path

                if isinstance(value, dict):
                    # Recursively search nested dictionaries
                    _search_and_assign(value, current_path)
                else:
                    # Assign values directly, handle flat structure
                    _assign_value(key, value, current_path)

        def _assign_value(key, value, full_key):
            """
            Assigns a value to the correct dictionary based on its key and checks for potential key collisions.
            Args:
                key (str): The original or the last segment of the nested key.
                value (Any): The value to be assigned.
                full_key (str): The fully constructed key path from `search_and_assign` used for collision checking.
            """
            base_key = key  # Use the last segment of the nested key if available
            if '.' in full_key:
                base_key = full_key.split('.')[-1]

            if base_key in used_keys:
                raise ValueError(
                    f"key collision detected for '{base_key}' while processing path '{full_key}")

            # Determine if the key should go to method_kwargs or extra_kwargs
            if base_key in sig.parameters:
                if base_key in method_kwargs:
                    raise ValueError(f"key collision in method_kwargs for '{base_key}'.")
                method_kwargs[base_key] = value
            else:
                if base_key in extra_kwargs:
                    raise ValueError(f"key collision in extra_kwargs for '{base_key}'.")
                extra_kwargs[base_key] = value
            used_keys.add(base_key)

        _search_and_assign(kwargs)
        return method_kwargs, extra_kwargs

    @staticmethod
    def validate_executable_path(path):
        """ Validate the executable path for existence and execution permissions. """
        if path is None:
            raise ValueError("executable path is not specified.")

        if not isinstance(path, str):
            raise ValueError("executable path must be a string.")

        expanded_path = os.path.expanduser(os.path.expandvars(path))
        expanded_path = os.path.abspath(expanded_path)  # Resolve relative paths to absolute paths

        if not os.path.isfile(expanded_path):
            raise FileNotFoundError(f"the specified executable path does not exist: {expanded_path}")

        if not os.access(expanded_path, os.X_OK):
            raise PermissionError(f"the file is not executable: {expanded_path}")

        return expanded_path

    @staticmethod
    def is_process_running(process_name: str) -> int:
        """
        Determines the number of processes with a specified name currently running on the system.
        Args:
            process_name (str): The name of the process to check. This function checks if the process_name
                                is a substring of the names of currently running processes, allowing partial matches.

        Returns:
            int: The number of matching processes running. Returns 0 if no matching processes are found.
                 Returns a negative value to indicate an error.

        Notes:
            - The function catches exceptions such as psutil.NoSuchProcess, psutil.AccessDenied,
              and psutil.ZombieProcess which may occur if a process terminates before it can be checked or if the
              process information is not accessible due to permission issues or because the process is a zombie.
        """
        count = 0
        with suppress(Exception):
            for proc in psutil.process_iter():
                if process_name.lower() in proc.name().lower():
                    count += 1
            return count

        return -1  # Indicate an error occurred

    @staticmethod
    def convert_to_int(value: Optional[Union[str, SupportsInt, float, set]] = None) -> int:
        """
        Attempts to convert an ambiguous value to an integer. The value could be of any type that is sensibly
        convertible to an integer, such as string representations of integers, floating point numbers that are
        equivalent to integers (e.g., 1.0), or actual integers.
        Args:
            value (Any): The input to convert to an integer.
        Returns:
            int: The converted value if conversion is possible.
        """
        if value is None:
            raise ValueError("input is 'None', cannot convert to integer.")

        try:
            # If it's a float and is an integer equivalent, this will work directly
            if isinstance(value, float) and value.is_integer():
                return int(value)
            # Attempt to convert from string directly
            if isinstance(value, (int, str)):
                return int(value)
        except ValueError:
            pass  # Handle conversion failure for int and str types in the except block below

            # Check for iterable cases like {1} -> single item, direct conversion
        if isinstance(value, set) and len(value) == 1:
            return int(next(iter(value)))  # Extract the single element and convert

            # If no conditions match, raise an error
        raise ValueError(f"cannot convert '{value}' to an integer.")

    @staticmethod
    def get_expanded_path(path: str, to_absolute: bool = True) -> str:
        """
        Expands environment variables and user symbols in the given path.
        Args:
            path (str): The input path string, which may contain '~' or environment variables.
            to_absolute (bool): If True (default), the path is resolved to an absolute path.
        Returns:
            str: The expanded (and optionally absolute) path.
        """
        if not path.strip():
            return ""  # do NOT expand empty string

        expanded_path = os.path.expanduser(os.path.expandvars(path))

        # Only call abspath if it's safe
        if to_absolute and not expanded_path.endswith(os.sep + '.'):
            expanded_path = os.path.abspath(expanded_path)

        return expanded_path

    @staticmethod
    def set_terminal_title(title: Optional[str] = None):
        """
        Sets the terminal title
        Args:
            title (str,Optional): The new title for the terminal.
        """
        with suppress(Exception):
            if title:
                sys.stdout.write(f"\033]0;{title}\007")
            else:
                sys.stdout.write("\033]0;\007")
            sys.stdout.flush()

    @staticmethod
    def validate_path(text: str, raise_exception: Optional[bool] = True) -> Optional[bool]:
        """
        Check whether the given text represents an existing directory.

        Args:
            text (str): The path string to check.
            raise_exception (bool, optional): If True, raises an exception when the path is invalid.
                                              Defaults to True.
        Returns:
            bool: True if the path exists and is a directory, False otherwise.
        """
        try:
            expanded_path = os.path.expanduser(os.path.expandvars(text))
            path = Path(expanded_path)
            if path.exists() and path.is_dir():
                return True
            if raise_exception:
                raise FileNotFoundError(f"path does not exist or is not a directory: {text}")
        except Exception as e:
            if raise_exception:
                raise e
        return False

    @staticmethod
    def tail(f, n):
        """
        Efficiently reads the last n lines from a file object.
        Args:
            f (file object): The file object from which to read.
            n (int): The number of lines to read from the end of the file.

        Returns:
            list: A list containing the last n lines of the file.
        """
        assert n >= 0, "n must be non-negative"
        if n == 0:
            return []

        pos, lines = n + 1, []
        while len(lines) <= n:
            try:
                f.seek(-pos, os.SEEK_END)
            except OSError:  # more general than IOError
                f.seek(0)
                lines = f.readlines()
                break
            lines = f.readlines()
            pos *= 2
        return lines[-n:]

    @staticmethod
    def get_temp_filename() -> Optional[str]:
        """
        Generates a unique temporary filename without creating a persistent file on disk.
        """
        try:
            temp_path_name = ToolBox.get_temp_pathname(create_path=True)
            fd, temp_file = tempfile.mkstemp(dir=temp_path_name, suffix=".temp.af")
            os.close(fd)  # Close the file descriptor to avoid resource leakage
            os.remove(temp_file)  # Delete the file, keeping the path
            return temp_file
        except Exception:
            raise

    @staticmethod
    def get_temp_pathname(create_path: Optional[bool] = False) -> Optional[str]:
        """
        Generates a unique temporary directory path without creating the actual directory.
        Args:
            create_path (bool, optional): If True, creates a new temporary directory path.
        """
        try:
            temp_path = tempfile.mkdtemp(prefix=AUTO_FORGE_TEMP_PATTERN)
            if not create_path:  # Typically we're only interested ony in a temporary name without creating the path
                os.rmdir(temp_path)
            return temp_path
        except Exception:
            raise

    @staticmethod
    def clear_residual_files() -> None:
        """
        Scans the system temporary directory and deletes any residual
        AutoForge-related directories (those starting with '__AUTO_FORGE_').
        """
        with suppress(Exception):
            temp_dir = tempfile.gettempdir()

            for entry in os.scandir(temp_dir):
                if entry.is_dir() and entry.name.startswith(AUTO_FORGE_TEMP_PATTERN):
                    with suppress(Exception):
                        shutil.rmtree(entry.path)

    @staticmethod
    def file_to_base64(file_name: str) -> Optional[str]:
        """
        Encode the content of a file to base64 format and return it as a string
        Parameters:
        file_name (str): The path to the file to be encoded.
        Returns:
        str: A string containing the base64 encoded content
             or exception if an error occurs during file reading or encoding.
        """
        try:
            # Read the file content in binary mode
            with open(file_name, 'rb') as file:
                file_content = file.read()

            # Encode the content to base64
            encoded_content = base64.b64encode(file_content).decode('utf-8')
            return encoded_content

        except Exception as encode_error:
            raise encode_error

    @staticmethod
    def set_cursor(visible: bool = False):
        """
        Sets the visibility of the terminal cursor using ANSI escape codes.
        Args:
        visible (bool): If True, shows the cursor. If False, hide the cursor.
        """
        if visible:
            sys.stdout.write('\033[?25h')
        else:
            sys.stdout.write('\033[?25l')
        sys.stdout.flush()

    @staticmethod
    def is_likely_under_debugger() -> bool:
        """
        Attempts to determine if the Python script is running under a debugger using multiple heuristics.
        Returns:
            bool: True if likely running under a debugger, False otherwise.
        """
        # Check for common debugger modules
        debugger_modules = ['pydevd', 'pdb']
        for module in debugger_modules:
            if module in sys.modules:
                return True

        # Check if a trace function is set (common with debugging)
        if sys.gettrace() is not None:
            return True

        # Environment variable checks (adjust as needed for your environment)
        # noinspection SpellCheckingInspection
        debug_env_vars = ['VSCODE_DEBUGGER', 'PYCHARM_DEBUG', 'PYTHONUNBUFFERED']
        for var in debug_env_vars:
            if var in os.environ:
                return True

        # Check if standard input is not a tty (weak indicator)
        if not sys.stdin.isatty():
            return True

        return False

    @staticmethod
    def is_likely_editable() -> tuple[bool, Optional[str]]:
        """
        Determines if the current running environment is within a Python virtual environment,
        which typically indicates a non-editable (production) environment. Otherwise, it suggests
        a development setup.

        Returns:
            tuple[bool, Optional[str]]: A tuple containing a boolean indicating if the current environment
            is likely in editable mode (not in a virtual environment), and the path to the project's base directory.
        """
        with suppress(Exception):
            package_path = PROJECT_BASE_PATH  # Use a global variable that indicates where the project is running from
            virtual_env_path = os.getenv('VIRTUAL_ENV')

            # Check if the package path starts with the virtual environment path if it's set
            in_virtual_env = virtual_env_path and str(package_path).startswith(virtual_env_path)

            # Determine if it's likely editable: editable if not in a virtual environment
            is_development = not in_virtual_env
            return is_development, str(package_path)

        return False, None  # Returns False and None if an exception occurs

    @staticmethod
    def unzip_file(zip_file_name: str, destination_path: Optional[str] = None) -> Optional[str]:
        """
        Unzips a zip archive into a destination directory.
        Args:
            zip_file_name (str): Path to the zip file to extract.
            destination_path (Optional[str], optional): Path where contents should be extracted.
                If None, the archive directory will be used.
        Returns:
            str: Path to the directory where files were extracted.
        """

        zip_file_name = ToolBox.get_expanded_path(zip_file_name)
        if not os.path.isfile(zip_file_name):
            raise FileNotFoundError(f"Zip file '{zip_file_name}' does not exist or is not a file.")

        if destination_path is None:
            # Use the archive path when not specified
            destination_path = os.path.dirname(zip_file_name)
        else:
            destination_path = ToolBox.get_expanded_path(destination_path)

        try:
            with zipfile.ZipFile(zip_file_name, 'r') as zip_ref:
                zip_ref.extractall(destination_path)
        except zipfile.BadZipFile as zip_error:
            raise zipfile.BadZipFile(f"Failed to extract '{zip_file_name}': {zip_error}") from zip_error
        except Exception as exception:
            raise Exception(f"Unexpected error while extracting '{zip_file_name}': {exception}") from exception

        return destination_path

    @staticmethod
    def get_address_and_port(endpoint: Optional[str]) -> Optional[AddressInfoType]:
        """
        Parses an endpoint string of the form 'host:port' and returns an AddressInfo tuple.
        Args:
            endpoint (Optional[str]): The endpoint string to parse.
        Returns:
            Optional[AddressInfoType]: A named tuple containing:
                - host (str): Hostname or IP address.
                - port (int): TCP port number.
                - is_host_name (bool): True if host is a name, False if host is an IP address.
            None if the input is invalid.
        """
        if endpoint is None or ':' not in endpoint:
            return None

        host_part, port_part = endpoint.rsplit(":", 1)

        # Validate port
        if not port_part.isdigit():
            return None

        port = int(port_part)
        if not (1 <= port <= 65535):
            return None

        # Check if host looks like an IP address
        is_ip = bool(re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", host_part))
        if is_ip:
            octets = host_part.split(".")
            if not all(0 <= int(octet) <= 255 for octet in octets):
                return None  # Invalid IP address

        # Reconstruct endpoint as a host:port string
        endpoint = f"{host_part}:{port!s}"

        # Otherwise, assume it's a hostname
        return AddressInfoType(host=host_part, port=port, endpoint=endpoint, is_host_name=not is_ip)

    @staticmethod
    def normalize_text(text: Optional[str], allow_empty: bool = False) -> str:
        """
        Normalize the input string by stripping leading and trailing whitespace.
        Args:
            text (Optional[str]): The string to be normalized.
            allow_empty (Optional[bool]): No exception to the output is an empty string

        Returns:
            str: A normalized string with no leading or trailing whitespace.
        """
        # Check for None or empty string after potential stripping
        if text is None or not isinstance(text, str):
            raise ValueError("input must be a non-empty string.")

        # Strip whitespace
        normalized_string = text.strip()
        if not allow_empty and not normalized_string:
            raise ValueError("input string cannot be empty after stripping")

        return normalized_string

    @staticmethod
    def normalize_to_github_api_url(url: str) -> Optional[str]:
        """
        Validates and normalizes a GitHub URL to its corresponding GitHub API URL.
        Args:
            url (str): The input GitHub URL, either a 'tree' URL or GitHub.com contents URL.
        Returns:
            Optional[str]: The GitHub API URL if valid and successfully converted, otherwise None.
        """
        # Quietly validate the URL structure
        with suppress(Exception):
            parsed: ParseResult = urlparse(url)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                return None

        # Already an API URL
        if 'api.github.com' in parsed.netloc:
            return url

        # Convert GitHub tree URL to API format
        match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*)", url)
        if not match:
            return url  # Nothing to convert - return original URL

        owner, repo, branch, path = match.groups()
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        return api_url

    def file_from_url(self, url: str, enforce_only_file: bool = False) -> Optional[str]:
        """
        Extracts the file or path name (last component) from a given URL.
        Args:
            url (str): The URL from which to extract the filename or path segment.
            enforce_only_file (bool): Fail if the URL does not point to a file.
        Returns:
            Optional[str]: The extracted filename or path segment, or None if extraction fails.
        """

        if enforce_only_file and self.is_url_path(url):
            # URL points to a path rather than a file name.
            return None

        with suppress(Exception):
            parsed_url = urlparse(url)
            path = parsed_url.path
            if not path:
                return None
            filename = os.path.basename(unquote(path.rstrip('/')))
            return filename

        return None

    @staticmethod
    def is_url(text: str) -> bool:
        """
        Determines if a given text is likely a URL.
        Args:
            text (str): The text to evaluate.
        Returns:
            bool: True if the text looks like a URL, False otherwise.
        """
        parsed = urlparse(text)
        return bool(parsed.scheme) and bool(parsed.netloc)

    @staticmethod
    def is_url_path(url: str) -> Optional[bool]:
        """
        Determines if a given URL likely points to a directory (path) rather than a file.
        Args:
            url (str): The URL to evaluate.
        Returns:
            Optional[bool]:
                - True if the URL likely refers to a path (directory),
                - False if it likely refers to a file,
                - None if the URL is invalid or cannot be evaluated.
        """
        with suppress(Exception):
            parsed = urlparse(url)
            path = parsed.path
            if not path:
                return None

            last_segment = os.path.basename(path.rstrip('/'))

            if not last_segment:
                return None

            # If last segment contains a dot ('.'), assume it's a file
            return '.' not in last_segment

        return None

    @staticmethod
    def is_directory_empty(path: str, raise_exception: bool = False) -> bool:
        """
        Check if the given directory is empty.
        Args:
            path (str): The directory path to check.
            raise_exception (bool): If True, raises an exception if the directory does not exist,
                                    is not a directory, or is not empty.

        Returns:
            bool: True if the directory is empty, False otherwise.
        """
        if not os.path.exists(path):
            if raise_exception:
                raise FileNotFoundError(f"'{path}' does not exist: {path}")
            return False

        if not os.path.isdir(path):
            if raise_exception:
                raise ValueError(f"'{path}' is not a directory")
            return False

        # List the contents of the directory
        if os.listdir(path):
            if raise_exception:
                raise RuntimeError(f"'{path}' path not empty")
            return False

        return True

    @staticmethod
    def strip_ansi(text: str, bare_text: bool = False) -> str:
        """
        Removes ANSI escape sequences and broken hyperlink wrappers,
        but retains useful text such as GCC warning flags.

        Args:
            text (str): The input string possibly containing ANSI and broken links.
            bare_text (bool): If True, reduce to printable ASCII only.

        Returns:
            str: Cleaned text, preserving meaningful info like [-W...]
        """

        if not isinstance(text, str):
            return text

        # Strip and see if we got anything to process
        text = text.strip()
        if not text:
            return text

        # Strip ANSI escape sequences (CSI, OSC, etc.)
        ansi_escape = re.compile(r'''
            \x1B
            (?:
                [@-Z\\-_] |
                \[ [0-?]* [ -/]* [@-~]
            )
        ''', re.VERBOSE)
        text = ansi_escape.sub('', text)

        # GCC junk and do everything possible to get a clear human readable string.
        text = text.replace("8;;", "")
        text = text.replace("->", "").strip()

        # Remove GCC Source code references
        text = re.sub(r'^\|\s*[~^]+\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\s*\|.*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s*\|', '', text)

        if not text:
            return text

        # Extract and preserve [-W...warning...] from broken [https://...] blocks
        def recover_warning_flag(match):
            url = match.group(1)
            warning_match = re.search(r'(-W[\w\-]+)', url)
            return f"[{warning_match.group(1)}]" if warning_match else ""

        text = re.sub(r'\[(https?://[^]]+)]', recover_warning_flag, text).strip()

        # Step 4: Optionally reduce to printable ASCII
        if bare_text:
            allowed = set(string.ascii_letters + string.digits + string.punctuation + ' \t\n')
            text = ''.join(c for c in text if c in allowed)

        return text.strip()

    @staticmethod
    def print_logo(banner_file: Optional[str] = None, clear_screen: bool = False,
                   terminal_title: Optional[str] = None) -> None:
        """
        Displays an ASCII logo from a file with alternating colors per line.
        Args:
            banner_file (str): Path to an ASCI art banner text file.
            clear_screen (bool): Whether to clear the screen before printing.
            terminal_title (Optional[str]): Optional text to use as the terminal title.
        """

        # Demo ASCII Art file
        demo_file = str(PROJECT_SHARED_PATH / "banner.txt")

        # Use to the demo file if not provided
        if not banner_file or not os.path.isfile(banner_file):
            banner_file = demo_file
            if not os.path.isfile(banner_file):
                return None

        # Clear screen and move cursor to top-left
        if clear_screen:
            sys.stdout.write(TerminalAnsiCodes.CLS_SB)
        sys.stdout.write('\n')

        with open(banner_file, encoding='utf-8') as f:
            for i, line in enumerate(f):
                color = Fore.LIGHTBLACK_EX if i % 2 == 0 else Fore.LIGHTWHITE_EX
                sys.stdout.write(f"{color}{line}")

        sys.stdout.write('\n')  # Final newlines
        sys.stdout.flush()

        if terminal_title is not None:
            ToolBox.set_terminal_title(terminal_title)
        return None

    @staticmethod
    def get_module_docstring(python_module_type: Optional[ModuleType] = None) -> Optional[str]:
        """
        Returns the 'Description:' section of a module docstring, if present.
        If no 'Description:' section exists, returns the full module docstring.
        If no docstring exists, returns None.
        Args:
            python_module_type (Optional[ModuleType]): The module to extract the docstring from.
                                           Defaults to the calling module.
        Returns:
            Optional[str]: The extracted description or full docstring.
        """
        if python_module_type is None:
            python_module_type = sys.modules[__name__]

        doc = python_module_type.__doc__
        if not doc:
            return None

        # Normalize indentation
        doc = textwrap.dedent(doc).strip()
        # Match the Description section (including indented lines until next heading or EOF)
        match = re.search(
            r'^Description:\s*\n((?:\s{2,}.*\n?)+)',
            doc,
            re.IGNORECASE | re.MULTILINE
        )

        if match:
            description = match.group(1).strip()
            return description

        return doc

    @staticmethod
    def has_method(instance: object, method_name: str) -> bool:
        """
        Checks if the given class instance provides a method with the given name.
        Args:
            instance (object): The class instance to check.
            method_name (str): The method name to look for.

        Returns:
            bool: True if the method exists and is callable, False otherwise.
        """
        is_callable: bool = False
        with suppress(Exception):
            is_callable = callable(getattr(instance, method_name, None))

        return is_callable

    @staticmethod
    def get_terminal_width(default_width: Optional[int] = 100) -> int:
        """
        Attempts to detect the terminal width.
        Args:
            default_width (Optional[int]): Width to return if detection fails (defaults to 80).
        Returns:
            int: Detected terminal width or `default_width` if detection fails.
        """
        width = None
        with suppress(Exception):
            width = shutil.get_terminal_size().columns

        return width if width is not None else default_width

    def flatten_text(self, text: str, default_text: Optional[str] = None) -> str:
        """
        Flattens a block of text into a cleaned, single-line form.
        Actions:
            Remove ANSI sequences.
            Convert carriage returns '\r' to line feeds '\n'.
            Replace single or multiple '\n' with a dot '.'.
            Collapse multiple consecutive dots into a single dot.
            Capitalize the first letter of each sentence.
            Preserve URLs and email addresses without altering their internal dots.
        Args:
            text (str): The input text to flatten.
            default_text (Optional[str]): Text to return if the result is empty or None.
        Returns:
            str: The flattened and formatted text.
        """
        cleared_text = "" if text is None else self.strip_ansi(text).strip()

        cleared_text = cleared_text.replace('\r', '\n')

        # Protect URLs and emails
        url_pattern = r'\b(?:https?|ftp)://[^\s]+'
        email_pattern = r'\b[\w\.-]+@[\w\.-]+\.\w+\b'

        protected = {}

        def protect(match):
            protected_token = f"__PROTECTED_{len(protected)}__"
            protected[protected_token] = match.group(0)
            return protected_token

        cleared_text = re.sub(url_pattern, protect, cleared_text)
        cleared_text = re.sub(email_pattern, protect, cleared_text)

        # Work safely
        cleared_text = re.sub(r'\n+', '.', cleared_text)
        cleared_text = re.sub(r'\.{2,}', '.', cleared_text)

        cleared_text = cleared_text.strip('.').strip()

        if not cleared_text:
            return default_text if default_text is not None else ""

        # --- Capitalize sentences, but skip inside __PROTECTED__ blocks ---
        parts = re.split(r'(__PROTECTED_\d+__)', cleared_text)  # split into normal / protected pieces

        result = []
        capitalize_next = True

        for part in parts:
            if part.startswith("__PROTECTED_") and part.endswith("__"):
                # Protected part - don't touch
                result.append(part)
                capitalize_next = False
            else:
                new_part = []
                for c in part:
                    if capitalize_next and c.isalpha():
                        new_part.append(c.upper())
                        capitalize_next = False
                    else:
                        new_part.append(c)

                    if c in '.!?':
                        capitalize_next = True
                    elif c.strip():
                        capitalize_next = False

                result.append(''.join(new_part))

        flattened_text = ''.join(result).strip()

        # Restore URLs and emails
        for token, original in protected.items():
            flattened_text = flattened_text.replace(token, original)

        # Now final cleaning phase:
        # Collapse any remaining multiple dots
        flattened_text = re.sub(r'\.{2,}', '.', flattened_text)

        # Collapse multiple spaces into a single space
        flattened_text = re.sub(r'\s{2,}', ' ', flattened_text)

        # Ensure a single final dot
        flattened_text = flattened_text.strip()
        if flattened_text and not flattened_text.endswith('.'):
            flattened_text += '.'

        return flattened_text

    @staticmethod
    def cp(pattern: str, dest_dir: str):
        """
        Copies files matching a wildcard pattern to the destination directory.\
        If the destination directory does not exist, it will be created.
        Metadata such as timestamps and permissions are preserved.

        Args:
            pattern (str): Wildcard pattern (e.g. 'a/*.txt', 'a/*.*').
            dest_dir (str): Target directory to copy files into.
        """
        # Expand the pattern into a list of matching files
        matched_files = glob.glob(pattern)
        if not matched_files:
            raise FileNotFoundError(f"no files match pattern: {pattern}")

        os.makedirs(dest_dir, exist_ok=True)

        for src_file in matched_files:
            if os.path.isfile(src_file):
                base_name = os.path.basename(src_file)
                dst_path = os.path.join(dest_dir, base_name)
                shutil.copy2(src_file, dst_path)

    def get_man_description(self, command: str) -> Optional[str]:
        """
        Retrieve the first paragraph from the DESCRIPTION section of a man page.
        TInternally we runs `man <command>` through `col -bx` to clean formatting, extracts
        the DESCRIPTION section, and returns the first paragraph. If no DESCRIPTION
        section is found or an error occurs, None is returned.

        Args:
            command (str): The name of the command to query.
        Returns:
            Optional[str]: The first paragraph of the DESCRIPTION section, or None if unavailable.
        """
        with suppress(Exception):
            # Run `man <command>` and clean formatting
            man_proc = subprocess.run(
                ["man", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )

            # Remove overs trike formatting using `col -bx`
            col_proc = subprocess.run(
                ["col", "-bx"],
                input=man_proc.stdout,
                stdout=subprocess.PIPE,
                text=True
            )
            man_text = col_proc.stdout

            # Find the DESCRIPTION section
            desc_match = re.search(r'\nDESCRIPTION\n(.*?)(\n\n|\Z)', man_text, re.DOTALL)
            if desc_match:
                # Extract the first paragraph (up to first blank line)
                description = desc_match.group(1).strip()
                first_paragraph = description.split("\n\n", 1)[0].strip()
                return self.flatten_text(text=first_paragraph)

        return None

    @staticmethod
    def is_another_autoforge_running() -> bool:
        """
            Checks if another instance of AutoForge is currently running on the system.
            Returns:
                bool: True if another AutoForge process is detected, False otherwise.
            """
        current_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
            try:
                if proc.pid == current_pid:
                    continue
                cmdline = proc.info["cmdline"]
                if not cmdline:
                    continue
                # Match typical AutoForge entry call
                if "autoforge" in cmdline[0] or any("autoforge" in arg for arg in cmdline[1:]):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
