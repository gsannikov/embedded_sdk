"""
Script:         zephyr_sdk.py
Author:         AutoForge Team

Description:
    Support tool for querying Zephyr SDK installation details using the CMake user package registry.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Any

# AutoForge imports
from auto_forge import (CLICommandInterface, CLICommandInfo, AutoLogger)

AUTO_FORGE_COMMAND_NAME = "zephyr_sdk"
AUTO_FORGE_COMMAND_DESCRIPTION = "Zephyr SDK utilities"
AUTO_FORGE_COMMAND_VERSION = "1.0"

# Default CMake user package registry path where the Zephyr SDK is expected to be registered
CMAKE_PACKAGE_PATH: Path = Path.home() / ".cmake/packages/Zephyr-sdk"


class ZephyrSDKCommand(CLICommandInterface):
    """
    CLI command for interacting with the Zephyr SDK.

    This tool attempts to locate a Zephyr SDK installation by scanning
    the CMake user package registry and provides access to the SDK path
    and version if found.
    """

    def __init__(self, **kwargs: Any):
        """
        Constructor for ZephyrSDKCommand.

        Initializes internal state and allows for optional overrides such as custom CMake
        package registry path and error handling behavior.

        Args:
            **kwargs (Any): Optional keyword arguments:
                - raise_exceptions (bool): Whether to raise exceptions on error instead of returning codes.
                - cmake_pkg_dir (Path or str): Custom path to the CMake package registry directory.
        """
        self._path: Optional[str] = None  # Detected Zephyr SDK path
        self._version: Optional[str] = None  # Detected SDK version

        # Get logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_COMMAND_NAME)

        # Extract optional parameters
        raise_exceptions: bool = kwargs.get('raise_exceptions', False)
        self._cmake_pkg_dir: Path = Path(kwargs.get('cmake_pkg_dir', CMAKE_PACKAGE_PATH))

        # Base class initialization
        super().__init__(raise_exceptions=raise_exceptions)

    def initialize(self, **kwargs: Any) -> bool:
        """
        Detect the installed Zephyr SDK by examining the CMake user package registry.
        Note: Assumes standard SDK install with 'zephyr-sdk-setup.sh' registration.

        Args:
            **kwargs (Any): Optional keyword arguments:
        Returns:
            bool: True if initialization succeeded, False otherwise.
        """

        if not self._cmake_pkg_dir.is_dir():
            return False

        for pkg_file in self._cmake_pkg_dir.iterdir():
            if not pkg_file.is_file():
                continue

            try:
                with pkg_file.open("r", encoding="utf-8") as f:
                    first_line = f.readline().strip()
            except (OSError, UnicodeDecodeError):
                continue

            if first_line.startswith("%"):
                first_line = first_line[1:]

            cmake_dir = Path(first_line)
            sdk_path = cmake_dir.parent

            # Validate existence of expected toolchain binary
            if not (sdk_path / "arm-zephyr-eabi" / "bin" / "arm-zephyr-eabi-gcc").exists():
                continue

            # Extract version
            version = (
                sdk_path.name.replace("zephyr-sdk-", "").upper()
                if sdk_path.name.startswith("zephyr-sdk-")
                else None
            )

            self._path = sdk_path.__str__()
            self._version = version
            return True

        return False

    def get_info(self) -> CLICommandInfo:
        """
        Returns:
            CLICommandInfo: a named tuple containing the implemented command id
        """
        # Populate and return the command info type
        if self._command_info is None:
            self._command_info = CLICommandInfo(name=AUTO_FORGE_COMMAND_NAME,
                                                description=AUTO_FORGE_COMMAND_DESCRIPTION,
                                                version=AUTO_FORGE_COMMAND_VERSION,
                                                class_name=self.__class__.__name__,
                                                class_instance=self)
        return self._command_info

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        parser.add_argument('-p', '--get-path', action='store_true',
                            help='Prints the detected SDK installation path.')
        parser.add_argument('-v', '--get-version', action='store_true',
                            help='Prints the detected SDK version.')
        parser.add_argument("-ver", "--version", action="store_true", help="Show this command version and exit.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:
            args (argparse.Namespace): The parsed CLI arguments.

        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """
        return_value: int = 0

        # The SDK path should have been discovered when this class was created.
        if not self._path:
            print("Error: Zephyr SDK not found.")
            return 1

        # Handle arguments
        if args.get_path:
            print(self._path)
        elif args.get_version:
            print(self._version)
        elif args.version:
            print(f"{AUTO_FORGE_COMMAND_NAME} version {AUTO_FORGE_COMMAND_VERSION}")
        else:
            # No arguments provided, show command usage
            sys.stdout.write("No arguments provided.\n")
            self._parser.print_usage()
            return_value = 1

        return return_value
