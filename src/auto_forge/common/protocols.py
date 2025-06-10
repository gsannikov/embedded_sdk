"""
Script:         protocols.py
Author:         AutoForge Team

Description:
    Centralized module for defining core interface protocols used across AutoForge.
    These protocols define the required methods for key components such as the processor,
    variables handler, and CLI command modules.

    This module avoids package-wide imports to minimize the risk of circular dependencies.
"""

from pathlib import Path
from typing import Protocol, Union, Optional, Any, runtime_checkable

# Avoid importing from the root package to prevent circular dependencies
from auto_forge.common.local_types import ModuleInfoType

AUTO_FORGE_MODULE_NAME: str = "Protocols"
AUTO_FORGE_MODULE_DESCRIPTION: str = "Interfaces Protocols"


@runtime_checkable
class CoreProcessorProtocol(Protocol):
    """
    Defines the required interface for CoreProcessor implementations.
    """

    def preprocess(self, file_name: Union[str, Path]) -> Optional[dict[str, Any]]: ...


@runtime_checkable
class CoreVariablesProtocol(Protocol):
    """
    Defines the required interface for core variable expansion handlers.
    """

    def expand(self, key: Optional[str], allow_environment: bool = True, quiet: bool = False) -> Optional[str]: ...


@runtime_checkable
class CommandInterfaceProtocol(Protocol):
    """
    Defines the required interface for CLI command modules.
    """

    def get_info(self) -> ModuleInfoType: ...

    def update_info(self, command_info: ModuleInfoType) -> None: ...
