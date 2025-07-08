"""
Module: proxy_race_command.py
Author: AutoForge Team

Description:
    Experimental tool for conducting "proxy drag racing," where multiple proxy servers
    are tested simultaneously. The module collects performance statistics to help
    dynamically select the best available proxy during a session.
    
    Note: This command is currently inactive and may serve as a placeholder for future tools.
"""

import argparse
import asyncio
import time
import uuid
from contextlib import suppress
from typing import Any, Optional

import httpx
from rich.console import Console
from rich.live import Live
# Third-party
from rich.table import Table

# AutoForge imports
from auto_forge import (CommandInterface)

AUTO_FORGE_MODULE_NAME = "prox"
AUTO_FORGE_MODULE_DESCRIPTION = "Proxy Measurement Tool"


class ProxyRaceCommand(CommandInterface):
    """
    Implements a command to allow interacting with an AI.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the EditCommand class.
        Args:
            **_kwargs (Any): Optional keyword arguments, such as:
        """

        self._proxy_servers: Optional[list[dict]] = None
        self._proxy_dummy_files: Optional[list[dict]] = None
        self._proxy_timout: Optional[int] = None
        self._proxy_auto_update: Optional[bool] = None
        self._console = Console(force_terminal=True)
        self._system_info_data = self.sdk.system_info.get_data
        self._configured: bool = False

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME)

    def initialize(self, **_kwargs: Any) -> bool:
        """Performs late initialization after the abstract base class has completed its setup."""

        def _is_list_of_dictionaries(*args):
            """Returns True if all arguments are non-empty lists containing only dictionaries."""

            def is_valid(obj):
                return isinstance(obj, list) and len(obj) > 0 and all(isinstance(item, dict) for item in obj)

            return all(is_valid(arg) for arg in args)

        # Validate and populate the class with data retried from configuration
        with suppress(Exception):
            self._proxy_servers = self._configuration.get("proxy_servers")
            self._proxy_dummy_files = self._configuration.get("proxy_dummy_files")
            self._proxy_timout = self._configuration.get("proxy_timout")
            self._proxy_auto_update = self._configuration.get("proxy_auto_update")

        if not _is_list_of_dictionaries(self._proxy_servers, self._proxy_dummy_files) or \
                None in (self._proxy_timout, self._proxy_auto_update):
            raise RuntimeError("failed to retrieve essential data from configuration")

        self._configured = True
        return True

    async def rank_proxies(self) -> list[dict]:
        """
        Tests all configured proxy servers using dummy file downloads and ranks them by average speed.

        Returns:
            List[dict]: Sorted list of proxy test results, fastest first.
        """

        progress = {}  # Keyed by "host:port"

        def render_progress():
            table = Table(title="Proxy Benchmark Progress", expand=False)
            table.add_column("Proxy", style="bold")
            table.add_column("Status")
            table.add_column("Speed (KB/s)", justify="right")
            table.add_column("Total (KB)", justify="right")

            for key, info in progress.items():
                status = info.get("status", "waiting")
                speed = f"{info.get('speed', 0):.1f}" if info.get("speed") else "-"
                total = f"{info.get('total', 0):.1f}" if info.get("total") else "-"
                table.add_row(key, status, speed, total)

            return table

        async def test_proxy(proxy_cfg: dict, _test_files: list[dict], _timeout: int) -> dict:
            proxy_key = f"{proxy_cfg['host']}:{proxy_cfg['port']}"
            progress[proxy_key] = {"status": "starting...", "speed": None}

            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Connection": "close"
            }

            timeout = httpx.Timeout(_timeout, connect=_timeout, read=_timeout, write=_timeout, pool=_timeout)
            proxy_url = f"http://{proxy_cfg['host']}:{proxy_cfg['port']}"

            target_bytes = 1024 * 1024  # 1MB
            total_speed = 0.0
            passed = 0
            last_url = ""

            with suppress(Exception):
                async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
                    progress[proxy_key]["status"] = "testing"

                    for resource in _test_files:
                        url = f"{resource['url']}?nocache={uuid.uuid4().hex}"
                        last_url = url
                        if not url:
                            continue

                        try:
                            downloaded = 0
                            start = time.perf_counter()

                            async with client.stream("GET", url, headers=headers) as r:
                                async for chunk in r.aiter_bytes(1024 * 64):
                                    downloaded += len(chunk)
                                    progress[proxy_key]["total"] = (int(downloaded / 1024))
                                    if downloaded >= target_bytes:
                                        break

                            elapsed = time.perf_counter() - start
                            if elapsed > 0 and downloaded >= target_bytes:
                                speed = downloaded / elapsed / 1024  # KB/s
                                total_speed += speed
                                passed += 1
                                progress[proxy_key]["speed"] = round(total_speed / passed, 1)
                                progress[proxy_key]["status"] = f"{passed}/{len(_test_files)}"
                        except Exception as e:
                            progress[proxy_key]["status"] = f"failed: {e}"

            if passed == 0:
                progress[proxy_key]["status"] = "[red]failed"
            else:
                progress[proxy_key]["status"] = "[green]done"

            return {
                "proxy": proxy_cfg,
                "success": passed,
                "timeout": _timeout,
                "url": last_url,
                "avg_speed_kbps": round(total_speed / passed, 2) if passed > 0 else 0.0,
            }

        with Live(render_progress(), console=self._console, refresh_per_second=4) as live:
            tasks = [
                asyncio.create_task(test_proxy(proxy, self._proxy_dummy_files, self._proxy_timout))
                for proxy in self._proxy_servers
            ]

            while not all(t.done() for t in tasks):
                live.update(render_progress())
                await asyncio.sleep(0.25)

            results = await asyncio.gather(*tasks)

        results.sort(key=lambda x: (-x["avg_speed_kbps"], -x["success"]))
        return results

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("-r", "--race", action="store_true", help="Start Proxy racing")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:q
            args (argparse.Namespace): The parsed arguments.
        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """
        if args.race:
            results = asyncio.run(self.rank_proxies())
            print(results)
            return 0

        return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
