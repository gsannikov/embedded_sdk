"""
Script:         ai.py
Author:         AutoForge Team

Description:
    Bridge the build system with an AI model to allow for..
"""
from typing import Optional

# from openai.lib.azure import AzureOpenAI
import httpx

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

    def _initialize(self, proxy: Optional[str] = None) -> None:
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
        self._model = self._variables.get("AI_MODEL",quiet=True)
        self._endpoint = self._variables.get("AI_ENDPOINT",quiet=True)
        self._req_timeout  = int(self._variables.get("AI_REQ_TIMEOUT", quiet=True))
        self._api_key  = self.auto_forge.configuration.get("api_key")
        self._proxies = {"https://": proxy} if proxy else None

        if None in (self._model, self._endpoint, self._req_timeout, self._api_key):
            raise RuntimeError("environment is missing critical AI_* variables")

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

    async def query(self, prompt: str, context:str, max_tokens: int = 300) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": f"{context}"},
                {"role": "user", "content": str(prompt)}
            ],
            "max_tokens": max_tokens
        }

        try:
            async with httpx.AsyncClient(timeout=self._req_timeout) as client:
                response = await client.post(self._endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()

        except httpx.RequestError as e:
            print(f"Network error: {e}")
        except httpx.HTTPStatusError as e:
            print(f"HTTP error: {e.response.status_code} - {e.response.text}")
        except (KeyError, IndexError):
            print("Unexpected response format.")
        return None
