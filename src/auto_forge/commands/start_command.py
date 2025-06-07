"""
Module: start_command.py
Author: AutoForge Team

Description:
    Provides a cross-platform function to open the default file manager or associated application for a given path.
    This utility mimics the behavior of the 'start' command on Windows, 'open' on macOS, and 'xdg-open' on Linux,
    intelligently detecting the operating system and desktop environment,
    including support for Windows Subsystem for Linux (WSL).
"""

import argparse
import os
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Optional, Union, Any

# AutoForge imports
from auto_forge import (CLICommandInterface, )

AUTO_FORGE_MODULE_NAME = "start"
AUTO_FORGE_MODULE_DESCRIPTION = "Windows start command"
AUTO_FORGE_MODULE_VERSION = "1.0"


class StartCommand(CLICommandInterface):
    """
    Implements a command cross-platform command similar to Windows 'start'.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the StartCommand class.
        """

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, raise_exceptions=True, hidden=True)

    # noinspection SpellCheckingInspection
    @staticmethod
    def _start(path: Optional[Union[Path, str]] = None) -> None:
        """
        Opens the file manager at the specified path (or current directory if not specified).
        Args:
            path: The path to open in the file manager. Can be a string or a Path object.
                  If None, the current working directory will be opened.
        """
        if path is None:
            target_path = Path.cwd()
        else:
            target_path = Path(path).resolve()  # Resolve to an absolute path

        # --- Handle Windows ---
        if os.name == 'nt':
            try:
                # Check if running inside WSL
                if os.getenv("WSL_DISTRO_NAME"):
                    # For WSL, we need to convert the Linux path to a Windows path
                    # and then use explorer.exe. This relies on the 'wslpath' utility.
                    wsl_path_output = subprocess.run(
                        ["wslpath", "-w", str(target_path)],
                        capture_output=True, text=True, check=True
                    )
                    windows_path = wsl_path_output.stdout.strip()
                    subprocess.run(['explorer.exe', str(windows_path)], check=True)
                else:
                    # Regular Windows
                    subprocess.run(['explorer', str(target_path)], check=True)
                return
            except FileNotFoundError:
                raise RuntimeError("'explorer' command not found, this should not happen on Windows")
            except subprocess.CalledProcessError as process_error:
                raise RuntimeError(f"opening file manager on Windows: {process_error}")

        # Handle macOS
        elif os.uname().sysname == 'Darwin':
            try:
                subprocess.run(['open', str(target_path)], check=True)
                return
            except FileNotFoundError:
                raise RuntimeError("'explorer' command not found, this should not happen on macOS")
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"opening file manager on macOS: {e}")

        # Handle Linux/Unix-like systems
        elif os.name == 'posix':
            # List of common Linux file managers to try in order of preference/likelihood
            file_managers = [
                'xdg-open',  # Standard way to open files/folders on most Linux desktops
                'nautilus',  # GNOME
                'thunar',  # XFCE
                'dolphin',  # KDE Plasma
                'pcmanfm',  # LXDE/LXQt
                'nemo',  # Cinnamon
                'caja',  # MATE
                'konqueror',  # Older KDE
                'gvfs-open',  # Older GNOME/GTK
            ]

            # First, try xdg-open as it's the most universal for Linux desktop environments
            with suppress(Exception):
                subprocess.run(['xdg-open', str(target_path)], check=True)
                return

            # If xdg-open failed, try specific file managers
            for manager in file_managers[1:]:  # Skip xdg-open as we already tried it
                with suppress(Exception):
                    # Some file managers like nautilus prefer a URI-like path for folders
                    # However, for simplicity and broader compatibility, passing the raw path is often sufficient.
                    # If a manager consistently fails, you might need to adjust arguments.
                    subprocess.run([manager, str(target_path)], check=True)
                    return

                # Failed to open, trying next...

            raise RuntimeError(f"no suitable file manager found or able to open '{target_path}' on this system")
        else:
            raise RuntimeError(f"unsupported operating system '{os.name}'")

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("-p", "--path", type=str, required=True,
                            help="Opens the file manager at the specified path.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """
        return_code: int = 0

        if args.path is not None:
            self._start(path=args.path)

        else:
            # Error: no arguments
            return_code = CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_code
