"""
Script:         builder_interface.py
Author:         AutoForge Team

Description:
    ToDo
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


class BuilderInterface(ABC):
    """

    """

    def __init__(self, builder_name: Optional[str] = None):
        caller_frame = inspect.stack()[1].frame
        caller_globals = caller_frame.f_globals

        caller_module_name = caller_globals.get("AUTO_FORGE_MODULE_NAME", None)
        caller_module_description = caller_globals.get("AUTO_FORGE_MODULE_DESCRIPTION", "Description not provided")
        caller_module_version = caller_globals.get("AUTO_FORGE_MODULE_VERSION", "0.0.0")

        self._builder_name: str = builder_name if builder_name is not None else caller_module_name
        # Create a builder dedicated logger instance
        self._logger = AutoLogger().get_logger(name=self._builder_name)

        self._build_profile: Optional[BuildProfileType] = None
        self._tool_box = ToolBox().get_instance()

        # Persist this builder instance in the global registry for centralized access
        registry = Registry.get_instance()
        self._module_info: ModuleInfoType = (
            registry.register_module(name=self._builder_name,
                                     description=caller_module_description,
                                     version=caller_module_version,
                                     auto_forge_module_type=AutoForgeModuleType.BUILDER))

    @abstractmethod
    def build(self, build_profile: BuildProfileType) -> Optional[int]:
        """

        """
        raise NotImplementedError("must implement 'build'")
