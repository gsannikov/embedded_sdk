"""
Script:         ai.py
Author:         AutoForge Team

Description:
    Bridge the build system with an AI model to allow for..
"""

# from openai.lib.azure import AzureOpenAI

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreModuleInterface, CoreRegistry,
                        CoreVariables, CoreTelemetry, CoreLogger)

AUTO_FORGE_MODULE_NAME = "AI"
AUTO_FORGE_MODULE_DESCRIPTION = "AI Services Bridge"


class CoreAI(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initialize CoreAI class.
        """
        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()
        self._variables = CoreVariables.get_instance()
        self._registry = CoreRegistry.get_instance()

        # Dependencies check
        if None in (self._core_logger, self._logger, self._telemetry, self._variables, self._registry):
            raise RuntimeError("failed to instantiate critical dependencies")

        # Get mandatory variables
        self._model = self._variables.get("AI_MODE1", "*")
        self._endpoint = self._variables.get("AI_ENDPOINT", None)

        if None in (self._model, self._endpoint):
            raise RuntimeError("environment is missing AI_MODEL or AI_ENDPOINT")

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)
