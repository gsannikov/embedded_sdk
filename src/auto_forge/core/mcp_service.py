"""
Script:         mcp_service.py
Author:         AutoForge Team

Description:
    Thin MCP adapter for AutoForge to function as standard Model Context Protocol Server.
"""

import asyncio
import contextlib
import io
import json
import os
from dataclasses import dataclass

# Third‑party (MCP 1.12.4)
from aiohttp import web

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreBuildShell, CoreLogger, CoreModuleInterface,
                        CoreTelemetry, CoreRegistry)

AUTO_FORGE_MODULE_NAME = "MCP"
AUTO_FORGE_MODULE_DESCRIPTION = "Make AutoForge play nice with MCP (Model Context Protocol Server)"


@dataclass
@dataclass
class _CoreMCPConfigType:
    host: str = "127.0.0.1"
    port: int = 6274
    readonly: bool = False


class CoreMCPService(CoreModuleInterface):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        self._mcp_config = _CoreMCPConfigType()
        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger("MCP")
        self._build_shell = CoreBuildShell.get_instance()
        self._telemetry = CoreTelemetry.get_instance()
        self._configuration = self.auto_forge.get_instance().configuration
        self._tool_prefix = "af.cmd."

        self._mcp_config.host = self._configuration.get("mcp_host", self._mcp_config.host)
        self._mcp_config.port = self._configuration.get("mcp_port", self._mcp_config.port)

        self._commands_data = self._build_shell.commands_metadata
        self._app = web.Application()
        self._register_all_commands()

        # Manual /tool/af_health endpoint
        async def af_health_handler(_request):
            return self._json_response({"text": "OK"})

        async def list_tools_handler(_request):
            result = []
            prefix = "/tool/" + self._tool_prefix  # usually "/tool/af.cmd."
            for route in self._app.router.routes():
                if route.method != "POST":
                    continue
                path = route.resource.canonical
                if path.startswith(prefix):
                    cmd_key = path[len("/tool/") + len(self._tool_prefix):]  # extract just the key
                    entry = self._commands_data.get(cmd_key, {})
                    result.append({
                        "path": path,
                        "name": f"{self._tool_prefix}{cmd_key}",
                        "description": entry.get("description", "(no description)")
                    })
            return self._json_response({"tools": result})

        self._app.router.add_post("/tool/af_health", af_health_handler)
        self._app.router.add_get("/tool/list", list_tools_handler)

        CoreRegistry.get_instance().register_module(
            name="MCP",
            description="Make AutoForge play nice with MCP (SSE)",
            auto_forge_module_type=AutoForgeModuleType.CORE
        )
        self._telemetry.mark_module_boot(module_name="MCP")

    @staticmethod
    def _json_response(data: dict) -> web.Response:
        """
        Create a consistent JSON response with indentation.
        Args:
            data (dict): The data to serialize and return as JSON.
        Returns:
            web.Response: A JSON response with pretty indentation.
        """
        return web.json_response(data, dumps=lambda x: json.dumps(x, indent=2))

    def _register_all_commands(self):

        def _run_one_cmdline(line: str):
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

            self._app.router.add_post(route, asyncio.run(make_handler(cmds)))

    def start(self) -> int:
        try:
            print(f"[MCP] SSE server running on {self._mcp_config.host}:{self._mcp_config.port}")
            asyncio.run(self._run_sse())
            return 0
        except Exception as e:
            print(f"[MCP] Error: {e}")
            return 1

    async def _run_sse(self):
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self._mcp_config.host, self._mcp_config.port)
        await site.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
