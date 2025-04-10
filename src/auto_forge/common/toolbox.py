#!/usr/bin/env python3
"""
Script:     toolbox,py
Author:     Intel AutoForge team

Description:
    A collection of general-purpose functions required by the AutoForge system.
"""
import base64
import importlib.metadata
import importlib.util
import inspect
import logging
import os
import re
import sys
import tempfile
from contextlib import suppress
from multiprocessing import Lock
from pathlib import Path
from typing import Any, Dict, Tuple, Type, Union, SupportsInt
from typing import Optional

import psutil

import auto_forge
# Retrieve our package base path from settings
from auto_forge.settings import PROJECT_BASE_PATH

AUTO_FORGE_MODULE_NAME = "ToolBox"
AUTO_FORGE_MODULE_DESCRIPTION = "General Purpose Support Routines"


class ToolBox:
    _instance = None
    _is_initialized = False
    _global_lock = Lock()

    def __new__(cls, parent: Any = None, logger_level: int = logging.INFO):
        """
        Create a new instance if one doesn't exist, or return the existing instance.
        Args:
            parent (Any, optional): The parent context or object for this queue.
            logger_level(int,Optional): specific required logging level

        Returns:
            ToolBox: The singleton instance of this class.
        """
        if cls._instance is None:
            cls._instance = super(ToolBox, cls).__new__(cls)
            cls._logger_level = logger_level
            cls.storage = {}

            # Initialize the instance variables only once
            cls._instance.parent = parent

        return cls._instance

    def __init__(self, parent: Any, logger_level: int = logging.INFO):
        """
        Initialize the class; actual initialization logic is handled in __new__.
        Args:
            parent (Any): Unused, the parent context or object for this queue.
            logger_level(int,Optional): specific required logging level.
        """
        if not self._is_initialized:
            self._logger: logging.Logger = logging.getLogger(AUTO_FORGE_MODULE_NAME)
            self._logger.setLevel(level=logger_level)
            self.parent: auto_forge.AutoForge = parent
            self._is_initialized = True

    def print_byte_array(self, byte_array, bytes_per_line=16):
        """
        Prints a byte array as hex values formatted in specified number of bytes per line.
        Args:
        byte_array (bytes): The byte array to be printed.
        bytes_per_line (int, optional): Number of hex values to print per line. Default is 16.
        """
        if byte_array is None:
            return

        with self._global_lock:
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
    def looks_like_path(might_be_path: str) -> bool:
        """
        Determines if a given string looks like a filesystem path.
        Using regular expressions to check for patterns that are typical in filesystem paths.
        It considers a string as a path if it includes directory separators that indicate a hierarchy,
        or is formatted like a full path with a drive letter on Windows systems.

        Additionally, it excludes common HTML patterns to prevent misidentification.

        Args:
            might_be_path (str): The string to check.

        Returns:
            bool: True if the string looks like a path, False otherwise.
        """
        # Exclude common HTML tag patterns to avoid misidentifying HTML as paths
        if re.search(r"<\s*[a-zA-Z]+.*?>", might_be_path):
            return False

        # Check for directory separators or Windows drive letters
        unix_path_pattern = r".+/.+"
        windows_path_pattern = r"(?:[a-zA-Z]:\\).+"
        if re.match(unix_path_pattern, might_be_path) or re.match(windows_path_pattern, might_be_path):
            return True

        # Alternatively, check if the path parses into multiple parts indicating a hierarchy
        path = Path(might_be_path)
        if len(path.parts) > 1:
            return True
        return False

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
        if normalized_key in self.storage and self.storage[normalized_key] == value:
            self._logger.warning(f"Key '{normalized_key}' is already stored and has the same value '{value}'")
            return True

        # Store the value
        self.storage[normalized_key] = value
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
                value = self.storage.get(key, default_value)
            else:
                # Normalize the key to ensure case-insensitivity
                key = key.lower()
                # Return the value if the key exists, otherwise return an empty string
                value = self.storage.get(key, default_value)

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
        except ValueError:
            raise ValueError(f"Invalid input: {seconds} cannot be converted to a float")

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
            with open(file_path, 'r', encoding='utf-8') as file:
                for line in file:
                    if line.startswith('class ') and class_name in line:
                        return True
        except IOError:
            return False
        return False

    @staticmethod
    def find_class_in_project(class_name: str, root_path: str = PROJECT_BASE_PATH) -> Optional[Type]:
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
        for subdir, dirs, files in os.walk(root_path):
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
                            raise RuntimeError(f"Failed to import {module_name} from {file_path}: {exception}")
                        finally:
                            # Ensure the modified path is always cleaned up
                            if subdir in sys.path:
                                sys.path.remove(subdir)

        return None

    @staticmethod
    def find_class_in_module(class_name: str, module_name: str) -> Optional[Type]:
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

    def find_method_name(self, method_name: str, directory: str = None) -> (
            Tuple)[Optional[str], Optional[str], Optional[str]]:
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
        for root, dirs, files in os.walk(directory):
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
                        with open(file_path, 'r', encoding='utf-8') as f:
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
    def filter_kwargs_for_method(kwargs: Dict[str, Any], sig: inspect.Signature) -> (
            Tuple)[Dict[str, Any], Dict[str, Any]]:
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

        def search_and_assign(current_dict, path=''):
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
                    search_and_assign(value, current_path)
                else:
                    # Assign values directly, handle flat structure
                    assign_value(key, value, current_path)

        def assign_value(key, value, full_key):
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

        search_and_assign(kwargs)
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
                sys.stdout.write(f"\033]0;\007")
            sys.stdout.flush()

    @staticmethod
    def validate_path(path: str) -> Optional[str]:
        """
        Validates a provided path, expands it, and checks if it exists.
        Args:
            path (str): The input path to validate.
        Returns:
            Optional[str]: The absolute path if valid, None otherwise.
        """
        path = os.path.expanduser(os.path.expandvars(path))
        if ToolBox.looks_like_path(path):
            path = os.path.abspath(path)  # Resolve relative paths to absolute paths

        # Check if the path exists
        if os.path.exists(path):
            return path
        else:
            return None

    @staticmethod
    def tail(f, n):
        """
        Efficiently reads the last n lines from a file object.
        Parameters:
            f (file object): The file object from which to read.
            n (int): The number of lines to read from the end of the file.
        Returns:
            list: A list containing the last n lines of the file.
        """
        assert n >= 0  # Ensure that n is non-negative
        pos, lines = n + 1, []
        while len(lines) <= n:
            try:
                f.seek(-pos, os.SEEK_END)
            except IOError:  # Handle the case where pos is greater than the file size
                f.seek(0)
                break
            finally:
                lines = list(f)
            pos *= 2  # Increase the seek position to move further back in the file
        return lines[-n:]

    @staticmethod
    def get_temp_filename():
        # Create a temporary file and immediately close it
        fd, temp_path = tempfile.mkstemp()
        os.close(fd)  # Close the file descriptor to avoid resource leakage

        # Optionally, delete the file if you just need the name
        os.remove(temp_path)

        return temp_path

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
        debug_env_vars = ['VSCODE_DEBUGGER', 'PYCHARM_DEBUG', 'PYTHONUNBUFFERED']
        for var in debug_env_vars:
            if var in os.environ:
                return True

        # Check if standard input is not a tty (weak indicator)
        if not sys.stdin.isatty():
            return True

        return False

    @staticmethod
    def is_likely_editable() -> Tuple[bool, Optional[str]]:
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
    def normalize_text(text: Optional[str], allow_empty: bool = False) -> str:
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
    def is_empty_directory(path: str, raise_exception: bool = False) -> bool:
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
