"""
Script:         dynamic_loader.py
Author:         AutoForge Team

Description:
    Core module which is responsible for dynamically discovering, validating, executing and registering modules
    that implement any of the supported interfaces types.
    This mechanism supports flexible plugin-like extensibility without static dependencies or
    premature imports during application startup
"""

import glob
import importlib.util
import inspect
import io
import os
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout, suppress
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, FunctionType
from typing import Any, Optional, Union, Type, cast, Callable

# AutoForge imports
from auto_forge import (
    AutoForgeModuleType, CoreLogger, BuildProfileType, BuilderRunnerInterface,
    CommandInterface, CommandInterfaceProtocol, CoreModuleInterface, CoreToolBox, CoreTelemetry,
    ModuleInfoType, CoreRegistry, TerminalTeeStream)

AUTO_FORGE_MODULE_NAME = "DynamicLoader"
AUTO_FORGE_MODULE_DESCRIPTION = "Dynamically search and load supported modules"


class CoreDynamicLoader(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        self._execution_output: Optional[str] = None

        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initializes the 'CoreLoader' class and prepares the command registry.
        """

        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry: CoreRegistry = CoreRegistry.get_instance()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()
        self._tool_box: CoreToolBox = CoreToolBox.get_instance()

        # Dependencies check
        if None in (self._core_logger, self._logger, self._registry, self._telemetry,
                    self._tool_box):
            raise RuntimeError("failed to instantiate critical dependencies")

        self._loaded_commands: int = 0

        # Supported base interfaces for command classes
        self._supported_interfaces = {CommandInterface: "CommandInterface",
                                      BuilderRunnerInterface: "BuilderRunnerInterface", }

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running.
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

    def _resolve_registered_instance(self, name: str, expected_type: AutoForgeModuleType, required_method: str) -> Any:
        """
        Common logic for resolving and validating a registered module instance.
        Args:
            name (str): The name of the registered module.
            expected_type (AutoForgeModuleType): The expected module type (e.g., BUILDER, COMMAND).
            required_method (str): The method the instance must implement (e.g., 'build', 'execute').
        Returns:
            Any: The validated class instance.
        """

        module_record = self._registry.get_module_record_by_name(module_name=name.strip())
        if module_record is None:
            raise RuntimeError(f"'{name}' was not recognized as a registered module")

        module_type = module_record.get("auto_forge_module_type", AutoForgeModuleType.UNKNOWN)
        if module_type is not expected_type:
            raise RuntimeError(f"module '{name}' is registered, but not marked as a {expected_type.name.lower()}")

        class_instance: Any = module_record.get("class_instance")
        if class_instance is None:
            raise RuntimeError(f"could not find an instance of '{name}' in the registry")

        if not self._tool_box.has_method(class_instance, required_method):
            raise RuntimeError(f"module '{name}' does not implement '{required_method}'")

        return class_instance

    @staticmethod
    def _command_init_is_kwargs_only(cls: Type[Any]) -> bool:
        with suppress(Exception):
            init = inspect.unwrap(cls.__init__)
            if not isinstance(init, FunctionType):
                return False

            sig = inspect.signature(init)
            params = list(sig.parameters.values())

            return (
                    len(params) == 2 and
                    params[0].name == "self" and
                    params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD and
                    params[1].kind == inspect.Parameter.VAR_KEYWORD
            )

        return False

    def probe(  # noqa: C901
            self, paths: Union[str, Path, Sequence[Union[str, Path]]]) -> int:
        """
        Scans one or more paths for Python modules, searches for classes derived from known base classes,
        instantiates them, and registers them.
        Args:
            paths (str or list of str/Paths): A single path or a list of paths to search for modules.
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
                callable_object: Optional[object] = None

                try:
                    # Attempt to dynamically import the file
                    python_module_spec: Optional[ModuleSpec] = (
                        importlib.util.spec_from_file_location(file_stem_name, file))
                    if python_module_spec is None:
                        self._logger.warning(f"Unable to import '{file_base_name}'. Skipping")
                        continue

                    if python_module_spec is not None:
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
                        if (isinstance(attr, type) and issubclass(attr, tuple(self._supported_interfaces.keys()))
                                and attr not in self._supported_interfaces):
                            callable_object = attr
                            break

                    # If no compatible class was found, skip this module
                    if not callable_object:
                        self._logger.warning(f"No supported class found in module '{file_stem_name}'. Skipping")
                        continue

                    interface_name: str = next((name for base, name in self._supported_interfaces.items()
                                                if issubclass(callable_object, base)), None)

                    if not interface_name:
                        self._logger.warning(f"Unsupported command interface in module '{file_stem_name}'. Skipping")
                        continue

                    if callable_object is None or not isinstance(callable_object, type):
                        self._logger.warning(f"Invalid class object in '{file_stem_name}'. Skipping")
                        continue

                    if not self._command_init_is_kwargs_only(callable_object):
                        self._logger.warning(f"Command init does not have the expected signature (**kwargs). Skipping")
                        continue

                    # Instantiate the class (command), this will auto update the command properties in the registry.
                    try:
                        command_class = cast(Callable[..., CommandInterfaceProtocol], callable_object)
                        command_instance = command_class()
                    except Exception as instantiate_error:
                        self._logger.warning(f"Failed to instantiate '{callable_object}': {instantiate_error}")
                        continue

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
                    docstring_description = self._tool_box.get_module_docstring(python_module_type=python_module_type)
                    command_description = docstring_description if docstring_description else module_info.description

                    # The command should have automatically updated its metadata in the registry; next we validate this.
                    command_record: Optional[dict[str, Any]] = (
                        self._registry.get_module_record_by_name(module_name=module_info.name))

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
                                                                      file_name=file,
                                                                      command_type=module_info.command_type, )

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

    def get_command_known_args(self, name: str) -> Optional[list[str]]:
        """
        Attempts to retrieve the known argument list for a registered command.
        This is a best-effort method that searches the command registry for a class instance
        matching the given command name and expected interface. If found, it invokes
        the instance's `get_known_args()` method to retrieve the list of supported arguments.
        Args:
            name (str): The name of the command to inspect.

        Returns:
            Optional[list[str]]: A list of known argument strings (e.g., ['--input', '-f']),
            or None if the command is unknown or an error occurs.
        """
        with suppress(Exception):
            class_instance = self._resolve_registered_instance(name=name, expected_type=AutoForgeModuleType.COMMAND,
                                                               required_method='get_known_args')
            if class_instance:
                return class_instance.get_known_args(raise_exceptions=False)

        return None  # Unknown command, error, or method not implemented

    def execute_build(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        Executes the 'build' method of a registered builder module.
        Args:
            build_profile (BuildProfileType): The build profile to use.
        Returns:
            Optional[int]: The result of the build process.
        """
        class_instance = self._resolve_registered_instance(name=build_profile.build_system,
                                                           expected_type=AutoForgeModuleType.BUILDER,
                                                           required_method='build')
        return class_instance.build(build_profile=build_profile)

    def execute_command(self, name: str, arguments: Optional[str] = None, suppress_output: bool = False) -> Optional[
        int]:
        """
        Executes the 'execute' method of a registered command module.
        Args:
            name (str): The name of the registered command.
            arguments (Optional[str]): Shell-style argument string.
            suppress_output (bool): If True, suppress stdout/stderr during execution.

        Returns:
            Optional[int]: The result of the command execution.
        """
        self._execution_output = None

        class_instance = self._resolve_registered_instance(name=name, expected_type=AutoForgeModuleType.COMMAND,
                                                           required_method='execute')

        buffer = io.StringIO()
        output_stream = buffer if suppress_output else TerminalTeeStream(sys.stdout, buffer)

        with redirect_stdout(output_stream), redirect_stderr(output_stream):
            result = class_instance.execute(flat_args=arguments)

        self._execution_output = buffer.getvalue()
        return result
