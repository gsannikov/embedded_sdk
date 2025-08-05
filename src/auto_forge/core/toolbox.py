"""
Script:         toolbox,py
Author:         AutoForge Team

Description:
    Auxiliary module defining the 'ToolBox' class, which provides utility functions
    used throughout the AutoForge library. It contains a collection of general-purpose
    methods for common tasks.
"""

import base64
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
import time
import tty
import zipfile
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Optional, SupportsInt, Union, Callable, Tuple
from urllib.parse import ParseResult, unquote, urlparse

import psutil
# Third-party
from pyfiglet import Figlet
# Rich terminal utilities
from rich.console import Console
from rich.text import Text
from wcwidth import wcswidth

# AutoForge imports
from auto_forge import (
    AddressInfoType, AutoForgFolderType, AutoForgeModuleType, AutoForgeWorkModeType, CoreJSONCProcessor,
    CoreLogger, CoreModuleInterface, CoreRegistry, CoreSystemInfo, CoreTelemetry, MethodLocationType,
    PackageGlobals, PromptStatusType,
)

# Note: Compatibility bypass - no native "UTC" import in Python 3.9.
UTC = timezone.utc

AUTO_FORGE_MODULE_NAME = "ToolBox"
AUTO_FORGE_MODULE_DESCRIPTION = "General purpose support routines"


