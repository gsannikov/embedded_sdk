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
import contextlib
import multiprocessing
import os
import signal
import socket
import sys
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
        self._proxy_timeout: Optional[int] = None
        self._proxy_auto_update: Optional[bool] = None
        self._console = Console(force_terminal=True, file=sys.__stdout__, legacy_windows=False,
                                color_system="truecolor", soft_wrap=False)

        self._system_info_data = self.sdk.system_info.get_data
        self._configured: bool = False

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=True)

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
            self._proxy_timeout = self._configuration.get("proxy_timeout", 10)
            self._proxy_auto_update = self._configuration.get("proxy_auto_update")

        if not _is_list_of_dictionaries(self._proxy_servers, self._proxy_dummy_files) or \
                None in (self._proxy_timeout, self._proxy_auto_update):
            raise RuntimeError("failed to retrieve essential data from configuration")

        self._configured = True
        return True

    @staticmethod
    def _test_proxy_process(proxy_cfg, test_files, timeout, progress):
        """
        Runs in a separate process. Attempts to download 1MB through proxy.
        Updates shared progress dict for real-time status.
        """

        # Ignore SIGINT (Ctrl+C) in child process
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        # Redirect stdout/stderr to avoid extra clutter
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

        key = f"{proxy_cfg['host']}:{proxy_cfg['port']}"
        progress[key] = {"status": "Starting...", "speed": None, "total": None}

        proxy_url = f"http://{proxy_cfg['host']}:{proxy_cfg['port']}"
        headers = {
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "close"
        }

        timeout_cfg = httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout, pool=timeout)
        total_speed = 0.0
        passed = 0
        last_url = ""

        try:
            socket.setdefaulttimeout(timeout)
            with httpx.Client(proxy=proxy_url, timeout=timeout_cfg) as client:
                for resource in test_files:
                    url = f"{resource['url']}?nocache={uuid.uuid4().hex}"
                    last_url = url
                    downloaded = 0
                    start = time.perf_counter()
                    chunk_start = start

                    with client.stream("GET", url, headers=headers) as r:
                        # Mark as receiving after the stream is open
                        progress[key] = {
                            **progress.get(key, {}),
                            "status": "[cyan]Receiving...[/]"
                        }
                        for chunk in r.iter_bytes():
                            now = time.perf_counter()
                            if now - chunk_start > timeout:
                                raise TimeoutError(f"Stalled: no data received in {timeout}s")
                            chunk_start = now

                            downloaded += len(chunk)
                            progress[key] = {
                                **progress.get(key, {}),
                                "total": round(downloaded / 1024, 1)
                            }
                            if downloaded >= 1024 * 1024:
                                break

                    if time.perf_counter() - start > timeout:
                        raise TimeoutError(f"Total download time exceeded {timeout}s")

                    elapsed = time.perf_counter() - start
                    if elapsed > 0 and downloaded >= 1024 * 1024:
                        speed = downloaded / elapsed / 1024
                        total_speed += speed
                        passed += 1
                        progress[key] = {
                            **progress.get(key, {}),
                            "speed": round(total_speed / passed, 1),
                            "status": f"{passed}/{len(test_files)}"
                        }

            progress[key] = {
                **progress.get(key, {}),
                "status": "[green]Done" if passed else "[red]Failed",
                "result": {
                    "proxy": proxy_cfg,
                    "status": "Done" if passed else "Failed",
                    "success": passed,
                    "avg_speed_kbps": round(total_speed / passed, 2) if passed else 0.0,
                    "url": last_url,
                }
            }

        except TimeoutError:
            progress[key] = {
                **progress.get(key, {}),
                "status": "[red]Timeout",
                "result": {
                    "proxy": proxy_cfg,
                    "status": "Timeout",
                    "success": 0,
                    "avg_speed_kbps": 0.0,
                    "url": last_url,
                }
            }

        except Exception as e:
            progress[key] = {
                **progress.get(key, {}),
                "status": f"[red]Failed: {type(e).__name__}",
                "result": {
                    "proxy": proxy_cfg,
                    "status": f"Failed: {type(e).__name__}",
                    "success": 0,
                    "avg_speed_kbps": 0.0,
                    "url": last_url,
                }
            }

    @staticmethod
    def _handle_best_proxy(results: Any):
        """
        Identifies and prints the best-performing proxy from the results list.
        Args:
            results (Any): Expected to be a list of dictionaries, each representing
                           a proxy test result with 'proxy' and 'avg_speed_kbps' keys.
        Returns:
            None. Prints the best proxy details or a warning if input is invalid.
        """
        if not isinstance(results, list):
            print("Invalid result: not a list.")
            return

        # Filter valid proxy entries containing required fields
        valid_entries = [
            r for r in results
            if isinstance(r, dict)
               and 'proxy' in r
               and isinstance(r['proxy'], dict)
               and 'host' in r['proxy']
               and 'port' in r['proxy']
               and 'avg_speed_kbps' in r
        ]

        if not valid_entries:
            print("No valid proxy entries found.")
            return

        # Pick proxy with the highest avg_speed_kbps
        best = max(valid_entries, key=lambda r: r['avg_speed_kbps'])
        host = best['proxy']['host']
        port = best['proxy']['port']
        speed = best['avg_speed_kbps']
        print(f"\n🏆 Best Proxy: {host}:{port} ({speed:.2f} kbps)\n")

    def rank_proxies(self, quiet: bool = False) -> list[dict]:
        """
        Benchmarks all configured proxy servers using real multiprocessing.
        Each proxy test runs in its own process, with hard timeout enforcement.
        """
        manager = multiprocessing.Manager()
        progress = manager.dict()

        def _render_progress():
            if quiet:
                return None

            table = Table(title=f"Proxy Test Results  •  Timeout = {self._proxy_timeout}s", expand=False)
            table.add_column("Proxy", style="bold", width=30, no_wrap=True)
            table.add_column("Status", width=20)
            table.add_column("Speed (KB/s)", justify="right", width=14)
            table.add_column("Total (KB)", justify="right", width=12)

            for _proxy in self._proxy_servers:
                _key = f"{_proxy['host']}:{_proxy['port']}"
                entry = progress.get(_key, {})
                status = entry.get("status", "Waiting")
                speed = f"{entry.get('speed', 0):.1f}" if entry.get("speed") else "-"
                total = f"{entry.get('total', 0):.1f}" if entry.get("total") else "-"
                table.add_row(_key, status, speed, total)

            return table

        # Launch processes and track their start times
        procs = []
        start_times = {}

        try:
            for proxy in self._proxy_servers:
                p = multiprocessing.Process(
                    target=type(self)._test_proxy_process,
                    args=(proxy, self._proxy_dummy_files, self._proxy_timeout, progress)
                )
                key = f"{proxy['host']}:{proxy['port']}"
                procs.append((proxy, p))
                start_times[key] = time.time()
                p.start()

            if quiet:
                for proxy, p in procs:
                    key = f"{proxy['host']}:{proxy['port']}"
                    deadline = start_times[key] + self._proxy_timeout + 2

                    while time.time() < deadline:
                        if not p.is_alive():
                            break
                        time.sleep(0.1)

                    if p.is_alive():
                        p.terminate()
                        progress[key] = {
                            "status": "[red]Hard timeout",
                            "speed": None,
                            "total": None,
                            "result": {
                                "proxy": proxy,
                                "status": "Hard timeout",
                                "success": 0,
                                "avg_speed_kbps": 0.0,
                                "url": "",
                            }
                        }
            else:
                self._console.print("\n")
                with contextlib.redirect_stdout(sys.__stdout__), contextlib.redirect_stderr(sys.__stderr__):
                    with Live(_render_progress(), console=self._console, refresh_per_second=4) as live:
                        done = set()
                        while len(done) < len(procs):
                            for proxy, p in procs:
                                key = f"{proxy['host']}:{proxy['port']}"
                                if key in done:
                                    continue
                                if not p.is_alive():
                                    done.add(key)
                                elif time.time() - start_times[key] > self._proxy_timeout + 2:
                                    p.terminate()
                                    progress[key] = {
                                        "status": "[red]Hard timeout",
                                        "speed": None,
                                        "total": None,
                                        "result": {
                                            "proxy": proxy,
                                            "status": "Hard timeout",
                                            "success": 0,
                                            "avg_speed_kbps": 0.0,
                                            "url": "",
                                        }
                                    }
                                    done.add(key)
                            live.update(_render_progress())
                            time.sleep(0.25)

            results = [progress[f"{p['host']}:{p['port']}"]["result"] for p in self._proxy_servers]

        except KeyboardInterrupt:
            self._console.print("\n[bold red]Interrupted! Terminating all proxy tests...[/]")
            for _, p in procs:
                if p.is_alive():
                    p.terminate()
            raise  # Optional: re-raise to exit or propagate upward

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
            results = self.rank_proxies()
            self._handle_best_proxy(results)
            return 0

        return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
