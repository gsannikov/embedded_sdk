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
from pathlib import Path
from typing import List, NamedTuple
from typing import Optional, Any, Dict, TextIO, cast

from auto_forge import (PROJECT_COMMANDS_PATH, CLICommandInterface, CLICommandInfo, AutoLogger)

AUTO_FORGE_MODULE_NAME = "CommandsLoader"
AUTO_FORGE_MODULE_DESCRIPTION = "Dynamically search and load CLI commands"


class _TeeStream:
    """
    A simple output stream duplicator that writes data to multiple target streams.
    """

    def __init__(self, *targets: TextIO):
        """
        Initialize the _TeeStream with one or more target streams.
        Args:
            *targets (TextIO): Output streams to write to (e.g., sys.stdout, StringIO).
        """
        self._targets = targets

    def write(self, data: str) -> int:
        """
        Write data to all registered target streams.
        Args:
            data (str): The string data to write.
        Returns:
            int: The number of characters written (equal to len(data)).
        """
        for target in self._targets:
            target.write(data)
        return len(data)

    def flush(self) -> None:
        """
        Flush all target streams that support flushing.
        """
        for target in self._targets:
            if hasattr(target, "flush"):
                target.flush()


class CommandSummary(NamedTuple):
    """
    Represents a minimal summary of a registered command.

    Attributes:
        name (str): The name of the command.
        description (str): A brief description of what the command does.
    """
    name: str
    description: str


class CommandsLoader:
    """
    The command loader class provides support for dynamically searching and loading commands.
    Args:
        parent (Any, optional): Our parent AutoForge class instance.
    """
    _instance = None
    _is_initialized = False

    def __new__(cls, parent: Optional[Any] = None):
        """
        Create a new instance if one doesn't exist, or return the existing instance.
        Returns:
            CommandsLoader: The singleton instance of this class.
        """
        if cls._instance is None:
            cls._instance = super(CommandsLoader, cls).__new__(cls)

        return cls._instance

    def __init__(self, parent: Optional[Any] = None) -> None:
        """
        Initializes the 'CommandsLoader' class and prepares the command registry.
        """

        if not self._is_initialized:
            try:

                if parent is None:
                    raise RuntimeError("AutoForge instance must be specified when initializing core module")
                self._autoforge = parent  # Store parent' AutoForge' class instance.

                # Get a logger instance
                self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

                self._loaded_commands: int = 0
                self._commands_registry: Dict[str, Dict[str, Any]] = {}
                self._commands_path: Path = PROJECT_COMMANDS_PATH
                self._command_output: Optional[str] = None

                # Supported base interfaces for command classes
                self._supported_interfaces = {
                    CLICommandInterface: "CLICommandInterface"
                }

                # Search for commands and register them
                self._probe()
                self._is_initialized = True

            # Propagate exceptions
            except Exception:
                raise

    def _get_command_record_by_name(self, command_name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves a command record from the registry by its registered name.
        Args:
            command_name (str): The exact name of the command.

        Returns:
            Optional[Dict[str, Any]]: The matching command record, or None.
        """
        command_name = command_name.strip()
        if not command_name:
            raise ValueError("command name must not be empty")

        return self._commands_registry.get(command_name) or None

    def _find_command_record(self, value: str, key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Searches the command registry for a record matching the given value.
        Args:
            value (str): The value to search for.
            key (Optional[str]): Specific key to search within each command record.
                                 If not provided, all keys are scanned.

        Returns:
            Optional[Dict[str, Any]]: The first matching command record, or None.
        """
        for record in self._commands_registry.values():
            if key:
                if key in record and record[key] == value:
                    return record
            else:
                for _, v in record.items():
                    if v == value or (isinstance(v, list) and value in v):
                        return record
        return None

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
            module_name = os.path.splitext(os.path.basename(file))[0]

            try:
                # Dynamically import the module
                spec = importlib.util.spec_from_file_location(module_name, file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for a valid command class
                command_class = None
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, tuple(self._supported_interfaces.keys())):
                        if attr not in self._supported_interfaces:
                            command_class = attr
                            break

                if not command_class:
                    self._logger.warning(f"No valid command class found in module '{module_name}', skipping")
                    continue

                interface_type = next(
                    (name for base, name in self._supported_interfaces.items() if issubclass(command_class, base)),
                    None
                )

                if not interface_type:
                    self._logger.warning(f"Unsupported command interface in '{module_name}', skipping")
                    continue

                command_instance = command_class()
                command_info: CLICommandInfo = command_instance.get_info(module=module)

                if not command_info:
                    raise RuntimeError(f"module '{module_name}' did not return valid command info")

                if self._get_command_record_by_name(command_info.name):
                    raise RuntimeError(f"duplicate command registration: '{command_info.name}'")

                self._commands_registry[command_info.name] = {
                    "command_version": command_info.version,
                    "command_description": command_info.description,
                    "command_aliases": [command_info.name],
                    "interface": interface_type,
                    "instance": command_instance,
                    "module_name": module_name,
                    "file_name": file,
                    "class_name": command_info.class_name,
                    "class_alias": str(command_info.class_name).lower()
                }

                sys.modules[spec.name] = module
                self._loaded_commands += 1
                self._logger.debug(f"Command '{command_info.name}' v{command_info.version} loaded")

            except Exception as err:
                raise RuntimeError(f"failed to load command from '{module_name}': {err}")

        if self._loaded_commands == 0:
            raise RuntimeError("no commands were successfully loaded")

        return self._loaded_commands

    @staticmethod
    def get_instance() -> "CommandsLoader":
        """
        Returns the singleton instance of this class.
        Returns:
            CommandsLoader: The global stored class instance.
        """
        return CommandsLoader._instance

    def get_commands(self) -> List[CommandSummary]:
        """
        Returns a list of command summaries (name and description only),
        omitting all other internal or non-serializable details.
        """
        return [
            CommandSummary(name, meta.get("command_description", ""))
            for name, meta in self._commands_registry.items()
        ]

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

        command_record = self._get_command_record_by_name(command.strip())
        if command_record is None:
            raise RuntimeError(f"command '{command}' is not recognized.")

        # Get the stored instance, cast it the base interface class and execute.
        command_instance: CLICommandInterface = cast(CLICommandInterface, command_record["instance"])
        if not isinstance(command_instance, CLICommandInterface):
            raise RuntimeError(f"command '{command}' does not implement the expected 'CLICommandInterface'")

        buffer = io.StringIO()
        output_stream = buffer if suppress_output else _TeeStream(sys.stdout, buffer)

        with redirect_stdout(output_stream), redirect_stderr(output_stream):
            result = command_instance.execute(flat_args=arguments)

        # Store the command output in the class - lsat command output
        self._command_output = buffer.getvalue()
        return result
