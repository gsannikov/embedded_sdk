"""
Script:         mcp_service.py
Author:         AutoForge Team

Description:
    Lightweight MCP adapter that allows AutoForge to function as a
    Model Context Protocol (MCP) server, exposing its commands and
    features over the MCP SSE transport. Designed for quick integration
    with compatible clients (e.g., VS Code, MCP CLI) without requiring
    full AutoForge deployment, while maintaining clean startup, shutdown,
    and request handling.
"""

import asyncio
import contextlib
import io
import json
import os
import re
import signal
import socket
from dataclasses import dataclass
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Optional, Any, Callable, Awaitable, Union

from aiohttp import ContentTypeError
# Third?party
from aiohttp import web

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreBuildShell, CoreLogger, CoreModuleInterface,
                        CoreTelemetry, CoreRegistry, CoreVariables)

AUTO_FORGE_MODULE_NAME = "MCP"
AUTO_FORGE_MODULE_DESCRIPTION = "MCP (Model Context Protocol) integration for AutoForge"
AUTO_FORGE_MAX_BATCH_MCP_COMMANDS = 64


@dataclass
class _CoreMCPConfigType:
    """ Configuration for the MCP server connection. """
    host: Optional[str] = None
    port: int = 6274
    readonly: bool = False


@dataclass
class _CoreMCPToolType:
    """
    Represents a callable MCP (Model Context Protocol) tool.

    Attributes:
        name (str): Unique tool name as exposed to MCP clients.
        description (str): Short human-readable description of the tool's purpose.
        input_schema (dict[str, Any]): JSON Schema describing the tool's expected input
            parameters (used for validation and discovery in MCP clients).
        handler (Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]):
            Async or sync callable that executes the tool logic.
            Accepts a dictionary of parameters matching `input_schema` and returns
            a result dictionary (arbitrary structure, but JSON-serializable).

    Methods:
        call(params):
            Executes the tool handler with the given parameters.
            Supports both asynchronous and synchronous handlers.
    """
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]  # Params -> result (may be async)

    async def call(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Invoke the tool with the given parameters.
        Args:
            params (dict[str, Any]): Parameters to pass to the tool's handler.
                Must conform to the tool's `input_schema`.
        Returns:
            dict[str, Any]: The tool's result payload.
        Notes:
            - Supports both synchronous and asynchronous handler callables.
            - Caller is responsible for schema validation before invoking.
        """
        res = self.handler(params)
        if hasattr(res, "__await__"):  # async handler
            res = await res
        return res


class CoreMCPService(CoreModuleInterface):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _initialize(self, mcp_server_name: str = "MCP Server", tools_prefix: str = "af_",
                    patch_vscode_config: bool = False) -> None:
        """
        Initialize MCP server state and register routes.
        - Loads configuration (host/port).
        - Prepares the aiohttp app and registers command/tool routes.
        - Adds health and tool-list endpoints.
        - Registers the module with telemetry/registry.

        Args:
            patch_vscode_config (bool): Maintain VSCode 'mcp.json' file automatically.
            tools_prefix (str): Prefix added to all published tools.
        """
        self._mcp_config = _CoreMCPConfigType()
        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger("MCP")
        self._build_shell = CoreBuildShell.get_instance()
        self._telemetry = CoreTelemetry.get_instance()
        self._variables = CoreVariables.get_instance()
        self._configuration = self.auto_forge.get_instance().configuration
        self._shutdown_event = asyncio.Event()
        self._tools_registry: dict[str, _CoreMCPToolType] = {}
        self._mcp_server_name: str = mcp_server_name
        self._patch_vscode_config: bool = patch_vscode_config
        self._tool_prefix: str = tools_prefix

        # Allow to override default port with package configuration
        self._mcp_config.port = self._configuration.get("mcp_port", self._mcp_config.port)

        self._commands_data = self._build_shell.commands_metadata
        self._app = web.Application()

        # Register all tool routes derived from commands metadata
        self._register_all_commands()

        # Manual endpoints
        self._app.router.add_get("/status", self._status_handler)
        self._app.router.add_post("/shutdown", self._shutdown_handler)
        self._app.router.add_get("/sse", self._sse_handler)
        self._app.router.add_post("/message", self._rpc_handler)

        # HTTP (streamable) at base URL:
        self._app.router.add_post("/", self._rpc_handler)

        # SSE at base URL:
        self._app.router.add_get("/", self._sse_handler)

        CoreRegistry.get_instance().register_module(
            name="MCP",
            description=AUTO_FORGE_MODULE_DESCRIPTION,
            auto_forge_module_type=AutoForgeModuleType.CORE
        )
        self._telemetry.mark_module_boot(module_name="MCP")

    @staticmethod
    def _log_line(msg: str):
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            print(f"{ts} [info] {msg}", flush=True)
        except Exception as e:
            # Fall back to plain print so it never kills the server
            print(f"MCP Service log error: {e!r} | original message: {msg!r}", flush=True)

    async def _status_handler(self, _request):
        """Basic runtime status (no secrets)."""
        return self._json_response({
            "host": self._mcp_config.host,
            "port": self._mcp_config.port,
            "readonly": bool(self._mcp_config.readonly),
            "tool_count": sum(
                1 for k, v in self._commands_data.items() if not v.get("hidden")
            )
        })

    async def _shutdown_handler(self, _request: web.Request) -> web.Response:
        """
        Initiate a graceful shutdown of the MCP server.
        Returns:
            200 OK with {"status": "shutting_down"} when accepted.
            403 if server is in readonly mode.
        """
        if getattr(self._mcp_config, "readonly", False):
            return self._json_response({"error": "readonly mode"}, status=403)

        # Signal the run loop to exit; cleanup happens in _run_sse()'s finally block.
        self._shutdown_event.set()
        return self._json_response({"status": "shutting_down"})

    async def _broadcast(self, obj: dict[str, Any]) -> None:
        """
        Broadcast a JSON-serializable object to all connected SSE clients.
        Args:
            obj (dict[str, Any]): The message payload to send. Must be JSON-serializable.
        Behavior:
            - Encodes the object as compact JSON (no extra whitespace).
            - Frames the data per SSE spec: prefix with "data: " and terminate
              with a double newline.
            - Attempts to send to all active SSE clients; one failing client will
              not disrupt others (exceptions are suppressed).
            - If the client stream supports `.flush()`, it is called after writing.
            - Sending is asynchronous: writes are awaited (or scheduled with
              `asyncio.create_task`) so the server does not block.

        Notes:
            - This is a best-effort broadcast; slow or disconnected clients may
              miss events if they cannot keep up.
            - Assumes `self._sse_clients` is a set of `aiohttp.web.StreamResponse`
              instances that have been prepared by `_sse_handler()`.
        """
        if not hasattr(self, "_sse_clients"):
            return
        frame = (b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n\n")
        for ws in list(self._sse_clients):
            with contextlib.suppress(Exception):
                await ws.write(frame)
                if hasattr(ws, "flush"):
                    await ws.flush()

    async def _sse_handler(self, request: web.Request) -> web.StreamResponse:
        """
        Handle a Server-Sent Events (SSE) client connection.
        Behavior:
            - Prepares an SSE-compatible HTTP response with required headers.
            - Adds the connection to `self._sse_clients` for use by `_broadcast()`.
            - Sends an initial `: connected` comment to confirm the stream is active.
            - Periodically sends a `heartbeat` event every 15 seconds until shutdown.
            - Suppresses all exceptions from the write loop to avoid noisy disconnect errors.
            - Removes the connection from the active client set on exit.
        Args:
            request (web.Request): The aiohttp request object.
        Returns:
            web.StreamResponse: The prepared SSE response that will remain open
            until the client disconnects or the server shuts down.
        """
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await resp.prepare(request)

        if not hasattr(self, "_sse_clients"):
            self._sse_clients = set()
        self._sse_clients.add(resp)

        with contextlib.suppress(Exception):
            # Send initial connection comment
            await resp.write(b": connected\n\n")
            if hasattr(resp, "flush"):
                await resp.flush()

            # Periodic heartbeat
            while not self._shutdown_event.is_set():
                await asyncio.sleep(15)
                await resp.write(b"event: heartbeat\ndata: {}\n\n")
                if hasattr(resp, "flush"):
                    await resp.flush()

        self._sse_clients.discard(resp)
        return resp

    async def _rpc_handler(self, request: web.Request) -> web.Response:
        """
        JSON-RPC endpoint for MCP over HTTP POST.
        Supports:
          - Single requests and batches per JSON-RPC 2.0
          - Methods: initialize, tools/list, tools/call, ping
          - Notifications (requests without 'id'): no HTTP response item is emitted
            for those entries (but server may broadcast via SSE).

        Error behavior:
          - Returns 400 for invalid/malformed JSON bodies.
          - Uses JSON-RPC error envelopes for method and internal errors.
        """
        self._log_line("Received /message POST")

        # Parse JSON body (strict)
        try:
            payload = await request.json()
            # formatted_payload = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            # self._log_line(f"Payload:\n\n{formatted_payload}\n")
            self._log_line(f"RPC handler got payload")

        except (json.JSONDecodeError, ContentTypeError, UnicodeDecodeError):
            return self._json_response({"error": "invalid json"}, status=400)

        if request.method != "POST":
            return self._json_response({"error": "method not allowed"}, status=405)

        async def _handle_one(m: dict[str, Any]) -> Optional[dict[str, Any]]:
            """
            Handle a single JSON-RPC message. Returns a response dict,
            or None if the input was a notification (no 'id').
            """
            jid = m.get("id", None)
            is_notification = jid is None
            method = m.get("method")
            params = m.get("params") or {}

            self._log_line(f"Incoming method: {method}, id: {jid}")

            if is_notification:
                def ok(_result: Any) -> None:
                    return None

                def err(_code: int, _message: str) -> None:
                    return None
            else:
                def ok(_result: Any) -> dict[str, Any]:
                    return {"jsonrpc": "2.0", "id": jid, "result": _result}

                def err(code: int, message: str) -> dict[str, Any]:
                    return {"jsonrpc": "2.0", "id": jid, "error": {"code": code, "message": message}}

            if not isinstance(m, dict) or not isinstance(method, str):
                return err(-32600, "invalid request")
            if not isinstance(params, dict):  # <-- simplified
                return err(-32602, "invalid params")

            try:
                if method == "initialize":
                    info = {
                        "protocolVersion": "2024-07-01",
                        "serverInfo": {"name": {self._mcp_server_name}, "version": str(self.auto_forge.version)},
                        "capabilities": {"tools": True, "resources": False, "prompts": False},
                    }
                    await  self._broadcast({"jsonrpc": "2.0", "method": "server/ready", "params": info})
                    return ok(info)

                if method == "tools/list":
                    return ok(self._rpc_tools_list())

                if method == "tools/call":
                    tool_name = params.get("name", "<?>")
                    self._log_line(f"Calling tool: {tool_name} with: {params}")

                    result = await self._rpc_tools_call(params)

                    self._log_line(f"Tool '{tool_name}' completed, result keys: {list(result.keys())}")

                    await self._broadcast({
                        "jsonrpc": "2.0",
                        "method": "tools/result",
                        "params": {"name": tool_name, "result": result}
                    })

                    return ok(result)

                if method == "ping":
                    return ok({"pong": True})

                return err(-32601, f"unknown method: {method}")

            except KeyError as ke:
                return err(-32601, str(ke))
            except Exception as ex:
                await self._broadcast({
                    "jsonrpc": "2.0",
                    "method": "tools/error",
                    "params": {"error": str(ex)}
                })
                return err(-32000, "internal error")

        # Single vs batch
        if isinstance(payload, list):
            if len(payload) > AUTO_FORGE_MAX_BATCH_MCP_COMMANDS:
                return self._json_response(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch too large"}},
                    status=400,
                )
            replies = [r for r in [await _handle_one(m) for m in payload] if r is not None]
            return self._json_response(replies)
        else:
            reply = await _handle_one(payload)
            if reply is None:
                # VS Code compatibility: return 200 with empty JSON instead of 204
                return self._json_response({})
            return self._json_response(reply)

    def _add_tool(self, tool: _CoreMCPToolType):
        """
        Register a new MCP tool in the server's tool registry.
        Args:
            tool (_CoreMCPToolType): The tool instance to register. The `name`
                attribute is used as the registry key.
        Notes:
            - If a tool with the same name already exists, it will be overwritten.
            - Registered tools are discoverable via `tools/list` and callable via
              `tools/call` in the MCP JSON-RPC API.
        """
        self._tools_registry[tool.name] = tool

    @staticmethod
    def _json_response(data: Any, status: int = 200) -> web.Response:
        """
        Create a consistent JSON response with indentation.
        Args:
            data (dict): The data to serialize and return as JSON.
        Returns:
            web.Response: A JSON response with pretty indentation.
        """
        return web.json_response(
            data,
            status=status,
            dumps=lambda x: json.dumps(x, indent=2, ensure_ascii=False) + "\n",
        )

    async def _run_one_cmdline_async(self, line: str) -> dict[str, Any]:
        """Run a single command line and return status, logs, and a summary."""

        def _do_run() -> dict[str, Any]:
            runner = (
                    getattr(self._build_shell, "execute_cmdline", None)
                    or getattr(self._build_shell, "execute_line", None)
                    or getattr(self._build_shell, "onecmd", None)
            )
            if not runner:
                raise RuntimeError("No command runner found on CoreBuildShell.")

            self._core_logger.start_log_capture()
            command_response = io.StringIO()
            with contextlib.redirect_stdout(command_response):
                runner(line)

            log_capture: list[str] = self._core_logger.get_log_capture()
            status = self._build_shell.last_result
            return {
                "status": int(status) if isinstance(status, int) else 0,
                "logs": log_capture,
                "summary": f"Executed: {line}",
            }

        return await asyncio.to_thread(_do_run)

    def _register_all_commands(self) -> None:
        """
        Register all loaded commands both as MCP tools (for SSE JSON-RPC)
        and, optionally, as REST POST endpoints under /tool/<name>.

        Commands marked as hidden or of type 'NAVIGATE' are skipped.
        Tool names are prefixed and must match MCP naming rules [a-z0-9_-].
        """

        for key, entry in self._commands_data.items():
            if entry.get("hidden") or entry.get("cmd_type", "").upper() == "NAVIGATE":
                continue

            cmds = entry.get("command")
            if isinstance(cmds, str):
                cmds = [cmds]
            elif not isinstance(cmds, list):
                continue

            tool_name = f"{self._tool_prefix}{key}"
            if not re.fullmatch(r"[a-z0-9_-]+", tool_name):
                continue  # Skip tools with MCP-invalid names

            description = entry.get("description") or f"Run '{key}' command(s)."
            _cmds_tuple = tuple(cmds)

            # MCP-compatible JSON schema
            input_schema = {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional command-line arguments",
                    }
                },
                "required": [],  # Accepts empty args by default
                "additionalProperties": False
            }

            async def _tool_handler(params: dict[str, Any], _cmds=_cmds_tuple) -> dict[str, Any]:
                """
                Execute one or more registered command lines with optional arguments.
                """
                raw_args = params.get("args", "")
                if isinstance(raw_args, list):
                    extra = " ".join(str(a) for a in raw_args).strip()
                else:
                    extra = str(raw_args).strip()

                lines = []
                for raw in _cmds:
                    line = raw
                    if raw.startswith("do_") and extra:
                        line = f"{raw} {extra}"
                    line = os.path.expandvars(line)
                    lines.append(line)

                results = [await self._run_one_cmdline_async(line) for line in lines]
                return {"results": results}

            # Register as MCP tool
            self._add_tool(_CoreMCPToolType(
                name=tool_name,
                description=description,
                input_schema=input_schema,
                handler=_tool_handler
            ))

            # Legacy REST fallback
            def make_handler(_cmds=_cmds_tuple):
                async def handler(request):
                    payload = {}
                    with contextlib.suppress(Exception):
                        payload = await request.json()

                    raw_args = payload.get("args", "")
                    if isinstance(raw_args, list):
                        extra = " ".join(str(a) for a in raw_args).strip()
                    else:
                        extra = str(raw_args).strip()

                    lines = []
                    for raw in _cmds:
                        line = raw
                        if raw.startswith("do_") and extra:
                            line = f"{raw} {extra}"
                        line = os.path.expandvars(line)
                        lines.append(line)

                    try:
                        results = [await self._run_one_cmdline_async(line) for line in lines]
                        return self._json_response({"results": results})
                    except Exception as e:
                        return self._json_response({"error": str(e)}, status=500)

                return handler

            self._app.router.add_post(f"/tool/{tool_name}", make_handler())

    async def _rpc_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Invoke a registered MCP tool by name.
        Args:
            params (dict[str, Any]): JSON-RPC parameters with:
                - "name" (str): The registered tool's name.
                - "arguments" (dict): Arguments to pass to the tool's handler.
                  Must conform to the tool's `input_schema`.
        Returns:
            dict[str, Any]: The tool's result payload (JSON-serializable).
        """
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = self._tools_registry.get(name)
        if not tool:
            raise KeyError(f"unknown tool: {name}")
        # Optionally validate 'arguments' against tool.input_schema here.
        return await tool.handler(arguments)

    def _rpc_tools_list(self) -> dict[str, Any]:
        """
        List all registered MCP tools.
        Returns:
            dict[str, Any]: A dictionary with key "tools" containing a list of
            tool descriptors, where each descriptor includes:
                - "name" (str): Tool name.
                - "description" (str): Tool description.
                - "inputSchema" (dict): JSON Schema for the tool's input.
        """
        tools = [{
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema
        } for t in self._tools_registry.values()]

        return {"tools": tools}

    async def _run_sse(self):
        """
        Internal async loop that configures and starts the SSE server.

        - Uses `aiohttp.web.AppRunner` to attach `self._app` to an HTTP server.
        - Binds to the configured host and port.
        - Stays alive indefinitely, sleeping in 1-hour intervals until stopped.
        - Ensures cleanup of resources on shutdown.
        """
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self._mcp_config.host, self._mcp_config.port)
        await site.start()

        try:
            # Wait until /shutdown is called
            await self._shutdown_event.wait()
        finally:
            await runner.cleanup()

    def _remove_vscode_config(self,
                              base_path: Optional[Union[Path, str]],
                              host_ip: str,
                              host_port: int,
                              server_name: str = "autoforge") -> bool:
        """
        Quietly remove a server entry from an existing VS Code MCP config.
        Args:
            base_path (Union[Path, str], optional):
                Workspace base directory containing the .vscode folder.
                If None, use the solution workspace (PROJ_WORKSPACE).
            host_ip (str): Host IP address to match in the config.
            host_port (int): Host port to match in the config.
            server_name (str): Server key to remove (default: "autoforge").

        Returns:
            bool: True if the config was updated or nothing needed removal,
                  False if an error occurred.
        """
        if base_path is None:
            base_path = self._variables.get("PROJ_WORKSPACE")

        vscode_dir = Path(base_path).expanduser().resolve() / ".vscode"
        config_path = vscode_dir / "mcp.json"

        if not config_path.exists():
            return True  # nothing to remove

        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, OSError):
            with config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            url_to_remove = f"http://{host_ip}:{host_port}"
            servers = data.get("servers", {})

            if server_name in servers:
                if servers[server_name].get("url") == url_to_remove:
                    del servers[server_name]

            # Clean up if servers now empty
            if not servers:
                data.pop("servers", None)

            with contextlib.suppress(Exception):
                config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                return True

        return False

    def _generate_vscode_config(self,
                                base_path: Optional[Union[Path, str]],
                                host_ip: str,
                                host_port: int,
                                server_name: str = "autoforge",
                                overwrite_existing: bool = True,
                                create_parents: bool = False,
                                ensure_inputs: bool = True) -> bool:
        """
        Generate/merge a VS Code MCP config without clobbering existing servers.
        Args:
            base_path: Base directory where '.vscode/mcp.json' will be written.
                         If None, uses self._variables['PROJ_WORKSPACE'].
            host_ip: IP for the SSE server.
            host_port: Port for the SSE server.
            server_name: Key under "servers" to write/update (default: "autoforge").
            overwrite_existing: If True, overwrite the existing <server_name> entry
                                if present. If False, only add it if missing.
            create_parents: If True, create <dir>/.vscode if missing.
            ensure_inputs: If True, add a minimal "inputs" section when absent.

        Returns:
            bool: True if the config file was written/updated, False otherwise.
        """
        if base_path is None:
            base_path = self._variables.get("PROJ_WORKSPACE")
        base_dir = Path(base_path).expanduser().resolve()

        vscode_dir = base_dir / ".vscode"
        config_path = vscode_dir / "mcp.json"

        if create_parents:
            vscode_dir.mkdir(parents=True, exist_ok=True)

        data: Optional[dict] = None

        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (JSONDecodeError, UnicodeDecodeError):
                with contextlib.suppress(Exception):
                    config_path.rename(config_path.with_suffix(".json.bak"))
                data = {}

        if data is None:
            data = {}

        servers = data.setdefault("servers", {})
        new_entry = {"type": "sse", "url": f"http://{host_ip}:{host_port}"}

        if server_name not in servers or overwrite_existing or servers[server_name] != new_entry:
            servers[server_name] = new_entry
        else:
            return False  # no change needed

        if ensure_inputs and "inputs" not in data:
            data["inputs"] = [
                {"id": "args", "type": "promptString", "description": "Extra arguments"}
            ]

        with contextlib.suppress(Exception):
            config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True

        return False

    def start(self) -> int:
        """
        Start the MCP server in SSE (Server-Sent Events) mode.
        Attempts to determine the system's primary external IPv4 address
        (non-loopback) by connecting to a known public IP (Google DNS at 8.8.8.8).
        This does not require actual network reachability — no data is sent.
        Runs the asynchronous SSE server loop until interrupted.

        Returns:
            int: 0 if the server started successfully, 1 if an exception occurred.
        """

        def _handle_term_signal():
            # Attach signal handlers so Ctrl+C triggers shutdown cleanly
            print("\033]0;\007\nInterrupted by user, shutting down...")

            # Quietly remove the server entry from VScode 'mcp.json'
            if self._generate_vscode_config:
                self._remove_vscode_config(base_path=None, host_ip=self._mcp_config.host,
                                           host_port=self._mcp_config.port, server_name=self._mcp_server_name)
            # Self terminate aggressively since its faster and we dont really care about clean shutdown.
            os.kill(os.getpid(), signal.SIGKILL)

        def _show_start_message():
            # Greetings, usage and examples
            _ip = self._mcp_config.host
            _port = self._mcp_config.port
            base = f"http://{_ip}:{_port}"

            print("\033]0;AutoForge MCP Service\007\033[2J\033[3J\033[H", end="")  # Clear screen, set title
            print(f"\nAutoForge: MCP SSE server running on local host {_ip}:{_port}, name: {self._mcp_server_name}")
            print("Press Ctrl+C to stop the service.\n")
            print(
                "MCP Endpoints:\n"
                f"- SSE stream:            GET  {base}/sse\n"
                f"- JSON-RPC message bus:  POST {base}/message\n")

        try:
            # Determine local outward-facing IP
            with contextlib.suppress(Exception):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    self._mcp_config.host = s.getsockname()[0]

            if not isinstance(self._mcp_config.host, str):
                raise RuntimeError("Can't determine local IP address")

            # Create VSCode 'mcp.json' file in the solution workspace
            if self._generate_vscode_config:
                self._generate_vscode_config(base_path=None, host_ip=self._mcp_config.host,
                                             host_port=self._mcp_config.port, server_name=self._mcp_server_name,
                                             overwrite_existing=True, create_parents=True)

            _show_start_message()

            # Prepare asyncio loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Attach signal handlers so Ctrl+C triggers shutdown cleanly
            for sig in (signal.SIGINT, signal.SIGTERM):
                # noinspection PyTypeChecker
                loop.add_signal_handler(sig, _handle_term_signal)

            # Run the SSE server
            if loop.is_running():
                asyncio.create_task(self._run_sse())
            else:
                loop.run_until_complete(self._run_sse())

            return 0

        except Exception as e:
            print(f"MCP Error: {e}")
            return 1
