"""
Script:         builder_interface.py
Author:         AutoForge Team

Description:
    Core abstract base class that defines a standardized interface for implementing a builder instance.
    Each builder implementation is registered at startup with a unique name, and can be invoked as needed based on the
    solution branch configuration, which specifies the registered name of the builder.
"""

import inspect
import logging
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Tuple, Union, Any

from colorama import Fore, Style

# AutoForge imports
from auto_forge import (AutoForgeModuleType, AutoLogger, ModuleInfoType, BuildProfileType,
                        CommandResultType, VersionCompare, CoreToolBoxProtocol, )
# Direct internal imports to avoid circular dependencies
from auto_forge.core.registry import CoreRegistry

# Module identification
AUTO_FORGE_MODULE_NAME = "BuilderInterface"
AUTO_FORGE_MODULE_DESCRIPTION = "Dynamic loadable builder interface"


class BuilderToolChain:
    """
    Tool chaim validation auxiliary cass.
    """

    def __init__(self, toolchain: dict[str, object], builder_instance: Optional["BuilderRunnerInterface"]) -> None:
        """
        Checks that the specified tool chin exists and that its different components,has the correct version.
        Args:
            toolchain (dict[str, object]): The toolchain to check.
            builder_instance (Optional[BuilderRunnerInterface]): The parent builder instance.

        """
        self._toolchain = toolchain
        self._resolved_tools: dict[str, str] = {}
        self._builder_instance = builder_instance

        self._registry = CoreRegistry.get_instance()
        # Retrieve a Toolbox instance and its protocol interface via the registry.
        # This lazy access pattern minimizes startup import overhead and avoids cross-dependency issues.
        self._tool_box_proto: Optional[CoreToolBoxProtocol] = self._registry.get_instance_by_class_name(
            "CoreToolBox", return_protocol=True)
        if self._tool_box_proto is None:
            raise RuntimeError("unable to instantiate dependent core module")

    def validate(self, show_help_on_error: bool = False) -> Optional[bool]:
        """
        Validates the toolchain structure and required tools specified by the solution.
        For each tool:
          - Attempts to resolve the binary using the defined path.
          - Confirms the version requirement is met.
          - Optionally shows help (Markdown-rendered) if validation fails.
        Args:
            show_help_on_error (bool): Show help message if validation fails.
        Return:
            bool: True if validation passes, otherwise an exception is raised.
        """
        required_keys = {"name", "platform", "architecture", "build_system", "required_tools"}
        missing = required_keys - self._toolchain.keys()
        if missing:
            raise ValueError(f"missing top-level toolchain keys: {missing}")

        tools = self._toolchain["required_tools"]
        if not isinstance(tools, dict) or not tools:
            raise ValueError("'required_tools' must be a non-empty dictionary")

        for name, definition in tools.items():
            if not isinstance(definition, dict):
                raise ValueError(f"Tool '{name}' definition must be a dictionary")

            tool_path = definition.get("path")
            version_expr = definition.get("version")
            help_path = definition.get("help")

            if not tool_path or not version_expr:
                raise ValueError(f"toolchain element '{tool_path}' must define 'path' and 'version' fields")

            resolved_t_tool_path = self._resolve_tool([tool_path], version_expr)
            if not resolved_t_tool_path:
                # If we have to auto show help
                if show_help_on_error:
                    if help_path:
                        if self._tool_box_proto.show_help_file(help_path) != 0:
                            self._builder_instance.print_message(
                                message=f"Error displaying help file '{help_path}' see log for details",
                                log_level=logging.WARNING)
                # Break the build
                raise RuntimeError(f"missing toolchain component: {name}")

            self._resolved_tools[name] = resolved_t_tool_path

        return True

    def get_tool(self, tool_name: str) -> Optional[str]:
        """
        Returns the resolved absolute path of the specified tool name,
        or None if not found.
        """
        return self._resolved_tools.get(tool_name)

    def get_value(self, key_name: str) -> Optional[str]:
        """
        Returns the value of a top-level key in the toolchain dictionary,
        only if it is a string. Returns None otherwise.
        """
        value = self._toolchain.get(key_name)
        return value if isinstance(value, str) else None

    def _resolve_tool(self, candidates: list[str], version_expr: str) -> Optional[str]:
        """
        Attempts to locate a binary from the provided list of candidates that satisfies the required version expression.
        Args:
            candidates: A list of binary names or absolute paths to check.
            version_expr: A version requirement string (e.g., ">=3.2").
        Returns:
            The resolved binary path if found and version is valid, otherwise None.
        """
        for binary in candidates:
            path = binary if os.path.isabs(binary) else shutil.which(binary)

            if not path:
                self._builder_instance.print_message(message=f"Toolchain item '{binary}' not found.",
                                                     log_level=logging.ERROR)
                continue

            version_ok, detected_version = self._version_ok(path, version_expr)
            if not version_ok:
                base_name = os.path.basename(path)
                if detected_version:
                    msg = (f"Toolchain item '{base_name}' version {detected_version} "
                           f"does not satisfy required {version_expr}.")
                else:
                    msg = f"Toolchain item '{base_name}' version could not be determined."
                self._builder_instance.print_message(message=msg, log_level=logging.ERROR)
                continue
            return path
        return None

    @staticmethod
    def _version_ok(binary_path: str, version_expr: str) -> Optional[tuple[bool, Optional[str]]]:
        """
        Checks whether the binary at binary_path satisfies the version constraint (e.g., ">=10.0").
        Args:
            binary_path (str): Path to the binary.
            version_expr (str): Version constraint expression (e.g., ">=10.0", "==1.2.3").
        Returns:
            Tuple[bool, Optional[str]]: A tuple of (is_satisfied, detected_version_str).
        """
        try:
            # Run the binary with --version and capture output
            binary_output = subprocess.check_output(args=[binary_path, "--version"], stderr=subprocess.STDOUT,
                                                    text=True)

            compare_results = VersionCompare().compare(detected=binary_output, expected=version_expr)
            return compare_results

        except Exception as version_verify_error:
            raise version_verify_error from version_verify_error


