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
from dataclasses import asdict
from re import Match
from typing import Any, Optional

# Builtin AutoForge core libraries
from auto_forge import (
    AutoForgeModuleType,
    AutoLogger,
    CoreModuleInterface,
    CoreProcessor,
    Registry,
    ToolBox,
    VariableFieldType,
)

AUTO_FORGE_MODULE_NAME = "Variables"
AUTO_FORGE_MODULE_DESCRIPTION = "Variables manager"
AUTO_FORGE_MODULE_CONFIG_FILE = "variables.jsonc"


class CoreVariables(CoreModuleInterface):
    """
    Manages a collection of variables derived from a JSON dictionary and provides
    functionality to manipulate these variables efficiently. The class supports operations such
    as adding, removing, and updating variables, ensuring data integrity and providing thread-safe access.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared earlier in `__init__()`
        See 'CoreModuleInterface' usage.
        """
        self._variables: Optional[
            list[VariableFieldType]] = None  # Inner variables stored as a sorted listy of objects

        super().__init__(*args, **kwargs)

    def _initialize(self, variables_config_file_name: str,
                    workspace_path: str,
                    solution_name: str,
                    workspace_creation_mode: bool = False) -> None:
        """
        Initialize the 'Variables' class using a configuration JSON file.
        Args:
            variables_config_file_name (str): Configuration JSON file name.
            workspace_path (str): The workspace path.
            solution_name (str): Solution name.
            workspace_creation_mode (bool): When set to True, path existence checks are skipped,
                assuming the workspace is not yet created and is being initialized.
        """

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._toolbox = ToolBox.get_instance()
        self._lock: threading.RLock = threading.RLock()  # Initialize the re-entrant lock
        self._config_file_name: Optional[str] = variables_config_file_name
        self._base_config_file_name: Optional[str] = None
        self._variable_auto_prefix: bool = False  # Enable auto variables prefixing with the project name
        self._variable_prefix: Optional[str] = None  # Prefix auto added to all variables
        self._variable_capitalize_description: bool = True  # Description field formatting
        self._variables_defaults: Optional[dict] = None  # Optional default variables properties
        self._variable_force_upper_case_names: bool = False  # Instruct to force variables to be allways uppercased
        self._workspace_creation_mode: bool = workspace_creation_mode
        self._search_keys: Optional[
            list[tuple[bool, str]]] = None  # Allow for faster binary search on the signatures list

        # Create an instance of the JSON preprocessing library
        self._processor: CoreProcessor = CoreProcessor.get_instance()

        # Get the workspace from AutoForge
        self._workspace_path = workspace_path

        # Build variables list
        if self._config_file_name is not None:
            self._load_from_file(config_file_name=variables_config_file_name, solution_name=solution_name, rebuild=True)
        else:
            raise RuntimeError("variables configuration file not specified")

        self._logger.debug(f"Initialized using '{self._base_config_file_name}'")

        # Persist this module instance in the global registry for centralized access
        registry = Registry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME,
                                 description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

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
            raise RuntimeError(f"failed to convert {value} to string {conversion_error!s}") from conversion_error

    def _get_index(self, variable_name: str, flexible: bool = False) -> Optional[int]:
        """
        Finds a Variable index by its name using binary search.
        Args:
            variable_name (str): The name of the Variable to find.
            flexible (bool): If True, allows partial matching of a variable prefix.

        Returns:
            Optional[int]: The index of the object if found, -1 otherwise.
        """
        with self._lock:
            if self._variables is None or self._search_keys is None or len(self._variables) == 0:
                return -1

            # Try exact match first
            key_to_find = (False, variable_name)
            index = bisect_left(self._search_keys, key_to_find)

            if index != len(self._variables) and self._variables[index].name == variable_name:
                return index

            # If flexible search requested, attempt prefix matching
            if flexible:
                # Check backwards from the insert position
                for prefix_len in range(len(variable_name), 0, -1):
                    candidate_name = variable_name[:prefix_len]
                    key_candidate = (False, candidate_name)
                    idx = bisect_left(self._search_keys, key_candidate)
                    if idx != len(self._variables) and self._variables[idx].name == candidate_name:
                        return idx

            return -1

    def _construct_name(self, variable_name: Any) -> str:
        """
        Constructs a modified variable name by applying normalization rules such as trimming,
        adding a prefix, and adjusting case sensitivity based on the class configuration.

        Args:
            variable_name (Any): The raw variable name to be processed.

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

    def _load_from_file(self, config_file_name: str, solution_name: str, rebuild: bool = False) -> Optional[int]:
        """
        Constructs or rebuilds the configuration data based on an variables JSONc file.

        If `rebuild` is True, any existing configuration is discarded before rebuilding,
        ensuring that the list is refreshed entirely from the raw data. If `rebuild` is False
        and `_variables` is already initialized, the method raises a RuntimeError.

        Args:
            config_file_name(str): JSON file containing variables to load
            solution_name (str): The name of the solution name
            rebuild (bool): Specifies whether to forcibly rebuild the variable list even if it
                            already exists. Defaults to False.

        Returns:
            Optional[int]: The count of variables successfully initialized and stored in the
                           `_variables` list if the operation is successful, otherwise 0.
        """
        with (self._lock):
            try:
                if self._variables is not None and not rebuild:
                    raise RuntimeError("variables dictionary exist")

                # Preprocess
                raw_data: Optional[dict[str, Any]] = self._processor.preprocess(file_name=config_file_name)
                if raw_data is None:
                    raise RuntimeError(f"unable to load variables file: {config_file_name}")

                # Extract variables, defaults and other options
                raw_variables = raw_data.get('variables', {})
                if raw_variables is None or len(raw_variables) == 0:
                    raise RuntimeError(f"variables file: '{config_file_name}' contain no variables")

                self._base_file_name = os.path.basename(config_file_name)
                self._variables_defaults = raw_data.get('defaults', {})

                #  If auto prefix is enabled, use the solution name (upper cased) as prefix
                self._variable_auto_prefix = raw_data.get('auto_prefix', self._variable_auto_prefix)
                if self._variable_auto_prefix:
                    self._variable_prefix: Optional[str] = f"{solution_name.upper()}_"

                self._variable_force_upper_case_names = raw_data.get('force_upper_case_names', False)
                self._base_config_file_name = os.path.basename(config_file_name)

                # Invalidate the list we might have
                if rebuild is True:
                    self._variables = None

                # Statically add the solution name and the workspace path
                self.add(variable_name="SOLUTION_NAME", value=solution_name, description="Solution name", is_path=False)
                self.add(variable_name="PROJ_WORKSPACE", value=self._workspace_path, description="Workspace path")

                # Process each variable from the dictionary
                for var in raw_variables:
                    kwargs = {k: v for k, v in var.items() if
                              k not in (
                                  'name', 'value', 'description', 'path_must_exist',
                                  'create_path_if_not_exist')}

                    variable_name = var.get('name', None)
                    variable_value = var.get('value', None)
                    if variable_name is None or variable_value is None:
                        raise RuntimeError(
                            f"invalid variable without 'name' or 'value' or both in '{config_file_name}'")

                    self.add(variable_name=variable_name, value=variable_value,
                             description=var.get('description', "Description not provided"),
                             is_path=var.get('is_path', True),
                             path_must_exist=var.get('path_must_exist', None),
                             create_path_if_not_exist=var.get('create_path_if_not_exist', None),
                             **kwargs)

            except Exception as exception:
                self._variables = None
                raise RuntimeError(f"variables file '{self._base_file_name}' error {exception}") from exception

    def _expand_variable_value(self, value: Any) -> Any:
        """
        Expands a given value by replacing placeholders with actual values from a dictionary
        and by expanding variables variables and user home directories.
        Args:
            value (Any): The input value which may contain placeholders. If `value` is not a string, it is
                         returned as-is without modification.
        Returns:
            Any: The expanded value if `value` is a string; otherwise, the original value.

        Notes:
            The function uses regular expressions to identify and replace placeholders and relies on
            `os.path.expandvars` and `os.path.expanduser` for variable and user directory expansion.
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

        # Now handle the variable expansions
        expanded = self.expand(value)
        if '$' in expanded and any(char.isalpha() for char in
                                   expanded.split('$')[1]):  # Check for unresolved variables
            first_unresolved = expanded.split('$')[1].split('/')[0].split('\\')[0]
            raise ValueError(f"variable ${first_unresolved} could not be expanded.")

        return expanded

    def get(self, variable_name: str, flexible: bool = False, quiet: bool = False) -> Optional[str]:
        """
        Gets a Variable value by its name. If not found, attempts to expand as an variable.
        Args:
            variable_name (str): The name of the Variable to find.
            flexible (bool): If True, allows partial matching of a variable prefix.
            quiet (bool): If True, exceptions will be suppressed.

        Returns:
            Optional[str]: The value converted to string if found, raises Exception otherwise.
        """
        with self._lock:
            index = self._get_index(variable_name=variable_name, flexible=flexible)
            if index == -1:
                # Try again without initial $ if it exists
                if variable_name.startswith('$'):
                    variable_name = variable_name[1:]
                    index = self._get_index(variable_name=variable_name, flexible=flexible)
                    if index == -1:

                        # Attempt to resolve as environment, restore the '$' as needed
                        env_var = f"${variable_name}" if not variable_name.startswith("$") else variable_name
                        # Expand variables and user home directory notations
                        expanded = os.path.expanduser(os.path.expandvars(env_var))
                        if expanded == variable_name:  # No expansion occurred
                            if not quiet:
                                raise RuntimeError(f"variable '{env_var}' was not resolved or expanded")
                            return None

                        return expanded
                else:
                    if not quiet:
                        raise RuntimeError(f"variable '{variable_name}' not found")
                    return None

            return self._to_string(self._variables[index].value)

    def set(self, variable_name: str, value: Any) -> bool:
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

    def add(self, variable_name: str, value: Any,
            description: str,
            is_path: bool = True,
            path_must_exist: bool = True,
            create_path_if_not_exist: Optional[bool] = None,
            **_kwargs) -> Optional[bool]:
        """
        Adds a new Variable to the list if no variable with the same name already exists.
        Args:
            variable_name (str): The name of the variable to update.
            value (Any): The new value to assign to the variable.
            description (str): Description of the variable.
            is_path (Optional[bool]): Whether the variable is a path or not.
            path_must_exist (bool): If True, the path will be validated.
            create_path_if_not_exist (Optional[bool]): If True, the path will be created.
            _kwargs(optional): Additional keyword arguments to pass to the variable.

        Returns:
            Optional[bool]: Returns True if the variable was successfully added. Returns
                            None if a variable with the same name already exists.
        """

        new_var = VariableFieldType()

        # Construct name
        new_var.base_name = (variable_name.upper() if self._variable_force_upper_case_names else variable_name).strip()
        new_var.name = self._construct_name(variable_name)
        new_var.description = description

        index = self._get_index(new_var.name)
        if index != -1:
            raise RuntimeError(f"variable '{new_var.name}' already exists at index {index}")

        # Format fields
        if self._variable_capitalize_description:
            new_var.description = new_var.description.capitalize()

        # When it's not a path
        if not is_path:
            new_var.is_path = False
            new_var.path_must_exist = False
            new_var.create_path_if_not_exist = False
            new_var.value = value
        else:
            new_var.is_path = True
            new_var.value = self._expand_variable_value(value)

            # The variable should be treated as a path
            if not self._toolbox.looks_like_unix_path(new_var.value):
                raise RuntimeError(f"value '{new_var.value}' set by '{new_var.name}' does not look like a unix path")

            new_var.path_must_exist = path_must_exist
            new_var.create_path_if_not_exist = create_path_if_not_exist

            # Use defaults if None was specified
            if new_var.create_path_if_not_exist is None:
                new_var.create_path_if_not_exist = self._variables_defaults.get('create_path_if_not_exist', False)

            # Only enforce 'create_path_if_not_exist' and 'path_must_exist' directives during normal operation,
            # not during initial workspace creation.

            if not self._workspace_creation_mode:
                if new_var.create_path_if_not_exist:
                    os.makedirs(new_var.value, exist_ok=True)

                if new_var.path_must_exist is None:
                    new_var.path_must_exist = self._variables_defaults.get('path_must_exist', True)

                if new_var.path_must_exist:
                    path_exist = os.path.exists(new_var.value)
                    if not path_exist:
                        if not new_var.create_path_if_not_exist:
                            raise RuntimeError(
                                f"path '{new_var.value}' reqwired by '{variable_name}' does not exist and marked as must exist")
                        else:
                            self._logger.warning(
                                f"Specified path: '{new_var.value}' does not exist and needs be created ")

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

        if not self._is_initialized:
            raise RuntimeError("variables not initialized")

        with self._lock:
            index = self._get_index(variable_name)
            if index == -1:
                raise RuntimeError(f"variable '{variable_name}' not found")

            self._variables.pop(index)  # Remove it
            self._refresh()  # Update the list and the search dictionary

    def expand(self, text: str, expand_path: bool = True) -> str:
        """
        Expands variables embedded within the input text and resolves the path.
        Supports both $VAR and ${VAR} syntax, correctly handling adjacent expansions.
        Args:
            text (str): The text to expand.
            expand_path (bool): If true, treat the input as path and expand accordingly
        """

        if text:
            text = text.strip()

        if not text:
            return ""

        result = []
        i = 0
        length = len(text)

        while i < length:
            if text[i] == '$':
                if i + 1 < length and text[i + 1] == '{':
                    # ${VAR}
                    end_brace = text.find('}', i + 2)
                    if end_brace != -1:
                        var_name = text[i + 2:end_brace]
                        var_value = self.get(variable_name=var_name, quiet=True, flexible=True)
                        result.append(var_value if var_value is not None else text[i:end_brace + 1])
                        i = end_brace + 1
                        continue
                else:
                    # $VAR
                    j = i + 1
                    while j < length and (text[j].isalnum() or text[j] == '_'):
                        j += 1
                    if j > i + 1:
                        var_name = text[i + 1:j]
                        var_value = self.get(variable_name=var_name, quiet=True, flexible=True)
                        result.append(var_value if var_value is not None else text[i:j])
                        i = j
                        continue

            # Normal character
            result.append(text[i])
            i += 1

        expanded_text = ''.join(result)

        # Now expand ~ and make absolute
        if expand_path:
            expanded_text = self._toolbox.get_expanded_path(path=expanded_text, to_absolute=True)

        return expanded_text

    def export(self) -> list[dict]:
        """
        Exports the internal list of VariableFieldType instances into
        a list of dictionaries.

        Returns:
            list[dict]: A list of dictionaries representing the variables.

        """

        if not isinstance(self._variables, list):
            raise ValueError("storage empty, no variables to export")

        return [asdict(var) for var in self._variables if isinstance(var, VariableFieldType)]
