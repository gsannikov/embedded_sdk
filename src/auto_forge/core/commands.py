"""
Script:         commands_loader.py
Author:         AutoForge Team

Description:
    Core module which defines the 'CommandsLoader' class, responsible for dynamically
    discovering, validating, executing and registering CLI command modules that implement
    supported interface types such as CLICommandInterface.
"""

import glob
import importlib.util
import io
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Optional, cast, Dict, Any

# AutoGorge local imports
from auto_forge import (CoreModuleInterface, CLICommandInterface,
                        AutoForgeModuleType, AutoForgeModuleInfo, TerminalTeeStream,
                        PROJECT_COMMANDS_PATH,
                        Registry, AutoLogger)

AUTO_FORGE_MODULE_NAME = "CommandsLoader"
AUTO_FORGE_MODULE_DESCRIPTION = "Dynamically search and load CLI commands"


class CoreCommands(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared earlier in `__init__()`
        See 'CoreModuleInterface' usage.
        """
        self._command_output: Optional[str] = None

        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initializes the 'CommandsLoader' class and prepares the command registry.
        """

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry = Registry.get_instance()
        self._loaded_commands: int = 0
        self._commands_path: Path = PROJECT_COMMANDS_PATH

        # Supported base interfaces for command classes
        self._supported_interfaces = {
            CLICommandInterface: "CLICommandInterface"
        }

        # Search for commands and register them
        self._probe()

        # Persist this module instance in the global registry for centralized access
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME,
                                       description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

    def _probe(self) -> int:
        """
        Scans the project commands path for Python modules, validates command classes,
        and registers them into the internal command registry.
        Returns:
            int: Number of successfully loaded commands.
        """
        if not self._commands_path.exists():
            raise RuntimeError(f"commands path not found: {self._commands_path}")

        for file in glob.glob(str(self._commands_path / "*.py")):

            file_base_name: str = os.path.basename(file)
            file_stem_name = os.path.splitext(file_base_name)[0]  # File name excluding the extension
            python_module_type: Optional[ModuleType] = None
            class_object: Optional[object] = None

            try:
                # Attempt to dynamically import the file
                python_module_spec: Optional[ModuleSpec] = importlib.util.spec_from_file_location(file_stem_name, file)
                if python_module_spec is None:
                    self._logger.warning(f"Unable to import '{file_base_name}'. Skipping")
                    continue

                if python_module_spec:
                    python_module_type: Optional[ModuleType] = importlib.util.module_from_spec(python_module_spec)
                    python_module_spec.loader.exec_module(python_module_type)

                if python_module_type is None:
                    self._logger.warning(
                        f"File '{file_base_name}' was found, but dynamic import returned None. Skipping")
                    continue

                # Now that the file is imported, inspect its contents and find the first class
                # that inherits from one of the supported interfaces defined in 'self._supported_interfaces'.

                for attr_name in dir(python_module_type):
                    attr = getattr(python_module_type, attr_name)

                    if isinstance(attr, type) and issubclass(attr, tuple(self._supported_interfaces.keys())):
                        if attr not in self._supported_interfaces:
                            class_object = attr
                            break

                # If no compatible class was found, skip this module
                if not class_object:
                    self._logger.warning(f"No supported class found in module '{file_stem_name}'. Skipping")
                    continue

                interface_type = next(
                    (name for base, name in self._supported_interfaces.items() if issubclass(class_object, base)),
                    None
                )

                if not interface_type:
                    self._logger.warning(f"Unsupported command interface in module '{file_stem_name}'. Skipping")
                    continue

                # Instantiate the class (command), this will auto update the command properties in the registry.
                command_instance = class_object()

                # Invoke 'get_info()', which is defined by the interface. Since this class was loaded dynamically,
                # we explicitly pass the Python 'ModuleType' to the implementation so it can update its own metadata.
                # This type is known to us but cannot be inferred automatically by the loaded class.
                module_info: AutoForgeModuleInfo = command_instance.get_info(python_module_type=python_module_type)

                if not module_info:
                    raise RuntimeError(
                        f"Loaded class '{command_instance.__class__.__name__}' did not return module info")

                # The command should have automatically updated its metadata in the registry; next we validate this.
                command_record: Optional[Dict[str, Any]] = self._registry.get_module_record_by_name(
                    module_name=module_info.name,
                    case_insensitive=False
                )

                if not command_record:
                    raise RuntimeError(f"Command '{module_info.name}' could not be found in the registry.")

                # Ensure the dynamically loaded module is accessible via sys.modules,
                # allowing standard import mechanisms and references to resolve it by name.
                sys.modules[python_module_spec.name] = python_module_type

                self._loaded_commands += 1
                self._logger.debug(f"Command '{module_info.name}' loaded from module '{file_stem_name}'")

            # Propagate exceptions
            except Exception:
                raise

        if self._loaded_commands == 0:
            raise RuntimeError("no commands were successfully loaded")

        return self._loaded_commands

    def get_last_output(self) -> Optional[str]:
        """
        Returns the last executed command output.
        Handy when a command is being executed silenced where later we need to observe its output.
        """
        return self._command_output

    def execute(self, command: str, arguments: Optional[str] = None, suppress_output: bool = False) -> Optional[int]:
        """
        Executes a registered CLI command by name with optional shell-style arguments.

        Args:
            command (str): The name of the command to execute.
            arguments (Optional[str]): A shell-style argument string (e.g., "-p --verbose").
                                       If None, the command will run with default or no arguments.
            suppress_output (bool): suppress_output (bool): If True, suppress terminal output.

        Returns:
            Optional[int]: The result code returned by the command's `execute()` method.
                           Typically 0 for success, non-zero for error.
        """

        self._command_output = None  # Invalidate last command output
        self._logger.debug(f"Executing AutoForge command: '{command}'")

        # Registry lookup
        command_record = self._registry.get_module_record_by_name(module_name=command.strip())
        if command_record is None:
            raise RuntimeError(f"command '{command}' is not recognized")

        # Making sure the record belongs to an AutoForge dynamically loaded CLI command
        auto_forge_module_type = command_record.get("auto_forge_module_type", AutoForgeModuleType.UNKNOWN)
        if auto_forge_module_type != AutoForgeModuleType.CLI_COMMAND:
            raise RuntimeError(f"module '{command}' is registered, but is not marked as a CLI command.")

        # Get the stored class instance, cast it the base interface class and execute.
        class_instance = command_record.get("class_instance", None)
        if class_instance is None:
            raise RuntimeError(f"could not find an instance of '{command}' in the registry.")

        # Being pedantic, making sure the command was derived from 'CLICommandInterface'
        command_instance: CLICommandInterface = cast(CLICommandInterface, class_instance)
        if not isinstance(command_instance, CLICommandInterface):
            raise RuntimeError(f"command '{command}' does not implement the expected 'CLICommandInterface'")

        # Finally execute, optionally with or without terminal output.
        buffer = io.StringIO()
        output_stream = buffer if suppress_output else TerminalTeeStream(sys.stdout, buffer)

        with redirect_stdout(output_stream), redirect_stderr(output_stream):
            result = command_instance.execute(flat_args=arguments)

        # Store the command output in the class - lsat command output
        self._command_output = buffer.getvalue()
        return result
