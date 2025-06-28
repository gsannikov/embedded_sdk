"""
Script:         variables.py
Author:         AutoForge Team

Description:
    Core module is designed to initialize variables with specific attributes and values,
    prevent duplicates, and allow for quick lookup and modification through methods that leverage binary search.
    It also handles dynamic changes to the variables' configuration by maintaining a sorted state and updating search
    keys accordingly.
"""

import os
import re
import threading
from bisect import bisect_left
from contextlib import suppress
from dataclasses import asdict
from typing import Any, Optional, Union, Iterator
from urllib.parse import urlparse

# Third-party
from jsonschema.validators import validate

# AutoForge imports
from auto_forge import (
    AutoForgFolderType, AutoForgeModuleType, AutoForgeWorkModeType, CoreJSONCProcessor, CoreLogger, CoreTelemetry,
    CoreModuleInterface, CoreRegistry, CoreToolBox, VariableFieldType, VariableType
)

AUTO_FORGE_MODULE_NAME = "Variables"
AUTO_FORGE_MODULE_DESCRIPTION = "Variables Manager"


class CoreVariables(CoreModuleInterface):
    """
    Manages a collection of variables derived from a JSON dictionary and provides
    functionality to manipulate these variables efficiently. The class supports operations such
    as adding, removing, and updating variables, ensuring data integrity and providing thread-safe access.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        self._variables: list[VariableFieldType] = []  # Inner variables stored as a sorted listy of objects
        super().__init__(*args, **kwargs)

    def _initialize(self, workspace_path: str, solution_name: str, work_mode: AutoForgeWorkModeType) -> None:
        """
        Initialize the 'Variables' class using a configuration JSON file.
        Args:
            workspace_path (str): The workspace path.
            solution_name (str): Solution name.
        Note:
            These core modules may be initialized before the main AutoForge controller is constructed.
            As such, they must receive configuration data directly from the top-level auto_forge bootstrap logic
            to support early startup execution.
        """
        try:
            self._registry = CoreRegistry.get_instance()
            self._telemetry = CoreTelemetry.get_instance()
            self._processor = CoreJSONCProcessor.get_instance()
            self._tool_box = CoreToolBox.get_instance()
            self._core_logger = CoreLogger.get_instance()
            self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)

            # Dependencies check
            if None in (self._registry, self._telemetry, self._processor, self.auto_forge.configuration):
                raise RuntimeError("failed to instantiate critical dependencies")

            self._ignore_path_errors: bool = False
            self._essential_variables_essential_variables: Optional[list[dict]] = None
            self._lock: threading.RLock = threading.RLock()  # Initialize the re-entrant lock
            self._search_keys: Optional[list[tuple[bool, str]]] = None  # Allow for faster binary search
            self._configuration: dict[str, Any] = self.auto_forge.configuration

            # Set to ignore invalid path when in environment t creation mode
            if work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_SEQUENCE:
                self._ignore_path_errors = True

            # Get the workspace from AutoForge
            self._workspace_path = workspace_path
            self._solution_name = solution_name

            # Are we allow to override 'is_path' using auto-detection?
            self._auto_categorize: bool = bool(self._configuration.get('auto_categorize', False))

            # Get essential variables list from the package configuration
            if "essential_variables" in self._configuration:
                essential_variables_data: list[list] = self._configuration["essential_variables"]
                if essential_variables_data is None:
                    raise ValueError("could not get essential_variables from the project package configuration")
                # Convert the raw list on a variable recognized dictionary
                self._essential_variables = self._get_from_list(essential_variables_data)

            if self._essential_variables is None:
                raise RuntimeError("missing essential_variables")

            # Reset and initialize the internal database.
            self._reset()

            # Register this module with the package registry
            self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                           auto_forge_module_type=AutoForgeModuleType.CORE)

            # Inform telemetry that the module is up & running
            self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

        except Exception as exception:
            self._variables = None
            raise RuntimeError(f"variables error {exception}") from exception

    @staticmethod
    def _get_from_list(compressed_list: list[list]) -> list[dict]:
        """
        Inflate a compressed list of variable entries into a list of dictionaries.
        Each inner list must follow the field order:
            [name, value, description, path_must_exist, create_path_if_not_exist]
        Args:
            compressed_list (list[list]): List of compressed variable records.
        Returns:
            list[dict]: List of dictionaries representing full variable records.
        """
        keys = ["name", "value", "description", "path_must_exist", "create_path_if_not_exist", "folder_type"]
        defaults = [None, None, None, True, True, AutoForgFolderType.UNKNOWN]

        if not isinstance(compressed_list, list) or not all(isinstance(row, list) for row in compressed_list):
            raise TypeError("expected a list of lists")

        result: list[dict] = []
        for i, entry in enumerate(compressed_list):
            if len(entry) > len(keys):
                raise ValueError(f"too many elements in entry at index {i}: {entry}")

            full_entry = entry + defaults[len(entry):]
            result.append(dict(zip(keys, full_entry)))

        return result

    def _load_from_dictionary(self, var_dict: list[dict]) -> None:
        """
        Load variables into internal storage from a list of dictionaries.
        Each dictionary must contain at least 'name' and 'value', and may optionally include
        'description', 'is_path', 'path_must_exist', and 'create_path_if_not_exist'.
        Arfs:
            var_dict (list[dict]): List of dictionaries.
        """
        if not isinstance(var_dict, list) or not all(isinstance(v, dict) for v in var_dict):
            raise TypeError("Expected a list of dictionaries representing variables.")

        for i, var in enumerate(var_dict):
            key: str = var.get("name")
            value: str = var.get("value")

            if not key or not value:
                raise ValueError(f"Variable entry at index {i} is missing 'name' or 'value': {var}")

            # Collect any unexpected fields as additional kwargs
            known_fields = {"name", "value", "description", "is_path", "path_must_exist", "create_path_if_not_exist",
                            "folder_type"}
            extra_kwargs = {k: v for k, v in var.items() if k not in known_fields}

            folder_type = var.get("folder_type")
            if not isinstance(folder_type, AutoForgFolderType):
                folder_type = AutoForgFolderType.from_str(var.get("folder_type", None))

            self.add(key=key, value=value, description=var.get("description", "Description not provided"),
                     is_path=var.get("is_path", True), path_must_exist=var.get("path_must_exist"),
                     create_path_if_not_exist=var.get("create_path_if_not_exist"), folder_type=folder_type,
                     **extra_kwargs, )

    def _classify_variable(self, value: Optional[Any]) -> VariableType:
        """
        Attempt to classify a variable type based on its value.
        Args:
            value (Optional[Any]): The value to analyze.
        Returns:
            VariableType: The detected type of the variable. Returns UNKNOWN if the type cannot be determined.
        """
        if value is None:
            return VariableType.UNKNOWN
        if isinstance(value, int):
            return VariableType.INT
        if isinstance(value, float):
            return VariableType.FLOAT
        if not isinstance(value, str):
            return VariableType.UNKNOWN

        val = value.strip()

        # Check for Unix path
        if self._tool_box.looks_like_unix_path(val):
            return VariableType.PATH

        # Check for URL
        with suppress(Exception):
            parsed = urlparse(val)
            if parsed.scheme and parsed.netloc:
                return VariableType.URL

        # Check for integer-like string
        if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
            return VariableType.INT

        # Check for float-like string
        with suppress(Exception):
            if "." in val or "e" in val.lower():
                float(val)
                return VariableType.FLOAT

        # Windows Path
        if (len(val) >= 3
            and val[1] == ':'
            and val[2] in ('\\', '/')
            and val[0].isalpha()
        ) or val.startswith('\\\\'):
            return VariableType.WIN_PATH

        # When you have eliminated the impossible, whatever remains, however improbable, must be the truth
        return VariableType.STRING

    @staticmethod
    def _to_string(value: Optional[Any]) -> Optional[str]:
        """
        Safely converts a given value to a string.
        Args:
            value (Optional[Any]): The value to be converted.
        Returns:
            Optional[str]: The string representation of the value, or None if conversion is not possible.
        """
        # When it's already a string, return as-is
        if isinstance(value, str):
            return value

        # None? return None explicitly
        if value is None:
            raise RuntimeError("value cannot be None")
        try:
            return str(value)
        except Exception as conversion_error:
            raise RuntimeError(f"failed to convert {value} to string {conversion_error!s}") from conversion_error

    def _get_index(self, key: str, flexible: bool = False) -> Optional[int]:
        """
        Finds a Variable index by its name using binary search.
        Args:
            key (str): The name of the Variable to find.
            flexible (bool): If True, allows partial matching of a variable prefix.
        Returns:
            Optional[int]: The index of the object if found, -1 otherwise.
        """
        with self._lock:
            if self._variables is None or self._search_keys is None or len(self._variables) == 0:
                return -1

            # Try exact match first
            key_to_find = (False, key)
            index = bisect_left(self._search_keys, key_to_find)

            if index != len(self._variables) and self._variables[index].key == key:
                return index

            # If flexible search requested, attempt prefix matching
            if flexible:
                # Check backwards from the insert position
                for prefix_len in range(len(key), 0, -1):
                    candidate_name = key[:prefix_len]
                    key_candidate = (False, candidate_name)
                    idx = bisect_left(self._search_keys, key_candidate)
                    if idx != len(self._variables):
                        var_at_index: VariableFieldType = self._variables[idx]
                        if var_at_index.key == candidate_name:
                            return idx

            return -1

    def _refresh(self):
        """
        Sorts the internal list of variable objects and refreshes the search keys to enable fast binary search.
        This is required after any modification (insertion, deletion, or change) to the variables list.
        """
        with self._lock:
            if self._variables is None or not self._variables:
                return

            # Sort the variables list based on whether the name is None and the name itself.
            self._variables.sort(key=lambda var: (var.key is None, var.key or ""))

            # Prepare a list of keys for searching after sorting, treating None names as empty strings.
            self._search_keys = [(var.key is None, var.key or "") for var in self._variables]

    def _reset(self):
        """
        Purge the variables list, invalidate all keys and reload the essentials.
        """
        with self._lock:
            self._variables = None
            self._search_keys = None

            # Statically add the solution name and the workspace path
            self.add(key="SOLUTION_NAME", value=self._solution_name, description="Solution name", is_path=False)
            self.add(key="PROJ_WORKSPACE", value=self._workspace_path, description="Workspace path",
                     create_path_if_not_exist=False)

            # Load essential
            self._load_from_dictionary(self._essential_variables)

    def load_from_file(self, config_file_name: str, reset: bool = False,
                       variables_schema: Optional[dict] = None) -> Optional[int]:
        """
        Constructs or rebuilds the configuration data based on a JSONc file.

        If `rebuild` is True, any existing configuration is discarded before rebuilding,
        ensuring that the list is refreshed entirely from the raw data. If `rebuild` is False
        and `_variables` is already initialized, the method raises a RuntimeError.
        Args:
            config_file_name(str): JSON file containing variables to load
            reset (bool): Specifies whether to forcibly rebuild the variable.
            variables_schema (dict): If specified we will validate the variables against it.
        Returns:
            Optional[int]: The count of variables successfully initialized and stored in the
                           `_variables` list if the operation is successful, otherwise 0.
        """
        with (self._lock):

            # Preprocess
            variables_root: Optional[dict[str, Any]] = self._processor.render(file_name=config_file_name)
            if variables_root is None:
                raise RuntimeError(f"unable to load variables file: {config_file_name}")

            # If a schema was specified, use it to validate the variables structure
            if variables_schema is not None:
                validate(instance=variables_root, schema=variables_schema)

            # Extract variables, defaults and other options
            variables_data: Optional[list[dict]] = variables_root.get('variables', [])
            if not isinstance(variables_data, list):
                raise RuntimeError(f"could not find list of variable in: '{config_file_name}'")

            # Reset and initialize the internal database.
            if reset:
                self._reset()

            # Load the dictionary
            self._load_from_dictionary(variables_data)
            return len(variables_data)

    def get(self, key: str, default: Optional[str] = None,
            flexible: bool = False, quiet: Optional[bool] = None) -> Optional[str]:
        """
        Gets a Variable value by its key. If not found, attempts to expand as a variable.
        Args:
            key (str): The name of the Variable to find.
            default (Optional[str]): Value to return if the key is not found or unresolved.
            flexible (bool): If True, allows partial matching of a variable prefix.
            quiet (bool): If True, exceptions will be suppressed.
        Returns:
            Optional[str]: The variable value as a string, or the default if not found.
        """

        if quiet is None:
            quiet = default is not None

        with self._lock:
            index = self._get_index(key=key, flexible=flexible)
            if index == -1:
                # Try again without initial $ if it exists
                if key.startswith('$'):
                    key = key[1:]
                    index = self._get_index(key=key, flexible=flexible)
                    if index == -1:

                        # Attempt to resolve as environment, restore the '$' as needed
                        env_var = f"${key}" if not key.startswith("$") else key
                        # Expand variables and user home directory notations
                        expanded = os.path.expanduser(os.path.expandvars(env_var))
                        if expanded == env_var:  # No expansion occurred
                            if not quiet:
                                raise RuntimeError(f"variable '{env_var}' was not resolved or expanded")
                            return default
                        return expanded
                else:
                    if not quiet:
                        raise RuntimeError(f"variable '{key}' not found")
                    return default

            return self._to_string(self._variables[index].value)

    def get_by_folder_type(self, folder_type: Union[AutoForgFolderType, str]) -> Optional[Union[list[str], str]]:
        """
        Retrieves variable values whose folder_type matches the given type.
        Args:
            folder_type (AutoForgFolderType): The folder type to search for.
        Returns:
            Optional[Union[list[str], str]]:
                - None if no match is found.
                - str if a single match is found.
                - list of str if multiple matches are found.
        """

        if not isinstance(folder_type, AutoForgFolderType):
            folder_type = AutoForgFolderType.from_str(folder_type)

        with self._lock:
            matches = [
                self._to_string(var.value)
                for var in self._variables
                if var.folder_type == folder_type and var.value is not None
            ]

            if not matches:
                return None
            if len(matches) == 1:
                return matches[0]
            return matches

    def set(self, key: str, value: str) -> bool:
        """
        Update the value of a variable identified by its key.
        Args:
            key (str): The name of the variable to update.
            value (Any): The new value to assign to the variable.
        Returns:
            bool: True if the variable was found and updated, False otherwise.
        """
        with self._lock:
            index: int = self._get_index(key)
            if index == -1:
                raise RuntimeError(f"variable '{key}' not found")

            # Update the variables list
            self._variables[index].value = value
            return True

    def iter_matching_keys(self, clue: str) -> Iterator[VariableFieldType]:
        """
        Yields VariableFieldType entries whose key matches the given clue (prefix-based).
        Supports glob-style '*' wildcard as a suffix.
        Args:
            clue (str): The clue to match - can use wildcards.
        """
        use_wildcard = clue.endswith('*')
        prefix = clue[:-1] if use_wildcard else clue

        for var in self._variables:
            if var.key and var.key.startswith(prefix):
                yield var

    def get_matching_keys(self, clue: str) -> list[VariableFieldType]:
        """ Gets a list of variables with a key matching the given clue (prefix-based)."""
        return [var for var in self.iter_matching_keys(clue) if var.key is not None]

    def add(self, key: str, value: str,
            is_path: Optional[bool] = None,
            description: Optional[str] = None,
            path_must_exist: Optional[bool] = True,
            create_path_if_not_exist: Optional[bool] = True,
            folder_type: Optional[AutoForgFolderType] = AutoForgFolderType.UNKNOWN,
            **_kwargs) -> Optional[bool]:
        """
        Adds a new Variable to the list if no variable with the same key name already exists.
        Args:
            key (str): The name of the variable to update.
            value (str): The new value to assign to the variable.
            description (str): Description of the variable.
            is_path (bool): Whether the variable is a path or not.
            path_must_exist (bool): If True, the path will be validated.
            create_path_if_not_exist (bool): If True, the path will be created.
            folder_type (AutoForgFolderType): The type of the path, when it's a path.
            _kwargs(optional): Additional keyword arguments to pass to the variable.
        Returns:
            Optional[bool]: Returns True if the variable was successfully added. Returns
                            None if a variable with the same name already exists.
        """

        new_var = VariableFieldType()
        new_var.key = key.strip().upper()

        # Auto-detect the variable type
        classification: VariableType = self._classify_variable(value=value)
        if self._classify_variable:
            if classification == VariableType.UNKNOWN:
                raise RuntimeError(f"variable '{key}' with value '{value}' could not be classified")
            # Internally we only have path / non path
            elif classification == VariableType.PATH:
                is_path = True
            else:
                is_path = path_must_exist = False
                folder_type = AutoForgFolderType.UNKNOWN

        # Force defaults when not provided
        new_var.description = description if description is not None else "Description not specified"
        new_var.folder_type = folder_type if folder_type is not None else AutoForgFolderType.UNKNOWN
        new_var.is_path = is_path if is_path is not None else self._tool_box.looks_like_unix_path(value)
        new_var.path_must_exist = path_must_exist if path_must_exist is not None else True
        new_var.create_path_if_not_exist = create_path_if_not_exist if create_path_if_not_exist is not None else True
        new_var.type = classification
        new_var.kwargs = _kwargs

        # Normalize description field
        new_var.description = new_var.description.strip().capitalize()

        index = self._get_index(new_var.key)
        if index != -1:
            raise RuntimeError(f"variable '{new_var.key}' already exists at index {index}")

        # When it's not a path
        if not new_var.is_path:
            # Normalize other properties when variable is not a path
            new_var.path_must_exist = False
            new_var.create_path_if_not_exist = False
            new_var.folder_type = AutoForgFolderType.UNKNOWN
            new_var.value = value
        else:

            new_var.value = self.expand(value)

            # The variable should be treated as a path
            if not self._tool_box.looks_like_unix_path(new_var.value):
                raise RuntimeError(f"value '{new_var.value}' set by '{new_var.key}' does not look like a unix path")

            # Only enforce 'create_path_if_not_exist' and 'path_must_exist' directives during normal operation,
            # not during initial workspace creation.

            if not self._ignore_path_errors:
                if new_var.create_path_if_not_exist:
                    os.makedirs(new_var.value, exist_ok=True)

                if new_var.path_must_exist:
                    path_exist = os.path.exists(new_var.value)
                    if not path_exist:
                        if not new_var.create_path_if_not_exist:
                            raise RuntimeError(
                                f"path '{new_var.value}' required by '{key}' does not exist and marked as must exist")

        with self._lock:
            if self._variables is None:
                self._variables = []

            self._variables.append(new_var)
            self._refresh()
            return True

    def remove(self, key: str) -> Optional[bool]:
        """
        Removes a specified variable from the internal variables list if it exists.
        Args:
            key (str): The Variable name to be removed.
        Returns:
            Optional[bool]: Returns True if the variable was successfully removed.
                            Raises RuntimeError for any issues encountered.
        """

        if not self._is_initialized:
            raise RuntimeError("variables not initialized")

        with self._lock:
            index = self._get_index(key)
            if index == -1:
                raise RuntimeError(f"variable '{key}' not found")

            self._variables.pop(index)  # Remove it
            self._refresh()  # Update the list and the search dictionary

    def expand(self, key: Optional[str], allow_environment: bool = True, quiet: bool = False) -> Optional[str]:
        """
        Expands variables embedded within the input text and resolves the path.
        Supports both $VAR and ${VAR} syntax, correctly handling adjacent expansions.
        Args:
            key (str): The text to expand.
            allow_environment (bool): Use the system environment when a variable is not internally resolved.
            quiet (bool): If False, expansion misses will result in raising an exception.
        Returns:
            str: The expanded text, or None if input is invalid.
        """
        if not isinstance(key, str):
            return None

        length = len(key)
        result = []
        i = 0

        if length == 0:
            return key

        def _is_valid_shell_var_ref(text: str) -> bool:
            """ Validate variable expressed as {VAR} """
            if not text.startswith("${") or not text.endswith("}"):
                return False
            _var = text[2:-1]
            if not _var or "$" in _var or "{" in _var:
                return False
            return re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", _var) is not None

        def _expand_using_sys(_var: str) -> Optional[str]:
            """ Expand a single variable using system environment if it's exported """
            if not allow_environment:
                return None
            if _var in os.environ:
                return self._tool_box.get_expanded_path(os.environ[_var])
            return None

        def _resolve_var(_var: str) -> str:
            value = self.get(key=_var, quiet=True, flexible=False)
            if value is None:
                value = _expand_using_sys(_var)
            if value is None:
                if not quiet:
                    raise ValueError(f"variable '{_var}' could not be expanded")
                return ""
            return value

        while i < length:
            if key[i] == '$':
                #
                # Handle variables like '${VAR}'
                #
                if i + 1 < length and key[i + 1] == '{':
                    end_brace = key.find('}', i + 2)
                    if end_brace != -1:
                        candidate = key[i:end_brace + 1]
                        if not _is_valid_shell_var_ref(candidate):
                            if not quiet:
                                raise ValueError(f"invalid variable syntax: {candidate}")
                            result.append(key[i])
                            i += 1
                            continue
                        var_name = candidate[2:-1]
                        result.append(_resolve_var(var_name))
                        i = end_brace + 1
                        continue
                else:
                    #
                    # Handle variables like '$VAR'
                    #
                    j = i + 1
                    while j < length and (key[j].isalnum() or key[j] == '_'):
                        j += 1
                    if j > i + 1:
                        var_name = key[i + 1:j]
                        result.append(_resolve_var(var_name))
                        i = j
                        continue
            elif key[i] == '~':
                #
                # Expand '~' to the user's home directory, only if at start or after a separator
                #
                if i == 0 or not (key[i - 1].isalnum() or key[i - 1] in ['_', '$', '}']):
                    j = i + 1
                    # Capture optional username: ~ or ~user
                    while j < length and (key[j].isalnum() or key[j] in ('-', '_')):
                        j += 1
                    username = key[i + 1:j]
                    with suppress(Exception):
                        home = os.path.expanduser(f"~{username}")
                        result.append(home)
                        i = j
                        continue

            # Append literal character
            result.append(key[i])
            i += 1

        return ''.join(result)

    def export(self, as_env: bool = False) -> Union[list[dict], dict[str, str]]:
        """
        Exports the internal list of VariableFieldType instances.
        Args:
            as_env (bool): Returns a dictionary of {key: value} pairs suitable for subprocess environments.
        Returns:
            Union[list[dict], dict[str, str]]: Either a list of dictionaries or an env-compatible dict.
        """
        if not isinstance(self._variables, list):
            raise ValueError("storage empty, no variables to export")

        if as_env:
            return {var.key: var.value for var in self._variables if
                    isinstance(var, VariableFieldType) and var.key and var.value is not None}

        return [asdict(var) for var in self._variables if isinstance(var, VariableFieldType)]
