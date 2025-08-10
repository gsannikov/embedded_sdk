"""
Script:         mcp_service.py
Author:         AutoForge Team

Description:
    Thin MCP adapter for AutoForge to function as standard Model Context Protocol Server.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set

# Third‑party (MCP 1.12.4)
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreBuildShell, CoreLogger, CoreModuleInterface, CoreTelemetry,
                        CoreRegistry)

AUTO_FORGE_MODULE_NAME = "MCP"
AUTO_FORGE_MODULE_DESCRIPTION = "Make AutoForge play nice with MCP (Model Context Protocol Server)"


@dataclass
class _CoreMCPConfigType:
    """ Configuration for CoreMCP."""
    transport: str = "stdio"  # "stdio" | "sse"
    host: str = "127.0.0.1"  # used for SSE
    port: int = 6274  # used for SSE
    readonly: bool = False  # gate FS‑mutating tools
    workdir_root: Optional[Path] = None  # sandbox root for file access (resources)
    allow_tools: Optional[Set[str]] = None  # None = all registered tools


class CoreMCPService(CoreModuleInterface):
    """
       Thin MCP adapter around AutoForge core services.
       You pass in your native 'core' facade that implements the real work:
       - core.health_report() -> str or object with .as_text()
       - core.provision(workspace:str, solution:str, profile:str, vars:dict) -> obj
       - core.build(target:str, profile:str, flags:str) -> build_id:str
       - core.logs_tail(build_id:str, tail:int) -> str
       - core.analyze_log(build_id:str) -> str | dict
       - core.log_resource(workspace:str, build_id:str) -> bytes | file-like | mapping
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """

        self._cfg = _CoreMCPConfigType()

        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:

        self._mcp = FastMCP("autoforge")
        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry = CoreRegistry.get_instance()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()
        self._build_shell = CoreBuildShell.get_instance()

        # Dependencies check
        if None in (self._core_logger, self._logger, self._registry, self._telemetry, self._build_shell):
            raise RuntimeError("failed to instantiate critical dependencies")

        # Register this module with the package registry
        registry = CoreRegistry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

        # Register tools (define inside __init__ so they can close over self)
        @self._mcp.tool()
        def af_health() -> TextContent:
            # later: call your native health reporter
            return TextContent(text="OK")

    def start(self) -> None:
        # stdio by default
        if self._cfg.transport == "stdio":
            self._mcp.run()
        elif self._cfg.transport == "sse":
            # only if your FastMCP build exposes run_sse
            self._mcp.run_sse(host=self._cfg.host, port=self._cfg.port)  # type: ignore[attr-defined]
        else:
            raise ValueError(f"Unsupported transport: {self._cfg.transport}")
