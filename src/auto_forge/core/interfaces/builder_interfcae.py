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
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Optional

from colorama import Fore, Style

# AutoForge imports
from auto_forge import AutoForgeModuleType, AutoLogger, ModuleInfoType, BuildProfileType
from auto_forge.common.registry import Registry  # Runtime import to prevent circular import
from auto_forge.common.toolbox import ToolBox

AUTO_FORGE_MODULE_NAME = "MakeBuilder"
AUTO_FORGE_MODULE_DESCRIPTION = "Make build tool"


class BuilderToolChainInterface(ABC):
    """
    Abstract base class for toolchain definitions.
    Implementing classes must provide 'validate()' to check structural and semantic correctness.
    Common resolution logic is built-in.
    """

    def __init__(self, toolchain: dict[str, object], builder_instance: Optional["BuilderInterface"]) -> None:
        self._toolchain = toolchain
        self._resolved_tools: dict[str, str] = {}
        self._tool_box = ToolBox().get_instance()
        self._builder_instance = builder_instance

        # Delegate validation to concrete class
        self.validate()

    @abstractmethod
    def validate(self) -> None:
        """
        Perform structural and semantic validation of the toolchain.
        Must populate self._resolved_tools with valid tools or raise an exception.
        """
        raise NotImplementedError("must implement 'validate'")

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
        Attempts to find a binary from the candidates list that meets the version requirement.
        """
        for binary in candidates:
            path = shutil.which(binary) if not binary.startswith("/") else binary
            if path and self._version_ok(path, version_expr):
                return path
        return None

    @staticmethod
    def _version_ok(binary_path: str, version_expr: str) -> bool:
        """
        Checks if the binary at binary_path meets the version constraint (e.g., ">=10.0").
        Returns False if version can't be parsed or the check fails.
        """
        with suppress(Exception):
            output = subprocess.check_output([binary_path, "--version"], stderr=subprocess.STDOUT, text=True)
            match = re.search(r"\d+(\.\d+)+", output)
            if not match:
                return False
            current_version = tuple(map(int, match.group(0).split(".")))
            required_version = tuple(map(int, version_expr[2:].split(".")))

            if version_expr.startswith(">="):
                return current_version >= required_version
            elif version_expr.startswith(">"):
                return current_version > required_version
            elif version_expr.startswith("=="):
                return current_version == required_version
            return False

        return False


class BuilderInterface(ABC):
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
        self._tool_box = ToolBox().get_instance()

        # Persist this builder instance in the global registry for centralized access
        registry = Registry.get_instance()
        self._module_info: ModuleInfoType = (
            registry.register_module(name=self._build_system, description=caller_module_description,
                                     version=caller_module_version, auto_forge_module_type=AutoForgeModuleType.BUILDER))

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
                logging.WARNING: Fore.YELLOW, logging.INFO: Fore.CYAN, logging.DEBUG: Fore.LIGHTGREEN_EX, }
            color = level_color_map.get(log_level, Fore.WHITE)
            leading_text = f"{color}-- {self._build_label}:{Style.RESET_ALL} "

        else:
            leading_text = f"-- {self._build_label}: "
            message = self._tool_box.strip_ansi(text=message, bare_text=True)

        # Optionally log the message
        if log_level is not None:
            self._logger.log(log_level, message)

        print(leading_text + message)

    def update_info(self, command_info: ModuleInfoType):
        """
        Updates information about the implemented builder.
        """
        self._module_info = command_info