class CoreToolBox(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        super().__init__(*args, **kwargs)

    def _initialize(self, *_args, **_kwargs) -> None:
        """
        Initialize the 'CoreToolBox' class.
        """

        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._system_info = CoreSystemInfo()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()
        self._registry = CoreRegistry.get_instance()
        self._preprocessor: Optional[CoreJSONCProcessor] = CoreJSONCProcessor.get_instance()
        self._configuration: Optional[dict[str, Any]] = None

        # Dependencies check
        if None in (self._core_logger, self._logger, self._system_info, self._telemetry, self._preprocessor,
                    self.auto_forge.configuration):
            raise RuntimeError("failed to instantiate critical dependencies")

        self._ansi_codes: Optional[dict[str, str]] = None
        self._preprocessor: Optional[CoreJSONCProcessor] = None
        self._dynamic_vars_storage: dict = {}  # Dictionary for managed arbitrary session variables
        self._show_status_lock = threading.RLock()
        self._pre_compiled_escape_patterns = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
        self._configuration = self.auto_forge.configuration

        # Populate ANSI codes from the package configuration data
        self._ansi_codes = self._configuration.get("ansi_codes", {})

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

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

    def print_lolcat(self, text: str, freq: float = None, spread: float = 1, seed: float = 64738):
        """
        Print text to terminal with rainbow 24-bit color effect (like lolcat).
        Parameters:
            text (str): The text to print.
            freq (float, optional): Frequency of the rainbow hue changes.
            spread (float, optional): Spread factor controlling how quickly colors change across characters.
            seed (float, optional): Phase base offset (applied to all channels).
        """

        # Only apply terminal effects in non-automatic sessions
        if self.auto_forge.work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION:
            return

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

    @staticmethod
    def looks_like_unix_file(might_be_file: str) -> bool:
        """
        Determines if a string looks like a Unix-style file path (not a directory).
        Args:
            might_be_file (str): The string to evaluate.
        Returns:
            bool: True if the string appears to be a valid Unix-style file path, False otherwise.
        """
        if not might_be_file or not isinstance(might_be_file, str):
            return False

        # Reject strings with characters forbidden or risky in filenames
        if '<' in might_be_file or '>' in might_be_file:
            return False

        if re.search(r'[<>:"|?*]', might_be_file):
            return False

        # Basic check for having at least one '/'
        if '/' not in might_be_file:
            return False

        path = Path(might_be_file)

        # Must have a suffix (file extension)
        if not path.suffix:
            return False

        # Ensure last part is not empty
        if not path.name or path.name.endswith('/'):
            return False

        # Avoid purely directory-looking paths like /usr/local/bin/
        if might_be_file.endswith('/'):
            return False

        return True

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
    def format_productivity(events_per_minute: Optional[Union[float, int]], total_seconds: Optional[float] = None) -> \
    Optional[str]:
        """
        Translates raw events-per-minute into a mysterious productivity descriptor,
        using a pseudo-scientific algorithm known only to ancient CI monks.
        Args:
            events_per_minute (float): The measured events per minute.
            total_seconds (float): Total recorded duration in seconds (for sanity check)
        Returns:
            str: A productivity label, or None if input is invalid.
        """
        if not isinstance(events_per_minute, (float, int)):
            return None

        if isinstance(total_seconds, float) and (total_seconds / 60) < 3:
            return "Productivity Level: ⚠️  Under 3 minutes logged (we're going to pretend this didn't happen)"

        if events_per_minute < 0.5:
            label = "🧘 Zen Mode (possibly sleeping with eyes open)"
        elif events_per_minute < 2:
            label = "🐢 Sub-threshold throughput (try more coffee)"
        elif events_per_minute < 5:
            label = "🚶‍♂️ Nominal motion detected (typing with one finger?)"
        elif events_per_minute < 10:
            label = "🚴 Productive (clearly multitasking and winning)"
        elif events_per_minute < 20:
            label = "🏃‍♂️ High throughput (keyboard may be smoking)"
        elif events_per_minute < 40:
            label = "🚀 Hyperproductive (caffeine at dangerous levels)"
        else:
            label = "🧠 Quantum typing event detected — seek medical attention"

        return f"Productivity Leve: {label} (~{events_per_minute:.1f} events/min)"

    @staticmethod
    def format_duration(seconds: Union[int, float], add_ms: bool = True) -> str:
        """
        Converts a number of total seconds into a human-readable string representing the duration
        in hours, minutes, seconds, and optionally milliseconds.
        Args:
            seconds (float or int): Total duration in seconds (may include fractional part).
            add_ms (bool, optional): If True (default), includes milliseconds in the output.
                                                   If False, rounds to full seconds and omits milliseconds.
        Returns:
            str: A formatted string such as '1 minute, 17 seconds' or '2 seconds, 803 milliseconds' or
                'conversion error' on any exception.
        """

        def _pluralize(_value: int, _unit: str) -> str:
            """ Convert to plural day -> days """
            return f"{_value} {_unit}" + ("s" if _value != 1 else "")

        with suppress(Exception):
            seconds = float(seconds)

            if not add_ms:
                seconds = round(seconds)

            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            seconds_int = int(seconds % 60)
            milliseconds = int((seconds - int(seconds)) * 1000) if add_ms else 0

            parts = []
            if hours:
                parts.append(_pluralize(hours, "hour"))
            if minutes:
                parts.append(_pluralize(minutes, "minute"))
            if seconds_int or (not hours and not minutes and not add_ms):
                parts.append(_pluralize(seconds_int, "second"))
            if add_ms and milliseconds:
                parts.append(_pluralize(milliseconds, "millisecond"))

            return ", ".join(parts) if parts else "0 seconds"

        # Suppressed conversion related exception
        return "Conversion error"

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
    def find_class_in_project(class_name: str, root_path: str = PackageGlobals.PACKAGE_PATH) -> Optional[type]:
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
            directory = PackageGlobals.PACKAGE_PATH

        base_package_name = os.path.basename(directory)
        class_regex = re.compile(r'^class\s+(\w+)\s*:', re.MULTILINE)
        method_regex = re.compile(r'^\s*def\s+' + re.escape(method_name) + r'\s*\(', re.MULTILINE)

        for root, _, files in os.walk(directory):
            for file in files:

                if not file.endswith('.py'):
                    continue

                file_path = os.path.join(root, file)
                content: str = ""
                module_path: str = ""

                with suppress(Exception):
                    module_path = str(os.path.relpath(str(file_path), str(directory)))
                    module_path = module_path.replace(os.sep, '.')[:-3]
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
                    if key in sig.parameters:
                        # Don't descend — assign entire dict as-is
                        _assign_value(key, value, current_path)
                    else:
                        # Not a top-level param — recurse
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
    def get_expanded_placeholders(var: str) -> str:
        """
        Replaces placeholders like <VAR_NAME> in the input string
        with the value of the environment variable $VAR_NAME,
        but only if it exists. Leaves the placeholder untouched otherwise.
        """

        def _replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, f"<{var_name}>")

        return re.sub(r"<([A-Za-z_][A-Za-z0-9_]*)>", _replacer, var)

    @staticmethod
    def get_valid_path(raw_value: Any, create_if_missing: bool = False) -> Optional[Path]:
        """
        Validates and resolves a raw value into a `Path` object.
        Args:
            raw_value (Any): The raw input to be interpreted as a filesystem path.
                             Can be a string or `pathlib.Path` object.
            create_if_missing (bool, optional): If True, creates the path if it doesn't exist. Defaults to False.
        Returns:
            Optional[Path]: A resolved `Path` object if the input is valid and exists or was created.
        """
        if not raw_value or not isinstance(raw_value, (str, Path)):
            raise ValueError(f"Invalid path value: {raw_value!r}")
        try:
            path = Path(raw_value).expanduser().resolve(strict=False)
        except Exception as e:
            raise ValueError(f"Cannot resolve path from value '{raw_value}': {e}")

        if not path.exists():
            if create_if_missing:
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except Exception as os_error:
                    raise OSError(f"Could not create missing directory at {path}: {os_error}")
            else:
                raise FileNotFoundError(f"Path does not exist: {path}")
        return path

    @staticmethod
    def markdown_to_text(md: str) -> Optional[str]:
        """
        Converts basic Markdown to plain text using regex.
        Handles headers, bold/italic, links, lists, code blocks, and inline code.
        Does not require external packages.
        """

        # Make sure we have something to work on
        if not isinstance(md, str) or len(md) == 0:
            return md

        text = md.strip()

        # Remove opening fenced code block marker (e.g., ```python or ```sql)
        text = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", text, flags=re.MULTILINE)

        # Remove closing triple backticks
        text = re.sub(r"^```$", "", text, flags=re.MULTILINE)

        # Remove inline code backticks
        text = re.sub(r"`([^`]*)`", r"\1", text)

        # Remove images
        text = re.sub(r"!\[[^]]*]\([^)]+\)", "", text)

        # Convert links [text](url) -> text
        text = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", text)

        # Headers
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)

        # Bold/italic
        text = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", text)
        text = re.sub(r"___([^_]+)___", r"\1", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"__([^_]+)__", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"_([^_]+)_", r"\1", text)

        # Blockquotes
        text = re.sub(r"^\s*> ?", "", text, flags=re.MULTILINE)

        # Lists
        text = re.sub(r"^\s*[-+*]\s+", "- ", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\.\s+", "- ", text, flags=re.MULTILINE)

        # Remove triple quotes
        text = re.sub(r'("""|\'\'\')', '', text)

        # Remove visual separator lines (e.g., ====..., ---..., ***..., etc.)
        text = re.sub(r"^\s*([=\-*_.~#])\1{3,}.*$", "", text, flags=re.MULTILINE)

        # Remove leading colon + quote pattern like: ': "text'
        text = re.sub(r"^[:\-–—]\s*[\"']+", "", text, flags=re.MULTILINE)

        # Remove trailing quote-only lines or dangling trailing quotes
        text = re.sub(r"[\"']+\s*$", "", text)

        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

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

    def reset_terminal(self, use_shell: bool = True, flush_buffers: bool = True):
        # noinspection SpellCheckingInspection
        """
        Restore terminal to a usable, interactive state after full-screen or raw-mode manipulation.
        It performs two key actions:
            1. Attempts a low-level terminal reset via Python's `termios` and `tty` modules (avoids subprocess when possible).
            2. Optionally invokes `stty sane` as a fallback or additional layer of reset (if `use_shell=True`).

        Optionally also flushes screen artifacts and exits alternate screen buffer.
        Args:
            use_shell (bool): If True, invokes `stty sane` via subprocess for POSIX-style terminal reset.
                              Set to False to avoid any shell subprocess usage.
            flush_buffers (bool): If True, forcibly exits alternate screen buffer (e.g., after `nano`)
                                  and clears both the visible screen and scrollback buffer.
        """
        # Method not applicable when automating a command
        if self.auto_forge.work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION:
            return

        if use_shell:
            subprocess.run(["stty", "sane"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if sys.stdin.isatty():
            with suppress(Exception):
                fd = sys.stdin.fileno()
                with suppress(Exception):
                    tty.setcbreak(fd)  # minimal reset (line buffering on, echo preserved)
                    attrs = termios.tcgetattr(fd)
                    attrs[3] |= termios.ECHO | termios.ICANON  # enable echo and canonical mode
                    termios.tcsetattr(fd, termios.TCSADRAIN, attrs)

                if flush_buffers:
                    print("\033[?1049l", end="", flush=True)  # Exit alt screens (for ex. 'nano')
                    print("\033[3J\033[H\033[2J", end="", flush=True)

    def safe_start_keyboard_listener(self, listener_handler: Callable) -> Optional[Any]:
        """
        Safely attempts to import `pynput.keyboard` if the system environment supports it.
        Args:
            listener_handler (Callable): Function which will be called when a keyboard key is pressed.
        Returns:
            The `pynput.keyboard.Listener` module if available and safe to use, otherwise None.
        Notes:
            The actual return type is `pynput.keyboard.Listener`, but `Any` is used to avoid
            importing the module at the top level, which may crash in unsupported environments.
        """
        # Check for GUI session (X11 or Wayland)
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            self._logger.warning("Skipping scanner - no graphical session detected")
            return None

        # Check for WSL
        if self._system_info.is_wsl:
            self._logger.warning("Skipping scanner - running under WSL")
            return None

        # Check for SSH or headless session
        if os.environ.get("SSH_CONNECTION") or not sys.stdout.isatty():
            self._logger.warning("Skipping scanner - SSH or headless session")
            return None

        # Try importing pynput
        with suppress(Exception):
            from pynput import keyboard
            listener = keyboard.Listener(on_press=listener_handler)
            listener.start()
            self._logger.info("Scanner started")
            return listener

        # Skipping pynput - backend import failed
        self._logger.info("Skipping scanner - backend import failed")
        return None

    @staticmethod
    def get_wsl_unc_path(linux_path: str) -> Optional[str]:
        r"""
        Convert a WSL Linux path to its UNC Windows path.
        For example: /home/user -> \\wsl.localhost\IMCv2\home\user
        Args:
            linux_path (str): Absolute Linux path inside WSL.
        Returns:
            Optional[str]: UNC path usable from Windows, or None on failure.
        """
        if not os.path.isabs(linux_path):
            return None

        with suppress(Exception):
            result = subprocess.run(['wslpath', '-m', '/'], capture_output=True, text=True, check=True)
            root_unc_path = result.stdout.strip()
            parts = root_unc_path.strip('\\').split('\\')
            if len(parts) >= 2 and parts[0].lower() == 'wsl.localhost':
                distro = parts[1]
                relative_path = linux_path.lstrip('/')
                return f"\\\\wsl.localhost\\{distro}\\{relative_path.replace('/', '\\')}"

        return None

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
            temp_path = tempfile.mkdtemp(prefix=PackageGlobals.TEMP_PREFIX)
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
                if entry.is_dir() and entry.name.startswith(PackageGlobals.TEMP_PREFIX):
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

    def print(self, *args, **kwargs):
        """Prints only if not in NON_INTERACTIVE_ONE_COMMAND mode."""
        if self.sdk.auto_forge.work_mode != AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION:
            print(*args, **kwargs)

    def print_same_line(self, *args, sleep_after: float = 0.001, **kwargs):
        """
        Print text on the same terminal line by:
        - Moving cursor to start of line
        - Clearing the current line
        - Printing text without newline
        - Returning cursor to line start
        """

        if self.sdk.auto_forge.work_mode != AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION:
            print(*args, **kwargs)
        else:
            sep = kwargs.get('sep', ' ')
            end = kwargs.get('end', '')
            text = sep.join(str(arg) for arg in args) + end

            text = text.rstrip('\r\n')  # Strip trailing line breaks
            sys.stdout.write('\r\033[K')  # Move to start and clear line
            sys.stdout.write(text)  # Print text
            sys.stdout.write('\r')  # Return cursor to start
            sys.stdout.flush()  # Ensure it's written out

            if sleep_after > 0:
                time.sleep(sleep_after)

    def set_cursor(self, visible: bool = False):
        """
        Sets the visibility of the terminal cursor using ANSI escape codes.
        Args:
        visible (bool): If True, shows the cursor. If False, hide the cursor.
        """
        # Method is not applicable when automating a command
        if self.sdk.auto_forge.work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION:
            return

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

    def decompress_archive(self, archive_path: str, destination_path: Optional[str] = None, delete_after: bool = False,
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
                            update_progress(f"{PurePosixPath(member.filename).name}")
                        zf.extract(member, path=destination_path)
                        self._logger.debug(f"Extracting {member.filename} to {destination_path}")
                if delete_after:
                    os.remove(archive_path)

            elif tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path, 'r:*') as tf:
                    while True:
                        member = tf.next()
                        if member is None:
                            break
                        if update_progress:
                            update_progress(f"{PurePosixPath(member.name).name}")

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
        # noinspection HttpUrlsUsage
        """
        Parses an endpoint string of the form 'host:port' or a full URL like 'http://host:port',
        and returns an AddressInfo tuple.
        Args:
            endpoint (Optional[str]): The endpoint string or URL to parse.
        Returns:
            Optional[AddressInfoType]: A named tuple containing:
                - host (str): Hostname or IP address.
                - port (int): TCP port number.
                - endpoint (str): Reconstructed 'host:port' string.
                - is_host_name (bool): True if host is a name, False if it's an IP address.
                None if the input is invalid.
        """
        if endpoint is None:
            return None

        # If it's a full URL (e.g., http://user:pass@host:port)
        if "://" in endpoint:
            parsed = urlparse(endpoint)
            host = parsed.hostname
            port = parsed.port
            url = endpoint
            if not host or not port:
                return None
        else:
            # Assume raw host:port
            if ':' not in endpoint:
                return None
            host, port_str = endpoint.rsplit(":", 1)
            if not port_str.isdigit():
                return None
            port = int(port_str)
            if not (1 <= port <= 65535):
                return None
            # noinspection HttpUrlsUsage
            url = f"http://{host}:{port}"

        # Check if host is an IP
        is_ip = bool(re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", host))
        if is_ip:
            if not all(0 <= int(octet) <= 255 for octet in host.split(".")):
                return None  # Invalid IP

        return AddressInfoType(
            host=host,
            port=port,
            endpoint=f"{host}:{port}",
            is_host_name=not is_ip,
            url=url
        )

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
    def strip_ansi(text: Optional[str], bare_text: bool = False) -> Optional[str]:
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
        if not text:
            return text

        def _recover_warning_flag(match):
            """ # Extract and preserve [-W...warning...] from broken [https://...] blocks """
            url = match.group(1)
            warning_match = re.search(r'(-W[\w\-]+)', url)
            return f"[{warning_match.group(1)}]" if warning_match else ""

        text = re.sub(r'\[(https?://[^]]+)]', _recover_warning_flag, text).strip()

        # Optionally reduce to printable ASCII
        if bare_text:
            allowed = set(string.ascii_letters + string.digits + string.punctuation + ' \t\n')
            text = ''.join(c for c in text if c in allowed)

        return text.strip()

    def print_banner(self, text: str, font_name: str = "ansi_shadow", clear_screen: bool = False,
                     terminal_title: Optional[str] = None) -> None:
        """
        Displays an ASCII logo rendered from `text` using pyfiglet and a consistent horizontal RGB gradient
        (same for every line, from dark to bright).
        Args:
            text (str): The text to render as ASCII art.
            font_name (str): The pyfiglet font name to use. Defaults to 'ansi_shadow'.
            clear_screen (bool): If True, clears the screen before printing.
            terminal_title (Optional[str]): Optional terminal window title to set.
        """

        # Only apply terminal effects in non-automatic sessions
        if self.auto_forge.work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION:
            return

        if not text:
            return

        # We must have the ANSI codes for this to work
        if self._ansi_codes is None:
            return

        if clear_screen:
            sys.stdout.write(self._ansi_codes.get('SCREEN_CLS_SB'))
        sys.stdout.write('\n')

        # Generate ASCII art using pyfiglet
        banner_lines: Optional[list] = None
        with suppress(Exception):
            fig = Figlet(font=font_name)
            banner_lines = fig.renderText(text).splitlines()

        if not isinstance(banner_lines, list):  # Suppressed exception
            return

        # Pick a base color and brighten it across the line width
        r_base, g_base, b_base = (random.randint(0, 100) for _ in range(3))
        r_delta, g_delta, b_delta = (random.randint(80, 155) for _ in range(3))

        max_line_len = max(len(line) for line in banner_lines)

        def get_rgb_gradient(pos, width):
            """ Computes an RGB ANSI color escape sequence based on horizontal gradient position. """
            t = pos / max(1, width - 1)
            r = int(r_base + r_delta * t)
            g = int(g_base + g_delta * t)
            b = int(b_base + b_delta * t)
            return f"\033[38;2;{r};{g};{b}m"

        for line in banner_lines:
            colored_line = "".join(f"{get_rgb_gradient(x, max_line_len)}{ch}"
                                   for x, ch in enumerate(line))
            sys.stdout.write(colored_line + "\033[0m\n")

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
    def get_text_width(text: str, tab_spaces: int = 4) -> Optional[int]:
        """
        Calculates the "display width" of a string, factoring in ANSI escape codes,
        emojis, and tabs. It uses wcwidth for accurate Unicode character width.
        Args:
            text: The input string.
            tab_spaces: The number of spaces a tab character (\\t) should represent.
        Returns:
            The raw character count as an integer, or None if an unexpected error occurs.
            Any exceptions are forwarded to the caller.
        """
        try:
            # Regular expression to find ANSI escape codes
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

            # Remove ANSI escape codes
            cleaned_text = ansi_escape.sub('', text)

            width = 0
            for char in cleaned_text:
                if char == '\t':
                    width += tab_spaces
                else:
                    # Use wcwidth to get the display width of the character
                    char_width = wcswidth(char)
                    if char_width == -1:
                        # -1 means the character is not printable or has an indeterminate width.
                        # This might indicate a problem or a character that shouldn't be displayed.
                        # For a general purpose method, we might treat it as 0 or 1,
                        # depending on the desired behavior.
                        # For now, let's treat it as 0 (doesn't occupy space).
                        width += 0
                    else:
                        width += char_width
            return width
        except Exception as parser_error:
            # Re-raise any unexpected exceptions to the caller
            raise parser_error from parser_error

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

    def copy_files(self, source: Union[Path, str], destination: Union[Path, str], pattern: Union[str, list[str]],
                   descend: bool = False) -> Optional[int]:
        """
        Copies files from a source path to a destination path based on wildcard patterns.
        Args:
            source: The path to the source directory. Must exist.
            destination: The path to the destination directory. Will be created as needed.
            pattern: A single wildcard pattern (e.g., '*.json') or a list of wildcard patterns.
            descend: If true, searches for patterns in all subfolders of 'source_path'
                     and creates relative paths under 'destination_path' for matches.
        Returns:
            The number of files copied if no exception occurred, otherwise None.
        """
        source = Path(source)
        destination = Path(destination)
        copied_files_count = 0

        if not source.exists():
            raise FileNotFoundError(f"Source path '{source}' does not exist.")

        destination.mkdir(parents=True, exist_ok=True)
        patterns = [pattern] if isinstance(pattern, str) else pattern

        try:
            if descend:
                for root, _, _ in os.walk(source):
                    root_path = Path(root)
                    current_relative_path = root_path.relative_to(source)
                    current_destination_dir = destination / current_relative_path
                    current_destination_dir.mkdir(parents=True, exist_ok=True)

                    for p in patterns:
                        for file_path in root_path.glob(p):
                            if file_path.is_file():
                                shutil.copy2(file_path, current_destination_dir)
                                copied_files_count += 1
            else:
                for p in patterns:
                    for file_path in source.glob(p):
                        if file_path.is_file():
                            shutil.copy2(file_path, destination)
                            copied_files_count += 1

            return copied_files_count

        except Exception as copy_error:
            self._logger.error(f"Copy files failed with : {str(copy_error)}")
        return None

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
        json_viewer_tool: Path = PackageGlobals.VIEWERS_PATH / "json_viewer.py"

        if not json_viewer_tool.exists():
            return 1

        try:
            if isinstance(json_path_or_data, str):

                # Since we need to use classes from modules that may not be directly imported at startup,
                # we retrieve their instances dynamically from the registry.
                if not self.sdk.variables:
                    raise RuntimeError("required component instances could not be retrieved for this operation")

                title = self.sdk.variables.expand(key=json_path_or_data,
                                                  quiet=True) if title is not None else "JSON Viewer"
                json_file_path: Optional[str] = self.sdk.variables.expand(key=json_path_or_data, quiet=True)
                if json_file_path and os.path.exists(json_file_path):
                    json_file_path = os.path.abspath(json_file_path)
                    json_path_or_data = self.sdk.jsonc_processor.render(file_name=json_file_path)

            if isinstance(json_path_or_data, (dict, list)):
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

    def resolve_help_file(self, relative_path: Union[str, Path]) -> Optional[Path]:
        """
        Returns the path to a help (markdown) file based on its relative name if that the file was found.
        Args:
            relative_path (str, Path): Relative path to the help file.
            e.g. If we got 'commands/tool
        Returns:
            str: The resolved path to the .md help file if the file exists, else None.
        """

        def _to_path_list(_v: Union[str, list[str], None]) -> list[Path]:
            """Normalize str, list of str, or None into list of Path objects."""
            if _v is None:
                return []
            if isinstance(_v, str):
                return [Path(_v)]
            return [Path(p) for p in _v]

        # Since we need to use classes from modules that may not be directly imported at startup,
        # we get their instance through centralized 'sdk' object.

        if not self.sdk.variables:
            raise RuntimeError("required component instances could not be retrieved for this operation")

        # Get a list of all path which ware tagged as 'help'
        help_paths = _to_path_list(self.sdk.variables.get_by_folder_type(AutoForgFolderType.HELP))

        for help_path in help_paths:
            help_file_path = help_path / Path(relative_path)

            # Must have a markdown (.md) extension
            if not help_file_path.exists() or help_file_path.suffix.lower() != ".md":
                continue

            return help_file_path

        return None

    def show_markdown_file(self, path: Union[str, Path]) -> Optional[int]:
        """
        Displays a markdown file by spawning a separate python that execute textual markdown viewer.
        Args:
            path (str): file path or relative path to the md file under registered help paths.
        Returns:
            int: 0 on success, else error or exception
        """
        markdown_viewer_tool = PackageGlobals.VIEWERS_PATH / "md_viewer.py"
        if not markdown_viewer_tool.exists():
            raise RuntimeError("required viewer could not be found")

        # Expand and convert to 'Path' object'
        resource_path: Optional[str] = self.sdk.variables.expand(key=str(path), quiet=True)
        markdown_file_path: Optional[Path] = Path(resource_path)  # Convert to Path

        if not markdown_file_path.exists():
            # Resolve the file path
            markdown_file_path = self.resolve_help_file(markdown_file_path)
            if markdown_file_path is None or not markdown_file_path.exists():
                raise RuntimeError(f"markdown file '{resource_path}' could not be found")

        # Make sure we're dealing with something that looks like a markdown
        if not (markdown_file_path.is_file() and markdown_file_path.suffix.lower() == '.md'):
            raise RuntimeError(f"Input file '{markdown_file_path}' is not a valid Markdown file.")

        if markdown_file_path.stat().st_size > 64 * 1024:
            raise RuntimeError("markdown file size too large")

        status = subprocess.run(["python3", str(markdown_viewer_tool), "--markdown", str(markdown_file_path)],
                                env=os.environ.copy())
        return_code = status.returncode

        # Reset TTY settings
        os.system("stty sane")
        sys.stdout.flush()
        sys.stderr.flush()
        return return_code

    def show_status(self, message: Optional[str] = None,
                    status_type: PromptStatusType = PromptStatusType.INFO,
                    expire_after: float = 0.0,
                    erase_after: bool = False) -> None:
        """
        Briefly display or clear a styled status message at the top of the terminal.
        Args:
            message (Optional[str]): The message to display. If None, line 0 is cleared.
            status_type (PromptStatusType): Type of message (affects color).
            expire_after (float): Seconds to keep message visible. Ignored when message is None.
            erase_after (bool): Whether to erase message after it wqs shown.
        """

        # Only apply terminal effects in non-automatic sessions
        if self.auto_forge.work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION:
            return

        with self._show_status_lock:
            console = Console(force_terminal=True)
            term_width = shutil.get_terminal_size().columns

            # Save cursor position
            sys.stdout.write("\0337")
            sys.stdout.write("\033[0;0H")  # Move to top-left corner (line 0)

            if message is None:
                sys.stdout.write("\033[2K")  # Erase entire line
                sys.stdout.write("\0338")  # Restore cursor
                sys.stdout.flush()
                return

            # Truncate message to fit terminal width (with 1-char margin)
            truncated = (message[:term_width - 1] + "") if len(message) > term_width else message
            padded = truncated.ljust(term_width)

            # Style map per status type
            style_map = {
                PromptStatusType.INFO: "bold white on blue",
                PromptStatusType.DEBUG: "black on yellow",
                PromptStatusType.ERROR: "bold white on red",
            }

            style = style_map.get(status_type, "bold blue on white")
            status_line = Text(padded, style=style)

            console.print(status_line, end="")  # Write styled line
            sys.stdout.write("\0338")  # Restore cursor
            sys.stdout.flush()

            if expire_after > 0:
                time.sleep(expire_after)
            if erase_after:
                self.show_status(None)

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
    def clang_formatter(code: str, indent: int = 4, use_tabs: bool = False) -> str:
        """
        Basic C/C++ formatter:
        - Indents after '{' and de-dents after '}'
        - Handles multi-line definitions and statements
        - Supports switch/case indentation
        - Keeps pre-processor lines and block comments aligned
        - Handles 'else', 'else if', 'while (...)' after closing brace properly
        - Compacts excessive blank lines
        """
        indent_str = "\t" if use_tabs else " " * indent
        lines = code.strip().splitlines()
        formatted_lines = []
        level = 0
        hanging_paren = 0
        blank_count = 0

        for line in lines:
            line = line.rstrip()
            stripped = line.strip()

            # Compact multiple blank lines to 1
            if not stripped:
                blank_count += 1
                if blank_count > 1:
                    continue
                formatted_lines.append("")
                continue
            else:
                blank_count = 0

            # Keep pre-processor lines at column 0
            if stripped.startswith("#"):
                formatted_lines.append(stripped)
                continue

            # Dedent before a closing brace
            if stripped.startswith("}"):
                level = max(level - 1, 0)

            # Check for special keywords that should not be indented further
            if (formatted_lines and
                    formatted_lines[-1].strip().endswith("}") and
                    stripped.startswith(("else", "else if", "while"))):
                level = max(level - 1, 0)

            # Determine indent level for current line
            current_indent = level

            # Special case: 'case:' and 'default:' should be one level deeper inside switch
            if stripped.startswith("case ") or stripped.startswith("default:"):
                current_indent += 1

            # Add indented line
            formatted_lines.append(f"{indent_str * current_indent}{stripped}")

            # Track parentheses for multi-line declarations
            open_parens = stripped.count("(")
            close_parens = stripped.count(")")
            hanging_paren += open_parens - close_parens

            # Increase indent after opening brace (unless in a hanging paren block)
            if stripped.endswith("{") and hanging_paren == 0:
                level += 1

        return "\n".join(formatted_lines)

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

    @staticmethod
    def is_recent_event(event_date: Optional[datetime] = None, days_back: int = 1) -> bool:
        """
        Returns True if 'event_data' is within the past 'days_back' days (inclusive), and not in the future.
        Args:
            event_date (datetime, optional): The event datetime (must be timezone-aware).
            days_back (int): Number of days back to consider valid.
        Returns:
            bool: True if event_data is within [now - days_back, now], else False.
        """
        if not isinstance(event_date, datetime) or event_date.tzinfo is None:
            return False
        if days_back < 0:
            return False

        now = datetime.now(UTC)
        window_start = now - timedelta(days=days_back)
        return window_start <= event_date <= now

    @staticmethod
    def find_pattern_in_line(line: str, patterns: list[str]) -> Optional[Tuple[str, int]]:
        """
        Searches for the first occurrence of any pattern (case-insensitive) in the given line.
        Args:
            line (str): The text line to search.
            patterns (list[str]): List of patterns to search for.

        Returns:
            Optional[Tuple[str, int]]: A tuple of (matched_pattern_original_case, position_in_line),
                                       or None if no pattern is found.
        """
        line_lower = line.lower()
        for pattern in patterns:
            idx = line_lower.find(pattern.lower())
            if idx != -1:
                # Return the pattern from the original line based on its position
                return line[idx:idx + len(pattern)], idx
        return None

    # noinspection SpellCheckingInspection
    def truncate_for_terminal(self, text: Optional[str], reduce_by_chars: int = 0,
                              fallback_width: int = 120) -> Optional[str]:
        """
        Truncates a string to fit within the terminal width, adding "..." if truncated.
        Handles truncation on a line-by-line basis, preserving original newlines or lack thereof, and attempts to
        correctly handle ANSI escape codes by calculating visible width and preserving codes at the end of lines.
        Args:
            text: The string to truncate.
            reduce_by_chars: An optional number of characters to reduce the effective
                             terminal width by (e.g., for padding or other elements).
            fallback_width: The width to use if the terminal size cannot be determined.
                            Defaults to 120.
        Returns:
            The truncated string.
        """

        def _get_visible_width(_text: Optional[str]) -> int:
            """
            Calculates the visible width of a string by removing ANSI escape codes.
            This assumes escape codes don't affect character width (e.g., no double-width chars).
            """
            return len(self._pre_compiled_escape_patterns.sub('', text))

        if not isinstance(text, str):
            return text

        # Calculate width
        terminal_size = shutil.get_terminal_size(fallback=(fallback_width, 24))
        terminal_width = terminal_size.columns

        # Calculate the effective width available for the text
        effective_width = terminal_width - reduce_by_chars

        # Account for the "..." that will be added if truncation occurs
        dots_length = 3
        dots = "." * dots_length

        # Pattern to extract the trailing newline sequence (including \r\n, \n, \r)
        newline_pattern = re.compile(r'(\r?\n|\r)$')

        truncated_segments = []
        # splitlines(keepends=True) correctly separates lines and keeps their specific endings
        segments = text.splitlines(keepends=True)

        # noinspection GrazieInspection
        for segment in segments:
            # Separate actual content from its potential trailing newline
            line_content_with_codes = segment
            line_ending = ""
            match = newline_pattern.search(segment)
            if match:
                line_ending = match.group(0)
                line_content_with_codes = segment[:-len(line_ending)]

            # Extract trailing escape codes (like \x1b[K) that should be preserved
            # This is tricky: we want to preserve codes that clear the line AFTER the content.
            # We assume these codes are at the very end of the *content* part.
            trailing_codes = ""
            content_without_trailing_codes = line_content_with_codes

            # Find all escape sequences in the content part
            all_codes_in_content = list(self._pre_compiled_escape_patterns.finditer(line_content_with_codes))

            if all_codes_in_content:
                # Check if the last found code is at the very end of the content
                last_match = all_codes_in_content[-1]
                if last_match.end() == len(line_content_with_codes):
                    trailing_codes = last_match.group(0)
                    content_without_trailing_codes = line_content_with_codes[:last_match.start()]
                # else: The last code is not at the very end, so we treat it as part of the content
                # that might be truncated. This is a simplification; a full solution might
                # need to render and measure, or parse more deeply.

            # Calculate visible width of the content *without* trailing codes
            visible_width = _get_visible_width(content_without_trailing_codes)

            # Perform truncation based on visible width
            if visible_width > effective_width:
                # Determine target visible length for the actual text part
                target_visible_length = effective_width - dots_length

                if target_visible_length < 0:  # Not even enough space for dots
                    # Fill with as many dots as possible, preserving trailing codes and ending
                    truncated_segment_text = "." * effective_width
                else:
                    current_visible_length = 0
                    truncated_text_chars = []
                    # Iterate through the characters of the string (excluding trailing codes)
                    # and build up the truncated string while tracking visible width.
                    idx = 0
                    while idx < len(content_without_trailing_codes) and current_visible_length < target_visible_length:
                        char = content_without_trailing_codes[idx]
                        if char == '\x1b' and self._pre_compiled_escape_patterns.match(content_without_trailing_codes,
                                                                                       idx):
                            # It's the start of an escape sequence, find its end
                            match: re.Match = self._pre_compiled_escape_patterns.match(content_without_trailing_codes,
                                                                                       idx)
                            if match:
                                # Add the full escape sequence without counting it towards visible width
                                truncated_text_chars.append(match.group(0))
                                idx = match.end()
                                continue

                        # Regular character, count it
                        truncated_text_chars.append(char)
                        current_visible_length += 1
                        idx += 1

                    truncated_segment_text = "".join(truncated_text_chars) + dots

                # Combine truncated text with preserved trailing codes and line ending
                truncated_segments.append(truncated_segment_text + trailing_codes + line_ending)
            else:
                # No truncation needed for this segment's visible content.
                # Keep the segment as is (including its original codes and ending).
                truncated_segments.append(segment)

        return "".join(truncated_segments)
