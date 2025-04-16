"""
Script:         commands_loader.py
Author:         AutoForge Team

Description:
    This module defines the CommandsLoader class, responsible for dynamically
    discovering, validating, executing and registering CLI command modules that implement
    supported interface types such as CLICommandInterface.
"""

import glob
import importlib.util
import io
import logging
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Optional, Any, Dict, TextIO, cast

from auto_forge import PROJECT_COMMANDS_PATH, CLICommandInterface, CLICommandInfo

AUTO_FORGE_MODULE_NAME = "CommandsLoader"
AUTO_FORGE_MODULE_DESCRIPTION = "Dynamically search and load CLI commands"


class TeeStream(io.StringIO):  # Yes, inherit from StringIO directly
    def __init__(self, *targets: TextIO):
        super().__init__()
        self._targets = targets

    def write(self, data: str) -> int:
        for target in self._targets:
            target.write(data)
        return len(data)

    def flush(self) -> None:
        for target in self._targets:
            if hasattr(target, "flush"):
                target.flush()


class CommandsLoader:
    def __init__(self):
        """Initializes the command loader and prepares the command registry."""

        self._logger: logging.Logger = logging.getLogger(AUTO_FORGE_MODULE_NAME)
        # self._logger.setLevel(logging.DEBUG)
        self._loaded_commands: int = 0
        self._commands_registry: Dict[str, Dict[str, Any]] = {}
        self._commands_path: Path = PROJECT_COMMANDS_PATH
        self._command_output: Optional[str] = None

        # Supported base interfaces for command classes
        self._supported_interfaces = {
            CLICommandInterface: "CLICommandInterface"
        }

        # Search and register
        self._probe()

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
                command_info: CLICommandInfo = command_instance.get_info()

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

                self._loaded_commands += 1
                self._logger.debug(f"Command '{command_info.name}' v{command_info.version} loaded")

            except Exception as err:
                raise RuntimeError(f"failed to load command from '{module_name}': {err}")

        if self._loaded_commands == 0:
            raise RuntimeError("no commands were successfully loaded")

        return self._loaded_commands

    def get_last_output(self) -> Optional[str]:
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
        # Set the stream based on the suppress flag
        output_stream = buffer if suppress_output else TeeStream(sys.stdout, buffer)

        with redirect_stdout(output_stream), redirect_stderr(output_stream):
            result = command_instance.execute(flat_args=arguments)

        # Store the command output in the class - lsat command output
        self._command_output = buffer.getvalue()
        return result
