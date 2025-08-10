"""
Script:         mcp_service.py
Author:         AutoForge Team

Description:
    Thin MCP adapter for AutoForge to function as standard Model Context Protocol Server.
"""

import asyncio
import inspect
import json
import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, Any, Callable, Union

from mcp.server import Server
# Third‑party (MCP 1.12.4)
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

# AutoForge imports
from auto_forge import (AutoForgeModuleType, AutoForgCommandType, CoreBuildShell, CoreLogger, CoreModuleInterface,
                        CoreTelemetry, CoreRegistry, MCPServerProtocol)

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


class _CoreMCPCommandBridge:
    """
    Auto-register AutoForge commands as MCP tools.

    Converts dynamically discovered AutoForge commands into MCP tool
    registrations, respecting visibility, read-only restrictions,
    and naming conventions.
    """

    def __init__(
            self,
            srv: Union[FastMCP, Server, MCPServerProtocol],
            commands_data: dict[str, dict[str, Any]],
            mutating_types: Optional[set[Union[str, AutoForgCommandType]]] = None,
            run_one: Optional[Callable[[str], Any]] = None,
            run_batch: Optional[Callable[[list[str]], Any]] = None,
            expand: Optional[Callable[[str], str]] = None,
            readonly: bool = False,
            allow_tools: Optional[set[str]] = None,
            include_hidden: bool = False,
            tool_prefix: str = "af.cmd.",
            args_policy: Optional[Callable[[str, dict[str, Any]], bool]] = None
    ) -> None:
        """Initialize the MCP command bridge.

        Args:
            srv: Instantiated MCP server object (e.g., FastMCP or Server).
            commands_data: Mapping of command keys to their metadata dictionaries.
            mutating_types: Set of AutoForgCommandType values considered mutating
                (subject to read-only restrictions). Defaults to BUILD, AUTOMATION, and GIT.
            run_one: Callback to execute a single command string. Required unless
                run_batch handles all execution.
            run_batch: Callback to execute a list of commands. Defaults to sequential
                execution via run_one.
            expand: Callback to expand variables in a single command string.
                Defaults to os.path.expandvars.
            readonly: If True, mutating commands are registered as inert/blocked tools.
            allow_tools: Optional whitelist of tool names to register. Others are skipped.
            include_hidden: If False, skips commands with 'hidden': True in metadata.
            tool_prefix: String prefix for registered MCP tool names.
            args_policy: Optional function (key, entry) -> bool deciding whether a tool
                should accept a free-form "args" parameter.
        """
        self._srv = srv
        self._commands_data: Optional[dict] = commands_data

        self._mutating_types = {
            AutoForgCommandType.from_str(mt) if isinstance(mt, str) else mt
            for mt in (mutating_types or set())
        }

        self._run_one = run_one
        self._run_batch = run_batch
        self._expand = expand or os.path.expandvars
        self._readonly = readonly
        self._allow_tools = allow_tools
        self._include_hidden = include_hidden
        self._tool_prefix = tool_prefix
        self._args_policy = args_policy

    def _default_run_batch(self, commands: list[str]) -> Any:
        """Run commands sequentially using run_one."""
        results = []
        for cmd in commands:
            results.append(self._run_one(cmd))
        return results

    def register_all(self) -> list[str]:
        """Register all commands as MCP tools according to settings.
        Returns:
            list[str]: Names of registered MCP tools.

        Raises:
            RuntimeError: If neither run_one nor run_batch is provided.
        """
        if not callable(self._run_one) and not callable(self._run_batch):
            raise RuntimeError("Either run_one or run_batch must be provided.")

        registered: list[str] = []
        run_batch = self._run_batch or self._default_run_batch

        for key, entry in self._commands_data.items():
            # Skip hidden commands unless explicitly allowed
            if not self._include_hidden and entry.get("hidden"):
                continue

            # Tool allowlist filter
            tool_name = f"{self._tool_prefix}{key}"
            if self._allow_tools and tool_name not in self._allow_tools:
                continue

            # Command type classification
            cmd_type = AutoForgCommandType.from_str(entry.get("cmd_type"))

            # Read-only handling
            if self._readonly and cmd_type in self._mutating_types:
                def blocked_handler(*_):
                    return "Read-only mode: command blocked"

                if hasattr(self._srv, "tool"):  # FastMCP
                    try:
                        self._srv.tool(tool_name, desc=entry.get("description", "") + " [READ-ONLY]")(blocked_handler)
                    except TypeError:
                        self._srv.tool()(blocked_handler)
                elif hasattr(self._srv, "add_tool"):  # Server style
                    self._srv.add_tool(
                        blocked_handler,
                        tool_name,
                        description=entry.get("description", "") + " [READ-ONLY]"
                    )
                registered.append(tool_name)
                continue

            # Determine commands list
            cmds = self._as_batch(entry.get("command"))
            if not cmds:
                continue

            # Register active tool
            self._register_active(
                tool_name,
                str(entry.get("description", "")),
                cmds,
                with_args=self._decide_args(key, entry),
                run_batch=run_batch
            )
            registered.append(tool_name)

        self._validate_init()
        return registered

    def _validate_init(self) -> None:
        """Validate that required bridge initialization parameters are set and usable."""
        # Server API check
        if not (hasattr(self._srv, "tool") or hasattr(self._srv, "add_tool")):
            raise RuntimeError(
                f"Invalid srv object: {type(self._srv).__name__} — missing tool/add_tool API"
            )

        # 'commands_data' must be dict
        if not isinstance(self._commands_data, dict):
            raise RuntimeError("commands_data is None — cannot register MCP tools.")

        # Check callable
        if not callable(self._run_one) and not callable(self._run_batch):
            raise RuntimeError("Either run_one or run_batch must be callable.")

        # Warn if empty
        if not self._commands_data:
            print("⚠ Warning: No commands found — MCP tool registry will be empty.")

        # Optional: test tool registration
        try:
            def _fake_tool():
                """test tool"""
                return "ok"

            if hasattr(self._srv, "tool"):
                try:
                    # FastMCP style: parameterless
                    self._srv.tool()(_fake_tool)
                except TypeError:
                    # Server style: name + description
                    self._srv.tool("af.test", description="test tool")(_fake_tool)

            elif hasattr(self._srv, "add_tool"):
                # Low-level add_tool API: func, name, description
                self._srv.add_tool(_fake_tool, "af.test", description="test tool")

        except Exception as e:
            raise RuntimeError(f"srv tool API test failed: {e}")

    def _register_blocked(self, tool_name: str, desc: str) -> None:
        """Register a disabled MCP tool that returns a read-only mode message."""

        def _blocked(**_kwargs) -> TextContent:
            return TextContent(text=f"Tool '{tool_name}' is disabled in read-only mode.")

        _blocked.__name__ = tool_name.replace(".", "_").replace("-", "_") + "_blocked"
        _blocked.__doc__ = f"{desc} (read-only blocked)"

        # Explicitly call as decorator — both styles still allowed
        decorator = self._srv.tool(tool_name, desc=desc)
        decorator(_blocked)

    def _register_active(self, tool_name: str, desc: str, cmds: list[str], with_args: bool,
                         run_batch: Optional[Callable[[list[str]], Any]] = None) -> None:
        """Register an active MCP tool bound to one or more AutoForge commands."""
        run_batch_fn = run_batch or self._run_batch or self._default_run_batch
        handler = self._make_handler(tool_name, desc, cmds, with_args, run_batch_fn)

        # FastMCP: decorator is parameterless; tool name = function name
        # Give the wrapper a sanitized function name and docstring (used as description)
        safe_func_name = tool_name.replace(".", "_").replace("-", "_")
        handler.__name__ = safe_func_name
        handler.__doc__ = desc

        # Try FastMCP style first
        try:
            deco = self._srv.tool()  # FastMCP
            deco(handler)
            return
        except TypeError:
            pass

        # Fallback: low-level Server style (if you ever swap srv to Server)
        try:
            deco = self._srv.tool(tool_name, desc=desc)
            deco(handler)
            return
        except TypeError as e:
            raise RuntimeError(f"Unsupported MCP server API for tool '{tool_name}': {e}")

    def _make_handler(
            self,
            tool_name: str,
            desc: str,
            cmds: list[str],
            with_args: bool,
            run_batch: Optional[Callable[[list[str]], Any]] = None
    ):
        """
        Register an active MCP tool bound to one or more AutoForge commands.
        Args:
            tool_name: Fully qualified MCP tool name to register.
            desc: Short description of the tool.
            cmds: List of command strings to execute when the tool is invoked.
            with_args: Whether to allow additional free-form arguments at runtime.
            run_batch: Optional callable to execute multiple commands in sequence;
                defaults to the bridge's batch runner if not provided.
        """
        expander = self._expand or os.path.expandvars
        run_batch_fn = run_batch or self._run_batch or self._default_run_batch

        def handler(**kwargs):
            extra = (kwargs.get("args") or "").strip()
            lines: list[str] = []
            for raw in cmds:
                line = raw
                if with_args and raw.startswith("do_") and extra:
                    line = f"{raw} {extra}"
                line = expander(line)
                lines.append(line)

            try:
                out = run_batch_fn(lines)
            except Exception as e:
                return TextContent(text=f"[ERROR] {e}")

            if out is None:
                return TextContent(text="OK")
            if isinstance(out, (list, tuple)):
                out = "\n".join(str(x) for x in out)
            return TextContent(text=str(out))

        handler.__name__ = tool_name.replace(".", "_")
        handler.__doc__ = desc
        handler.__signature__ = self._make_signature(with_args)
        return handler

    def _is_mutating(self, entry: dict[str, Any]) -> bool:
        """Return True if the command's type is in the mutating set."""
        ct = str(entry.get("cmd_type") or "").upper()
        return ct in self._mutating_types

    @staticmethod
    def _as_batch(cmd_field: Any) -> list[str]:
        """Normalize a command field into a list of strings."""
        if isinstance(cmd_field, list):
            return [str(c) for c in cmd_field if c is not None]
        if isinstance(cmd_field, str):
            return [cmd_field]
        return []

    def _decide_args(self, key: str, entry: dict[str, Any]) -> bool:
        """Determine if the tool should accept free-form arguments."""
        if self._args_policy:
            return bool(self._args_policy(key, entry))
        # Default heuristic: expose free-form args for function-like 'do_*' commands
        cmds = self._as_batch(entry.get("command"))
        return any(isinstance(c, str) and c.startswith("do_") for c in cmds)

    @staticmethod
    def _make_signature(with_args: bool) -> inspect.Signature:
        """Create a function signature for an MCP tool, optionally with 'args'."""
        params: list[inspect.Parameter] = []
        if with_args:
            params.append(
                inspect.Parameter(
                    name="args",
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default="",
                    annotation=str,
                )
            )
        return inspect.Signature(parameters=params, return_annotation=TextContent)

    @staticmethod
    def _sanitize_key(key: str) -> str:
        # Keep readable names; remove odd punctuation
        s = re.sub(r"[^a-zA-Z0-9_:-]+", "_", key).strip("_")
        return s or "unnamed"

    @staticmethod
    def _unique_tool_name(base: str, used: Set[str]) -> str:
        """Generate a unique tool name by appending a numeric suffix if needed."""
        name = base
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)
        return name


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
        self._tool_prefix:str = "af.cmd."

        # Dependencies check
        if None in (self._core_logger, self._logger, self._registry, self._telemetry, self._build_shell):
            raise RuntimeError("failed to instantiate critical dependencies")

        # Register this module with the package registry
        registry = CoreRegistry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)
        self._commands_data = self._build_shell.commands_metadata

        # A tiny adapter so we can execute a single AF command line
        def _run_one_cmdline(line: str):
            # Pick the runner your shell exposes; adjust if your API uses a different name
            runner = (getattr(self._build_shell, "execute_cmdline", None)
                      or getattr(self._build_shell, "execute_line", None)
                      or getattr(self._build_shell, "onecmd", None))
            if runner is None:
                raise RuntimeError("No command runner found on CoreBuildShell.")
            return runner(line)

        bridge = _CoreMCPCommandBridge(
            srv=self._mcp,
            commands_data=self._commands_data,
            mutating_types={"BUILD", "AUTOMATION", "GIT"},
            run_one=_run_one_cmdline,
            run_batch=None,  # Omit to use sequential default
            expand=getattr(self._build_shell, "expand_vars", None),  # None to use os.path.expandvars
            readonly=bool(getattr(self, "_readonly", False)),
            allow_tools=getattr(self, "_allow_tools", None),
            include_hidden=False,  # True if you want aliases too
            tool_prefix=self._tool_prefix,  # Keep consistent naming
        )

        self._registered_tools = bridge.register_all()

        # Register tools (define inside __init__ so they can close over self)
        @self._mcp.tool()
        def af_health() -> TextContent:
            # later: call your native health reporter
            return TextContent(text="OK")

    def _export_registered_tools(self, file_path: Optional[Union[Path, str]] = None) -> str:
        """Export registered MCP tools to a JSON file for later use.
        Args:
            file_path (Optional[Union[Path, str]]):
                Path to write the JSON file. If None, defaults to
                "mcp_registered_tools.json" in the current working directory.
        Returns:
            str: Absolute path to the written JSON file.
        """
        if not getattr(self, "_registered_tools", None):
            raise RuntimeError("No tools have been registered — nothing to export.")

        if not hasattr(self, "_commands_data"):
            raise RuntimeError("Missing commands_data — cannot export tool descriptions.")

        manifest = {"mcp_tools": []}

        for tool_name in self._registered_tools:
            # Strip tool_prefix to find original key in commands_data
            if hasattr(self, "_tool_prefix"):
                original_key = tool_name[len(self._tool_prefix):] if tool_name.startswith(
                    self._tool_prefix) else tool_name
            else:
                original_key = tool_name

            entry = self._commands_data.get(original_key, {})
            description = str(entry.get("description", ""))

            manifest["mcp_tools"].append({
                "name": tool_name,
                "description": description
            })

        out_path = Path(file_path or "mcp_registered_tools.json").resolve()
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        return str(out_path)

    def start(self) -> int:
        """
        Run the MCP server and block until shutdown.
            - Starts the server using the configured transport:
            * "stdio": runs in MCP stdio mode (stdin/stdout owned by MCP).
            * "sse":   runs an SSE HTTP server on `host:port` (if supported).
            - Blocks until the session ends (EOF) or a shutdown signal/interrupt occurs.
            - Normalizes shutdown to a process-style return code instead of raising.
        Returns:
            int: Exit code indicating how the server terminated:
                0   — clean shutdown (EOF / client closed) else error.
        """

        self._export_registered_tools("mcp_registered_tools.json")

        try:
            if self._cfg.transport == "stdio":
                self._mcp.run()  # blocks
            elif self._cfg.transport == "sse":
                self._mcp.run_sse(host=self._cfg.host, port=self._cfg.port)  # type: ignore[attr-defined]
            else:
                raise ValueError(f"Unsupported transport: {self._cfg.transport}")
            return 0
        except (KeyboardInterrupt, asyncio.CancelledError):
            # user interrupted / server cancelled – treat as normal shutdown
            return 130  # Sig init
        except SystemExit as e:
            return int(e.code) if isinstance(e.code, int) else 1
        except Exception as e:
            raise e
        finally:
            with suppress(Exception):
                if hasattr(self._mcp, "close"):
                    self._mcp.close()
