"""
Script:         make_builder.py
Author:         AutoForge Team

Description:
    ToDo
"""

from typing import Any, Optional

# AutoForge imports
from auto_forge import (
    BuilderInterface,
    BuildProfileType,
)

AUTO_FORGE_MODULE_NAME = "make"
AUTO_FORGE_MODULE_DESCRIPTION = "make files builder"
AUTO_FORGE_MODULE_VERSION = "1.0"


class MakeBuilder(BuilderInterface):
    """
    A simple 'hello world' command example for AutoForge CLI.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the MakeBuilder class.
        Args:
            **_kwargs (Any): Optional keyword arguments.
        """

        super().__init__(build_system=AUTO_FORGE_MODULE_NAME)

    def build(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        Validates the provided build configuration and executes the corresponding build flow.
        Args:
            build_profile (BuildProfileType): The build profile containing solution, project,
            configuration, and toolchain information required for the build process.

        Returns:
            Optional[int]: The return code from the build process, or None if not applicable.
        """
        print(f"Building...{build_profile.build_dot_notation}")
