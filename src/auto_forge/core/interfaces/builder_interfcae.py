"""
Script:         builder_interface.py
Author:         AutoForge Team

Description:
    Core abstract base class that defines a standardized interface for implementing a builder instance.
    Each builder implementation is registered at startup with a unique name, and can be invoked as needed based on the
    solution branch configuration, which specifies the registered name of the builder.
"""

import inspect
from abc import ABC, abstractmethod
from typing import Optional

# AutoForge imports
from auto_forge import AutoForgeModuleType, AutoLogger, ModuleInfoType, BuildProfileType
from auto_forge.common.registry import Registry  # Runtime import to prevent circular import
from auto_forge.common.toolbox import ToolBox

AUTO_FORGE_MODULE_NAME = "MakeBuilder"
AUTO_FORGE_MODULE_DESCRIPTION = "Make build tool"


class BuilderToolchainValidationError(Exception):
    """Raised when the toolchain validation fails."""


class BuilderConfigurationBuildError(Exception):
    """Raised when a configuration build process fails."""


class BuilderInterface(ABC):
    """
    Abstract base class for builder instances that can be dynamically registered and executed by AutoForge.
    """

    def __init__(self, build_system: Optional[str] = None):
        """
        Initializes the builder and registers it with the AutoForge registry.

        Args:
            build_system (str, optional): The unique name of the builder instance build system to use, for ex.
                make, cmake and so on. If not provided, the value of the
                class field 'AUTO_FORGE_MODULE_NAME' will be used.
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

        self._build_profile: Optional[BuildProfileType] = None
        self._tool_box = ToolBox().get_instance()

        # Persist this builder instance in the global registry for centralized access
        registry = Registry.get_instance()
        self._module_info: ModuleInfoType = (
            registry.register_module(name=self._build_system,
                                     description=caller_module_description,
                                     version=caller_module_version,
                                     auto_forge_module_type=AutoForgeModuleType.BUILDER))

        super().__init__()

    @abstractmethod
    def build(self, build_profile: BuildProfileType, leading_text:Optional[str] = None) -> Optional[int]:
        """
        Validates the provided build configuration and executes the corresponding build flow.
        Args:
            build_profile (BuildProfileType): The build profile containing solution, project, configuration,
                and toolchain information required for the build process.
            leading_text (text, optional): If specified will be shown when the builder is running.

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

    def update_info(self, command_info: ModuleInfoType):
        """
        Updates information about the implemented builder.
        """
        self._module_info = command_info
