"""
Script:         watchdog.py
Author:         AutoForge Team

Description:
    Provides a singleton Watchdog class that runs in a background thread and forcefully terminates the process if
    not periodically refreshed. Useful for detecting application stalls or hangs, especially in GUI or long-running
    environments like WSL where process responsiveness may degrade silently.

Warning:
    This module is designed to be fully self-contained and must **not** import or depend on any other project-specific
    modules, classes, or third-party packages. It must rely strictly on Python's built-in standard library to ensure
    reliability, portability, and minimal failure risk in early-stage or low-level execution contexts.
"""

import os
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
from contextlib import suppress
from typing import Optional

AUTO_FORGE_MODULE_NAME = "WatchDog"
AUTO_FORGE_MODULE_DESCRIPTION = "Watchdog"
AUTO_FORGE_WATCHDOG_DEFAULT_TIMEOUTD = 10.0


class Watchdog:
    """
    A singleton watchdog timer that monitors application responsiveness and
    forcefully terminates the process if it is not refreshed within a specified timeout.

    Usage:
        Watchdog(timeout=10).start()   # Start watchdog (usually implicit)
        Watchdog().refresh()           # Refresh periodically (e.g., in main loop or timer)
        Watchdog().stop()              # Stop on clean exit
    Parameters:
        default_timeout (int): Timeout in seconds before the watchdog triggers.
        auto_start (bool): If True, starts the watchdog automatically on instantiation.
    Note:
        Termination is performed via SIGKILL and cannot be caught or bypassed.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, default_timeout: Optional[float] = None, auto_start: bool = True):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, default_timeout: Optional[float] = None, auto_start: bool = True):
        """" Singleton style implementation """
        if self._initialized:
            return

        self._timeout: float = 0
        self._default_timeout = default_timeout if default_timeout is not None else AUTO_FORGE_WATCHDOG_DEFAULT_TIMEOUTD
        self._auto_start: bool = auto_start
        self._last_refresh: float = time.time()
        self._reset_terminal_on_termination: bool = True
        self._active: bool = False
        self._thread_running: bool = False
        self._thread: threading.Thread = threading.Thread(target=self._watch, daemon=True, name="Watchdog")

        # Start the inner thread
        self._thread.start()
        while not self._thread_running:
            time.sleep(0.05)  # Wait for thread readiness

        # Start monitoring
        if self._auto_start:
            self.start()

        self._initialized = True

    def _watch(self):
        self._thread_running = True
        while True:
            if self._active:
                if time.time() - self._last_refresh > self._timeout:
                    self._terminate_process()
            else:
                self._last_refresh = 0
                self._timeout = 0

            time.sleep(0.5)

    def _terminate_process(self):
        """
        Attempts graceful termination using sys.exit().
        Falls back to SIGKILL if the process does not exit promptly.
        """

        if self._reset_terminal_on_termination:
            self.reset_terminal()
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
            self._active = True

    def refresh(self):
        """
        Resets the watchdog timer. Must be called periodically to prevent timeout.
        """
        if self._active:
            self._last_refresh = time.time()

    def stop(self):
        """
        Deactivates the watchdog. Should be called on normal application exit.
        """
        if self._active:
            self._active = False

    @staticmethod
    def reset_terminal(use_shell: bool = True):
        """
        Restores terminal to a sane state using term-ios.
        Equivalent to 'stty sane', but avoids shell calls.
        """
        if use_shell:
            subprocess.run(["stty", "sane"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        fd = sys.stdin.fileno()
        with suppress(Exception):
            tty.setcbreak(fd)  # minimal reset (line buffering on, echo preserved)
            attrs = termios.tcgetattr(fd)
            attrs[3] |= termios.ECHO | termios.ICANON  # enable echo and canonical mode
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)

        print("\033[?1049l", end="", flush=True)  # Exit alt screens (for ex. 'nano')
        print("\033[3J\033[H\033[2J", end="", flush=True)
