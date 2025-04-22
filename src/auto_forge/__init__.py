"""
Script:         __init__.py
Author:         AutoForge Team

Description:
    This module serves as the centralized import hub for the AutoForge application, managing the import of essential
    modules and configurations. It is critical not to reorganize the import order
    automatically (e.g., by IDE tools like PyCharm) as the sequence may impact application behavior due to
    dependencies and initialization order required by certain components.
"""

# Main module imports must not be optimized by PyCharm, order  does matter here.
# noinspection PyUnresolvedReferences

from .settings import (PROJECT_BASE_PATH, PROJECT_CONFIG_PATH, PROJECT_RESOURCES_PATH, PROJECT_COMMANDS_PATH,
                       PROJECT_SCHEMAS_PATH, PROJECT_VERSION, PROJECT_NAME, PROJECT_REPO, PROJECT_PACKAGE)

from auto_forge.logger import (AutoLogger, LogHandlersTypes)

# Common types
from auto_forge.common.local_types import (CLICommandInfo, CLICommandSummary,
                                           ValidationMethod, ExecutionMode,
                                           SignatureSchema, SignatureField, VariableField,
                                           TerminalTeeStream, TerminalAnsiCodes,
                                           TerminalAnsiGuru, TerminalFileIconInfo, TERMINAL_ICONS_MAP)

from auto_forge.common.toolbox import (ToolBox)

# Interfaces
from auto_forge.core.interfaces.cli_command_interface import (CLICommandInterface)

# Core / common modules
from auto_forge.common.progress_tracker import (ProgressTracker)
from auto_forge.core.processor import Processor
from auto_forge.core.environment import (Environment)
from auto_forge.core.variables import Variables
from auto_forge.core.commands_loader import (CommandsLoader)
from auto_forge.core.signatures import (Signatures, SignatureFileHandler, Signature)
from auto_forge.core.solution import Solution
from auto_forge.core.prompt import Prompt

# AutoForg main
from auto_forge.auto_forge import auto_forge_main as main

# Exported symbols
__all__ = [
    "ToolBox",
    "ProgressTracker",
    "Processor",
    "Variables",
    "Solution",
    "Environment",
    "Signatures",
    "TerminalAnsiCodes",
    "TerminalTeeStream",
    "TerminalAnsiGuru",
    "TerminalFileIconInfo",
    "TERMINAL_ICONS_MAP",
    "CLICommandInfo",
    "CLICommandSummary",
    "ValidationMethod",
    "ExecutionMode",
    "SignatureField",
    "SignatureSchema",
    "VariableField",
    "SignatureFileHandler",
    "Signature",
    "CommandsLoader",
    "CLICommandInterface",
    "Prompt",
    "PROJECT_BASE_PATH",
    "PROJECT_CONFIG_PATH",
    "PROJECT_COMMANDS_PATH",
    "PROJECT_RESOURCES_PATH",
    "PROJECT_SCHEMAS_PATH",
    "PROJECT_VERSION",
    "PROJECT_NAME",
    "PROJECT_REPO",
    "PROJECT_PACKAGE",
    "AutoLogger",
    "LogHandlersTypes",
    "main"
]
