"""
Script:     zephyr_sdk.py
Author:     AutoForge Team

Description:
    Support tool for querying Zephyr SDK installation details using the CMake user package registry.
"""

import argparse
from pathlib import Path
from typing import Optional

# AutoForge imports
from auto_forge import CLICommand

AUTO_FORGE_COMMAND_NAME = "zephyr_sdk"
AUTO_FORGE_COMMAND_DESCRIPTION = "Zephyr SDK utilities"


class ZephyrSDKCommand(CLICommand):
    """
    CLI command for interacting with the Zephyr SDK.

    This tool attempts to locate a Zephyr SDK installation by scanning
    the CMake user package registry and provides access to the SDK path
    and version if found.
    """

    def __init__(self):
        super().__init__()
        self._path: Optional[str] = None
        self._version: Optional[str] = None
        self._cmake_pkg_dir: Path = Path.home() / ".cmake/packages/Zephyr-sdk"
        self._detect()

    def _detect(self) -> bool:
        """
        Detect the installed Zephyr SDK by examining the CMake user package registry.

        Returns:
            dict or None: A dictionary with:
                - 'sdk_path' (str): Absolute path to the Zephyr SDK.
                - 'version' (str): Version string inferred from the directory name.
            Returns None if the SDK is not found or appears invalid.

        Notes:
            - This does not rely on environment variables or PATH.
            - Assumes standard SDK install with 'zephyr-sdk-setup.sh' registration.
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

    def get_name(self) -> str:
        """
        Returns:
            str: The CLI command keyword used to invoke this command.
        """
        return AUTO_FORGE_COMMAND_NAME

    def get_description(self) -> str:
        """
        Returns:
            str: A human-readable description of the command.
        """
        return AUTO_FORGE_COMMAND_DESCRIPTION

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        parser.add_argument(
            '-p', '--get-path',
            action='store_true',
            dest='get_path',
            help='Prints the detected SDK installation path.'
        )
        parser.add_argument(
            '-v', '--get-version',
            action='store_true',
            dest='get_version',
            help='Prints the detected SDK version.'
        )

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:
            args (argparse.Namespace): The parsed CLI arguments.

        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """
        if not self._path:
            print("Zephyr SDK not found.")
            return 1

        if args.get_path:
            print(self._path)
            return 0
        elif args.get_version:
            print(self._version)
            return 0
        else:
            print("No flag provided. Use -p or -v.")
            return 1
