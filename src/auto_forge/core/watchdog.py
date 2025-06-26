"""
Script:         watchdog.py
Author:         AutoForge Team

Description:
    Provides a singleton Watchdog class that runs in a background thread and forcefully terminates the process if
    not periodically refreshed. Useful for detecting application stalls or hangs, especially in GUI or long-running
    environments like WSL where process responsiveness may degrade silently.
"""

import os
import signal
import sys
import threading
import time
from contextlib import suppress
from typing import Optional, Any

# AutoForge essential imports
from auto_forge import (
    AutoForgeModuleType, CoreModuleInterface, CoreTelemetry, CoreRegistry)

AUTO_FORGE_MODULE_NAME = "WatchDog"
AUTO_FORGE_MODULE_DESCRIPTION = "Package Watchdog thread manager"
AUTO_FORGE_WATCHDOG_DEFAULT_TIMEOUT = 10.0


# ------------------------------------------------------------------------------
#
# Note:
#   This module is used during early initialization and must remain self-contained.
#   Avoid importing any project-specific code or third-party libraries to ensure
#   portability and prevent circular import issues.
#
# ------------------------------------------------------------------------------


class CoreWatchdog(CoreModuleInterface):
    """
    A core watchdog module that monitors application responsiveness and
    forcefully terminates the process if it is not refreshed within a specified timeout.
    Note:
        Termination is performed via SIGKILL and cannot be caught or bypassed.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """

        self._timeout: float = 0
        self._last_refresh: float = time.time()
        self._active: bool = False

        super().__init__(*args, **kwargs)

    def _initialize(self, default_timeout: Optional[float] = None) -> None:
        """
        Initializes the CoreWatchdog class.
        Args:
            default_timeout (int): Timeout in seconds before the watchdog triggers.
        """
        self._lock = threading.Lock()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()
        self._keyboard_listener: Optional[Any] = None
        self._default_timeout = default_timeout if default_timeout is not None else AUTO_FORGE_WATCHDOG_DEFAULT_TIMEOUT
        self._trigger = threading.Event()
        self._thread_running: bool = False
        self._thread: threading.Thread = threading.Thread(target=self._watch, daemon=True, name="WatchDog")

        # Dependencies check
        if self._telemetry is None:
            raise RuntimeError("failed to instantiate critical dependencies")

        # Start the inner thread
        self._thread.start()
        while not self._thread_running:
            time.sleep(0.05)  # Wait for thread readiness

        # Register this module with the package registry
        registry = CoreRegistry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        with suppress(Exception):
            self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

    def _watch(self):
        """ Watchdog inner thread """
        self._thread_running = True
        while True:
            self._trigger.wait(timeout=self._timeout if self._active else None)
            if not self._active:
                time.sleep(0.1)
                continue

            if time.time() - self._last_refresh > self._timeout:
                self._terminate_process()

            # Reset the event to sleep again unless `refresh()` pokes it
            self._trigger.clear()

    def _terminate_process(self):
        """
        Attempts graceful termination using sys.exit().
        Falls back to SIGKILL if the process does not exit promptly.
        """

        sys.stderr.write(
            f"\n\nCritical: AutoForge became unresponsive after {self._timeout} seconds and will be terminated.\n"
        )
        sys.stderr.flush()

        try:
            sys.exit(1)
        except SystemExit:
            # Give a moment for graceful exit
            time.sleep(0.5)

        # If still running, forcefully kill
        sys.stderr.write("Error: Graceful termination failed, forcing SIGKILL.\n\n")
        sys.stderr.flush()
        os.kill(os.getpid(), signal.SIGKILL)

    def start(self, timeout: float = None):
        """
        Activates the watchdog. If already running, this has no effect.
        Args:
            timeout (int, optional): If provided, overrides the current default timeout (in seconds).
        """
        if not self._active:
            if timeout is not None:
                self._timeout = timeout
            else:
                self._timeout = self._default_timeout

            self._last_refresh = time.time()
            self._trigger.set()
            self._active = True

    def refresh(self):
        """
        Resets the watchdog timer. Must be called periodically to prevent timeout.
        """
        if self._active:
            self._last_refresh = time.time()
            self._trigger.set()

    def stop(self):
        """
        Deactivates the watchdog. Should be called on normal application exit.
        """
        if self._active:
            self._active = False
            self._trigger.set()  # Wake the thread in case it's sleeping on a short timeout