class BuildLogAnalyzerInterface(ABC):
    """
    Abstract base class defining the interface for log analysis.
    Any specific log analyzer (e.g., GCC, Clang, Java) should
    inherit from this interface and implement the 'analyze' method.
    """

    def __init__(self):
        # Keep track of last analysis
        self._last_analysis: Optional[list[dict[str, Union[str, int, None, list[str]]]]] = None

    @abstractmethod
    def analyze(self, log_source: Union[Path, str], json_name: Optional[str] = None) -> Optional[list[dict[str, Any]]]:
        """
        Analyzes a log source, which can be a file path or a string,
        and extracts structured information.
        Args:
            log_source: The source of the log data, either a Path object
                        to a log file or a string containing the log content.
            json_name: Optional JSON export file path.
        Returns:
            A list of dictionaries, where each dictionary represents a parsed entry,
            or None if no relevant entries are found.
        """
        raise NotImplementedError("Subclasses must implement the 'analyze' method.")


class BuilderRunnerInterface(ABC):
    """
    Abstract base class for builder instances that can be dynamically registered and executed by AutoForge.
    """

    def __init__(self, build_system: Optional[str] = None, build_label: Optional[str] = None):
        """
        Initializes the builder and registers it with the AutoForge registry.

        Args:
            build_system (str, optional): The unique name of the builder instance build system to use, for ex.
                make, cmake and so on. If not provided, the value of the
                class field 'AUTO_FORGE_MODULE_NAME' will be used.
            build_label (str, optional): The unique name of the builder instance build label to use.
        """
        caller_frame = inspect.stack()[1].frame
        caller_globals = caller_frame.f_globals

        caller_module_name = caller_globals.get("AUTO_FORGE_MODULE_NAME", None)
        caller_module_description = caller_globals.get("AUTO_FORGE_MODULE_DESCRIPTION", "Description not provided")
        caller_module_version = caller_globals.get("AUTO_FORGE_MODULE_VERSION", "0.0.0")

        self._build_system: str = build_system if build_system is not None else caller_module_name
        if self._build_system is None:
            raise RuntimeError("build_system properties cannot be None")

        # Create a builder dedicated logger instance
        self._logger = AutoLogger().get_logger(name=self._build_system)
        # Set optional build label
        self._build_label: str = build_label if build_label is not None else "AutoForge"

        # Register this builder instance in the global registry for centralized access
        self._registry = CoreRegistry.get_instance()
        self._module_info: ModuleInfoType = (
            self._registry.register_module(name=self._build_system, description=caller_module_description,
                                           version=caller_module_version,
                                           auto_forge_module_type=AutoForgeModuleType.BUILDER))

        # Retrieve a Toolbox instance and its protocol interface via the registry.
        # This lazy access pattern minimizes startup import overhead and avoids cross-dependency issues.
        self._tool_box_proto: Optional[CoreToolBoxProtocol] = self._registry.get_instance_by_class_name(
            "CoreToolBox", return_protocol=True)
        if self._tool_box_proto is None:
            raise RuntimeError("unable to instantiate dependent core module")

        super().__init__()

    @abstractmethod
    def build(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        Validates the provided build configuration and executes the corresponding build flow.
        Args:
            build_profile (BuildProfileType): The build profile containing solution, project, configuration,
                and toolchain information required for the build process.
        Returns:
            Optional[int]: The return code from the build process, or None if not applicable.
        """
        raise NotImplementedError("must implement 'build'")

    def get_info(self) -> ModuleInfoType:
        """
        Retrievers information about the implemented builder.
        Note: Implementation class must call _set_info().
        Returns:
            ModuleInfoType: a named tuple containing the implemented command id
        """
        if self._module_info is None:
            raise RuntimeError('command info not initialized, make sure call set_info() first')

        return self._module_info

    def print_build_results(self, results: Optional[CommandResultType], raise_exception: bool = True) -> Optional[int]:
        """
        Handle and report the result of a build command.
        Args:
            results: The command result object containing return code and optional response.
            raise_exception: Whether to raise an exception if the build failed.
        Returns:
            The return code if results are provided; otherwise, None.
        """
        if results is None:
            return 1  # Error

        if results.return_code != 0:
            self.print_message(message=f"Build failed with error code: {results.return_code}", log_level=logging.ERROR)
            if results.response:
                self.print_message(message=f"Build response: {results.response}", log_level=logging.ERROR)

            if raise_exception:
                raise RuntimeError(f"Build failed with return code: {results.return_code}")

        return results.return_code

    def print_message(self, message: str, bare_text: bool = False, log_level: Optional[int] = logging.DEBUG) -> None:
        """
        Prints a build-time message prefixed with an AutoForge label.
        Args:
            message (str): The text to print.
            bare_text (bool, optional): If True, prints without ANSI color formatting.
            log_level (int, optional): Logging level to use (e.g., logging.INFO).
                                       If None, the message is not logged.
        """
        if not bare_text:
            # Map log levels to distinct label colors
            level_color_map = {logging.CRITICAL: Fore.LIGHTRED_EX, logging.ERROR: Fore.RED,
                               logging.WARNING: Fore.YELLOW, logging.INFO: Fore.CYAN,
                               logging.DEBUG: Fore.LIGHTGREEN_EX, }
            color = level_color_map.get(log_level, Fore.WHITE)
            leading_text = f"{color}-- {self._build_label}:{Style.RESET_ALL} "

        else:
            leading_text = f"-- {self._build_label}: "
            message = self._tool_box_proto.strip_ansi(text=message, bare_text=True)

        # Optionally log the message
        if log_level is not None:
            self._logger.log(log_level, message)

        print(leading_text + message)

    def update_info(self, command_info: ModuleInfoType):
        """
        Updates information about the implemented builder.
        """
        self._module_info = command_info
