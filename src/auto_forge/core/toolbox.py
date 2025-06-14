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
import gzip
import importlib.metadata
import importlib.util
import inspect
import json
import lzma
import math
import os
import random
import re
import shutil
import string
import subprocess
import sys
import tarfile
import tempfile
import termios
import textwrap
import threading
import zipfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, SupportsInt, Union, Callable
from urllib.parse import ParseResult, unquote, urlparse

# Third-party
import psutil

# AutoForge imports
from auto_forge import (
    AddressInfoType, AutoForgeModuleType, CoreJSONCProcessor,
    CoreModuleInterface, CoreRegistry, CoreVariablesProtocol, MethodLocationType,
    PROJECT_BASE_PATH, PROJECT_HELP_PATH, PROJECT_SHARED_PATH,
    PROJECT_TEMP_PREFIX, PROJECT_VIEWERS_PATH, XYType
)

AUTO_FORGE_MODULE_NAME = "ToolBox"
AUTO_FORGE_MODULE_DESCRIPTION = "General purpose support routines"


class CoreToolBox(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared earlier in `__init__()`
        See 'CoreModuleInterface' usage.
        """
        self._ansi_codes: Optional[dict[str, str]] = None
        super().__init__(*args, **kwargs)

    def _initialize(self, *_args, **_kwargs) -> None:
        """
        Initialize the 'ToolBox' class.
        """

        self._dynamic_vars_storage = {}  # Local static dictionary for managed session variables

        # Persist this module instance in the global registry for centralized access
        self._registry = CoreRegistry.get_instance()
        self._preprocessor: Optional[CoreJSONCProcessor] = CoreJSONCProcessor.get_instance()

        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
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
    def print_lolcat(text: str, freq: float = None, spread: float = 1, seed: float = 64738):
        """
        Print text to terminal with rainbow 24-bit color effect (like lolcat).

        Parameters:
            text (str): The text to print.
            freq (float, optional): Frequency of the rainbow hue changes.
            spread (float, optional): Spread factor controlling how quickly colors change across characters.
            seed (float, optional): Phase base offset (applied to all channels).
        """
        if freq is None:
            freq = random.uniform(0.05, 0.25)
        if spread is None:
            spread = random.uniform(2.0, 6.0)
        if seed is None:
            seed = random.uniform(0, 2 * math.pi)

        # Randomize channel-specific phase shifts
        phase_r = seed + random.uniform(0, 2 * math.pi)
        phase_g = seed + random.uniform(0, 2 * math.pi)
        phase_b = seed + random.uniform(0, 2 * math.pi)

        for i, char in enumerate(text):
            x = i / spread
            r = int(math.sin(freq * x + phase_r) * 127 + 128)
            g = int(math.sin(freq * x + phase_g) * 127 + 128)
            b = int(math.sin(freq * x + phase_b) * 127 + 128)
            sys.stdout.write(f"\033[38;2;{r};{g};{b}m{char}")
        sys.stdout.write("\033[0m")
        sys.stdout.flush()

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
            return True  # Key is already stored and has the same value

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
        """
        try:
            seconds = float(seconds)
        except ValueError as value_error:
            raise ValueError(f"invalid input: {seconds} cannot be converted to a float") from value_error

        def _pluralize(_time, _unit):
            """ Returns a string with the unit correctly pluralized based on the time. """
            if _time == 1:
                return f"{_time} {_unit}"
            else:
                return f"{_time} {_unit}s"

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds_int = int(seconds % 60)  # Get the integer part of the remaining seconds
        milliseconds = int((seconds - int(seconds)) * 1000)  # Correct calculation of milliseconds

        parts = []
        if hours:
            parts.append(_pluralize(hours, "hour"))
        if minutes:
            parts.append(_pluralize(minutes, "minute"))
        if seconds_int:
            parts.append(_pluralize(seconds_int, "second"))
        if milliseconds:
            parts.append(_pluralize(milliseconds, "millisecond"))

        return ", ".join(parts) if parts else "0 seconds"

    @staticmethod
    def class_has_property(class_name: str, property_name: str) -> bool:
        """
        Checks whether a specified property exists on a class with the given name.
        First, looks for the class in the global scope using `globals()`.
        If not found, it attempts to retrieve the class from the current module using `sys.modules`.

        Args:
            class_name (str): The name of the class to inspect.
            property_name (str): The name of the property to check for.

        Returns:
            bool: True if the class exists and has the given property, False otherwise.
        """
        cls = globals().get(class_name, None)
        if cls is None:
            # If the class is not in globals, check in sys.modules
            cls = getattr(sys.modules[__name__], class_name, None)

        return hasattr(cls, property_name) if cls else False

    @staticmethod
    def class_name_in_file(class_name: str, file_path: str) -> bool:
        """
        Determines whether the specified class name is defined in the given Python file.
        Scans the file line by line, looking for a class declaration that matches
        the given class name. It performs a simple string match and does not parse the file as AST.
        Args:
            class_name (str): The name of the class to search for.
            file_path (str): The full path to the Python source file.

        Returns:
            bool: True if the class definition is found, False otherwise (including file access errors).
        """
        with suppress(OSError), open(file_path, encoding='utf-8') as file:
            for line in file:
                stripped = line.lstrip()
                if stripped.startswith('class ') and class_name in stripped:
                    return True
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
                    if CoreToolBox.class_name_in_file(class_name,
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
                                f"failed to import {module_name} from {file_path}: {exception}") from exception
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
                raise ImportError(f"module '{module_name}' not found.") from import_error
        return None

    @staticmethod
    def find_method_name(method_name: str, directory: Optional[Union[str, os.PathLike[str]]] = None) -> Optional[
        MethodLocationType]:
        """
        Searches for a method definition by name within Python files under the specified directory.
        Returns a MethodLocationType tuple with the class name (if found), method name, and module path.
        If the method is found in global scope, class_name will be None.
        Returns None if the method is not found or if any error occurs during the search.

        Args:
            method_name (str): Name of the method to search for.
            directory (Optional[Union[str, os.PathLike[str]]]): Root directory to search in.
                Defaults to PROJECT_BASE_PATH if not provided.

        Returns:
            Optional[MethodLocationType]: A tuple (class_name, method_name, module_path),
                or None if not found or on error.
        """
        if directory is None:
            directory = PROJECT_BASE_PATH

        base_package_name = os.path.basename(directory)
        class_regex = re.compile(r'^class\s+(\w+)\s*:', re.MULTILINE)
        method_regex = re.compile(r'^\s*def\s+' + re.escape(method_name) + r'\s*\(', re.MULTILINE)

        for root, _, files in os.walk(directory):
            for file in files:
                if not file.endswith('.py'):
                    continue

                file_path = os.path.join(root, file)
                content = ""
                module_path = ""

                with suppress(Exception):
                    module_path = os.path.relpath(str(file_path), str(directory)).replace(os.sep, '.')[:-3]
                    if base_package_name not in module_path:
                        module_path = f"{base_package_name}.{module_path}"

                    with open(str(file_path), encoding='utf-8') as f:
                        content = f.read()

                if not module_path or not content:
                    return None

                current_class = None
                last_pos = 0

                with suppress(Exception):
                    for match in class_regex.finditer(content):
                        class_start = match.start()
                        method_match = method_regex.search(content, last_pos, class_start)
                        if method_match:
                            return MethodLocationType(current_class, method_match.group(1), module_path)

                        current_class = match.group(1)
                        last_pos = match.end()

                    method_match = method_regex.search(content, last_pos)
                    if method_match:
                        return MethodLocationType(current_class, method_match.group(1), module_path)

        return None

    @staticmethod
    def filter_kwargs_for_method(kwargs: dict[str, Any], sig: inspect.Signature) -> tuple[
        dict[str, Any], dict[str, Any]]:
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
                raise ValueError(f"key collision detected for '{base_key}' while processing path '{full_key}")

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
        except Exception as path_validation_exception:
            if raise_exception:
                raise path_validation_exception
        return False

    @staticmethod
    def validate_file(text: str, raise_exception: Optional[bool] = True) -> Optional[bool]:
        """
        Check whether the given text represents an existing file.
        Args:
            text (str): The path string to check.
            raise_exception (bool, optional): If True, raises an exception when the file is invalid.
        Returns:
            bool: True if the path exists and is a file, False otherwise.
        """
        try:
            expanded_path = os.path.expanduser(os.path.expandvars(text))
            path = Path(expanded_path)
            if path.exists() and path.is_file():
                return True
            if raise_exception:
                raise FileNotFoundError(f"file does not exist or is not a file: {text}")
        except Exception as file_validation_exception:
            if raise_exception:
                raise file_validation_exception
        return False

    @staticmethod
    def get_temp_filename() -> Optional[str]:
        """
        Generates a unique temporary filename without creating a persistent file on disk.
        """
        try:
            temp_path_name = CoreToolBox.get_temp_pathname(create_path=True)
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
            temp_path = tempfile.mkdtemp(prefix=PROJECT_TEMP_PREFIX)
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
                if entry.is_dir() and entry.name.startswith(PROJECT_TEMP_PREFIX):
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
                file_content: Any = file.read()

            # Encode the content to base64
            encoded_content = base64.b64encode(file_content).decode()
            return encoded_content

        except Exception as encode_error:
            raise encode_error

    @staticmethod
    def safe_backup_and_erase_file(file_path: Union[str, Path]) -> None:
        """
        Back up a file by copying it with a timestamped name, then delete the original.
        Args:
            file_path (Path): The full path to the file to be backed up and deleted.
        """

        if isinstance(file_path, str):
            file_path = Path(file_path)

        if not file_path.is_file():
            return

        # Extract filename components
        timestamp = datetime.now().strftime("%m_%d_%H_%M_%S")
        suffix = ''.join(file_path.suffixes)
        stem = file_path.name.removesuffix(suffix) if suffix else file_path.stem

        # Create backup filename
        backup_name = f"{stem}_{timestamp}{suffix}"
        backup_path = file_path.with_name(backup_name)

        try:
            shutil.copy2(file_path, backup_path)
            file_path.unlink(missing_ok=True)
        except Exception as erase_error:
            raise erase_error from erase_error

    @staticmethod
    def safe_erase_path(target_path: str, min_depth: int = 3, force: bool = False):
        """
        Safely erase a directory path if:
          - It exists.
          - It's deeper than min_depth levels above root.
          - It's not inside the user's home directory.
          - It's empty unless 'force' is True.
        Args:
            target_path (str): Path to delete.
            min_depth (int): Minimum directory depth (e.g., 3 for "/a/b/c").
            force (bool): If False, refuse to delete non-empty directories.
        """
        try:
            abs_path = Path(target_path).resolve(strict=True)
            home_path = Path.home().resolve()

            # Check if it's a directory
            if not abs_path.is_dir():
                raise ValueError(f"Not a directory:'{abs_path}'")

            # Refuse to delete if the path is directly under home (e.g., ~/Desktop)
            if abs_path.parent == home_path:
                raise ValueError(f"Refusing to delete path directly under home directory: '{abs_path}'")

            # Check minimum depth from root
            depth = len(abs_path.parts) - 1  # subtract 1 for the leading '/'
            if depth < min_depth:
                raise ValueError(f"Refusing to delete: path depth {depth} < minimum allowed depth '{min_depth}'")

            # If not forcing, ensure directory is empty
            if not force and any(abs_path.iterdir()):
                raise ValueError(f"Refusing to delete non-empty directory without force=True: '{abs_path}'")

            # Passed all checks — delete
            shutil.rmtree(str(abs_path))

        except Exception as erase_error:
            raise erase_error from erase_error

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
    def uncompress_file(archive_path: str, destination_path: Optional[str] = None, delete_after: bool = False,
                        update_progress: Optional[Callable[..., Any]] = None) -> Optional[str]:
        """
        Extracts a compressed archive (zip, tar, tar.gz, tar.bz2, etc.) into a destination directory.

        Args:
            archive_path (str): Path to the archive file to extract.
            destination_path (Optional[str]): Directory to extract to (defaults to archive directory).
            delete_after (bool): If True, deletes the archive after successful extraction.
            update_progress (Optional[Callable[[str], None]]): Optional callback to report extraction progress.

        Returns:
            str: Path to the directory where files were extracted.
        """
        archive_path = CoreToolBox.get_expanded_path(archive_path)
        if not os.path.isfile(archive_path):
            raise FileNotFoundError(f"Archive '{archive_path}' does not exist or is not a file.")

        if destination_path is None:
            destination_path = os.path.dirname(archive_path)
        else:
            destination_path = CoreToolBox.get_expanded_path(destination_path)

        try:
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path) as zf:
                    for member in zf.filelist:
                        if update_progress:
                            update_progress(f"{member.filename}")
                        zf.extract(member, path=destination_path)
                if delete_after:
                    os.remove(archive_path)

            elif tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path, 'r:*') as tf:
                    while True:
                        member = tf.next()
                        if member is None:
                            break
                        if update_progress:
                            update_progress(f"{member.name}")
                        tf.extract(member, path=destination_path)
                if delete_after:
                    os.remove(archive_path)
            else:
                raise ValueError(f"Unsupported archive format for file '{archive_path}'.")

        except Exception as decompress_error:
            raise Exception(
                f"Failed to extract '{archive_path}' to '{destination_path}': {decompress_error}") from decompress_error

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

        # GCC junk and do everything possible to get a clear human-readable string.
        text = text.replace("8;;", "")
        text = text.replace("->", "").strip()

        # Remove GCC Source code references
        text = re.sub(r'^\|\s*[~^]+\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\s*\|.*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s*\|', '', text)

        if not text:
            return text

        def recover_warning_flag(match):
            """ # Extract and preserve [-W...warning...] from broken [https://...] blocks """
            url = match.group(1)
            warning_match = re.search(r'(-W[\w\-]+)', url)
            return f"[{warning_match.group(1)}]" if warning_match else ""

        text = re.sub(r'\[(https?://[^]]+)]', recover_warning_flag, text).strip()

        # Step 4: Optionally reduce to printable ASCII
        if bare_text:
            allowed = set(string.ascii_letters + string.digits + string.punctuation + ' \t\n')
            text = ''.join(c for c in text if c in allowed)

        return text.strip()

    def print_logo(self, banner_file: Optional[str] = None, clear_screen: bool = False,
                   terminal_title: Optional[str] = None, blink_pixel: Optional[XYType] = None) -> None:
        """
        Displays an ASCII logo from a file using a consistent horizontal RGB gradient
        (same for every line, from dark to bright).
        """
        demo_file = str(PROJECT_SHARED_PATH / "banner.txt")
        banner_file = banner_file or demo_file

        if not os.path.isfile(banner_file):
            return

        # Retrieve the ANSI codes map from the main AutoForge instance.
        if self._ansi_codes is None:
            self._ansi_codes = self.auto_forge.ansi_codes
        if self._ansi_codes is None:
            return  # Could not get the ANSI codes tables

        if clear_screen:
            sys.stdout.write(self._ansi_codes.get('SCREEN_CLS_SB'))
        sys.stdout.write('\n')

        with open(banner_file, encoding='utf-8') as f:
            lines = [line.rstrip('\n') for line in f]

        if not lines:
            return

        # Pick a base color and brighten it across the line width
        r_base, g_base, b_base = (random.randint(0, 100) for _ in range(3))
        r_delta, g_delta, b_delta = (random.randint(80, 155) for _ in range(3))

        max_line_len = max(len(line) for line in lines)

        def get_rgb_gradient(height, width):
            """ Computes an RGB ANSI color escape sequence based on horizontal gradient position. """
            t = height / max(1, width - 1)
            r = int(r_base + r_delta * t)
            g = int(g_base + g_delta * t)
            b = int(b_base + b_delta * t)
            return f"\033[38;2;{r};{g};{b}m"

        for y, line in enumerate(lines):
            colored_line = ""
            for x, ch in enumerate(line):
                color_code = get_rgb_gradient(x, max_line_len)

                # Check for blink position
                if blink_pixel:
                    if blink_pixel.x == x and blink_pixel.y == y:
                        colored_line += f"\033[5m{color_code}{ch}\033[25m"
                    else:
                        colored_line += f"{color_code}{ch}"

            sys.stdout.write(colored_line + "\033[0m\n")

        sys.stdout.write('\n')
        sys.stdout.flush()

        if terminal_title is not None:
            CoreToolBox.set_terminal_title(terminal_title)

    @staticmethod
    def get_formatted_size(num_bytes: int, precision: int = 1) -> str:
        """
        Convert a byte count into a human-readable string.
        Args:
            num_bytes (int): The number of bytes. Must be >= 0.
            precision (int): Number of decimal places. Must be >= 0.
        Returns:
            str: Human-readable size string (e.g. '2.0 MB').
        """
        if not isinstance(num_bytes, (int, float)) or num_bytes < 0:
            raise ValueError("num_bytes must be a non-negative number.")
        if not isinstance(precision, int) or precision < 0:
            raise ValueError("precision must be a non-negative integer.")

        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
        size = float(num_bytes)
        for unit in units:
            if size < 1024.0:
                return f"{size:.{precision}f} {unit}"
            size /= 1024.0
        return f"{size:.{precision}f} PB"

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
        match = re.search(r'^Description:\s*\n((?:\s{2,}.*\n?)+)', doc, re.IGNORECASE | re.MULTILINE)

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

        def _protect(match):
            """ Replaces a matched string with a unique placeholder and stores the original for later restoration. """
            protected_token = f"__PROTECTED_{len(protected)}__"
            protected[protected_token] = match.group(0)
            return protected_token

        cleared_text = re.sub(url_pattern, _protect, cleared_text)
        cleared_text = re.sub(email_pattern, _protect, cleared_text)

        # Work safely
        cleared_text = re.sub(r'\n+', '.', cleared_text)
        cleared_text = re.sub(r'\.{2,}', '.', cleared_text)
        cleared_text = cleared_text.strip('.').strip()
        if not cleared_text:
            return default_text if default_text is not None else ""

        # Capitalize sentences, but skip inside __PROTECTED__ blocks
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

        # ToDo: Need to better handle than parser quirk
        flattened_text = flattened_text.replace(':. ', ': ')

        # Ensure a single final dot
        flattened_text = flattened_text.strip()
        if flattened_text and not flattened_text.endswith('.'):
            flattened_text += '.'

        return flattened_text

    @staticmethod
    def normalize_docstrings(doc: str, wrap_term_width: int = 0) -> str:
        """
        Simple docstring formatter for terminal display.
        Args:
            doc (str): The raw docstring input.
            wrap_term_width (int): The terminal width to wrap the docstring into.
        Returns:
            str: A cleaned, well-formatted, and wrapped docstring.
        """
        if not doc:
            return ""

        # Get current terminal width if not specified.
        if wrap_term_width == 0:
            wrap_term_width = shutil.get_terminal_size((80, 20)).columns - 8

        # 1. Remove newlines/tabs, collapse multiple spaces
        doc = re.sub(r"[\n\t]+", "", doc)
        doc = re.sub(r" {2,}", " ", doc).strip()
        parts = doc.split(".")
        for i, part in enumerate(parts):
            if not parts[i].strip():
                parts.pop(i)
                continue
            parts[i] = part.strip() + "."
            parts[i] = textwrap.fill(parts[i], width=wrap_term_width)
            parts[i] = "    " + parts[i].replace("\n", "\n    ") + "\n"
            parts[i] = parts[i].replace("Args:", "Args:\n        ")
            parts[i] = parts[i].replace("Returns:", "Returns:\n        ")
            parts[i] = parts[i].replace("Notes:", "Notes:\n        ")

        doc = "".join(parts)
        return doc.strip()

    @staticmethod
    def cp(pattern: Union[str, list[str]], dest_dir: str):
        """
        Copies files matching one or more wildcard patterns to the destination directory.
        If the destination directory does not exist, it will be created.
        Metadata such as timestamps and permissions are preserved.

        Args:
            pattern (Union[str, List[str]]): Wildcard pattern(s) (e.g. '*.txt' or ['*.json', '*.zip']).
            dest_dir (str): Target directory to copy files into.
        """
        if isinstance(pattern, str):
            patterns = [p.strip() for p in pattern.split(",")]
        else:
            patterns = pattern

        matched_files = []
        for pat in patterns:
            matched_files.extend(glob.glob(pat))

        if not matched_files:
            raise FileNotFoundError(f"no files match pattern(s): {patterns}")

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
            man_proc = subprocess.run(["man", command], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

            # Remove overs trike formatting using `col -bx`
            col_proc = subprocess.run(["col", "-bx"], input=man_proc.stdout, stdout=subprocess.PIPE, text=True)
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

    def show_json_file(self, json_path_or_data: Union[str, dict], title: Optional[str] = None,
                       panel_content: Optional[str] = None) -> Optional[int]:
        """
        Displays a JSON file using the textual json tree viewer.
        Args:
            json_path_or_data (str,dict): path to the JSON or data structure.
            title (str): The title to show in the terminal viewer.
            panel_content (str): Left side panel content.
        Returns:
            int: 0 on success, 1 on error or suppressed failure.
        """

        json_temp_file_path: Optional[Path] = None
        json_viewer_tool: Path = PROJECT_VIEWERS_PATH / "json_viewer.py"

        if not json_viewer_tool.exists():
            return 1

        try:
            if isinstance(json_path_or_data, str):

                # Since we need to use classes from modules that may not be directly imported at startup,
                # we retrieve their instances dynamically from the registry.
                variables_class: Optional[CoreVariablesProtocol] = self._registry.get_instance_by_class_name(
                    "CoreVariables", return_protocol=True)

                if not variables_class:
                    raise RuntimeError("required component instances could not be retrieved for this operation")

                json_file_path: Optional[str] = variables_class.expand(key=json_path_or_data, quiet=True)
                if json_file_path and os.path.exists(json_file_path):
                    json_path_or_data = self._preprocessor.render(file_name=json_file_path)

            if isinstance(json_path_or_data, dict):
                # Pretty print the dictionary to a JSON string
                json_string = json.dumps(json_path_or_data, indent=4, ensure_ascii=False)
                # Create a temporary file
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json', encoding='utf-8') as temp_f:
                    temp_f.write(json_string)
                # Get the path of the temporary file
                json_temp_file_path = Path(temp_f.name)

            if not json_temp_file_path:
                raise RuntimeError("input was not recognized as either JSON or JSONC")

            command = ["python3", str(json_viewer_tool), "--json", str(json_temp_file_path)]
            # Conditionally add the --title argument
            if title is not None:
                command.extend(["--title", title])
            if panel_content is not None:
                command.extend(["--panel_content", panel_content])

            status = subprocess.run(command, env=os.environ.copy())
            return_code = status.returncode
            # Reset TTY settings
            os.system("stty sane")
            sys.stdout.flush()
            sys.stderr.flush()

            json_temp_file_path and os.remove(str(json_temp_file_path))  # Delete temporary file
            return return_code

        except Exception as viewer_exception:
            raise viewer_exception from viewer_exception

    @staticmethod
    def resolve_help_file(relative_path: Union[str, Path]) -> Optional[Path]:
        """
        Returns the path to a help markdown file based on its relative name if that the file was found.
        Args:
            relative_path (str, Path): Relative path to the help file.
        Returns:
            str: The resolved path to the .md help file if the file exists, else None.
        """

        help_file_path: Path = PROJECT_HELP_PATH / Path(relative_path)

        # Must have a markdown (.md) extension
        if help_file_path.suffix.lower() != ".md" or not help_file_path.exists():
            return None

        return help_file_path

    @staticmethod
    def show_help_file(relative_path: Union[str, Path]) -> int:
        """
        Displays a markdown help file using the textual markdown viewer.
        Args:
            relative_path (str): Relative path to the help file under PROJECT_HELP_PATH.
        Returns:
            int: 0 on success, 1 on error or suppressed failure.
        """
        help_viewer_tool = PROJECT_VIEWERS_PATH / "help_viewer.py"

        # Resolve the file path
        help_file_path = CoreToolBox.resolve_help_file(relative_path)

        with suppress(Exception):
            if not help_viewer_tool.exists() or not help_file_path:
                return 1

            if help_file_path.stat().st_size > 64 * 1024:
                return 1

            status = subprocess.run(["python3", str(help_viewer_tool), "--markdown", str(help_file_path)],
                                    env=os.environ.copy())
            return_code = status.returncode

            # Reset TTY settings
            os.system("stty sane")
            sys.stdout.flush()
            sys.stderr.flush()

            return return_code

        return 1  # If anything failed silently

    @staticmethod
    def is_shell_builtin(tested_command: str) -> bool:
        """
        Checks whether the given command is a shell internal (builtin, reserved word, alias, or function).
        Returns:
            bool: True if the command is a shell internal, False otherwise.
        """
        with suppress(Exception):
            user_shell = os.environ.get("SHELL", "/bin/bash")
            result = subprocess.run([user_shell, "-c", f"type {tested_command}"], capture_output=True, text=True)
            return any(keyword in result.stdout for keyword in ("shell builtin", "reserved word", "alias", "function"))

        return False

    @staticmethod
    def set_terminal_input(state: bool = False, flush: bool = True) -> Optional[bool]:
        """
        Unix specific - Enable or disable terminal input (ECHO and line buffering).
        Args:
            state (bool): True to enable input, False to disable.
            flush (bool): If True, flush the input buffer before changing state.
        Returns:
            Optional[bool]: Final input state (True = enabled, False = disabled),
                            or None if an error occurred.
        """
        if os.name != 'posix':
            return None

        with suppress(Exception):
            fd = sys.stdin.fileno()
            attrs = termios.tcgetattr(fd)

            # Flush in any case if specified
            if flush:
                termios.tcflush(fd, termios.TCIFLUSH)

            input_enabled = bool(attrs[3] & termios.ECHO and attrs[3] & termios.ICANON)
            if input_enabled == state:
                return input_enabled  # Already in desired state

            if state:
                attrs[3] |= (termios.ECHO | termios.ICANON)
                termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
            else:
                attrs[3] &= ~(termios.ECHO | termios.ICANON)
                termios.tcsetattr(fd, termios.TCSADRAIN, attrs)

            return state

        return None  # Suppressed exception accused

    @staticmethod
    def is_valid_compressed_json(file_path: str) -> Optional[str]:
        """
        Detects the compression type and validates that the file contains line-delimited JSON.
        Args:
            file_path (str): Path to the file.
        Returns:
            Optional[str]: Returns the format name ('gzip', 'lzma', 'zip') if valid, None otherwise.
        """

        def _validate_json_any_format(file_obj) -> bool:
            """
            Validates the file contains either:
            - A single JSON object or array.
            - Line-delimited JSON objects.
            Returns:
                bool: True if any valid JSON format detected, else False.
            """
            content = file_obj.read()
            if isinstance(content, bytes):
                content = content.decode()

            # Try whole-file JSON first (pretty-printed or compact)
            with suppress(json.JSONDecodeError):
                json.loads(content)
                return True

            # Try line-delimited JSON
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                with suppress(json.JSONDecodeError):
                    json.loads(line)
                    continue
                return False

            return True

        # Check gzip
        with suppress(Exception), gzip.open(file_path, 'rt', encoding='utf-8') as f:
            if _validate_json_any_format(f):
                return 'gzip'

        # Check lzma (.xz)
        with suppress(Exception), lzma.open(file_path, 'rt', encoding='utf-8') as f:
            if _validate_json_any_format(f):
                return 'lzma'

        return None

    @staticmethod
    def append_timestamp_to_path(file_or_path: str, date_time_format: Optional[str] = None) -> Optional[str]:
        # noinspection SpellCheckingInspection
        """
        Append a timestamp to a filename or path using a standard strftime format.
        - If the input is a file (i.e., has an extension), the timestamp is inserted before the extension.
        - If the input is a directory or extension-less name, the timestamp is appended at the end.
        Args:
            file_or_path (str): The file or path to modify.
            date_time_format (Optional[str]): strftime-compatible format string for the timestamp.
                                              Defaults to "%d_%m_%Y_%H_%M_%S".
        Returns:
            Optional[str]: Modified string with the embedded timestamp, or None if an error occurs.
        """
        with suppress(Exception):
            stamp_format = date_time_format or "%d_%m_%Y_%H_%M_%S"
            timestamp = datetime.now().strftime(stamp_format)
            path = Path(file_or_path)

            if path.suffix:
                new_name = f"{path.stem}_{timestamp}{path.suffix}"
                return str(path.with_name(new_name))
            else:
                return f"{file_or_path}_{timestamp}"

        return None  # Returned if an exception was suppressed

    @staticmethod
    def has_nested_list(obj, require_non_empty_lists=False):
        """
        Check if the object is a dictionary with one or more top-level list values.
        Args:
            obj (Any): Object to inspect.
            require_non_empty_lists (bool): If True, only consider non-empty top-level lists.
        Returns:
            bool: True if a matching list is found at the top level, else False.
        """
        if not isinstance(obj, dict):
            return False

        for value in obj.values():
            if isinstance(value, list):
                if not require_non_empty_lists or len(value) > 0:
                    return True
        return False

    @staticmethod
    def substitute_keywords(text: Optional[str] = None, keywords: Optional[list] = None,
                            allow_spaces: bool = True) -> str:
        """
        Perform keyword substitution on a string using a list of keyword mappings.
        Args:
            text (str, optional): The input string potentially containing <keyword> placeholders.
            keywords (list, optional): A list of pairs like ["keyword", "replacement"].
            allow_spaces (bool): If True, allows whitespace within < and > (e.g., '<  key  >').

        Returns:
            str: The modified string with substitutions applied, or the original if substitution fails.
        """
        if not isinstance(text, str) or not isinstance(keywords, list):
            return text

        with suppress(Exception):
            mapping = {key.strip(): val for item in keywords if
                       isinstance(item, list) and len(item) == 2 and all(isinstance(i, str) for i in item) for key, val
                       in [item]}

            pattern = r"<\s*([^<>]+?)\s*>" if allow_spaces else r"<([^<>]+)>"

            def _replacer(_match: re.Match) -> str:
                key = _match.group(1).strip()
                return mapping.get(key, _match.group())

            return re.sub(pattern, _replacer, text)

        return text

    @staticmethod
    def set_timer(timer: Optional[threading.Timer], interval: Optional[float],
                  expiration_routine: Optional[Callable], timer_name: Optional[str] = None,
                  auto_start: bool = True):
        """
        General purpose timer handling routine.
        Cancels the current timer if it exists and sets a new timer with the specified interval and expiration routine.
        Args:
            timer: The existing timer object, which may be None if no timer was previously set.
            interval: The interval in seconds after which the expiration routine should be executed.
            expiration_routine: The function to call when the timer expires.
            timer_name: Optional name for the timer for easier identification.
            auto_start: set to auto start the timer upon creation.

        Returns:
            threading.Timer: The new timer object.
        """
        # Cancel the existing timer if it exists
        if timer is not None:
            timer.cancel()

        created_timer_name = timer_name if timer_name is not None else 'Timer'

        # Only create a new timer if both interval and routine are specified
        if interval is not None and expiration_routine is not None:
            timer = threading.Timer(interval=interval, function=expiration_routine, args=(created_timer_name,))

            # Set the timer's name if provided
            if timer_name is not None:
                timer.name = created_timer_name

            # Start the new timer
            if auto_start:
                timer.start()
        return timer

    @staticmethod
    def extract_bare_list(data: Any, name: Optional[str] = None) -> Optional[Any]:
        """
        Best-effort extraction of a bare list from input data.
        If a name is provided and matches a key in a dict holding a list, return that list.
        If name is not provided, try to extract a list from a dict with a single list-valued key.
        Otherwise, return the original data.
        Args:
            data (Any): JSON-like input data.
            name (Optional[str]): Expected key holding a list (if known).
        Returns:
            Any: A list if extractable, else the original data.
        """

        if data is None:
            return None

        with suppress(Exception):
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                if name and name in data and isinstance(data[name], list):
                    return data[name]
                if name is None and len(data) == 1:
                    sole_value = next(iter(data.values()))
                    if isinstance(sole_value, list):
                        return sole_value
        return data
