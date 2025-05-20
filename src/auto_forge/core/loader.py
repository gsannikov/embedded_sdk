"""
Script:         loader.py
Author:         AutoForge Team

Description:
    Core module which is responsible for dynamically discovering, validating, executing and registering modules
    that implement supported interfaces types.
"""

import glob
import importlib.util
import io
import os
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Union

# AutoGorge local imports
from auto_forge import (
    AutoForgeModuleType,
    AutoLogger,
    BuildProfileType,
    CLICommandInterface,
    CoreModuleInterface,
    BuilderInterface,
    ModuleInfoType,
    Registry,
    TerminalTeeStream,
    ToolBox,
)

AUTO_FORGE_MODULE_NAME = "Loader"
AUTO_FORGE_MODULE_DESCRIPTION = "Dynamically search and load supported modules"


class CoreLoader(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared earlier in `__init__()`
        See 'CoreModuleInterface' usage.
        """
        self._execution_output: Optional[str] = None

        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initializes the 'CoreLoader' class and prepares the command registry.
        """

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry: Registry = Registry.get_instance()
        self._toolbox: ToolBox = ToolBox.get_instance()
        self._loaded_commands: int = 0

        # Supported base interfaces for command classes
        self._supported_interfaces = {
            CLICommandInterface: "CLICommandInterface",
            BuilderInterface: "BuilderInterface",
        }

        # Persist this module instance in the global registry for centralized access
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME,
                                       description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

    def _resolve_registered_instance(self, name: str, expected_type: AutoForgeModuleType, required_method: str) -> Any:
        """
        Common logic for resolving and validating a registered module instance.
        Args:
            name (str): The name of the registered module.
            expected_type (AutoForgeModuleType): The expected module type (e.g., BUILDER, CLI_COMMAND).
            required_method (str): The method the instance must implement (e.g., 'build', 'execute').

        Returns:
            Any: The validated class instance.

        Raises:
            RuntimeError: If lookup fails, type mismatch, or method is missing.
        """
        self._logger.debug(f"Locating registered module for: '{name}'")

        module_record = self._registry.get_module_record_by_name(module_name=name.strip())
        if module_record is None:
            raise RuntimeError(f"module '{name}' is not recognized")

        module_type = module_record.get("auto_forge_module_type", AutoForgeModuleType.UNKNOWN)
        if module_type is not expected_type:
            raise RuntimeError(f"module '{name}' is registered, but not marked as a {expected_type.name.lower()}")

        class_instance: Any = module_record.get("class_instance")
        if class_instance is None:
            raise RuntimeError(f"could not find an instance of '{name}' in the registry")

        if not self._toolbox.has_method(class_instance, required_method):
            raise RuntimeError(f"module '{name}' does not implement '{required_method}'")

        return class_instance

    def probe(  # noqa: C901
            self,
            paths: Union[str, Sequence[str]]) -> int:
        """
        Scans one or more paths for Python modules, searches for classes derived from known base classes,
        instantiates them, and registers them.
        Args:
            paths (str or list of str): A single path or a list of paths to search for modules.
        Returns:
            int: Number of successfully instantiated classes.
        NOTE:
            This function exceeds typical complexity limits (C901) by design.
            It encapsulates a critical, tightly-coupled sequence of logic that benefits from being kept together
            for clarity, atomicity, and maintainability. Refactoring would obscure the execution flo
        """

        if isinstance(paths, str):
            paths = [paths]
        for path in paths:

            commands_path = Path(path)
            if not commands_path.exists():
                self._logger.warning(f"Specified commands path not found: {path}")
                return 0

            for file in glob.glob(str(commands_path / "*.py")):

                file_base_name: str = os.path.basename(file)
                file_stem_name = os.path.splitext(file_base_name)[0]  # File name excluding the extension
                python_module_type: Optional[ModuleType] = None
                class_object: Optional[object] = None

                try:
                    # Attempt to dynamically import the file
                    python_module_spec: Optional[ModuleSpec] = importlib.util.spec_from_file_location(file_stem_name,
                                                                                                      file)
                    if python_module_spec is None:
                        self._logger.warning(f"Unable to import '{file_base_name}'. Skipping")
                        continue

                    if python_module_spec:
                        python_module_type: Optional[ModuleType] = importlib.util.module_from_spec(python_module_spec)
                        python_module_spec.loader.exec_module(python_module_type)

                    if python_module_type is None:
                        self._logger.warning(
                            f"File '{file_base_name}' found, but dynamic import returned None. Skipping")
                        continue

                    # Now that the file is imported, inspect its contents and find the first class
                    # that inherits from one of the supported interfaces defined in 'self._supported_interfaces'.
                    for attr_name in dir(python_module_type):
                        attr = getattr(python_module_type, attr_name)

                        # Find a class object that is a subclass of a supported interface, but not already registered
                        if (
                                isinstance(attr, type)
                                and issubclass(attr, tuple(self._supported_interfaces.keys()))
                                and attr not in self._supported_interfaces
                        ):
                            class_object = attr
                            break

                    # If no compatible class was found, skip this module
                    if not class_object:
                        self._logger.warning(f"No supported class found in module '{file_stem_name}'. Skipping")
                        continue

                    interface_name: str = next(
                        (name for base, name in self._supported_interfaces.items() if issubclass(class_object, base)),
                        None
                    )

                    if not interface_name:
                        self._logger.warning(f"Unsupported command interface in module '{file_stem_name}'. Skipping")
                        continue

                    # Instantiate the class (command), this will auto update the command properties in the registry.
                    command_instance = class_object()

                    # Invoke 'get_info()', which is defined by the interface. Since this class was loaded dynamically,
                    # we explicitly pass the Python 'ModuleType' to the implementation so it can update its own metadata.
                    # This type is known to us but cannot be inferred automatically by the loaded class.
                    module_info: ModuleInfoType = command_instance.get_info()
                    if not module_info:
                        self._logger.warning(f"Loaded class '{command_instance.__class__.__name__}' "
                                             f"did not return module info. Skipping")
                        continue

                    # Gets extended command description from the module's docstring, which typically provides more
                    # detailed information than the default description.

                    command_name = module_info.name
                    docstring_description = self._toolbox.get_module_docstring(python_module_type=python_module_type)
                    command_description = docstring_description if docstring_description else module_info.description

                    # The command should have automatically updated its metadata in the registry; next we validate this.
                    command_record: Optional[dict[str, Any]] = self._registry.get_module_record_by_name(
                        module_name=module_info.name,
                        case_insensitive=False
                    )

                    if not command_record:
                        self._logger.warning(
                            f"Command '{module_info.name}' could not be found in the registry. Skipping")
                        continue

                    # Update the registry record and get an updated 'ModuleInfoType' type
                    module_info = self._registry.update_module_record(module_name=module_info.name,
                                                                      description=command_description,
                                                                      class_instance=command_instance,
                                                                      class_interface_name=interface_name,
                                                                      python_module_type=python_module_type,
                                                                      file_name=file)

                    if module_info is None:
                        self._logger.warning(f"Command '{command_name}' could not be update in the registry. Skipping")
                        continue

                    # Refresh the command with  updated info
                    command_instance.update_info(command_info=module_info)

                    # Ensure the dynamically loaded module is accessible via sys.modules,
                    # allowing standard import mechanisms and references to resolve it by name.
                    sys.modules[python_module_spec.name] = python_module_type

                    self._loaded_commands += 1
                    self._logger.debug(f"Module '{module_info.name}' dynamically loaded from '{file_stem_name}'")

                # Propagate exceptions
                except Exception:
                    raise

            if not self._loaded_commands:
                raise RuntimeError("no modules were successfully loaded")

        return self._loaded_commands

    def get_last_output(self) -> Optional[str]:
        """
        Returns the last execution stored output buffer.
        Handy when a command is being executed silenced where later we need to observe its output.
        """
        return self._execution_output

    def execute_build(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        Executes the 'build' method of a registered builder module.
        Args:
            build_profile (BuildProfileType): The build profile to use.

        Returns:
            Optional[int]: The result of the build process.
        """
        class_instance = self._resolve_registered_instance(
            name=build_profile.build_system,
            expected_type=AutoForgeModuleType.BUILDER,
            required_method='build'
        )

        return class_instance.build(build_profile=build_profile)

    def execute_command(self, name: str, arguments: Optional[str] = None, suppress_output: bool = False) -> Optional[
        int]:
        """
        Executes the 'execute' method of a registered CLI command module.
        Args:
            name (str): The name of the registered CLI command.
            arguments (Optional[str]): Shell-style argument string.
            suppress_output (bool): If True, suppress stdout/stderr during execution.

        Returns:
            Optional[int]: The result of the command execution.
        """
        self._execution_output = None

        class_instance = self._resolve_registered_instance(
            name=name,
            expected_type=AutoForgeModuleType.CLI_COMMAND,
            required_method='execute'
        )

        buffer = io.StringIO()
        output_stream = buffer if suppress_output else TerminalTeeStream(sys.stdout, buffer)

        with redirect_stdout(output_stream), redirect_stderr(output_stream):
            result = class_instance.execute(flat_args=arguments)

        self._execution_output = buffer.getvalue()
        return result
