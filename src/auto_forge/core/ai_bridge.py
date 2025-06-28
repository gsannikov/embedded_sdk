"""
Script:         ai_prompt.py
Author:         AutoForge Team

Description:
    Bridge the build system with an AI model.
"""
from typing import Optional

# from openai.lib.azure import AzureOpenAI
import httpx
from httpx import TimeoutException, RequestError, HTTPStatusError

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreModuleInterface, CoreRegistry,
                        CoreVariables, CoreTelemetry, CoreLogger)

AUTO_FORGE_MODULE_NAME = "AIBridge"
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
        self._model = self._variables.get("AI_MODEL", quiet=True)
        self._endpoint = self._variables.get("AI_ENDPOINT", quiet=True)
        self._req_timeout = int(self._variables.get("AI_REQ_TIMEOUT", quiet=True))

        # Get API key from the stored secrets
        self._api_key = self._get_key_from_secrets('openai_api_key')
        if self._api_key is None or not self._api_key:
            raise RuntimeError("failed to retrieve AI API key")

        self._proxies = {"https://": proxy} if proxy else None
        if None in (self._model, self._endpoint, self._req_timeout, self._api_key):
            raise RuntimeError("environment is missing critical AI_* variables")

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

    def _get_key_from_secrets(self, key_name: str) -> Optional[str]:
        """ Gets the API key from the secrets' dictionary. """
        secrets = self.auto_forge.secrets
        if secrets is not None:
            api_keys = secrets.get('api_keys')  # This will be None
            if isinstance(api_keys, dict):  # This condition will be False
                google_ai_key = api_keys.get(key_name)
                return google_ai_key.strip() if google_ai_key else None

        return None

    async def query(self, prompt: str, context: Optional[str] = None, max_tokens: int = 300,
                    temperature: Optional[float] = 0.7, timeout: Optional[int] = None) -> Optional[str]:
        """
        Asynchronously sends a prompt to the AI service and retrieves the generated response.

        Args:
            prompt (str): The user's input message or query.
            context (Optional[str]): System-level instruction or role description (e.g., "You are a helpful assistant").
            max_tokens (int, optional): The maximum number of tokens to generate in the response. Defaults to 300.
            timeout (Optional[int], optional): Request timeout in seconds. If None, uses self._req_timeout.
            temperature (Optional[float], optional): The temperature to use. Defaults to 0.7.

        Returns:
            Optional[str]: The AI-generated response, or None if an error occurred.
        """
        request_timeout = timeout if timeout is not None else self._req_timeout
        request_context = context or ("You are a helpful assistant specializing in "
                                      "embedded firmware development using C and C++.")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request_context},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.post(self._endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()

        except TimeoutException as e:
            print(f"Request timed out after {request_timeout} seconds: {e}")
        except HTTPStatusError as e:
            print(f"HTTP error: {e.response.status_code} - {e.response.text}")
        except RequestError as e:
            print(f"Network error: {e}")
        except (KeyError, IndexError):
            print("Unexpected response format")
        except Exception as exception:
            print(f"Unexpected exception: {exception}")

        return None
