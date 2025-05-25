"""
Script:         solution.py
Author:         AutoForge Team

Description:
    The AutoForge Solution Processor is designed to handle the processing of structured JSON configuration files,
    which are crucial for managing complex project builds. This script facilitates the dynamic resolution of
    configuration details, enhancing flexibility and maintainability of project setups.

    Key Features of the Solution Processor:
    - Variable Resolution: Automates the substitution of placeholders like <$var_name> with actual values
      provided during runtime or from environment settings.
    - Reference Resolution: Dynamically resolves references within the JSON structure, such as
      <$ref_property.sub_property>, to streamline the configuration of projects with interdependent properties.
    - Error Handling: Implements robust error handling mechanisms to provide clear, actionable feedback and
      prevent runtime failures due to configuration errors.
    - Context-Aware Parsing: Capable of understanding and processing nested and hierarchical structures within
      the configuration files to support complex project requirements.

    The accompanying 'config/solution.jsonc' file serves as a reference documentation, detailing the schema and
    setup of solutions as processed by this system.

"""
import copy
import json
import os
import re
from collections import deque
from collections.abc import Iterator
from contextlib import suppress
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

# Third-party
import jmespath
from jsonpath_ng.ext import parse
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validate

# Internal AutoForge imports
from auto_forge import (PROJECT_SCHEMAS_PATH, AutoForgeModuleType, AutoLogger, CoreModuleInterface, CoreProcessor,
                        CoreSignatures, CoreVariables, PrettyPrinter, Registry, ToolBox, )

AUTO_FORGE_MODULE_NAME = "Solution"
AUTO_FORGE_MODULE_DESCRIPTION = "Solution preprocessor core service"


