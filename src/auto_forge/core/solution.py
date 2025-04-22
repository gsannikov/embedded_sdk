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

TODO:
1. Extend functionality to support enumeration types, enhancing the configuration options available for
   project setups. This will allow more structured and type-safe configurations.

"""
import copy
import json
import os
import re
from collections import deque
from enum import Enum
from typing import Optional, Dict, Any, Set
from typing import Union, List

# JSONPath support ('XPath' for JSON)
from jsonpath_ng.ext import parse
# JSON Schema validation
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validate

# Internal AutoForge imports
from auto_forge import (Processor, Variables, Signatures, PROJECT_SCHEMAS_PATH, AutoLogger)

AUTO_FORGE_MODULE_NAME = "Solution"
AUTO_FORGE_MODULE_DESCRIPTION = "Solution preprocessor core service"


class Solution:
    """
    A class dedicated to preparing and processing solution files for execution. This includes
    resolving references within the solution's JSON data, validating configurations against
    predefined schemas, and expanding variables to their actual values.

    Args:
        solution_config_file_name (str): The path to the JSON configuration file.
        parent (Any): Our parent AutoForge class instance.

    """

    _instance: "Solution" = None
    _is_initialized: bool = False

    def __new__(cls, solution_config_file_name: str, parent: Any) -> "Solution":
        """
        Basic class initialization in a singleton mode
        """

        if cls._instance is None:
            cls._instance = super(Solution, cls).__new__(cls)

        return cls._instance

    def __init__(self, solution_config_file_name: str, parent: Any) -> None:
        """
        Initialize the 'Solution' class using a configuration JSON file.
        """

        if not self._is_initialized:
            try:
                if parent is None:
                    raise RuntimeError("AutoForge instance must be specified when initializing core module")
                self._autoforge = parent  # Store parent' AutoForge' class instance.

                if not solution_config_file_name:
                    raise RuntimeError("solution configuration file not specified")

                # Get a logger instance
                self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

                self._config_file_name: Optional[str] = None  # Loaded solution file name
                self._config_file_path: Optional[str] = None  # Loaded solution file path
                self._max_iterations: int = 20  # Maximum allowed iterations for resolving references
                self._pre_processed_iterations: int = 0  # Count of passes we did until all references ware resolved
                self._includes: Optional[Dict[str, Any]] = None  # Additional included JSONS
                self._scope = _ScopeState()  # Initialize scope state to track processing state and context
                self._solution_data: Optional[Dict[str, Any]] = None  # To store processed solution data
                self._solution_schema: Optional[Dict[str, Any]] = None  # To store solution schema data
                self._root_context: Optional[Dict[str, Any]] = None  # To store original, unaltered solution data
                self._caught_exception: bool = False  # Flag to manage exceptions during recursive processing
                self._signatures: Optional[Signatures] = None  # Product binary signatures core class
                self._variables: Optional[Variables] = None  # Instantiate variable management library
                self._solution_loaded: bool = False  # Indicates if we have a validated solution to work with
                self._processor = Processor.get_instance()  # Get the JSON preprocessing class instance.

                # Load the solution
                self._preprocess(solution_config_file_name)
                self._is_initialized = True

            # Propagate exceptions
            except Exception:
                raise

    @staticmethod
    def get_instance() -> "Solution":
        """
        Returns the singleton instance of this class.
        Returns:
            Solution: The global stored class instance.
        """
        return Solution._instance

    def query_solutions(self, solution_name: Optional[str] = None) -> Optional[Union[List, Dict]]:
        """
        Returns a specific solution or the solutions list.
        Args:
            solution_name (Optional[str]): The name of the solution to retrieve. If None, all solutions are retrieved.
        Returns:
            List, Dict: List of solutions dictionaries or a single solutions
        """
        path = f"$.solutions[?(@.name=='{solution_name}')]" if solution_name else "$.solutions[*]"
        return self._query_json_path(path)

    def get_solutions_list(self) -> Optional[Union[List, Dict]]:
        """
        Returns the solutions list.
        Returns:
            List, Dict: List of solutions names
        """
        path = f"$.solutions[*].name"
        return self._query_json_path(path)

    def query_projects(self, solution_name: str, project_name: Optional[str] = None) -> Optional[Union[List, Dict]]:
        """
        Returns a specific project or a list of all projects that belong to a given solution.
        Args:
            solution_name (str): The name of the solution.
            project_name (Optional[str]): The name of the project to retrieve. If None, all projects are retrieved.
        Returns:
            List, Dict:  List of project dictionaries or a single project
        """
        path = (f"$.solutions[?(@.name=='{solution_name}')].projects[?(@.name=='{project_name}')]"
                if project_name else f"$.solutions[?(@.name=='{solution_name}')].projects[*]")
        return self._query_json_path(path)

    def get_projects_list(self, solution_name: Optional[str]) -> Optional[Union[List, Dict]]:
        """
        Returns the projects list of all projects that belong to a given solution.
        Args:
            solution_name (str): The name of the solution.
        Returns:
            List, Dict: List of project names matching the criteria.
        """
        path = f"$.solutions[?(@.name=='{solution_name}')].projects[*].name"
        return self._query_json_path(path)

    def query_configurations(self, solution_name: Optional[str] = None, project_name: Optional[str] = None,
                             configuration_name: Optional[str] = None) -> Optional[Union[List, Dict]]:
        """
        Returns a specific configuration or a list of all configurations related to a specific project and solution.
        Args:
            solution_name (Optional[str]): The name of the solution.
            project_name (Optional[str]): The name of the project.
            configuration_name (Optional[str]): The name of the configuration to retrieve. If None, all configurations are retrieved.
        Returns:
            List, Dict:  List of configurations dictionaries or a single configuration
        """
        if configuration_name:
            path = (f"$.solutions[?(@.name=='{solution_name}')]."
                    f"projects[?(@.name=='{project_name}')].configurations[?(@.name=='{configuration_name}')]")
        else:
            path = f"$.solutions[?(@.name=='{solution_name}')].projects[?(@.name=='{project_name}')].configurations[*]"
        return self._query_json_path(path)

    def get_configurations_list(self, solution_name: Optional[str],
                                project_name: Optional[str]) -> Optional[Union[List, Dict]]:
        """
        Returns a list of  configuration names related to a specific project and solution.
        Args:
            solution_name (Optional[str]): The name of the solution.
            project_name (Optional[str]): The name of the project.
        Returns:
            List[Any]: List of configuration names matching the criteria.
        """
        path = f"$.solutions[?(@.name=='{solution_name}')].projects[?(@.name=='{project_name}')].configurations[*].name"
        return self._query_json_path(path)

    def get_primary_solution_name(self):
        """ Gets the name of the first solution """
        solutions = self.get_solutions_list()
        if solutions is not None and len(solutions) > 0:
            return solutions[0]
        else:
            raise Exception("no solutions found")

    def show(self):
        """Prints the loaded solution as a formated JSON string"""
        if not self._solution_loaded:
            raise RuntimeError(
                "no solution is currently loaded")
        print(json.dumps(self._solution_data, sort_keys=True, indent=4))

    def get_root(self) -> Optional[Dict[str, Any]]:
        """
        Retrieves a deep copy of the currently loaded solution data to prevent
        modifications to the original data.
        Returns:
            Optional[Dict[str, Any]]: A deep copy of the solution data if loaded.
        """
        if not self._solution_loaded:
            raise RuntimeError(
                "no solution is currently loaded")
        # Rerunning a copy rather than the inner solution data structure
        solution_copy = copy.deepcopy(self._solution_data)
        return solution_copy

    def _preprocess(self, solution_file_name: str) -> None:
        """
        Process the JSON configuration file to resolve references and variables.
        Args:
            solution_file_name (str): The path to the JSON configuration file.
        Returns:
            The processed JSON data as a dictionary.
        """
        try:
            # Preprocess the solution to clear non JSON data and load as JSON.
            self._root_context = self._processor.preprocess(file_name=solution_file_name)
            self._config_file_name = solution_file_name

            # Store the solution's path since we may have to load other files from that path
            self._config_file_path = os.path.dirname(self._config_file_name)

            # Get a reference to the include JSON list, we will use them to jump start other core modules
            self._includes = self._root_context.get("includes", {})
            if self._includes is None or len(self._includes) == 0:
                raise RuntimeError(f"no includes defined in '{os.path.basename(self._config_file_name)}'")

            # Initialize the variables core module based on the configuration file we got
            config_file = f"{self._config_file_path}/{self._includes.get('environment')}"
            self._variables = Variables(variables_config_file_name=config_file, parent=self._autoforge)

            schema_version = self._includes.get("schema")
            if schema_version is not None:
                schema_path = os.path.join(PROJECT_SCHEMAS_PATH.__str__(), schema_version)
                if os.path.exists(schema_path):

                    self._logger.debug(f"Using schemas version '{schema_version}' from '{schema_path}'")

                    # Try to locate and load expected schema files
                    signature_schema_file = os.path.join(schema_path.__str__(), "signature.jsonc")
                    solution_schema_file = os.path.join(schema_path.__str__(), "solution.jsonc")

                    # Instantiate the optional signatures core module based on the configuration file we got
                    if os.path.exists(signature_schema_file):
                        self._signatures = Signatures(signatures_config_file_name=signature_schema_file,
                                                      parent=self._autoforge)

                    # Initialize the optional schema used for validating the solution structuire
                    # If file is specified, attempt to preprocess and load it
                    if os.path.exists(solution_schema_file):
                        self._solution_schema = self._processor.preprocess(file_name=solution_schema_file)
                else:
                    self._logger.warning(f"Schemas path '{schema_path} does not exist'")

            # Having the solution structure validated we can build the tree
            self._solution_data = self._root_context

            # Start the heavy lifting
            self._build_solution_tree()
            self._logger.debug(f"Initialized using '{os.path.basename(self._config_file_name)}'")

        except Exception as exception:
            self._logger.error(exception)
            raise RuntimeError("solutions module not initialized")

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
            self._process_and_refresh(method=self._traverse_and_process_derivations)
            self._process_and_refresh(method=self._traverse_and_process_variables)

            # If a schema was set, validate the fully constructed solution configuration
            if self._solution_schema is not None:
                validate(instance=self._solution_data, schema=self._solution_schema)

            # Continues processing and refreshing until no more references are found
            # or the maximum number of iterations is reached to prevent infinite loops.
            while self._find_references(self._solution_data) and self._pre_processed_iterations < self._max_iterations:
                self._process_and_refresh(self._traverse_and_process_references)
                self._pre_processed_iterations += 1

            if self._pre_processed_iterations >= self._max_iterations:
                raise RuntimeError(
                    f"exceeded maximum reference resolution iterations '{self._max_iterations}', "
                    f"potential unresolved references or circular dependencies!")

            # From now on we can serve solution queries from 'AutoForge'
            self._solution_loaded = True

        except ValidationError as ve:
            print("Schema validation Error:")
            print(f"Message: {ve.message}")
            print("Path to the error:", " -> ".join(map(str, ve.path)))
            raise RuntimeError("validation Error")
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

    def _traverse_and_process_variables(self, node: Union[dict, list], parent_key: str = None):
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
                elif isinstance(item, dict) or isinstance(item, list):
                    self._traverse_and_process_variables(item, parent_key)

    def _traverse_and_process_derivations(self, node: Union[Dict[str, Any], List[Any]],
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

    def _traverse_and_process_references(self, node: Union[Dict[str, Any], List[Any]], parent_key: Optional[str] = None,
                                         current_context: Optional[Dict[str, Any]] = None):
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
                if key == "name" and parent_key in ('solutions', 'projects', 'configurations'):
                    self._scope.update(parent_key, node)
                    current_context = node  # Update current context

                if isinstance(value, str) and "<$ref_" in value:
                    resolved_value = self._resolve_variable_in_string(value, PreProcessType.REFERENCE)
                    if resolved_value is None:
                        raise ValueError(f"Unable to resolve reference '{value}' in '{parent_key or 'root'}'.")
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
        Resolves environment variables or references in a string based on the specified variable type.
        Args:
            text (str): The input string containing variables to be resolved.
            variable_type (PreProcessType): The type of variables to resolve (environment or reference).

        Returns:
            str: The string with all variables resolved according to the type.
        """
        if variable_type == PreProcessType.ENVIRONMENT:
            # Should not match when $ is followed by 'ref_' or surrounded by '<' and '>'
            return re.sub(r'\$(?!\{?ref_)(\w+)|\$\{([^}]*)}', lambda m: self._variables.expand(m.group(0)), text)

        elif variable_type == PreProcessType.REFERENCE:
            def _replace_match(match: re.Match) -> str:
                # Extract the content directly needed for resolving the reference
                ref_content = match.group(1)  # Adjusted to capture correctly
                resolved_value = self._resolve_reference(ref_content)
                if resolved_value is None:
                    self._logger.debug(f"'{ref_content}' could not be resolved")

                return str(resolved_value) if resolved_value is not None else match.group(0)

            regex_pattern = r"<\$ref_([^>]+)>"
            results = re.sub(regex_pattern, _replace_match, text)
            return results
        else:
            raise ValueError(f"unknown variable type: {variable_type}")

    def _resolve_reference(self, reference_path: str) -> str:
        """
        Resolves a single reference path `<$ref_???>` by retrieving the corresponding value
        from the current context, project, or solution.

        Supported reference formats:
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
        """

        resolved_reference: Optional[str] = None

        if self._scope.current_context is None or self._scope.current_context.node_data is None:
            raise RuntimeError("can't resolve context using invalid current context")

        if reference_path is None:
            raise RuntimeError("can't resolve using invalid reference path")

        if "." not in reference_path:

            # Resolve Local Referencing: Directly refers to keys within the current context (solution, project, or configuration).
            # Example:
            #   "board": "imc_simics",
            #   "cmake_top_level_path": "/home/dummy/<$ref_board>",

            key = reference_path
            context = self._scope.current_context.node_data
            if context and key in context:
                resolved_reference = context[key]
            else:
                raise KeyError(
                    f"reference: `{key}` not found in "
                    f"'{self._scope.current_context.type_name}[{self._scope.current_context.name_value}]'")

        else:

            # Resolve alternate Local Referencing: Offers the same functionality as local referencing, often used for enhanced
            # readability or specific contextual needs.
            # Example:
            #   "board": "imc_simics",
            #   "cmake_top_level_path": "/home/dummy/<$ref_configurations[].board>",

            ref_parts = reference_path.split(".")
            if not ref_parts:
                raise KeyError(f"invalid reference format: `{reference_path}`")

            match_list = re.match(r"([a-zA-Z]+)\[]", ref_parts[0])
            if match_list:
                context_type = match_list.group(1)
                key = ref_parts[1]

                context = self._scope.get_node(context_type)
                if context is not None:
                    if key in context:
                        resolved_reference = context[key]
                else:
                    raise KeyError(f"local reference `{reference_path}` not found in `{context_type}` context.")

        if resolved_reference is None:

            # Explicit Referencing: Enables the use of keys from different scopes, either locally or globally within
            # the document, here we must use the full path to the referenced variable.
            #   Example:
            #   "dummy": "<$ref_solutions[IMCv2].projects[Zephyr].configurations[debug].board>"

            pattern = r"solutions\[([^\]]+)\](\..+)?"  # Full path always starts with .solutions'
            match = re.search(pattern, reference_path)
            if not match:
                raise ValueError(f"reference '{reference_path}' format or scope not starting with 'solutions'")

            key, path = match.groups()  # Corrected to expect only two groups
            specific_solution: Optional[Dict[str, Any]] = self._get_solution_by_name(solution_name=key.strip(),
                                                                                     solutions=self._solution_data)
            if not specific_solution:
                raise ValueError(f"no solution found for key '{key}'")

            if path:
                # Removing the leading dot on the path if it exists
                resolved_reference = self._resolve_nested_path(specific_solution, path.strip('.'))

        # Finally, make sure we got something
        if resolved_reference is None or not isinstance(resolved_reference, str):
            raise RuntimeError(f"reference not resolved or was referencing a non-string '{reference_path}'")

        # Clean the resolved reference and check for circular references
        raw_resolved_reference = re.sub(r'^<\$ref_|>$', '', resolved_reference).strip()
        if raw_resolved_reference == reference_path:
            raise ValueError(f"circular reference in '{resolved_reference}'")

        return resolved_reference

    def _resolve_nested_path(self, element: Dict[str, Any], path: str) -> Any:
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
                        elif name == "configurations":
                            element = self._get_configuration_by_name(sub_element, key)
                    if not element:
                        raise ValueError(f"no {name} found with name '{key}' in path '{path}'")
                else:
                    raise ValueError("invalid path format")
            else:
                element = element.get(part, None)
                if element is None:
                    raise ValueError(f"'{path}' not found.")
        return element

    @staticmethod
    def _get_solution_by_name(solution_name: str, solutions: Dict[str, Any]) -> Dict[str, Any]:
        """ Retrieves a specific named solution from the global solutions list. """
        return next((s for s in solutions.get("solutions", []) if s.get("name") == solution_name), {})

    @staticmethod
    def _get_project_by_name(projects: list, project_name: str) -> Dict[str, Any]:
        """ Retrieves a specific named project from a list of projects. """
        return next((p for p in projects if p.get("name") == project_name), {})

    @staticmethod
    def _get_configuration_by_name(configurations: list, configuration_name: str) -> Dict[str, Any]:
        """ Retrieves a specific named configuration from a list of configurations. """
        return next((c for c in configurations if c.get("name") == configuration_name), {})

    def _get_configuration_by_path(self, solution_name: str, project_name: str, config_name: str) -> Dict[str, Any]:
        """
        Find a specific configuration within the stored JSON data structure based on full path.
        Args:
            solution_name (str): The name of the solution.
            project_name (str): The name of the project.
            config_name (str): The name of the configuration.
        """
        solutions = self._solution_data.get("solutions", [])
        for solution in solutions:
            if solution.get("name") == solution_name:
                projects = solution.get("projects", [])
                for project in projects:
                    if project.get("name") == project_name:
                        configurations = project.get("configurations", [])
                        for config in configurations:
                            if config.get("name") == config_name:
                                return config
        raise ValueError(f"configuration {config_name} not found in {project_name} of {solution_name}.")

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
                for key, value in current_node.items():
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
        matches: List[str] = re.findall(pattern, ref_value)
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

    def _validate_unique_name(self, name: str, name_set: Set[str], entity_type: str) -> None:
        """
        Ensures that names within specified contexts (solutions, projects, configurations) are unique.
        Args:
            name (str): The name to check for uniqueness.
            name_set (Set[str]): A set holding names that have already been used in the given context.
            entity_type (str): The type of the entity (solution, project, or configuration).
        """
        normalized_name = self._normalize_and_check_name(name=name, entity_type=entity_type)
        if normalized_name in name_set:
            raise ValueError(
                f"duplicate {entity_type} '{normalized_name}' found, "
                f"all {entity_type} names must be unique within the same scope.")
        name_set.add(normalized_name)

    def _resolve_derivation_path(self, derivation_string: str) -> Dict[str, Any]:
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
            return self._get_configuration_by_path(solution_name, project_name, config_name)
        else:
            raise ValueError(f"invalid derivation path: {derivation_string}")

    @staticmethod
    def _merge_configurations(target: Dict[str, Any], source: Dict[str, Any]) -> None:
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
    def _refresh_data(data: Dict[str, Any]) -> Dict[str, Any]:
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
            raise RuntimeError(f"error during data refresh: {str(json_error)}")

    def _query_json_path(self, path: str) -> Optional[Union[List, Dict]]:
        """
        Generic method to execute a JSONPath query on the solution data.
        Args:
            path (str): The JSONPath query string.
        Returns:
            Any: A list of dictionaries or a singe dictionary
        """
        try:

            if not self._solution_loaded:
                raise RuntimeError(
                    "no solution is currently loaded")

            expr = parse(path)
            elements = [match.value for match in expr.find(self._solution_data)]

            if not isinstance(elements, (list, dict)):
                raise ValueError(f"unsupported JSONPath return type: {type(elements)}")

            # If we got a single dictionary in the lisr, return that dictionary
            if len(elements) == 1 and isinstance(elements[0], dict):
                return elements[0]

            # Return a list of dictionaries
            return elements

        except Exception as jsonpath_exception:
            raise RuntimeError(jsonpath_exception) from Exception


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
            PROJECT (int): Represents a project-level scope.
            CONFIGURATION (int): Represents a configuration-level scope.
        """
        UNDEFINED = 0
        SOLUTION = 1
        PROJECT = 2
        CONFIGURATION = 2  # Possible typo: Should this be 3?

    def __init__(self, type_name: Optional[str] = None):
        """
        Initializes a ScopeInfo instance, determining its type based on `type_name`.

        Args:
            type_name (Optional[str]): The type of the scope, which should be one of:
                                       'solutions', 'projects', or 'configurations'.
                                       If None, the scope type remains UNDEFINED.
        """
        self.node_data: Optional[Dict[str, Any]] = None
        self.type_name: Optional[str] = None if type_name is None else type_name.lower().strip()
        self.name_value: Optional[str] = None
        self.type: Optional[ScopeInfo.ScopeType] = ScopeInfo.ScopeType.UNDEFINED

        # Determine the scope type based on the provided type name
        if self.type_name is not None:
            if self.type_name == "solutions":
                self.type = ScopeInfo.ScopeType.SOLUTION
            elif self.type_name == "projects":
                self.type = ScopeInfo.ScopeType.PROJECT
            elif self.type_name == "configurations":
                self.type = ScopeInfo.ScopeType.CONFIGURATION

    def update(self, node_data: Optional[Dict[str, Any]] = None):
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

    def update(self, scope_type_name: str, full_node: Dict[str, Any]) -> None:
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

        elif scope_type_name == "projects" and self.solution:
            self.configuration.update()  # Reset configuration
            self.project.update(node_data=full_node)
            self.current_context = self.project

        elif scope_type_name == "configurations" and self.solution and self.project:
            self.configuration.update(node_data=full_node)
            self.current_context = self.configuration

    def get_node(self, scope_type_name: str) -> Optional[Dict[str, Any]]:
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
        if scope_type_name == "solutions":
            return self.solution
        elif scope_type_name == "projects":
            return self.project
        elif scope_type_name == "configurations":
            return self.configuration
        return None

    def reset(self) -> None:
        """
        Resets all stored scopes, clearing their data and invalidating the current context.
        This is typically used when switching to a new solution to ensure there are no stale references.
        """
        self.solution.update()
        self.project.update()
        self.configuration.update()
        self.current_context = None
