#!/usr/bin/env python3
"""
Script:         environment.py
Author:         Intel AutoForge team

Description: The Environment core module is designed to initialize variables with specific attributes and values,
    prevent duplicates, and allow for quick lookup and modification through methods that leverage binary search.
    It also handles dynamic changes to the variables' configuration by maintaining a sorted state and updating search
    keys accordingly.
"""

import logging
import os
import re
import threading
from bisect import bisect_left
from typing import Optional, Any, Dict, List, Tuple, Match

# Builtin AutoForge core libraries
import auto_forge
from auto_forge import ( JSONProcessorLib)

AUTO_FORGE_MODULE_NAME = "Environment"
AUTO_FORGE_MODULE_DESCRIPTION = "Environment core service"


class Variable:
    """
    Auxilery class to manage a single variable
    """

    def __init__(self):
        self.name: Optional[str] = None
        self.base_name: Optional[Any] = None  # Without the suffix
        self.description: Optional[str] = None
        self.value: Optional[Any] = None
        self.path_must_exist: Optional[bool] = None
        self.create_path_if_not_exist: Optional[bool] = None
        self.kwargs: Optional[Dict[str, Any]] = None  # Store unrecognized JSON properties


class VariablesLib:
    _instance = None
    _is_initialized = False
    _lock = threading.RLock()  # Initialize the re-entrant lock

    def __new__(cls, config_file_name: Optional[str] = None):
        """
        Basic class initialization in a singleton mode
        """

        if cls._instance is None:
            cls._instance = super(VariablesLib, cls).__new__(cls)
            cls._config_file_name: Optional[str] = config_file_name

        return cls._instance

    def __init__(self, config_file_name: Optional[str] = None):
        """
        Manages a collection of configuration variables derived from a JSON dictionary and provides
        functionality to manipulate these variables efficiently. The class supports operations such
        as adding, removing, and updating variables, ensuring data integrity and providing thread-safe
        access.
        """

        if not self._is_initialized:

            try:
                self._service_name: str = self.__class__.__name__
                self._auto_forge = auto_forge.AutoForge()

                # Initialize a logger instance
                self._logger: logging.Logger = logging.getLogger(AUTO_FORGE_MODULE_NAME)
                self._logger.setLevel(level=logging.DEBUG)
                self._workspace_path = self._auto_forge.get_workspace_path()
                self._base_config_file_name:Optional[str] = None
                self._variable_auto_prefix: bool = False  # Enable auto variables prefixing with the project name
                self._variable_prefix: Optional[str] = None  # Prefix auto added to all variables
                self._variable_capitalize_description: bool = True  # Description field formatting
                self._variables_defaults: Optional[dict] = None  # Optional default variables properties
                self._variable_force_upper_case_names: bool = False  # Instruct to force variables to be allways uppercased
                self._variables: Optional[list[Variable]] = None  # Inner variables stored as a sorted listy of objects
                self._search_keys: Optional[
                    List[Tuple[bool, str]]] = None  # Allow for faster binary search on the signatures list

                # Create an instance of the JSON preprocessing library
                self._processor: JSONProcessorLib = JSONProcessorLib()

                # Build variables list
                if self._config_file_name is not None:
                    self._load_from_file(config_file_name=config_file_name, rebuild=True)

                self._logger.debug(f"Initialized using '{self._base_config_file_name}'")
                self._is_initialized = True

            except Exception as exception:
                raise RuntimeError(exception) from exception

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

        # Try converting:
        try:
            return str(value)
        except Exception as conversion_error:
            raise RuntimeError(f"failed to convert {value} to string {str(conversion_error)}")

    def _get_index(self, variable_name: str) -> Optional[int]:
        """
        Finds a Variable index by its name using binary search.
        Args:
            variable_name (str): The name of the Variable to find.

        Returns:
            Optional[int]: The index of the object if found, -1 otherwise.
        """
        with self._lock:

            # Must have something to search in
            if self._variables is None or self._search_keys is None or len(self._variables) == 0:
                return -1

            # Binary search for the key
            key_to_find = (False, variable_name)
            index = bisect_left(self._search_keys, key_to_find)

            if index != len(self._variables) and self._variables[index].name == variable_name:
                return index
            return -1

    def _construct_name(self, variable_name: str) -> str:
        """
        Constructs a modified variable name by applying normalization rules such as trimming,
        adding a prefix, and adjusting case sensitivity based on the class configuration.

        Args:
            variable_name (str): The raw variable name to be processed.

        Returns:
            str: The processed variable name, which is trimmed, potentially prefixed,
                 and adjusted for case sensitivity. If `variable_name` is None or not a
                 string, it returns the input without modification.
        """
        if variable_name is None or not isinstance(variable_name, str):
            return variable_name

        new_var_name: str = variable_name.strip()
        # Add prefix if not already present and a prefix is specified
        if self._variable_prefix and not new_var_name.startswith(self._variable_prefix):
            new_var_name = self._variable_prefix + new_var_name
        # Enforce upper case if required
        if self._variable_force_upper_case_names:
            new_var_name = new_var_name.upper()

        return new_var_name

    def _refresh(self):
        """
        Sorts the internal list of variable objects and refreshes the search keys to enable fast binary search.
        This is required after any modification (insertion, deletion, or change) to the variables list.
        """
        with self._lock:
            if self._variables is None or not self._variables:
                return

            # Sort the variables list based on whether the name is None and the name itself.
            self._variables.sort(key=lambda var: (var.name is None, var.name or ""))

            # Prepare a list of keys for searching after sorting, treating None names as empty strings.
            self._search_keys = [(var.name is None, var.name or "") for var in self._variables]

    def _reset(self):
        """
        Purge the variables list and invalidate the class member
        """
        with self._lock:
            self._variables = None
            self._variables_defaults = None
            self._search_keys = None

    def _load_from_file(self, config_file_name: str, rebuild: bool = False) -> Optional[int]:
        """
        Constructs or rebuilds the configuration data based on an environment JSONc file.

        If `rebuild` is True, any existing configuration is discarded before rebuilding,
        ensuring that the list is refreshed entirely from the raw data. If `rebuild` is False
        and `_variables` is already initialized, the method raises a RuntimeError.

        Args:
            config_file_name(str): JSON file containing variables to load
            rebuild (bool): Specifies whether to forcibly rebuild the variable list even if it
                            already exists. Defaults to False.

        Returns:
            Optional[int]: The count of variables successfully initialized and stored in the
                           `_variables` list if the operation is successful, otherwise 0.
        """
        with (self._lock):
            try:

                if self._variables is not None and not rebuild:
                    raise RuntimeError(f"variables dictionary exist")

                # Preprocess
                raw_data: Optional[Dict[str, Any]] = self._processor.preprocess(file_name=config_file_name)
                if raw_data is None:
                    raise RuntimeError(f"unable to load environment file: {config_file_name}")

                # Extract variables, defaults and other options
                raw_variables = raw_data.get('variables', {})
                if raw_variables is None or len(raw_variables) == 0:
                    raise RuntimeError(f"environment file: '{config_file_name}' contain no variables")

                self._base_file_name = os.path.basename(config_file_name)
                self._variables_defaults = raw_data.get('defaults', {})

                #  If auto prefix is enabled, use the project name (upper cased) as prefix
                self._variable_auto_prefix =  raw_data.get('auto_prefix', self._variable_auto_prefix)
                # Try to locate an element whose  'name' is "PROJECT_NAME"
                target_dict = next((item for item in raw_variables if item['name'] == 'PROJECT_NAME'), None)
                if target_dict:
                    project_name = target_dict.get('value', None)
                if self._variable_auto_prefix and isinstance(project_name,str):
                    self._variable_prefix: Optional[str] = f"{project_name.upper()}_"

                self._variable_force_upper_case_names = raw_data.get('force_upper_case_names', False)
                self._base_config_file_name = os.path.basename(config_file_name)

                # Invalidate the list we might have
                if rebuild is True:
                    self._variables = None

                # Statically add workspace path
                self.add(variable_name="PROJECT_WORKSPACE", value=self._workspace_path,
                             description="Workspace path", path_must_exist=True, create_path_if_not_exist=False)

                # Process each variable from the dictionary
                for var in raw_variables:
                    kwargs = {k: v for k, v in var.items() if
                              k not in (
                                  'name', 'value', 'description', 'path_must_exist',
                                  'create_path_if_not_exist')}

                    self.add(variable_name=var.get('name', None), value=var.get('value', None),
                             description=var.get('description', None),
                             path_must_exist=var.get('path_must_exist', None),
                             create_path_if_not_exist=var.get('create_path_if_not_exist', None), **kwargs)

            except Exception as exception:
                self._variables = None
                raise RuntimeError(f"environment file '{self._base_file_name}' error {exception}")

    def _expand_variable_value(self, value: Any) -> Any:
        """
        Expands a given value by replacing placeholders with actual values from a dictionary
        and by expanding environment variables and user home directories.
        Args:
            value (Any): The input value which may contain placeholders. If `value` is not a string, it is
                         returned as-is without modification.
        Returns:
            Any: The expanded value if `value` is a string; otherwise, the original value.

        Notes:
            The function uses regular expressions to identify and replace placeholders and relies on
            `os.path.expandvars` and `os.path.expanduser` for environment variable and user directory expansion.
            It iteratively replaces all placeholders until no more substitutions can be made, ensuring that
            nested placeholders are fully expanded.
        """
        if not isinstance(value, str):
            return value

        # Regex pattern to find "<$ref_variable_name>"
        pattern: str = r"<\$ref_(.*?)>"

        # Function to replace each match
        def replace_var(match: Match[str]) -> Optional[str]:
            var_name = match.group(1)  # Extract the variable name from the regex group
            var_full_name = self._construct_name(var_name)
            index = self._get_index(var_full_name)
            if index != -1:
                return self._variables[index].value
            else:
                raise ValueError(f"variable {var_name} could not be found among defined variables.")

        # Repeatedly apply the regex substitution until all replacements are made
        old_value = None
        while old_value != value:
            old_value = value
            value = re.sub(pattern, replace_var, value)

        # Now handle the environment variable expansions
        expanded = os.path.expanduser(os.path.expandvars(value))
        if '$' in expanded and any(char.isalpha() for char in
                                   expanded.split('$')[1]):  # Check for unresolved environment variables
            first_unresolved = expanded.split('$')[1].split('/')[0].split('\\')[0]
            raise ValueError(f"environment variable ${first_unresolved} could not be expanded.")

        return expanded

    def expand(self, variable_name: str) -> Optional[str]:
        """
        Gets a Variable value by its name. If not found, attempts to expand as an environment variable.

        Args:
            variable_name (str): The name of the Variable to find.

        Returns:
            Optional[str]: The value converted to string if found, raises Exception otherwise.
        """
        with self._lock:
            index = self._get_index(variable_name)
            if index == -1:
                # Try again without initial $ if it exists
                if variable_name.startswith('$'):
                    variable_name = variable_name[1:]
                    index = self._get_index(variable_name)
                    if index == -1:
                        # Expand environment variables and user home directory notations
                        expanded = os.path.expanduser(os.path.expandvars(variable_name))
                        if expanded == variable_name:  # No expansion occurred
                            raise RuntimeError(f"Variable '{variable_name}' not found")
                        return expanded
                else:
                    raise RuntimeError(f"Variable '{variable_name}' not found")
            return self._to_string(self._variables[index].value)

    def set_value(self, variable_name: str, value: Any) -> bool:
        """
        Update the value of a variable identified by its name.
        Args:
            variable_name (str): The name of the variable to update.
            value (Any): The new value to assign to the variable.

        Returns:
            bool: True if the variable was found and updated, False otherwise.
        """
        with self._lock:
            index: int = self._get_index(variable_name)
            if index == -1:
                raise RuntimeError(f"variable '{variable_name}' not found")

            # Update the variables list
            self._variables[index].value = value
            return True

    def add(self, variable_name: str, value: Any, description: Optional[str] = None,
            path_must_exist: Optional[bool] = None,
            create_path_if_not_exist: Optional[bool] = None,
            **_kwargs) -> Optional[bool]:
        """
        Adds a new Variable to the list if no variable with the same name already exists.
        Args:
            variable_name (str): The name of the variable to update.
            value (Any): The new value to assign to the variable.
            description (Optional[str], optional): Description of the variable.
            path_must_exist (Optional[bool]): If True, the path will be validated.
            create_path_if_not_exist (Optional[bool]): If True, the path will be created.
            _kwargs(optional): Additional keyword arguments to pass to the variable.

        Returns:
            Optional[bool]: Returns True if the variable was successfully added. Returns
                            None if a variable with the same name already exists.
        """
        # Basic sanity
        if variable_name is None or value is None:
            raise RuntimeError(f"bad variable name or value")

        new_var = Variable()

        # Construct name
        new_var.base_name = (variable_name.upper() if self._variable_force_upper_case_names else variable_name).strip()
        new_var.name = self._construct_name(variable_name)
        new_var.description = description if description is not None else "Description not provided"

        index = self._get_index(new_var.name)
        if index != -1:
            raise RuntimeError(f"variable '{new_var.name}' already exists at index {index}")

        # Format fields
        if self._variable_capitalize_description:
            new_var.description = new_var.description.capitalize()

        new_var.value = self._expand_variable_value(value)
        new_var.path_must_exist = path_must_exist
        new_var.create_path_if_not_exist = create_path_if_not_exist

        # Use defaults if None was specified
        if new_var.create_path_if_not_exist is None:
            new_var.create_path_if_not_exist = self._variables_defaults.get('create_path_if_not_exist', False)

        if new_var.create_path_if_not_exist:
            os.makedirs(new_var.value, exist_ok=True)

        if new_var.path_must_exist is None:
            new_var.path_must_exist = self._variables_defaults.get('path_must_exist', False)

        if new_var.path_must_exist:
            path_exist = os.path.exists(new_var.value)
            if not path_exist:
                if not new_var.create_path_if_not_exist:
                    raise RuntimeError(
                        f"path '{new_var.value}' does not exist and marked as must exist")
                else:
                    self._logger.warning(f"Specified path: '{new_var.value}' does not exist and needs be created ")

        new_var.kwargs = _kwargs

        with self._lock:
            if self._variables is None:
                self._variables = []

            self._variables.append(new_var)
            self._refresh()
            return True

    def remove(self, variable_name: str) -> Optional[bool]:
        """
        Removes a specified variable from the internal variables list if it exists.
        Args:
            variable_name (str): The Variable name to be removed.

        Returns:
            Optional[bool]: Returns True if the variable was successfully removed.
                            Raises RuntimeError for any issues encountered.
        """
        with self._lock:
            index = self._get_index(variable_name)
            if index == -1:
                raise RuntimeError(f"variable '{variable_name}' not found")

            self._variables.pop(index)  # Remove it
            self._refresh()  # Update the list and the search dictionary
