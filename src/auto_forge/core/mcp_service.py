"""
Script:         mcp_service.py
Author:         AutoForge Team

Description:
    Lightweight MCP adapter enabling AutoForge to operate as a Model Context Protocol (MCP) server.
"""

import asyncio
import contextlib
import io
import json
import os
import socket
from dataclasses import dataclass
from typing import Optional, Any, Callable, Awaitable

from aiohttp import ContentTypeError
# Third‑party
from aiohttp import web

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreBuildShell, CoreLogger, CoreModuleInterface,
                        CoreTelemetry, CoreRegistry)

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

    def _initialize(self) -> None:
        """
        Initialize MCP server state and register routes.

        - Loads configuration (host/port).
        - Prepares the aiohttp app and registers command/tool routes.
        - Adds health and tool-list endpoints.
        - Registers the module with telemetry/registry.
        """
        self._mcp_config = _CoreMCPConfigType()
        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger("MCP")
        self._build_shell = CoreBuildShell.get_instance()
        self._telemetry = CoreTelemetry.get_instance()
        self._configuration = self.auto_forge.get_instance().configuration
        self._tool_prefix = "af.cmd."
        self._shutdown_event = asyncio.Event()
        self._tools_registry: dict[str, _CoreMCPToolType] = {}

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

        CoreRegistry.get_instance().register_module(
            name="MCP",
            description=AUTO_FORGE_MODULE_DESCRIPTION,
            auto_forge_module_type=AutoForgeModuleType.CORE
        )
        self._telemetry.mark_module_boot(module_name="MCP")

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
        # --- Parse JSON body (strict) ---
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ContentTypeError, UnicodeDecodeError):
            return self._json_response({"error": "invalid json"}, status=400)

        if request.method != "POST":
            return self._json_response({"error": "method not allowed"}, status=405)

        async def _handle_one(m: dict[str, Any]) -> dict[str, Any] | None:
            """
            Handle a single JSON-RPC message. Returns a response dict,
            or None if the input was a notification (no 'id').
            """
            jid = m.get("id", None)
            is_notification = jid is None
            method = m.get("method")
            params = m.get("params") or {}

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
                        "serverInfo": {"name": "AutoForge MCP", "version": str(self.auto_forge.version)},
                        "capabilities": {"tools": True, "resources": False, "prompts": False},
                    }
                    await  self._broadcast({"jsonrpc": "2.0", "method": "server/ready", "params": info})
                    return ok(info)

                if method == "tools/list":
                    return ok(self._rpc_tools_list())

                if method == "tools/call":
                    result = await self._rpc_tools_call(params)
                    await self._broadcast({
                        "jsonrpc": "2.0",
                        "method": "tools/result",
                        "params": {"name": params.get("name"), "result": result}
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
                return web.Response(status=204)
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
        """

        # JSON Schema used by MCP tools: allow string OR array for args
        base_schema = {
            "type": "object",
            "properties": {
                "args": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}}
                    ],
                    "description": "Extra arguments appended to command lines."
                }
            },
            "additionalProperties": False
        }

        for key, entry in self._commands_data.items():
            if entry.get("hidden"):
                continue

            cmds = entry.get("command")
            if isinstance(cmds, str):
                cmds = [cmds]
            elif not isinstance(cmds, list):
                continue

            tool_name = f"{self._tool_prefix}{key}"
            description = entry.get("description") or f"Run '{key}' command(s)."

            # Freeze per-iteration values to avoid late binding in closures
            _cmds_tuple = tuple(cmds)

            async def _tool_handler(params: dict[str, Any], _cmds=_cmds_tuple) -> dict[str, Any]:
                # Normalize args
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
                input_schema=base_schema,
                handler=_tool_handler
            ))

            # Keep REST route for compatibility, but FIX the factory:
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

            route = f"/tool/{tool_name}"
            # NOTE: do NOT use asyncio.run() here; just pass the async function object
            self._app.router.add_post(route, make_handler())

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

    def start(self) -> int:
        """
        Start the MCP server in SSE (Server-Sent Events) mode.

        This method:
        1. Retrieves the configured host and port from `self._mcp_config`.
        2. Attempts to determine the system's primary external IPv4 address
           (non-loopback) by connecting to a known public IP (Google DNS at 8.8.8.8).
           This does not require actual network reachability — no data is sent.
        3. Prints the server's bind address and the detected external IP for reference.
        4. Runs the asynchronous SSE server loop until interrupted.

        Returns:
            int: 0 if the server started successfully, 1 if an exception occurred.
        """

        # noinspection SpellCheckingInspection
        def _show_start_message():
            _ip = self._mcp_config.host
            _port = self._mcp_config.port
            base = f"http://{_ip}:{_port}"

            print(f"\nAutoForge: MCP SSE server running on {_ip}:{_port}")
            print(
                "MCP endpoints:\n"
                f"• SSE stream:            GET  {base}/sse\n"
                f"• JSON-RPC message bus:  POST {base}/message\n"
                "\n"
                "Quick tests:\n"
                "1) Open the SSE stream (heartbeat every ~15s):\n"
                f"   curl -N --noproxy {_ip} {base}/sse\n"
                "\n"
                "2) Initialize (also emits 'server/ready' on SSE):\n"
                f"   curl --noproxy {_ip} -s -X POST {base}/message \\\n"
                "     -H 'Content-Type: application/json' \\\n"
                "     -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}'\n"
                "\n"
                "3) List tools:\n"
                f"   curl --noproxy {_ip} -s -X POST {base}/message \\\n"
                "     -H 'Content-Type: application/json' \\\n"
                "     -d '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}'\n"
                "\n"
                "4) Call a tool (example: af.cmd.call with --help):\n"
                f"   curl --noproxy {_ip} -s -X POST {base}/message \\\n"
                "     -H 'Content-Type: application/json' \\\n"
                "     -d '{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\","
                "\"params\":{\"name\":\"af.cmd.call\",\"arguments\":{\"args\":[\"--help\"]}}}'\n"
                "\n"
                "Optional ops:\n"
                f"• Liveness check:  GET  {base}/status\n"
                f"• Shutdown (dev):  POST {base}/shutdown\n"
            )

        try:
            with contextlib.suppress(Exception):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    self._mcp_config.host = s.getsockname()[0]
            if not isinstance(self._mcp_config.host, str):
                raise RuntimeError("can't determine local IP address")

            _show_start_message()

            try:
                asyncio.get_running_loop()
                # We're already in an event loop: schedule and return
                asyncio.create_task(self._run_sse())
            except RuntimeError:
                asyncio.run(self._run_sse())
            return 0
        except Exception as e:
            print(f"MCP Error: {e}")
            return 1