class CoreSolution(CoreModuleInterface):
    """
    A Core class dedicated to preparing and processing solution files for execution.
    This includes resolving references within the solution's JSON data, validating configurations against
    predefined schemas, and expanding variables to their actual values.
    """

    def _initialize(self, solution_config_file_name: str, solution_name: str, workspace_path: str) -> None:
        """
        Initializes the 'Solution' class using a configuration JSON file.
        Args:
            solution_config_file_name (str): The path to the JSON configuration file.
            solution_name (str): The name of the solution to load.
            workspace_path (str): The workspace path.
        """

        if not solution_config_file_name:
            raise RuntimeError("solution configuration file not specified")

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

        self._config_file_name: Optional[str] = None  # Loaded solution file name
        self._config_file_path: Optional[str] = None  # Loaded solution file path
        self._schema_files: Optional[dict[str, str]] = None  # Optional schema files path
        self._max_iterations: int = 20  # Maximum allowed iterations for resolving references
        self._pre_processed_iterations: int = 0  # Count of passes we did until all references ware resolved
        self._scope = _ScopeState()  # Initialize scope state to track processing state and context
        self._solution_name: Optional[str] = None  # The solution name we're using
        self._solution_data: Optional[dict[str, Any]] = None  # To store processed solution data
        self._solution_schema: Optional[dict[str, Any]] = None  # To store solution schema data
        self._root_context: Optional[dict[str, Any]] = None  # To store original, unaltered solution data
        self._caught_exception: bool = False  # Flag to manage exceptions during recursive processing
        self._signatures: Optional[CoreSignatures] = None  # Product binary signatures core class
        self._variables: Optional[CoreVariables] = None  # Instantiate variable management library
        self._solution_loaded: bool = False  # Indicates if we have a validated solution to work with
        self._processor = CoreProcessor.get_instance()  # Get the JSON preprocessing class instance.
        self._tool_box = ToolBox.get_instance()  # Get the TooBox auxiliary class instance.
        self._workspace_path: str = workspace_path  # Creation arguments

        # Load the solution
        self._preprocess(solution_file_name=solution_config_file_name, solution_name=solution_name)

        # Persist this module instance in the global registry for centralized access
        registry = Registry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

    def get_arbitrary_item(self, key: str, deep_search: bool = False) -> Optional[
        Union[list[Any], dict[str, Any], str]]:
        """
        Returns a list, dictionary, or string from the solution JSON by key.
        If deep_search is True, performs a recursive search through the entire structure.
        Args:
            key (str): The key to search for.
            deep_search (bool): Whether to search deeply through the structure.
        Returns:
            Optional[Union[list, dict, str]]: The value found, or None if not found or invalid type.
        """
        if not self._solution_loaded:
            return None

        def recursive_lookup(obj: Any) -> Optional[Union[list[Any], dict[str, Any], str]]:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == key and isinstance(v, (list, dict, str)):
                        return v
                    result = recursive_lookup(v)
                    if result is not None:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = recursive_lookup(item)
                    if result is not None:
                        return result
            return None

        if not deep_search:
            value = self._solution_data.get(key)
            if isinstance(value, (list, dict, str)):
                return value
        else:
            return recursive_lookup(self._solution_data)

        return None

    def query_projects(self, project_name: Optional[str] = None) -> Optional[Union[list, dict]]:
        """
        Returns a specific project or a list of all projects that belong to the loaded solution.
        Excludes projects where "disabled" is set to true.

        Args:
            project_name (Optional[str]): The name of the project to retrieve. If None, all projects are retrieved.

        Returns:
            Union[list, dict, None]: List of project dictionaries, a single project dict if only one is found,
            or None if nothing is found.
        """
        if not self._solution_loaded:
            return None

        # Get all projects
        data = self._query_json_path("$.projects[*]")

        if not isinstance(data, list):
            return None

        # Filter out disabled projects
        filtered = [p for p in data if not p.get("disabled", False)]

        # If looking for a specific project, filter again by name
        if project_name:
            named = [p for p in filtered if p.get("name") == project_name]
            if len(named) == 1:
                return named[0]
            elif not named:
                return None
            return named  # Just in case there are multiple with the same name
        else:
            return filtered if filtered else None

    def get_projects_names(self) -> Optional[list[str]]:
        """
        Returns the list of project names from the loaded solution.
        Returns:
            List[str]: List of project names, or None if no solution is loaded or no projects are found.
        """
        projects = self.query_projects()
        if projects is None:
            return None

        if isinstance(projects, dict):
            return [projects.get("name")]

        if isinstance(projects, list):
            return [proj.get("name") for proj in projects if isinstance(proj, dict)]

        return None

    def query_configurations(self, project_name: str, configuration_name: Optional[str] = None) -> Optional[
        Union[list, dict]]:
        """
        Returns a specific configuration or a list of all configurations related to a specific project
        of the loaded solution, excluding configurations where 'disabled' is set to true.
        Args:
            project_name (str): The name of the project.
            configuration_name (Optional[str]): The name of the configuration to retrieve. If None, all configurations are retrieved.

        Returns:
            Union[list, dict, None]: List of configuration dictionaries, a single configuration dict if only one is found,
            or None if nothing is found.
        """
        if not self._solution_loaded:
            return None

        # Retrieve all project objects
        projects = self._query_json_path("$.projects[*]")
        if not isinstance(projects, list):
            return None

        # Find the matching project
        project = next((p for p in projects if p.get("name") == project_name and not p.get("disabled", False)), None)
        if not project:
            return None

        configurations = project.get("configurations", [])
        if not isinstance(configurations, list):
            return None

        # Filter out disabled configurations
        active_configs = [cfg for cfg in configurations if not cfg.get("disabled", False)]

        if configuration_name:
            matching = [cfg for cfg in active_configs if cfg.get("name") == configuration_name]
            if len(matching) == 1:
                return matching[0]
            elif len(matching) == 0:
                return None
            return matching  # If multiple match (not expected, but handled)
        else:
            return active_configs if active_configs else None

    def get_configurations_names(self, project_name: str) -> Optional[list[str]]:
        """
        Returns a list of configuration names related to a specific project under the loaded solution.
        Args:
            project_name (str): The name of the project.
        Returns:
            List[str]: List of configuration names, or None if not found.
        """
        configurations = self.query_configurations(project_name=project_name)
        if configurations is None:
            return None

        if isinstance(configurations, dict):
            return [configurations.get("name")]

        if isinstance(configurations, list):
            return [conf.get("name") for conf in configurations if isinstance(conf, dict)]

        return None

    def iter_menu_commands_with_context(self) -> Optional[Iterator[tuple[str, str, dict]]]:
        """
        Iterates over all 'menu_command' entries from enabled projects and configurations,
        and yields their full context.
        Yields:
            Tuple of (project_name, configuration_name, menu_command_dict)
        """
        if not self._solution_loaded:
            return None

        def generator():
            for project in self._solution_data.get("projects", []):
                if project.get("disabled", False):
                    continue
                proj_name = project.get("name", "<unknown-project>")
                for config in project.get("configurations", []):
                    if config.get("disabled", False):
                        continue
                    cfg_name = config.get("name", "<unknown-config>")
                    menu_cmd = config.get("menu_command")
                    if isinstance(menu_cmd, dict):
                        yield proj_name, cfg_name, menu_cmd

        return generator()

    def show(self, pretty: bool = False):
        """
        Prints the loaded solution as a formated JSON string
        Args:
            pretty (bool): If True, prints pretty formatted JSON string with colors.
        """
        if not self._solution_loaded:
            raise RuntimeError("no solution is currently loaded")
        if not pretty:
            print(json.dumps(self._solution_data, indent=4))
        else:
            json_print = PrettyPrinter(indent=4, highlight_keys=["name", "build_path", "disabled"])
            json_print.render(self._solution_data)

    def get_loaded_solution(self, name_only: bool = False) -> Optional[Union[dict[str, Any], str]]:
        """
        Retrieves either a deep copy of the currently loaded solution data
        or just the solution name, depending on the argument.
        Args:
            name_only (bool): If True, returns the name of the loaded solution instead of the full data.
        Returns:
            Optional[Union[Dict[str, Any], str]]: The solution data copy or its name if loaded.

        """
        if not self._solution_loaded:
            raise RuntimeError("No solution is currently loaded.")

        if name_only:
            return self._solution_name

        return copy.deepcopy(self._solution_data)

    def _preprocess(self, solution_file_name: str, solution_name: str) -> None:
        """
        Process the JSON configuration file to resolve references and variables.
        Args:
            solution_file_name (str): The path to the JSON configuration file.
            solution_name (str) : The name of the solution to use locate in the configuration file.
        Returns:
            The processed JSON data as a dictionary.
        """

        # Preprocess the solution to clear non JSON data and load as JSON.
        self._root_context = self._processor.preprocess(file_name=solution_file_name)
        self._config_file_name = solution_file_name

        # Store the solution's path since we may have to load other files from that path
        self._config_file_path = os.path.dirname(self._config_file_name)

        solutions = self._root_context.get("solutions", [])
        solution_data: Optional[dict] = None
        variables_schema: Optional[dict] = None

        if isinstance(solutions, list) and solutions:
            solution_data = next(
                (item for item in solutions if isinstance(item, dict) and item.get("name") == solution_name), None)
            if not solution_data:
                raise RuntimeError(f"Solution named '{solution_name}' not found.")

        # Get an optional path to schema files
        schema_version = solution_data.get("schema")
        if schema_version is not None:
            schema_path = os.path.join(PROJECT_SCHEMAS_PATH.__str__(), schema_version)
            if os.path.exists(schema_path):
                self._schema_files = self._get_files_list(path=str(schema_path), extension=[".json", ".jsonc"])
                if self._schema_files is not None:
                    self._logger.debug(
                        f"Found {len(self._schema_files)} schemas under version '{schema_version}' in {schema_path}'")
                else:
                    self._logger.warning(f"No schema loaded: schemas path '{schema_path}' does not exist")

        # Get a reference to mandatory included JSON files, we will use them to jump start other core modules
        variables_config_file_name = self._resolve_include(element="variables", context=solution_data,
                                                           search_path=self._config_file_path, return_path=True)
        if variables_config_file_name is None:
            raise RuntimeError("'variables' mandatory include file could not be resolved")

        # Initialize the variables core module based on the configuration file we got

        if self._schema_files is not None and self._schema_files.get("variables"):
            variables_schema = self._processor.preprocess(file_name=self._schema_files.get("variables"))
        self._variables = CoreVariables(variables_config_file_name=variables_config_file_name,
                                        solution_name=solution_name, workspace_path=self._workspace_path,
                                        variables_schema=variables_schema)

        if self._schema_files is not None and self._schema_files.get("signatures"):
            # Instantiate the optional signatures core module based on the configuration file we got
            self._signatures = CoreSignatures(signatures_config_file_name=self._schema_files.get("signatures"))
        else:
            self._logger.warning("Signatures schema file not found, signature support is disables")

        # Preprocess the solution schema file if we have it
        if self._schema_files is not None and self._schema_files.get("solution"):
            self._solution_schema = self._processor.preprocess(file_name=self._schema_files.get("solution"))
        else:
            self._logger.warning(f"Solution schema file not foud")

        # Having the solution structure validated we can build the tree
        self._solution_data = solution_data
        self._solution_name = solution_name

        # Start the heavy lifting
        self._build_solution_tree()
        self._logger.debug(f"Initialized using '{os.path.basename(self._config_file_name)}'")

    def _build_solution_tree(self):
        """
        Orchestrates preprocessing steps to dynamically resolve and process elements within the JSON structure.
        This method expands environment variables, resolves reference markers, and validates the JSON structure
        against a predefined schema.

        Steps include:
        1. Process and resolve derivations.
        2. Expand environment variables.
        3. Validate the JSON structure against the schema.
        4. Resolve internal references.
        5. Output the fully expanded and resolved JSON structure.
        """

        try:
            # Each major step is processed and immediately refreshed
            self._process_and_refresh(method=self._traverse_and_process_syntax)
            self._process_and_refresh(method=self._traverse_and_process_includes)
            self._process_and_refresh(method=self._traverse_and_process_derivations)
            self._process_and_refresh(method=self._traverse_and_process_variables)

            # Continues processing and refreshing until no more references are found
            # or the maximum number of iterations is reached to prevent infinite loops.
            while self._find_references(self._solution_data) and self._pre_processed_iterations < self._max_iterations:
                self._process_and_refresh(self._traverse_and_process_references)
                self._pre_processed_iterations += 1

            if self._pre_processed_iterations >= self._max_iterations:
                raise RuntimeError(f"exceeded maximum reference resolution iterations '{self._max_iterations}', "
                                   f"potential unresolved references or circular dependencies!")

            # Finally, if a schema was specified, validate the fully constructed solution configuration
            if self._solution_schema is not None:
                validate(instance=self._solution_data, schema=self._solution_schema)

            # From now on we can serve solution queries from 'AutoForge'
            self._solution_loaded = True

        except ValidationError as validation_error:
            print("Schema validation Error:")
            print(f"Message: {validation_error.message}")
            print("Path to the error:", " -> ".join(map(str, validation_error.path)))
            raise RuntimeError("validation Error") from validation_error

        except Exception as exception:
            raise RuntimeError(exception) from exception

    def _process_and_refresh(self, method):
        """
        Helper function to process part of the JSON structure and immediately refresh it to maintain consistency.
        Args:
            method (callable): The method to execute that processes part of the JSON data.
        """
        method(self._solution_data)
        self._solution_data = self._refresh_data(self._solution_data)

    def _traverse_and_process_syntax(self, node, parent_key=None, solution_names=None, project_names=None,
                                     config_names=None):
        """
        Recursively traverses the JSON structure and performs syntax validation.

        Args:
            node (Union[dict, list]): The current JSON node being processed.
            parent_key (str, optional): The key of the parent node, used to track hierarchy.
            solution_names (set, optional): Set to track unique solution names.
            project_names (set, optional): Set to track unique project names within a solution.
            config_names (set, optional): Set to track unique configuration names within a project.
        Raises:
            ValueError: If any of the above validation rules are violated.
        """
        if solution_names is None:
            solution_names = set()
        if project_names is None:
            project_names = set()
        if config_names is None:
            config_names = set()

        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    if "<$ref" in key:
                        raise ValueError(f"Invalid reference in key '{key}', references can only appear as values.")
                    self._validate_reference_format(value, "value")

                if parent_key == "solutions" and key == "name":
                    self._validate_unique_name(value, solution_names, "solution")

                if parent_key == "projects" and key == "name":
                    self._validate_unique_name(value, project_names, "project")
                    # Reset config_names set for each new project to ensure configuration names are unique only within the same project
                    config_names = set()

                if parent_key == "configurations" and key == "name":
                    self._validate_unique_name(value, config_names, "configuration")

                # Recursive call with explicit persistence of sets
                self._traverse_and_process_syntax(value, key, solution_names, project_names, config_names)

        elif isinstance(node, list):
            for item in node:
                # Recursive call with explicit persistence of sets
                self._traverse_and_process_syntax(item, parent_key, solution_names, project_names, config_names)

    def _traverse_and_process_variables(self, node: Union[dict, list], parent_key: Optional[str] = None):
        """
        Recursively traverses the JSON structure, updating strings containing variables.
        Args:
            node (Union[dict, list]): The current JSON node being processed.
            parent_key (str, optional): The key of the parent node, used to track hierarchy.
        """
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    node[key] = self._resolve_variable_in_string(value, PreProcessType.ENVIRONMENT)
                self._traverse_and_process_variables(value, key)

        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, str):
                    node[i] = self._resolve_variable_in_string(item, PreProcessType.ENVIRONMENT)
                elif isinstance(item, (dict, list)):
                    self._traverse_and_process_variables(item, parent_key)

    def _traverse_and_process_derivations(self, node: Union[dict[str, Any], list[Any]],
                                          parent_key: Optional[str] = None) -> None:
        """
        Recursively traverses a JSON-like dictionary or list to process 'data' keys with derivation paths.
        Derivation paths are resolved and merged into the current context of the node.
        Args:
            node (Union[Dict[str, Any], List[Any]]): The current part of the data structure being processed.
            parent_key (Optional[str]): The key associated with the current node in its parent node.

        """
        if isinstance(node, dict):
            for key, value in list(node.items()):
                # Store current tool china scope
                if key == "name" and parent_key in ('solutions', 'tool_chains', 'projects', 'configurations'):
                    self._scope.update(parent_key, node)

                if key == "data" and isinstance(value, str) and "<$derived_from_" in value:
                    source_config = self._resolve_derivation_path(value)
                    if source_config:
                        self._merge_configurations(node, source_config)
                    node.pop("data", None)  # Clean up the 'data' key after processing
                else:
                    self._traverse_and_process_derivations(value, key)
        elif isinstance(node, list):
            for item in node:
                self._traverse_and_process_derivations(item, parent_key)

    def _traverse_and_process_includes(self, node: Union[dict[str, Any], list[Any]],
                                       parent_key: Optional[str] = None) -> None:
        """
        Recursively traverses a JSON-like structure to process <$include> directives.
        If a string value in the structure is a valid include directive, it replaces
        that value with the parsed contents of the referenced file.

        Args:
            node (Union[dict, list]): The data structure to process.
            parent_key (Optional[str]): The key associated with the current node in its parent node.
        """
        if isinstance(node, dict):
            for key, value in list(node.items()):

                # Store current tool china scope
                if key == "name" and parent_key in ('solutions', 'tool_chains', 'projects', 'configurations'):
                    self._scope.update(parent_key, node)

                # Case 1: value is a string and might be an include directive
                if isinstance(value, str) and value.strip().startswith("<$include>"):
                    resolved = self._resolve_include(key, node, search_path=self._config_file_path, return_path=True)
                    if resolved is not None:
                        node[key] = resolved
                # Case 2: value is nested dict or list
                elif isinstance(value, (dict, list)):
                    self._traverse_and_process_includes(value)

        elif isinstance(node, list):
            for i in range(len(node)):
                item = node[i]
                # Case 1: item is a string and might be an include directive
                if isinstance(item, str) and item.strip().startswith("<$include>"):
                    resolved = self._resolve_include(str(i), node, search_path=self._config_file_path, return_path=True)
                    if resolved is not None:
                        node[i] = resolved
                # Case 2: item is nested
                elif isinstance(item, (dict, list)):
                    self._traverse_and_process_includes(item, parent_key)

    def _traverse_and_process_references(self, node: Union[dict[str, Any], list[Any]], parent_key: Optional[str] = None,
                                         current_context: Optional[dict[str, Any]] = None):
        """
        Recursively traverses the JSON structure and resolves references such as (`<$ref_...>`) found in string values,
        updating the node with the resolved values. It also maintains `current_context`, updating it when a named
        solution, project, or configuration is encountered.

        Args:
            node (Union[Dict[str, Any], List[Any]]): The current JSON node being processed.
            parent_key (Optional[str]): The key of the parent node, used to track context.
            current_context (Optional[Dict[str, Any]]): The current scope (solution, project, or configuration).

        Raises:
            ValueError: If a circular reference is detected or a referenced key does not exist.
        """

        if isinstance(node, dict):

            for key, value in list(node.items()):  # Copy keys for safe iteration
                # Store current tool china scope
                if key == "name" and parent_key in ('solutions', 'tool_chains', 'projects', 'configurations'):
                    self._scope.update(parent_key, node)

                if isinstance(value, str) and "<$ref_" in value:

                    resolved_value = self._resolve_variable_in_string(value, PreProcessType.REFERENCE)
                    if resolved_value is None:
                        raise ValueError(f"unable to resolve reference '{value}' in '{parent_key or 'root'}'.")
                    node[key] = resolved_value  # Replace reference with resolved value

                # Recurse into nested structures
                self._traverse_and_process_references(value, key, current_context)

        elif isinstance(node, list):

            for index, item in enumerate(node):
                if isinstance(item, str) and "<$ref_" in item:
                    resolved_item = self._resolve_variable_in_string(item, PreProcessType.REFERENCE)
                    if resolved_item is None:
                        raise ValueError(
                            f"Unable to resolve reference '{item}' in list under '{parent_key or 'root'}'.")
                    node[index] = resolved_item  # Update the JSON list in place
                elif isinstance(item, (dict, list)):
                    self._traverse_and_process_references(item, parent_key, current_context)

    def _resolve_variable_in_string(self, text: str, variable_type: "PreProcessType") -> Any:
        """
        Resolves environment variables or reference tokens in a string based on the specified variable type.
        Args:
            text (str): The input string containing variable references.
            variable_type (PreProcessType): The type of variables to resolve — either environment variables or references.

        Returns:
            Any: The resolved result, which can be a string or a dictionary depending on the match.
        """

        if variable_type == PreProcessType.ENVIRONMENT:
            # Replace $VAR or ${VAR} — but skip $ref_ and <$ref_> patterns
            return re.sub(r'\$(?!\{?ref_)(\w+)|\$\{([^}]*)}', lambda m: self._variables.get(m.group(0)), text)

        elif variable_type == PreProcessType.REFERENCE:
            def _replace_match(match: re.Match) -> str:
                nonlocal matched_dictionary_data

                # Extract reference target from inside <$ref_...>
                ref_content = match.group(1)
                resolved_value = self._resolve_reference(ref_content)

                # Handle both dictionary and string resolutions:
                # - If a dictionary is returned, and it's the first match, store it (used as the actual return value)
                # - If it's a string, let re.sub() continue substituting normally
                if isinstance(resolved_value, dict) and matched_dictionary_data is None:
                    matched_dictionary_data = resolved_value

                if resolved_value is None:
                    self._logger.debug(f"'{ref_content}' could not be resolved")

                return str(resolved_value) if resolved_value is not None else match.group(0)

            regex_pattern = r"<\$ref_([^>]+)>"

            # Initialize the dictionary result placeholder
            matched_dictionary_data: Optional[dict[str, Any]] = None

            # re.sub will trigger _replace_match for each match, but only the first dict (if any) is preserved
            matched_data = re.sub(regex_pattern, _replace_match, text)

            # Return the dict if found, otherwise return the fully resolved string
            return matched_dictionary_data if matched_dictionary_data else matched_data

        else:
            raise ValueError(f"unknown variable type: {variable_type}")

    def _resolve_reference(  # noqa: C901,
            self, reference_path: str) -> Union[str, dict]:
        """
        Resolves a single reference path `<$ref_???>` by retrieving the corresponding value
        from the current context, project, or solution.
        Examples for reference formats:
            - `<$ref.key>`: Retrieves `key` from `state.current_context`.
            - `<$ref_solutions[].key>`: Retrieves `key` from `state.solution`.
            - `<$ref_projects[].key>`: Retrieves `key` from `state.project`.
            - `<$ref_configurations[].key>`: Retrieves `key` from `state.configuration`.
            - `<$ref_solutions[solution_name].key>`: Retrieves `key` from a named solution.
            - `<$ref_configurations[config_name].key>`: Retrieves `key` from a named configuration
              within the current project.
        Args:
            reference_path (str): The reference path to be resolved.
        Returns:
            str: The resolved value for the given reference.
        NOTE:
            This function exceeds typical complexity limits (C901) by design.
            It encapsulates a critical, tightly-coupled sequence of logic that benefits from being kept together
            for clarity, atomicity, and maintainability. Refactoring would obscure the execution flow.
        """
        resolved_reference: Optional[str] = None

        if self._scope.current_context is None or self._scope.current_context.node_data is None:
            raise RuntimeError("can't resolve context using invalid current context")

        if reference_path is None:
            raise RuntimeError("can't resolve using invalid reference path")

        if "." not in reference_path:

            # Resolve Local Referencing: Directly refers to keys within the current context (solution, project, or configuration).
            key = reference_path

            context = self._scope.current_context.node_data
            resolved_reference = jmespath.search(key, context)
            if resolved_reference is None:
                raise KeyError(f"reference: `{key}` not found in "
                               f"'{self._scope.current_context.type_name}[{self._scope.current_context.name_value}]'")
        else:

            # Resolve alternate Local Referencing: Offers the same functionality as local referencing, often used for enhanced
            # readability or specific contextual needs.

            ref_parts = reference_path.split(".")
            if not ref_parts:
                raise KeyError(f"invalid reference format: `{reference_path}`")

            match_list = re.match(r"([a-zA-Z]+)\[]", ref_parts[0])
            if match_list:
                context_type = match_list.group(1)

                fragmented_key = '.'.join(ref_parts[1:]) if len(ref_parts) > 2 else None
                key = ref_parts[1]

                context = self._scope.get_node(context_type)
                if context is not None:
                    if fragmented_key:
                        resolved_reference = jmespath.search(fragmented_key, context)
                    else:
                        if key in context:
                            resolved_reference = context[key]
                else:
                    raise KeyError(f"local reference `{reference_path}` not found in `{context_type}` context.")

        if resolved_reference is None:

            # Explicit Referencing: Enables the use of keys from different scopes, either locally or globally within
            # the document, here we must use the full path to the referenced variable.
            #   Example:
            #   "dummy": "<$ref_solutions[example].projects[test].configurations[debug].board>"

            pattern = r"solutions\[([^\]]+)\](\..+)?"  # Full path always starts with .solutions'
            match = re.search(pattern, reference_path)
            if not match:
                raise ValueError(f"reference '{reference_path}' format or scope not starting with 'solutions'")

            key, path = match.groups()  # Corrected to expect only two groups
            referenced_solution = key.strip()
            if referenced_solution != self._solution_name:
                raise ValueError(f"can't reference foreign solution ('{referenced_solution}')")

            if path:
                # Removing the leading dot on the path if it exists
                resolved_reference = self._resolve_nested_path(element=self._solution_data, path=path.strip('.'))

        # Finally, make sure we got something
        if not isinstance(resolved_reference, (str, dict)):
            raise RuntimeError(f"reference not resolved or was referencing a non-string '{reference_path}'")

        # Clean the resolved reference and check for circular references
        if isinstance(resolved_reference, str):
            raw_resolved_reference = re.sub(r'^<\$ref_|>$', '', resolved_reference).strip()
            if raw_resolved_reference == reference_path:
                raise ValueError(f"circular reference in '{resolved_reference}'")

        return resolved_reference

    def _resolve_nested_path(self, element: dict[str, Any], path: str) -> Any:
        """
        Resolves a nested path within a solution's data structure, handling navigation through projects and
        configurations by name.
        Args:
            element (Dict[str, Any]): The starting dictionary element from which to resolve the path.
            path (str): The dot-separated path string describing how to navigate through the data structure.

        Returns:
            Any: The data found at the specified path within the element. Can be of any type (dict, list, str, etc.).
        """
        parts = path.split('.')
        for part in parts:
            if '[' in part and ']' in part:
                match = re.match(r"([^[]+)\[([^]]+)]", part)
                if match:
                    name, key = match.groups()
                    sub_element = element.get(name, [])
                    if isinstance(sub_element, list):
                        if name == "projects":
                            element = self._get_project_by_name(sub_element, key)
                        elif name == "configurations" or name == "tool_chains":
                            element = self._get_configuration_by_name(sub_element, key)
                    if not element:
                        raise ValueError(f"no {name} found with name '{key}' in path '{path}'")
                else:
                    raise ValueError("invalid path format")
            else:
                element = element.get(part, None)
                if element is None:
                    raise ValueError(f"'{path}' not found")
        return element

    @staticmethod
    def _get_project_by_name(projects: list, project_name: str) -> dict[str, Any]:
        """ Retrieves a specific named project from a list of projects. """
        return next((p for p in projects if p.get("name") == project_name), {})

    @staticmethod
    def _get_configuration_by_name(configurations: list, configuration_name: str) -> dict[str, Any]:
        """ Retrieves a specific named configuration from a list of configurations. """
        return next((c for c in configurations if c.get("name") == configuration_name), {})

    def _get_configuration_by_path(self, project_name: str, config_name: str) -> dict[str, Any]:
        """
        Find a specific configuration within the stored JSON data structure based on full path.
        Args:
            project_name (str): The name of the project.
            config_name (str): The name of the configuration.
        """
        projects = self._solution_data.get("projects", [])
        for project in projects:
            if project.get("name") == project_name:
                configurations = project.get("configurations", [])
                for config in configurations:
                    if config.get("name") == config_name:
                        return config
        raise ValueError(
            f"configuration {config_name} not found in project '{project_name}' of solution '{self._solution_name}'")

    @staticmethod
    def _find_references(root: Union[dict, list]) -> bool:
        """
        Performs a breadth-first search on the JSON structure to find any references
        directly within the method without calling an external function.
        Args:
            root (Union[dict, list]): The root of the JSON structure.
        Returns:
            bool: True if a reference is found, False otherwise.
        """
        queue = deque([root])  # Initialize the queue with the root element
        pattern = r"<\$ref_[^>]+>"  # Anything that looks like a reference

        while queue:
            current_node = queue.popleft()  # Get the first element from the queue

            if isinstance(current_node, dict):
                for _key, value in current_node.items():
                    if isinstance(value, str):
                        if re.search(pattern, value):  # Check for reference pattern in string
                            return True  # Found a reference, return True
                    elif isinstance(value, (dict, list)):
                        queue.append(value)  # Enqueue the value for further processing

            elif isinstance(current_node, list):
                for item in current_node:
                    if isinstance(item, str):
                        if re.search(pattern, item):  # Check for reference pattern in string
                            return True  # Found a reference, return True
                    elif isinstance(item, (dict, list)):
                        queue.append(item)  # Enqueue the item for further processing

        return False  # No references found after processing all items

    @staticmethod
    def _validate_reference_format(ref_value: str, context: str) -> None:
        """
        Ensures that references in values are properly formatted.
        Args:
            ref_value (str): The string containing potential reference markers.
            context (str): Descriptive context in which this function is called, used in error messages.
        """
        # This regex will now check for references that are either standalone or correctly formatted within a string
        pattern = r'(<\$ref[^>]*>)'
        matches: list[str] = re.findall(pattern, ref_value)
        if matches:
            for match in matches:
                if not (match.startswith('<$ref') and match.endswith('>')):
                    raise ValueError(
                        f"malformed reference in {context} '{ref_value}', each reference must start with '<$ref' and end with '>'.")

    @staticmethod
    def _normalize_and_check_name(name: str, entity_type: str) -> Optional[str]:
        """
        Normalize the name by stripping and converting to lowercase.
        Raise an exception if the original name is not properly formatted.
        Args:
            name (str): The original name string.
            entity_type (str): The type of the entity (solution, project, or configuration).
        Returns:
            str: The normalized name.
        """
        stripped_name = name.strip()
        if stripped_name != name or not stripped_name.islower():
            raise ValueError(f"{entity_type} name '{name}' must be in lowercase "
                             f"and not contain leading or trailing spaces.")
        return stripped_name

    def _validate_unique_name(self, name: str, name_set: set[str], entity_type: str) -> None:
        """
        Ensures that names within specified contexts (solutions, projects, configurations) are unique.
        Args:
            name (str): The name to check for uniqueness.
            name_set (Set[str]): A set holding names that have already been used in the given context.
            entity_type (str): The type of the entity (solution, project, or configuration).
        """
        normalized_name = self._normalize_and_check_name(name=name, entity_type=entity_type)
        if normalized_name in name_set:
            raise ValueError(f"duplicate {entity_type} '{normalized_name}' found, "
                             f"all {entity_type} names must be unique within the same scope.")
        name_set.add(normalized_name)

    def _resolve_derivation_path(self, derivation_string: str) -> dict[str, Any]:
        """
        Parses the derivation path and fetches the corresponding configuration from the JSON data.
        Args:
            derivation_string (str): A string that encodes the path to a configuration using a specific syntax.

        Returns:
            Dict[str, Any]: The configuration dictionary found at the specified path.
        """
        pattern = r"<\$derived_from_solutions\[(.*?)\].projects\[(.*?)\].configurations\[(.*?)\]>"
        match = re.search(pattern, derivation_string)
        if match:
            solution_name, project_name, config_name = match.groups()
            if solution_name and solution_name != self._solution_name:
                raise ValueError(f"deriving from foreign solution is not allowed ('{solution_name}")
            if not project_name:
                project_name = self._scope.project.name_value if self._scope.project else None
                if not project_name:
                    raise ValueError(f"could resolve project name for current scope")

            return self._get_configuration_by_path(project_name=project_name, config_name=config_name)
        else:
            raise ValueError(f"invalid derivation path: {derivation_string}")

    @staticmethod
    def _merge_configurations(target: dict[str, Any], source: dict[str, Any]) -> None:
        """
        Merges source configuration into the target configuration without overwriting existing keys.

        Args:
            target (Dict[str, Any]): The target configuration where the source is merged into.
            source (Dict[str, Any]): The source configuration to be merged.
        """
        for key, value in source.items():
            if key not in target:
                target[key] = value

    @staticmethod
    def _refresh_data(data: dict[str, Any]) -> dict[str, Any]:
        """
        Refreshes the provided JSON-like data by serializing to JSON and then parsing it back.
        This ensures that all data structures are freshly instantiated without shared references.
        Args:
            data (Dict[str, Any]): The data to refresh.
        Returns:
            Dict[str, Any]: The refreshed data.
        """
        try:
            serialized_data = json.dumps(data)
            return json.loads(serialized_data)
        except (json.JSONDecodeError, TypeError) as json_error:
            raise RuntimeError(f"error during data refresh: {json_error!s}") from json_error

    def _query_json_path(self, path: str) -> Optional[list[Union[str, dict]]]:
        """
        Executes a JSONPath query against the loaded solution data and returns the results as a list.
        NoeL we always returns either a list of matched values (e.g., strings, dictionaries)
        or None if no matches were found. The type of elements in the list depends on the query.

        Args:
            path (str): The JSONPath query string to execute.

        Returns:
            Optional[list]: A list of matched values (such as str or dict), or None if no matches were found.
        """
        try:
            if not self._solution_loaded:
                raise RuntimeError("no solution is presently loaded into the system")

            expr = parse(path)
            matches = [match.value for match in expr.find(self._solution_data)]

            return matches if matches else None

        except Exception as json_query:
            raise RuntimeError(f"JSONPath query failed for path '{path}': {json_query}") from json_query

    def _resolve_include(self, element: str, context: Union[dict, list, str], search_path: Optional[str] = None,
                         return_path: bool = False) -> Optional[Any]:
        """
        Searches for the given element in the provided context. If the value is an include directive
        in the form "<$include>path/to/file", resolves and optionally loads the file contents.
        Args:
            element (str): The key or index to look for in the context.
            context (Union[dict, list, str]): Structure holding the value (dict, list, or str).
            search_path (Optional[str]): Directory to resolve relative include paths from, if needed.
            return_path (bool): If True, returns the resolved file path instead of its loaded contents.
        Returns:
            Optional[Any]: Parsed file content or the resolved path if return_path is True, else None.

        """
        include_prefix = "<$include>"

        # Extract the candidate value
        value = None
        if isinstance(context, dict):
            value = context.get(element)
        elif isinstance(context, list):
            try:
                index = int(element)
                value = context[index]
            except (ValueError, IndexError):
                return None
        elif isinstance(context, str) and element == "":
            value = context

        # If it's a valid include directive
        if isinstance(value, str) and value.startswith(include_prefix):
            raw_path = value[len(include_prefix):].strip()

            # First try resolving as is
            expanded_path = self._tool_box.get_expanded_path(raw_path)
            if not os.path.isfile(expanded_path) and search_path and not os.path.isabs(raw_path):
                fallback_path = os.path.join(search_path, raw_path)
                expanded_path = self._tool_box.get_expanded_path(fallback_path)

            if os.path.isfile(expanded_path):
                if return_path:
                    return expanded_path
                with suppress(Exception):
                    return self._processor.preprocess(file_name=expanded_path)

        return None

    @staticmethod
    def _get_files_list(path: str, extension: Union[str, list[str]]) -> Optional[dict[str, str]]:
        """
        Returns a dictionary mapping base file names (without extension) to their full paths,
        for files under the given path matching the given extension(s).
        Args:
            path (str): Path to a directory or file.
            extension (Union[str, List[str]]): File extension(s) to match (e.g., '.json' or ['.json', '.jsonc']).
        Returns:
            Optional[Dict[str, str]]: Mapping of base file name to full path, or None if path is invalid.
        """
        if not path:
            return None

        base_path = Path(path).expanduser().resolve()

        if isinstance(extension, str):
            extension = [extension]

        extensions = {ext if ext.startswith('.') else f'.{ext}' for ext in extension}

        result: dict[str, str] = {}

        if base_path.is_file():
            if base_path.suffix in extensions:
                result[base_path.stem] = str(base_path)
            return result

        if base_path.is_dir():
            for p in base_path.rglob("*"):
                if p.suffix in extensions and p.is_file():
                    result[p.stem] = str(p.resolve())
            return result

        return None


