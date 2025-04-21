"""
Script:         zephyr_sdk_command.py
Author:         AutoForge Team

Description:
    AutoForge command which attempts to locate a Zephyr SDK installation by scanning
    the CMake user package registry and provides access to the SDK path and version if found.
"""

import argparse
from pathlib import Path
from typing import Optional, Any, cast

# AutoForge imports
from auto_forge import (CLICommandInterface, AutoLogger)

AUTO_FORGE_COMMAND_NAME = "zephyr_sdk"
AUTO_FORGE_COMMAND_DESCRIPTION = "Zephyr SDK utilities"
AUTO_FORGE_COMMAND_VERSION = "1.0"

# Default CMake user package registry path where the Zephyr SDK is expected to be registered
CMAKE_PACKAGE_PATH: Path = Path.home() / ".cmake/packages/Zephyr-sdk"


class ZephyrSDKCommand(CLICommandInterface):

    def __init__(self, **kwargs: Any):
        """
        Initializes the ZephyrSDKCommand class.

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
        self._cmake_pkg_dir: Optional[Path] = Path(kwargs.get('cmake_pkg_dir', CMAKE_PACKAGE_PATH))

        # Extract optional parameters
        raise_exceptions: bool = kwargs.get('raise_exceptions', False)

        # Base class initialization
        super().__init__(name=AUTO_FORGE_COMMAND_NAME,
                         description=AUTO_FORGE_COMMAND_DESCRIPTION,
                         version=AUTO_FORGE_COMMAND_VERSION,
                         raise_exceptions=raise_exceptions)

    def initialize(self, **kwargs: Any) -> bool:
        """
        Detect the installed Zephyr SDK by examining the CMake user package registry.
        Note: Assumes standard SDK install with 'zephyr-sdk-setup.sh' registration.

        Args:
            **kwargs (Any): Optional keyword arguments:
        Returns:
            bool: True if initialization succeeded, False otherwise.
        """

        if not self._cmake_pkg_dir or not self._cmake_pkg_dir.is_dir():
            raise RuntimeError(f"CMake package registry path '{self._cmake_pkg_dir}' does not exist")

        # Workaround PyCharm's static analyzer quirks
        cmake_pkg_dir = cast(Path, self._cmake_pkg_dir)

        for pkg_file in cmake_pkg_dir.iterdir():
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

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        parser.add_argument('-p', '--get_zephyr_path', action='store_true',
                            help='Prints the detected Zephyrs SDK installation path.')
        parser.add_argument('-z', '--get_zephyr_version', action='store_true',
                            help='Prints the detected Zephyrs SDK version.')

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
        if args.get_zephyr_path:
            print(self._path)
        elif args.get_zephyr_version:
            print(self._version)
        else:
            return_value = CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_value
