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

# Basic types
from auto_forge.common.local_types import (AutoForgeModuleType, AutoForgeModuleInfo, AutoForgeModuleSummary,
                                           ValidationMethod, ExecutionMode,
                                           SignatureSchema, SignatureField, VariableField,
                                           TerminalTeeStream, TerminalAnsiCodes,
                                           TerminalAnsiGuru, TerminalFileIconInfo, TERMINAL_ICONS_MAP)
# Interfaces
from auto_forge.core.interfaces.core_module_interface import CoreModuleInterface
from auto_forge.core.interfaces.cli_command_interface import CLICommandInterface

# Common modules
from auto_forge.common.registry import Registry
from auto_forge.common.toolbox import ToolBox
from auto_forge.common.progress_tracker import (ProgressTracker)

# Core / common modules
from auto_forge.core.processor import CoreProcessor
from auto_forge.core.commands import CoreCommands
from auto_forge.core.environment import CoreEnvironment
from auto_forge.core.variables import CoreVariables
from auto_forge.core.signatures import (CoreSignatures, SignatureFileHandler, Signature)
from auto_forge.core.solution import CoreSolution
from auto_forge.core.prompt import CorePrompt


# AutoForg main
from auto_forge.auto_forge import auto_forge_main as main

# Exported symbols
__all__ = [
    "Registry",
    "ToolBox",
    "ProgressTracker",
    "CoreProcessor",
    "CoreVariables",
    "CoreSolution",
    "CoreEnvironment",
    "CoreSignatures",
    "TerminalAnsiCodes",
    "TerminalTeeStream",
    "TerminalAnsiGuru",
    "TerminalFileIconInfo",
    "TERMINAL_ICONS_MAP",
    "AutoForgeModuleType",
    "AutoForgeModuleInfo",
    "AutoForgeModuleSummary",
    "ValidationMethod",
    "ExecutionMode",
    "SignatureField",
    "SignatureSchema",
    "VariableField",
    "CLICommandInterface",
    "CoreModuleInterface",
    "SignatureFileHandler",
    "Signature",
    "CoreCommands",
    "CorePrompt",
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