# -----------------------------------------------------------------------------
#
# Auxiliary classes for keeping track of the context as
# we reverse and resolve references.
#
# -----------------------------------------------------------------------------

class PreProcessType(Enum):
    """
    Enum for distinguishing types of variables in configuration processing.
    ENVIRONMENT: Variables derived from the environment settings.
    REFERENCE: Variables that reference other values within the configuration.
    """
    ENVIRONMENT = 1
    REFERENCE = 2


class ScopeInfo:
    """
    Represents metadata about a scope (Solution, Project, or Configuration)
    within the system. This class is used to track the type of scope, its
    associated data, and its name.

    Attributes:
        node_data (Optional[Dict[str, Any]]): The dictionary containing data about the scope.
        type_name (Optional[str]): The type of the scope in lowercase (e.g., 'solutions', 'projects', 'configurations').
        name_value (Optional[str]): The name of the scope, extracted from `node_data` if available.
        type (Optional[ScopeInfo.ScopeType]): The type of scope as determined by `ScopeType` enum.
    """

    class ScopeType(Enum):
        """
        Enum for categorizing different scope types in the system.

        Attributes:
            UNDEFINED (int): Default value for uninitialized or unknown scopes.
            SOLUTION (int): Represents a solution-level scope.
            TOOL_CHAINS (int): Represents tool-chains scope.
            PROJECT (int): Represents a project-level scope.
            CONFIGURATION (int): Represents a configuration-level scope.
        """
        UNDEFINED = 0
        SOLUTION = 1
        TOOL_CHAINS = 2
        PROJECT = 3
        CONFIGURATION = 4

    def __init__(self, type_name: Optional[str] = None):
        """
        Initializes a ScopeInfo instance, determining its type based on `type_name`.
        Args:
            type_name (Optional[str]): The type of the scope, which should be one of:
                                       'solutions', 'projects', or 'configurations'.
                                       If None, the scope type remains UNDEFINED.
        """
        self.node_data: Optional[dict[str, Any]] = None
        self.type_name: Optional[str] = None if type_name is None else type_name.lower().strip()
        self.name_value: Optional[str] = None
        self.type: Optional[ScopeInfo.ScopeType] = ScopeInfo.ScopeType.UNDEFINED

        # Determine the scope type based on the provided type name
        if self.type_name is not None:
            if self.type_name == "solutions":
                self.type = ScopeInfo.ScopeType.SOLUTION
            elif self.type_name == "tool_chains":
                self.type = ScopeInfo.ScopeType.TOOL_CHAINS
            elif self.type_name == "projects":
                self.type = ScopeInfo.ScopeType.PROJECT
            elif self.type_name == "configurations":
                self.type = ScopeInfo.ScopeType.CONFIGURATION

    def update(self, node_data: Optional[dict[str, Any]] = None):
        """
        Updates the scope with a new node dictionary, setting its name value if present.
        If no node data is provided, the scope is invalidated.
        Args:
            node_data (Optional[Dict[str, Any]]): The dictionary containing the scope's data.
        """
        self.node_data = node_data
        if self.node_data is not None:
            # The JSONC schema dictates that all scopes must have a 'name' key.
            self.name_value = self.node_data.get("name", None)
            if self.name_value is None:
                raise RuntimeError(f"property 'name' is missing from node '{self.type_name}'")
        else:
            self.name_value = None  # Invalidate inner name


