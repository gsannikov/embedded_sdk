"""
Script:         ai_bridge.py
Author:         AutoForge Team

Description:
    Bridges the build system with an AI model. This core module performs the following tasks:

    1. Manages multiple AI connection profiles (referred to as AI providers) and stores their information securely.
    2. Enables sending requests to any of the configured providers, both synchronously and asynchronously.
    3. Provides a method to render AI responses into Markdown format.
"""
import ast
import contextlib
import json
import os
import re
from pathlib import Path
from typing import Optional, Union, Any
from urllib.parse import urlparse

import httpx
from httpx import TimeoutException, RequestError, HTTPStatusError

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreModuleInterface, CoreRegistry, AIProvidersType,
                        Crypto, CoreVariables, CoreTelemetry, CoreLogger, CoreToolBox, AIProviderType)

AUTO_FORGE_MODULE_NAME = "AIBridge"
AUTO_FORGE_MODULE_DESCRIPTION = "AI Services Bridge"
AUTO_FORGE_AI_DEFAULT_REQ_TIMEOUT = 30


class CoreAIBridge(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initializes the CoreAI class.
        """
        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._variables = CoreVariables.get_instance()
        self._registry = CoreRegistry.get_instance()
        self._tool_box = CoreToolBox.get_instance()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()

        # Dependencies check
        if None in (self._core_logger, self._logger, self._telemetry, self._variables, self._registry, self._tool_box):
            raise RuntimeError("failed to instantiate critical dependencies")

        self._providers = AIProvidersType()
        self._proxy_config: Optional[str] = None
        self._provider: Optional[AIProviderType] = None
        self._payload: Optional[dict] = None
        self._headers: Optional[dict] = None
        self._end_point_patched: bool = False
        self._robin: Optional[str] = None
        self._batman: Optional[str] = None
        self._enabled: bool = False

        # Load AI providers from the secured storage
        if not self._load_providers():
            return

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

        self._logger.info(
            f"AI Bridge successfully initialized with provider '{self._provider.pretty_name}', model: {self._provider.model}")
        self._enabled = True

    def _load_providers(self, joker: Optional[str] = None) -> bool:
        """
        Load and initialize the AI providers from encrypted storage.

        Internally, this method:
        - Uses 'robin' (a key file path) to initialize a secure Crypto container.
        - Uses 'batman' (an encrypted data file path) to load or create secured provider data.
        - Optionally, accepts 'joker' as a clear-text JSON file to reinitialize the storage.
        - Validates the stored data version and structure.
        - Activates the preferred provider if specified, or falls back to the first available one.
        - Normalizes required provider fields and configures proxy settings if needed.
        Parameters:
            joker (Optional[str]): Optional path to a JSON file containing plain provider definitions.
                                   If provided, this file is used to reset the encrypted storage.
                                   It must exist, be a valid `.json`, and contain a dictionary of providers.
        Returns:
            bool: True if the storage was loaded successfully and a valid provider is available;
                  False if validation failed or an error occurred during setup.
        """
        try:

            self._batman = self._variables.get("AF_STORAGE_BATMAN")
            self._robin = self._variables.get("AF_STORAGE_ROBIN")
            preferred_provider_name = self._variables.get("AF_AI_PROVIDER")

            if None in (self._batman, self._robin):
                raise RuntimeError("Required necessities ware missing")

            # Initialize Crypto with robin creating it if needed
            crypto = Crypto(key_file=self._robin, create_as_needed=True)

            # When joker was specified, use it and try to get a dictionary out of it to initialize a fresh storage
            if isinstance(joker, str) and joker:
                joker = Path(self._variables.expand(joker))
                if not joker.exists() or not joker.is_file() or joker.suffix.lower() != ".json":
                    raise FileNotFoundError(f"Providers file '{joker}' not found or not a .json file")
                self._logger.debug(f"Loading providers from '{str(joker)}'")
                joker_providers = AIProvidersType().from_json(joker)
                joker_data = joker_providers.to_dict()
                if not isinstance(joker_data, dict):
                    raise FileNotFoundError(f"Providers file '{joker}' cold be converted to a dictionary")
                os.unlink(self._batman) if os.path.exists(self._batman) else None
                crypto.create_or_load_encrypted_dict(filename=self._batman, default_data=joker_data)

            # Read the storage or create a fresh storage if we could not read it.
            storage_data = crypto.create_or_load_encrypted_dict(filename=self._batman)
            storage_version: Optional[str] = storage_data.get('version') if isinstance(storage_data, dict) else None

            if not isinstance(storage_version, str) or storage_version != self._providers.version:
                self._logger.warning("Storage version mismatch, generating new one")
                os.unlink(self._batman) if os.path.exists(self._batman) else None
                storage_data = crypto.create_or_load_encrypted_dict(
                    filename=self._batman, default_data=self._providers.to_dict())
                storage_version = storage_data.get('version') if isinstance(storage_data, dict) else None

            # Second time around after generating a fresh data with defaults
            if not isinstance(storage_version, str) or storage_version != self._providers.version:
                raise RuntimeError("Stored version mismatch after new storage was created")

            # Create providers instance based on the stored data
            self._providers = AIProvidersType().from_dict(storage_data)
            if not isinstance(self._providers.providers, list) or not self._providers.providers:
                raise RuntimeError("Storage providers list invalid or empty")

            # Look for the provider by name or fallback to the first one
            self._provider = (
                self._providers.get_provider(preferred_provider_name)
                if preferred_provider_name
                else (self._providers.providers[0] if self._providers.providers else None)
            )

            if self._provider is None:
                raise RuntimeError("No AI providers configured.")

            # Validate mandatory properties any provider should have
            if not isinstance(self._provider.name, str) or not self._provider.name:
                self._logger.error("'name' in the active provider is invalid")
                return False
            # Normalize
            self._provider.name = self._provider.name.strip().lower()

            if not isinstance(self._provider.model, str) or not self._provider.model:
                self._logger.error(f"'model' in provider {self._provider.name} is missing or invalid")
                return False
            # Normalize
            self._provider.model = self._provider.model.strip()

            if not isinstance(self._provider.endpoint, str) or not self._provider.endpoint:
                self._logger.error(f"'endpoint' in provider {self._provider.name} is missing or invalid")
                return False

            if not isinstance(self._provider.keys, list) or not self._provider.keys:
                self._logger.error(f"'keys' in provider {self._provider.name} are missing")
                return False

            if not isinstance(self._provider.request_time_out, (int, float)) or not self._provider.request_time_out:
                self._logger.error(
                    f"Specified 'request_time_out' in provider {self._provider.name} is invalid, setting default")
                self._provider.request_time_out = AUTO_FORGE_AI_DEFAULT_REQ_TIMEOUT

            # Some providers insists on no proxy be used when accessing their host
            if self._provider.proxy_allowed and self._provider.proxy_server:
                self._proxy_config = self._provider.proxy_server.url()

            # Hooray! looks like we got a valid provider
            return True

        except (ValueError, FileNotFoundError, RuntimeError) as exception:
            # Catch specific errors from Crypto methods or custom checks
            self._logger.error(f"Failed to initialize the storage: {exception}")
        except Exception as exception:
            # Catch any other unexpected errors during the process
            self._logger.error(f"unexpected error occurred during storage initialization: {exception}")

        return False

    def _prepare_request(self,
                         prompt: str,
                         request_context: str,
                         max_tokens: Optional[int] = None,
                         temperature: Optional[float] = None,
                         debug_output: bool = False
                         ):
        """
        Prepares headers and payload for an AI chat completion request based on the current provider.
        Construct the request for both OpenAI and Azure OpenAI providers.
        Args:
            prompt (str): The user's input prompt or query.
            request_context (str): A system prompt defining the assistant's behavior.
            max_tokens (int): The maximum number of tokens to be generated by the model.
            temperature (Optional[float]): Sampling temperature for creativity. Ignored if the model doesn't support it.
            debug_output (bool): If True, print debug information including endpoint, headers, and payload.

        """
        self._headers = dict()
        self._payload = dict()

        # ----------------------------------------------------------------------
        #
        # Open AI
        #
        # ----------------------------------------------------------------------

        if self._provider.name == "openai":

            # Get API key from the storage
            api_key = self._provider.get_key(name="api_key")
            if api_key is None or not api_key:
                raise RuntimeError("Failed to retrieve AI API key, request aborted")

            self._headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            self._payload = {
                "model": self._provider.model,
                "messages": [
                    {"role": "system", "content": request_context},
                    {"role": "user", "content": prompt}
                ],
                "temperature": temperature if temperature is not None else 0.7
            }

            # Only assign max_tokens if it is a valid int else use default
            if isinstance(max_tokens, int):
                self._payload["max_tokens"] = max_tokens

        # ----------------------------------------------------------------------
        #
        # Intel - AzureOpen AI
        #
        # ----------------------------------------------------------------------

        elif self._provider.name == "azure_openai":

            # Get API key from the storage
            api_key = self._provider.get_key(name="subscription_key")
            if api_key is None or not api_key:
                raise RuntimeError("Failed to retrieve AI API key, request aborted")

            self._headers = {
                "api-key": api_key,
                "Content-Type": "application/json"
            }

            self._payload = {
                "messages": [
                    {"role": "system", "content": request_context},
                    {"role": "user", "content": prompt}
                ],
                # Note: Current Intel AzureAI- o3mini model specific quirks.
                "max_completion_tokens": self._provider.max_tokens if max_tokens is not None else 10000
            }

            # Construct Azure-specific endpoint with deployment and version
            if not self._end_point_patched:
                self._provider.endpoint = (
                    f"{self._provider.endpoint.rstrip('/')}/openai/deployments/"
                    f"{self._provider.deployment}/chat/completions"
                    f"?api-version={self._provider.api_version}"
                )
                self._end_point_patched = True

            # Only assign max_tokens if it is a valid int else use default
            if isinstance(max_tokens, int):
                self._payload["max_completion_tokens"] = max_tokens

        else:
            raise RuntimeError(f"AI bridge does not currently support provider '{self._provider.name}'")

        if debug_output:
            print(f"\n{self._provider.pretty_name} Debug 🔍")
            print(f"Endpoint         : {self._provider.endpoint}")
            print(f"Deployment       : {self._provider.deployment}")
            print(f"API Version      : {self._provider.api_version}")
            print(f"API Key (partial): {api_key[:5]}... [len={len(api_key)}]")
            print(f"Headers          : {self._headers}")
            print(f"Payload          : {self._payload}\n")

    @staticmethod
    @contextlib.contextmanager
    def _disable_proxy_for_host(host: str):
        """Temporarily disable proxies for a specific host using NO_PROXY."""
        old_no_proxy = os.environ.get("NO_PROXY", "")
        try:
            if host not in old_no_proxy:
                new_no_proxy = ",".join(filter(None, [old_no_proxy, host]))
                os.environ["NO_PROXY"] = new_no_proxy
            yield
        finally:
            if old_no_proxy:
                os.environ["NO_PROXY"] = old_no_proxy
            else:
                os.environ.pop("NO_PROXY", None)

    def response_to_markdown(self, response: Optional[str], export_markdown_file: Union[str, Path],
                             prompt: Optional[str] = None,
                             context: Optional[str] = None,
                             debug: bool = True) -> bool:
        """
        Render the AI response as a Markdown file for later inspection using a textual viewer.
        Args:
            response (Optional[str]): The AI-generated response.
            export_markdown_file (str | Path): The file path where the Markdown output should be written.
            prompt(Optional[str]): The AI prompt used when the request was sent.
            context (Optional[str]): The AI request context used when the request was sent (e.g.'You are a helpful assistant...')
            debug (bool): Store the raw AI response to a text file
        Returns:
            bool: True if the file was successfully written, False otherwise.
        """

        def _to_dict(_a: Optional[Any] = None) -> Optional[dict]:
            """Converts a string to a dictionary if safely possible; otherwise returns None."""
            if not isinstance(_a, str) or not _a.strip():
                return None
            try:
                _obj = ast.literal_eval(_a.strip())
                return _obj if isinstance(_obj, dict) else None
            except (ValueError, SyntaxError):
                return None

        def _clean_snippet(_code: str) -> str:
            """
            Clean and format a C/C++ snippet using the SDK formatter.
            """
            _lines = [_line.rstrip() for _line in _code.strip().splitlines()]
            if not any(_line.strip() for _line in _lines):
                return "// (snippet content was omitted or removed for brevity)"

            compacted = []
            blank_count = 0
            for _line in _lines:
                if _line.strip():
                    compacted.append(_line)
                    blank_count = 0
                else:
                    blank_count += 1
                    if blank_count <= 1:
                        compacted.append("")
            try:
                return self._tool_box.clang_formatter("\n".join(compacted))
            except Exception as formatter_error:
                self._logger.warning(f"Failed to format snippet {formatter_error}")
                return "\n".join(compacted)

        if not isinstance(response, str) or not response.strip():
            self._logger.debug("Failed to export AI response, invalid input")
            return False

        try:

            export_markdown_file = Path(export_markdown_file).expanduser().resolve()
            raw_txt_file = export_markdown_file.with_suffix(".raw.txt")

            if debug:
                raw_txt_file.write_text(response.strip(), encoding="utf-8")
                self._logger.debug(f"AI raw response saved to: {raw_txt_file}")

            md_lines: list[str] = [f"# AI Report Provided By {self._provider.pretty_name}", ""]

            try:
                # Build error context Section
                # If the request_prompt is essentially a dictionary try to decode it as build error context
                if isinstance(prompt, str) and prompt:

                    error_ctx_data = _to_dict(prompt)
                    if isinstance(error_ctx_data, dict):
                        md_lines.append("## 🛫 Outgoing Prompt (❌ Error Context)")
                        md_lines.append("")

                        events = error_ctx_data.get("events") if isinstance(error_ctx_data, dict) else error_ctx_data
                        if not isinstance(events, list):
                            raise TypeError("Expected 'events' to be a list in context")

                        md_lines.append("| Type | File | Function | Line:Col | Message |")
                        md_lines.append("|------|------|----------|----------|---------|")

                        for entry in events:
                            file = Path(entry.get("file", "None")).name
                            function = entry.get("function", "-")
                            typ = entry.get("type", "")
                            line = entry.get("line", "?")
                            col = entry.get("column", "?")
                            msg = entry.get("message", "None")
                            md_lines.append(f"| {typ} | `{file}` | `{function}` | `{line}:{col}` | {msg} |")

                        md_lines.append("")

                        # Optional toolchain info
                        if isinstance(error_ctx_data, dict) and "toolchain" in error_ctx_data:
                            md_lines.append("### 🛠️ Toolchain Info")
                            md_lines.append("")
                            for k, v in error_ctx_data["toolchain"].items():
                                md_lines.append(f"- **{k}**: {v}")
                            md_lines.append("")

                        for entry in events:
                            snippet = entry.get("snippet", "")
                            if isinstance(snippet, str) and snippet.strip():
                                file = entry.get("file", "")
                                line = entry.get("line", "?")
                                cleaned_snippet = _clean_snippet(snippet)
                                md_lines.append(f"### ✂️ Snippet: {Path(file).name} at line {line}")
                                md_lines.append("")
                                md_lines.append("```c")
                                md_lines.append(f"// From file: {file} at line {line}")
                                md_lines.append(cleaned_snippet)
                                md_lines.append("```")
                                md_lines.append("")
                    else:
                        # Prompt is an ornery string
                        md_lines.append("## 🛫 Outgoing Prompt")
                        md_lines.append("")
                        md_lines.append(prompt)

            except Exception as format_error:
                self._logger.warning(f"Failed to format context: {format_error}")

            # Append the the AI request context ("You are an amazing assistant") if we have it.
            if isinstance(context, str) and context:
                md_lines.append("## 🛫 Outgoing Context")
                md_lines.append("")
                md_lines.append(context)

            # ------------------------------------------------------------------
            #
            # Rendering the actual AI response
            #
            # ------------------------------------------------------------------

            # Define multiple patterns
            markdown_pattern = re.compile(r"```(?:\w+)?\s*\n(.*?)```", re.DOTALL)
            dashed_block_pattern = re.compile(r"-{10,}\n(.*?)\n-{10,}", re.DOTALL)
            labeled_code_block_pattern = re.compile(
                r"(?:Original code:|Revised code:)\s*-{10,}\s*\n(.*?)\n-{10,}", re.DOTALL)
            response_body = response  # default

            # ------------------------------------------------------------------
            #
            # Formatting AzureOpanAI
            #
            # ------------------------------------------------------------------

            if self._provider.name == "azure_openai":
                # Try markdown first
                code_blocks = markdown_pattern.findall(response)
                if code_blocks:
                    response_body = markdown_pattern.sub("[[CODE_BLOCK]]", response)
                else:
                    # Try dashed block
                    code_blocks = dashed_block_pattern.findall(response)
                    if code_blocks:
                        response_body = dashed_block_pattern.sub("[[CODE_BLOCK]]", response)
                    else:
                        # Try labeled sections like 'Original code:' or 'Revised code:'
                        code_blocks = labeled_code_block_pattern.findall(response)
                        if code_blocks:
                            response_body = labeled_code_block_pattern.sub("[[CODE_BLOCK]]", response)


            # ------------------------------------------------------------------
            #
            # Formatting Open|AI
            #
            # ------------------------------------------------------------------

            elif self._provider.name == "openai" or self._provider.name is None:
                code_blocks = markdown_pattern.findall(response)
                response_body = markdown_pattern.sub("[[CODE_BLOCK]]", response)

            else:
                self._logger.warning(f"Unknown provider '{self._provider.name}'; using default markdown pattern")
                code_blocks = markdown_pattern.findall(response)
                response_body = markdown_pattern.sub("[[CODE_BLOCK]]", response)

            if debug:
                if not code_blocks:
                    self._logger.warning("No code blocks found or list is empty")
                else:
                    self._logger.debug(f"Code blocks found: {len(code_blocks)}")
                self._logger.debug(f"Response body after substitution: {repr(response_body)}")

            parts = re.split(r"\n\s*\n", response_body.strip())
            parts = [p.strip() for p in parts if p.strip()]

            md_lines.append(f"## 🛬 Incoming Response")
            md_lines.append("")

            for part in parts:
                if "[[CODE_BLOCK]]" in part:
                    segments = part.split("[[CODE_BLOCK]]")
                    for i, seg in enumerate(segments):
                        if isinstance(seg, str) and seg.strip():
                            for line in seg.strip().splitlines():
                                md_lines.append(f"> {line.strip()}")
                            md_lines.append("")
                        if i < len(segments) - 1 and code_blocks:
                            code = code_blocks.pop(0).strip()
                            md_lines.append("```c")
                            md_lines.append(code)
                            md_lines.append("```")
                            md_lines.append("")
                else:
                    for line in part.splitlines():
                        if isinstance(line, str) and line.strip():
                            md_lines.append(f"> {line.strip()}")
                    md_lines.append("")

            # Fallback for any remaining code blocks
            for leftover in code_blocks:
                md_lines.append("```c")
                md_lines.append(leftover.strip())
                md_lines.append("```")
                md_lines.append("")

            if not md_lines:
                self._logger.debug("No structured content detected; exporting as plain block.")
                md_lines.append("```text")
                md_lines.append(response.strip())
                md_lines.append("```")

            export_markdown_file.write_text("\n".join(md_lines), encoding="utf-8")
            return True

        except Exception as export_error:
            self._logger.error(f"Failed to write AI response to Markdown: {export_error}")
            return False

    def export_providers(self, file_name: Union[str, Path]) -> Optional[int]:
        """
        Export the AI providers dictionary to a JSON file.
        """
        if not self._enabled:
            raise RuntimeError("AI bridge is misconfigured; export aborted.")

        try:
            self._providers.to_json(file_name)
            return 0  # Shell standard success status
        except Exception as e:
            raise RuntimeError(f"Failed to export AI providers: {e}")

    def import_providers(self, file_name: Union[str, Path]) -> Optional[int]:
        """
        Import AI providers from a user-provided JSON file.
        """
        if not self._enabled:
            raise RuntimeError("AI bridge is misconfigured; import aborted.")

        try:
            if not self._load_providers(joker=file_name):
                raise RuntimeError(f"Import failed: invalid or incompatible file: {file_name}")
            return 0  # Shell standard success status
        except Exception as e:
            raise RuntimeError(f"Failed to import AI providers: {e}")

    async def query(self, prompt: str, context: Optional[str] = None, max_tokens: Optional[int] = None,
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

        request_timeout = AUTO_FORGE_AI_DEFAULT_REQ_TIMEOUT

        def _format_ai_error(_response_text: str) -> str:
            """Parse OpenAI-style error JSON and return a compact one-line error message."""
            try:
                _data = json.loads(_response_text)
                if isinstance(_data, dict) and "error" in _data:
                    err = _data["error"]
                    code = err.get("code", "UNKNOWN")
                    msg = err.get("message", "No message").replace("\n", " ").strip()
                    err_type = err.get("type", "")
                    param = err.get("param", "")
                    return f"[AI Error] type={err_type} code={code} param={param} msg='{msg}'"
            except json.JSONDecodeError:
                pass  # Fall through

            return _response_text.strip()

        try:
            if not self._enabled or self._provider is None:
                raise RuntimeError(f"AI bridge was misconfigured, cannot execute query")

            # Use default request context when not specified
            request_context = context or ("You are a helpful and technically skilled assistant "
                                          "supporting software developers in their daily work.")

            # Prefer external timeout value over the provider default
            request_timeout = self._provider.request_time_out if timeout is None else timeout

            # Prepare the request
            self._prepare_request(prompt=prompt, request_context=request_context, max_tokens=max_tokens,
                                  temperature=temperature)

            # Parse host-name from endpoint
            parsed = urlparse(self._provider.endpoint)
            hostname = parsed.hostname

            if not self._provider.proxy_allowed and hostname:
                with self._disable_proxy_for_host(hostname):
                    async with httpx.AsyncClient(timeout=request_timeout) as client:
                        response = await client.post(self._provider.endpoint, headers=self._headers, json=self._payload)
            else:
                async with httpx.AsyncClient(timeout=request_timeout, proxy=self._proxy_config) as client:
                    response = await client.post(self._provider.endpoint, headers=self._headers, json=self._payload)

            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()

        except TimeoutException as e:
            raise RuntimeError(f"Request timed out after {request_timeout} seconds: {e}")
        except HTTPStatusError as e:
            raise RuntimeError(f"HTTP error {e.response.status_code}: {_format_ai_error(e.response.text)}")
        except RequestError as e:
            raise RuntimeError(f"Network error: {e}")
        except (KeyError, IndexError):
            raise RuntimeError("Unexpected response format")
        except Exception as exception:
            raise RuntimeError(f"Unexpected exception: {exception}")
