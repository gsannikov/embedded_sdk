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
from typing import Optional

# Third‑party
from aiohttp import web

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreBuildShell, CoreLogger, CoreModuleInterface,
                        CoreTelemetry, CoreRegistry)

AUTO_FORGE_MODULE_NAME = "MCP"
AUTO_FORGE_MODULE_DESCRIPTION = "MCP (Model Context Protocol) integration for AutoForge"


@dataclass
class _CoreMCPConfigType:
    """ Configuration for the MCP server connection. """
    host: Optional[str] = None
    port: int = 6274
    readonly: bool = False


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

        # Allow to override default port with package configuration
        self._mcp_config.port = self._configuration.get("mcp_port", self._mcp_config.port)

        self._commands_data = self._build_shell.commands_metadata
        self._app = web.Application()

        # Register all tool routes derived from commands metadata
        self._register_all_commands()

        # Manual endpoints
        self._app.router.add_get("/status", self.status_handler)
        self._app.router.add_get("/tool/list", self.list_tools_handler)
        self._app.router.add_get("/tool/version", self.version_handler)
        self._app.router.add_get("/tool/{name}", self.tool_meta_handler)
        self._app.router.add_post("/shutdown", self.shutdown_handler)

        CoreRegistry.get_instance().register_module(
            name="MCP",
            description=AUTO_FORGE_MODULE_DESCRIPTION,
            auto_forge_module_type=AutoForgeModuleType.CORE
        )
        self._telemetry.mark_module_boot(module_name="MCP")

    async def version_handler(self, _request: web.Request) -> web.Response:
        """
        Return the AutoForge version in a JSON payload.

        Returns:
            200 OK with {"version": "<version-string>"}.
        """
        return self._json_response({"version": str(self.auto_forge.version)})

    async def list_tools_handler(self, _request: web.Request) -> web.Response:
        """
        Enumerate registered tool endpoints.
        Scans the router for POST routes under `/tool/{self._tool_prefix}*`
        (e.g., `/tool/af.cmd.build`) and returns their path, canonical name,
        and description (if available) from `self._commands_data`.

        Returns:
            200 OK with JSON: {"tools": [{path, name, description}, ...]}
        """
        result = []
        prefix = "/tool/" + self._tool_prefix  # usually "/tool/af.cmd."

        for route in self._app.router.routes():
            # Only list POST tool routes
            method = getattr(route, "method", None)
            if method != "POST":
                continue

            # Extract canonical path; fall back safely if attribute not present
            path = getattr(getattr(route, "resource", None), "canonical", None)
            if not isinstance(path, str):
                info = getattr(getattr(route, "resource", None), "get_info", lambda: {})()
                path = info.get("path") or ""
            if not path.startswith(prefix):
                continue

            # Extract the command key and metadata
            cmd_key = path[len("/tool/") + len(self._tool_prefix):]
            entry = self._commands_data.get(cmd_key, {})
            result.append({
                "path": path,
                "name": f"{self._tool_prefix}{cmd_key}",
                "description": entry.get("description", "(no description)")
            })

        return self._json_response({"tools": result})

    async def status_handler(self, _request):
        """Basic runtime status (no secrets)."""
        return self._json_response({
            "host": self._mcp_config.host,
            "port": self._mcp_config.port,
            "readonly": bool(self._mcp_config.readonly),
            "tool_count": sum(
                1 for k, v in self._commands_data.items() if not v.get("hidden")
            )
        })

    async def tool_meta_handler(self, request):
        """Metadata for a single tool."""
        name = request.match_info.get("name", "")
        entry = self._commands_data.get(name)
        if not entry:
            return self._json_response({"error": "unknown tool"}, status=404)
        return self._json_response({
            "name": f"{self._tool_prefix}{name}",
            "description": entry.get("description", "(no description)"),
            "hidden": bool(entry.get("hidden", False)),
            "command": entry.get("command"),
            "usage": entry.get("usage"),
            "examples": entry.get("examples"),
        })

    async def shutdown_handler(self, _request: web.Request) -> web.Response:
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

    @staticmethod
    def _json_response(data: dict) -> web.Response:
        """
        Create a consistent JSON response with indentation.
        Args:
            data (dict): The data to serialize and return as JSON.
        Returns:
            web.Response: A JSON response with pretty indentation.
        """
        return web.json_response(
            data,
            dumps=lambda x: json.dumps(x, indent=2) + "\n")

    def _register_all_commands(self):
        """
        Register all loaded commands as HTTP POST endpoints in the MCP SSE server.
        This method:
            1. Iterates through `self._commands_data` to find commands that should be
               exposed as HTTP endpoints (skips entries marked as `hidden`).
            2. Supports both single-command strings and lists of commands per entry.
            3. For each command set, constructs a handler bound to `/tool/{tool_name}`.
            4. Each handler:
                - Optionally reads a JSON payload with `"args"` (string).
                - Expands any environment variables in the command(s).
                - Executes each command line through the `CoreBuildShell` interface,
                  capturing logs and execution status.
                - Returns a JSON response with command results, or an error message
                  if execution fails..
        """

        def _run_one_cmdline(line: str):
            """Run a single command line and return status, logs, and a summary."""
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

            log_capture: list = self._core_logger.get_log_capture()
            status = self._build_shell.last_result

            return {
                "status": int(status) if isinstance(status, int) else 0,
                "logs": log_capture,
                "summary": f"Executed: {line}"
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
            route = f"/tool/{tool_name}"

            async def make_handler(_cmds, expand=os.path.expandvars):
                async def handler(request):
                    payload = {}
                    with contextlib.suppress(Exception):
                        payload = await request.json()

                    args = payload.get("args", "").strip()
                    lines = []
                    for cmd in _cmds:
                        if cmd.startswith("do_") and args:
                            cmd = f"{cmd} {args}"
                        lines.append(expand(cmd))

                    try:
                        results = [_run_one_cmdline(line) for line in lines]
                        return self._json_response({"results": results})
                    except Exception as e:
                        return self._json_response({"error": str(e)}, status=500)

                return handler

            # Bind the handler coroutine directly to the route
            self._app.router.add_post(route, asyncio.run(make_handler(cmds)))

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

        def _show_start_message():

            _ip_address: str = self._mcp_config.host
            _port: int = self._mcp_config.port

            print(f"\nAutoForge: MCP SSE server running on {_ip_address}:{_port}")
            print(
                "Note: MCP SSE mode is experimental.\n"
                "All the solution commands are accessible via tool routes in the form:\n"
                "    af.cmd.<command_name>\n"
                "You can test it with the following examples:\n"
                f"•  curl --noproxy {_ip_address} -X GET http://{_ip_address}:{_port}/tool/version\n"
                f"•  curl --noproxy {_ip_address} -X GET http://{_ip_address}:{_port}/tool/list\n"
                f"•  curl --noproxy localhost -X GET http://localhost:{_port}/tool/af.cmd.busd\n"
                "       assuming your solution defines a 'busd' command.\n"
                f"•  curl --noproxy localhost -X GET http://localhost:{_port}/shutdown\n\n")

        try:
            with contextlib.suppress(Exception):
                # Determine the primary external IP address (not localhost)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    # Google's public DNS — connection attempt is enough; no packets sent
                    s.connect(("8.8.8.8", 80))
                    self._mcp_config.host = s.getsockname()[0]

            if not isinstance(self._mcp_config.host, str):
                raise RuntimeError("can't determine local IP address")

            _show_start_message()
            asyncio.run(self._run_sse())
            return 0

        except Exception as e:
            print(f"MCP Error: {e}")
            return 1

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
            while True:
                # Wait until /shutdown is called
                await self._shutdown_event.wait()
        finally:
            await runner.cleanup()
