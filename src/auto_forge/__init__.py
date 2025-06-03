"""
Script:         __init__.py
Author:         AutoForge Team

Description:
    This module serves as the centralized import hub for the AutoForge application, managing the import of essential
    modules and configurations. It is critical not to reorganize the import order
    automatically (e.g., by IDE tools like PyCharm) as the sequence may impact application behavior due to
    dependencies and initialization order required by certain components.

Note:
    This file must not be optimized and sorted by PyCharm,
    >> Order does matter here! <<
"""
# @formatter:off
from .settings import (PROJECT_BASE_PATH, PROJECT_CONFIG_FILE, PROJECT_CONFIG_PATH, PROJECT_RESOURCES_PATH,
                       PROJECT_SHARED_PATH, PROJECT_COMMANDS_PATH, PROJECT_SAMPLES_PATH, PROJECT_BUILDERS_PATH,
                       PROJECT_HELP_PATH, PROJECT_SCHEMAS_PATH, PROJECT_VERSION, PROJECT_NAME, PROJECT_REPO,
                       PROJECT_PACKAGE, PROJECT_TEMP_PREFIX, PROJECT_LOG_FILE)

from auto_forge.logger import (AutoLogger, LogHandlersTypes)

# Basic types
from auto_forge.common.local_types import (AddressInfoType, AutoForgeModuleType, AutoForgCommandType,
                                           AutoForgeWorkModeType, BuildProfileType, BuildTelemetry,
                                           COMMAND_TYPE_COLOR_MAP, CommandResultType, ExecutionModeType, ExceptionGuru,
                                           FieldColorType, InputBoxButtonType, InputBoxLineType, InputBoxTextType,
                                           MessageBoxType, MethodLocationType, ModuleInfoType, SignatureFieldType,
                                           SignatureSchemaType, TerminalAnsiGuru, TerminalEchoType, TerminalTeeStream,
                                           ThreadGuru, ValidationMethodType, VariableFieldType, XYType,
                                           SysInfoPackageManagerType, SysInfoLinuxDistroType)

# Interfaces
from auto_forge.core.interfaces.core_module_interface import CoreModuleInterface
from auto_forge.core.interfaces.cli_command_interface import CLICommandInterface
from auto_forge.core.interfaces.builder_interfcae import (BuilderInterface, BuilderToolChainInterface)

# Common modules
from auto_forge.common.registry import Registry
from auto_forge.common.toolbox import ToolBox
from auto_forge.common.progress_tracker import ProgressTracker
from auto_forge.common.pretty_json_printer import PrettyPrinter
from auto_forge.common.system_info import SystemInfo

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
# @formatter:on

# Exported symbols
__all__ = ["AddressInfoType", "AutoForgeModuleType", "AutoForgCommandType", "AutoForgeWorkModeType", "AutoLogger",
           "BuilderInterface", "BuilderToolChainInterface", "BuildProfileType", "BuildTelemetry", "CLICommandInterface",
           "COMMAND_TYPE_COLOR_MAP", "CommandResultType", "CoreEnvironment", "CoreGUI", "CoreLoader",
           "CoreModuleInterface", "SysInfoPackageManagerType", "SysInfoLinuxDistroType",
           "CoreProcessor", "CorePrompt", "CoreSignatures", "CoreSolution", "CoreVariables", "ExceptionGuru",
           "ExecutionModeType", "FieldColorType", "InputBoxButtonType", "InputBoxLineType", "InputBoxTextType",
           "LogHandlersTypes", "MethodLocationType", "MessageBoxType", "ModuleInfoType", "PROJECT_BASE_PATH",
           "PROJECT_BUILDERS_PATH", "PROJECT_COMMANDS_PATH", "PROJECT_CONFIG_PATH", "PROJECT_CONFIG_FILE",
           "PROJECT_HELP_PATH", "PROJECT_LOG_FILE",
           "PROJECT_NAME", "PROJECT_PACKAGE", "PROJECT_REPO", "PROJECT_RESOURCES_PATH", "PROJECT_SAMPLES_PATH",
           "PROJECT_TEMP_PREFIX",
           "PROJECT_SCHEMAS_PATH", "PROJECT_SHARED_PATH", "PROJECT_VERSION", "PrettyPrinter", "ProgressTracker",
           "SystemInfo", "Registry",
           "Signature", "SignatureFieldType", "SignatureFileHandler", "SignatureSchemaType", "TerminalAnsiGuru",
           "TerminalEchoType", "TerminalTeeStream", "ThreadGuru", "ToolBox", "ValidationMethodType",
           "VariableFieldType",
           "XYType", "main"]