class _ScopeState:
    """
    Manages the hierarchical state while traversing a JSON structure.
    This class keeps track of the currently active solution, project, and configuration
    as the JSON is processed. Additionally, it maintains `current_context`, which dynamically
    points to the active context based on the processing depth.

    Attributes:
        solution (Optional[ScopeInfo]): The currently active solution scope.
        project (Optional[ScopeInfo]): The currently active project scope.
        configuration (Optional[ScopeInfo]): The currently active configuration scope.
        current_context (Optional[ScopeInfo]): The active context at the current processing level.
    """

    def __init__(self):
        """
        Initializes the `ScopeState` instance with no active context.
        Default scopes (`solution`, `project`, `configuration`) are created but unpopulated.
        """
        self.solution: Optional[ScopeInfo] = ScopeInfo(type_name="solutions")
        self.project: Optional[ScopeInfo] = ScopeInfo(type_name="projects")
        self.configuration: Optional[ScopeInfo] = ScopeInfo(type_name="configurations")
        self.current_context: Optional[ScopeInfo] = ScopeInfo()

    def update(self, scope_type_name: str, full_node: dict[str, Any]) -> None:
        """
        Updates the state based on the current JSON node being processed.
        Depending on the scope type, the method updates the corresponding
        scope and adjusts `current_context` accordingly.
        Args:
            scope_type_name (str): The type of scope being updated (`"solutions"`, `"projects"`, or `"configurations"`).
            full_node (Dict[str, Any]): The dictionary representation of the current scope node.

        Behavior:
            - If `scope_type_name` is `"solutions"`, resets all stored scopes and updates `solution`.
            - If `scope_type_name` is `"projects"`, updates `project` while retaining the active solution.
            - If `scope_type_name` is `"configurations"`, updates `configuration` while retaining the active project and solution.
        """
        if scope_type_name == "solutions":
            self.reset()  # Reset all stored scopes
            self.solution.update(node_data=full_node)
            self.current_context = self.solution

        elif scope_type_name == "tool_chains" and self.solution:
            self.configuration.update(node_data=full_node)
            self.current_context = self.configuration

        elif scope_type_name == "projects" and self.solution:
            self.configuration.update()  # Reset configuration
            self.project.update(node_data=full_node)
            self.current_context = self.project

        elif scope_type_name == "configurations" and self.solution and self.project:
            self.configuration.update(node_data=full_node)
            self.current_context = self.configuration

    def get_node(self, scope_type_name: str) -> Optional[dict[str, Any]]:
        """
        Retrieves the dictionary representation of a scope based on its type.
        Args:
            scope_type_name (str): The type of scope (`"solutions"`, `"projects"`, or `"configurations"`).
        Returns:
            Optional[Dict[str, Any]]: The node data of the requested scope, or None if not found.
        """
        scope_info = self.get_scope_info(scope_type_name)
        if scope_info is not None:
            return scope_info.node_data
        return None

    def get_scope_info(self, scope_type_name: str) -> Optional[ScopeInfo]:
        """
        Retrieves the `ScopeInfo` object for a given scope type.
        Args:
            scope_type_name (str): The type of scope (`"solutions"`, `"projects"`, or `"configurations"`).
        Returns:
            Optional[ScopeInfo]: The `ScopeInfo` instance of the requested scope, or None if not found.
        """
        return {"solutions": self.solution, "projects": self.project, "configurations": self.configuration, }.get(
            scope_type_name)

    def reset(self) -> None:
        """
        Resets all stored scopes, clearing their data and invalidating the current context.
        This is typically used when switching to a new solution to ensure there are no stale references.
        """
        self.solution.update()
        self.project.update()
        self.configuration.update()
        self.current_context = None
