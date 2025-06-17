"""
Script:         xray.py
Author:         AutoForge Team

Description:
    Provides ...
"""

# AutoForge imports
from auto_forge import (
    AutoForgeModuleType, CoreModuleInterface, CoreRegistry, CoreVariables
)

AUTO_FORGE_MODULE_NAME = "XRay"
AUTO_FORGE_MODULE_DESCRIPTION = "Files search tool"


class CoreXRay(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initialize CoreXRay.
        """

        self._variables = CoreVariables.get_instance()

        # Persist this module instance in the global registry for centralized access
        registry = CoreRegistry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)
