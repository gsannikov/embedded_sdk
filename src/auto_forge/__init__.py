"""
Script:         __init__.py
Author:         AutoForge Team

Description:
    This module serves as the centralized import hub for the AutoForge application, managing the import of essential
    modules and configurations. It is critical not to reorganize the import order
    automatically (e.g., by IDE tools like PyCharm) as the sequence may impact application behavior due to
    dependencies and initialization order required by certain components.

Note:
    This file must not be optimized and sorted by PyCharm, order does matter here!
"""

import sys
import platform


def check_critical_third_party_libraries() -> None:
    """
    Checks for required third-party or system-bound libraries like 'tkinter'.
    If not found, provides OS-specific installation instructions and exits.
    """
    try:
        import tkinter  # noqa: F401
    except ImportError:
        message = ["\nError: 'tkinter' is required but not installed."]

        system = platform.system()

        if system == "Linux":
            try:
                with open("/etc/os-release") as f:
                    os_release = f.read().lower()
                if "fedora" in os_release:
                    message.append("On Fedora, install it with: sudo dnf install python3-tkinter")
                elif "ubuntu" in os_release or "debian" in os_release:
                    message.append("On Ubuntu or Debian, install it with: sudo apt install python3-tk")
                else:
                    message.append("Please install 'tkinter' using your Linux distribution's package manager.")
            except Exception as exception:
                message.append(f"Exception: {exception}")
        elif system == "Darwin":
            message.append(
                "On macOS, ensure Python was installed via python.org or Homebrew with Tcl/Tk support."
            )
        elif system == "Windows":
            message.append(
                "On Windows, ensure you are using the official Python installer from python.org, which includes 'tkinter' by default."
            )
        else:
            message.append("Please ensure 'tkinter' is available in your Python environment.")

        sys.stderr.write("\n".join(message) + "\n\n")
        sys.exit(1)


check_critical_third_party_libraries()

from .settings import (PROJECT_BASE_PATH, PROJECT_CONFIG_PATH, PROJECT_RESOURCES_PATH, PROJECT_SHARED_PATH,
                       PROJECT_COMMANDS_PATH, PROJECT_SAMPLES_PATH,
                       PROJECT_SCHEMAS_PATH, PROJECT_VERSION, PROJECT_NAME, PROJECT_REPO, PROJECT_PACKAGE)

from auto_forge.logger import (AutoLogger, LogHandlersTypes)

# Basic types
from auto_forge.common.local_types import (AutoForgeModuleType, ModuleInfoType, ModuleSummaryType,
                                           ValidationMethodType, ExecutionModeType, MessageBoxType,
                                           InputBoxTextType, InputBoxButtonType, InputBoxLineType, AddressInfoType,
                                           SignatureSchemaType, SignatureFieldType, VariableFieldType,
                                           ExceptionGuru, ThreadGuru, TerminalAnsiGuru,
                                           TerminalTeeStream, TerminalAnsiCodes,
                                           TerminalFileIconInfo, TERMINAL_ICONS_MAP)
# Interfaces
from auto_forge.core.interfaces.core_module_interface import CoreModuleInterface
from auto_forge.core.interfaces.cli_command_interface import CLICommandInterface

# Common modules
from auto_forge.common.registry import Registry
from auto_forge.common.toolbox import ToolBox
from auto_forge.common.progress_tracker import ProgressTracker
from auto_forge.common.pretty_printer import PrettyPrinter

# Core / common modules
from auto_forge.core.processor import CoreProcessor
from auto_forge.core.loader import CoreLoader
from auto_forge.core.environment import CoreEnvironment
from auto_forge.core.variables import CoreVariables
from auto_forge.core.gui import CoreGUI
from auto_forge.core.signatures import (CoreSignatures, SignatureFileHandler, Signature)
from auto_forge.core.solution import CoreSolution
from auto_forge.core.prompt import CorePrompt

# AutoForg main
from auto_forge.auto_forge import auto_forge_main as main

# Exported symbols
__all__ = [
    "Registry", "ToolBox", "ProgressTracker", "PrettyPrinter",
    "CoreProcessor", "CoreVariables", "CoreSolution", "CoreEnvironment",
    "CoreSignatures", "CoreLoader", "CorePrompt", "CoreGUI",
    "ExceptionGuru", "ThreadGuru", "TerminalAnsiGuru",
    "TerminalAnsiCodes", "TerminalTeeStream", "TerminalFileIconInfo",
    "AutoForgeModuleType", "ModuleInfoType", "ModuleSummaryType", "ValidationMethodType", "ExecutionModeType",
    "MessageBoxType", "InputBoxTextType", "InputBoxButtonType", "InputBoxLineType", "AddressInfoType",
    "SignatureFieldType", "SignatureSchemaType", "VariableFieldType",
    "CLICommandInterface", "CoreModuleInterface",
    "SignatureFileHandler", "Signature",
    "TERMINAL_ICONS_MAP",
    "PROJECT_BASE_PATH", "PROJECT_CONFIG_PATH",
    "PROJECT_COMMANDS_PATH", "PROJECT_RESOURCES_PATH", "PROJECT_SHARED_PATH", "PROJECT_SAMPLES_PATH",
    "PROJECT_SCHEMAS_PATH", "PROJECT_VERSION", "PROJECT_NAME", "PROJECT_REPO", "PROJECT_PACKAGE",
    "AutoLogger", "LogHandlersTypes",
    "main"
]
