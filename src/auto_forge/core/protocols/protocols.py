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
from auto_forge import ModuleInfoType

AUTO_FORGE_MODULE_NAME: str = "Protocols"
AUTO_FORGE_MODULE_DESCRIPTION: str = "Interfaces Protocols"


@runtime_checkable
class CoreJSONCProcessorProtocol(Protocol):
    """
    Defines the required interface for the json processor mcore module..
    """

    def preprocess(self, file_name: Union[str, Path]) -> Optional[dict[str, Any]]: ...


@runtime_checkable
class CoreVariablesProtocol(Protocol):
    """
    Defines the required interface for core variable module.
    """

    def expand(self, key: Optional[str], allow_environment: bool = True, quiet: bool = False) -> Optional[str]: ...


@runtime_checkable
class CoreToolBoxProtocol(Protocol):
    """
    Defines the required interface for core toolbox module.
    """

    @staticmethod
    def resolve_help_file(relative_path: Union[str, Path]) -> Optional[Path]: ...

    @staticmethod
    def show_help_file(relative_path: Union[str, Path]) -> int: ...

    @staticmethod
    def strip_ansi(text: str, bare_text: bool = False) -> str: ...


@runtime_checkable
class CoreLinuxAliasesProtocol(Protocol):
    """
    Defines the required interface for Linux aliases core module.
    """

    def create(self, alias: str, command: str, can_update_existing: bool = True) -> bool: ...

    def commit(self) -> bool: ...


@runtime_checkable
class CommandInterfaceProtocol(Protocol):
    """
    Defines the required interface for the commands interface module.
    """

    def get_info(self) -> ModuleInfoType: ...

    def update_info(self, command_info: ModuleInfoType) -> None: ...
