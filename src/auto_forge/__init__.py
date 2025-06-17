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
import traceback

# Third-party
import pyperclip

try:

    # Disable clipboard access to prevent pyperclip/cmd2 errors in WSL or headless environments
    pyperclip.determine_clipboard()
    pyperclip.set_clipboard("no")

    from .settings import (PROJECT_BASE_PATH, PROJECT_CONFIG_FILE, PROJECT_CONFIG_PATH, PROJECT_RESOURCES_PATH,
                           PROJECT_SHARED_PATH, PROJECT_COMMANDS_PATH, PROJECT_SAMPLES_PATH, PROJECT_BUILDERS_PATH,
                           PROJECT_HELP_PATH, PROJECT_SCHEMAS_PATH, PROJECT_VERSION, PROJECT_VIEWERS_PATH, PROJECT_NAME,
                           PROJECT_REPO, PROJECT_PACKAGE, PROJECT_TEMP_PREFIX, PROJECT_LOG_FILE, )

    from auto_forge.common.watchdog import Watchdog
    from auto_forge.logger import (AutoLogger, QueueLogger, LogHandlersTypes)

    # Common types
    from auto_forge.common.local_types import (
        AddressInfoType, AutoForgFolderType, AutoForgeModuleType, AutoForgCommandType, AutoForgeWorkModeType,
        BuildProfileType, BuildTelemetry, COMMAND_TYPE_COLOR_MAP, CommandResultType,
        DataSizeFormatter, EventManager, ExceptionGuru, ExecutionModeType, ExpectedVersionInfoType,
        FieldColorType, InputBoxButtonType, InputBoxLineType, InputBoxTextType,
        LinuxShellType, MessageBoxType, MethodLocationType, ModuleInfoType,
        SignatureFieldType, SignatureSchemaType, SequenceErrorActionType, StatusNotifType,
        SysInfoLinuxDistroType, SysInfoPackageManagerType, TerminalAnsiGuru,
        TerminalEchoType, TerminalTeeStream, ValidationMethodType, VariableFieldType
    )

    # Common modules
    from auto_forge.common.version_compare import VersionCompare
    from auto_forge.common.progress_tracker import ProgressTracker

    # Protocols
    from auto_forge.core.protocols.protocols import (CoreJSONCProcessorProtocol, CoreVariablesProtocol,
                                                     CoreLinuxAliasesProtocol, CoreToolBoxProtocol,
                                                     CommandInterfaceProtocol)

    # Interfaces
    from auto_forge.core.interfaces.core_module_interface import CoreModuleInterface
    from auto_forge.core.interfaces.command_interface import CommandInterface
    from auto_forge.core.interfaces.builder_interfcae import (BuilderRunnerInterface, BuilderToolChain)

    # Core / common modules
    from auto_forge.core.registry import CoreRegistry
    from auto_forge.core.jsonc_processor import CoreJSONCProcessor
    from auto_forge.core.toolbox import CoreToolBox
    from auto_forge.core.variables import CoreVariables
    from auto_forge.core.gui import CoreGUI
    from auto_forge.core.signatures import (CoreSignatures, SignatureFileHandler, Signature)
    from auto_forge.core.solution import CoreSolution
    from auto_forge.core.system_info import CoreSystemInfo
    from auto_forge.core.dynamic_loader import CoreDynamicLoader
    from auto_forge.core.linux_aliases import CoreLinuxAliases
    from auto_forge.core.environment import CoreEnvironment
    from auto_forge.core.xray import CoreXRay
    from auto_forge.core.prompt import CorePrompt

    # AutoForg main
    from auto_forge.auto_forge import auto_forge_start as start


except ImportError as import_error:
    print(f"Critical Startup Exception: failed to import: {import_error.name}")
    traceback.print_exc()
    raise import_error from import_error
except Exception as exception:
    print(f"Critical Startup Unexpected error: {exception}")
    raise exception from exception

# Exported symbols
__all__ = [
    "AddressInfoType", "AutoForgCommandType", "AutoForgFolderType", "AutoForgeModuleType", "AutoForgeWorkModeType",
    "AutoLogger", "BuildProfileType", "BuildTelemetry", "BuilderRunnerInterface", "BuilderToolChain",
    "COMMAND_TYPE_COLOR_MAP", "CommandInterface", "CommandInterfaceProtocol", "CommandResultType",
    "CoreDynamicLoader", "CoreEnvironment", "CoreGUI", "CoreJSONCProcessor", "CoreJSONCProcessorProtocol",
    "CoreModuleInterface", "CorePrompt", "CoreRegistry", "CoreLinuxAliases", "CoreLinuxAliasesProtocol",
    "CoreSignatures", "CoreSolution", "CoreSystemInfo", "CoreToolBox", "CoreToolBoxProtocol", "CoreVariables",
    "CoreVariablesProtocol", "CoreXRay", "DataSizeFormatter", "EventManager", "ExceptionGuru", "ExecutionModeType",
    "ExpectedVersionInfoType", "FieldColorType", "InputBoxButtonType", "InputBoxLineType", "InputBoxTextType",
    "LinuxShellType", "LogHandlersTypes", "MessageBoxType", "MethodLocationType", "ModuleInfoType",
    "PROJECT_BASE_PATH", "PROJECT_BUILDERS_PATH", "PROJECT_COMMANDS_PATH", "PROJECT_CONFIG_FILE", "PROJECT_CONFIG_PATH",
    "PROJECT_HELP_PATH", "PROJECT_LOG_FILE", "PROJECT_NAME", "PROJECT_PACKAGE", "PROJECT_REPO",
    "PROJECT_RESOURCES_PATH", "PROJECT_SAMPLES_PATH", "PROJECT_SCHEMAS_PATH", "PROJECT_SHARED_PATH",
    "PROJECT_TEMP_PREFIX", "PROJECT_VERSION", "PROJECT_VIEWERS_PATH",
    "ProgressTracker", "QueueLogger",
    "SequenceErrorActionType", "Signature", "SignatureFieldType", "SignatureFileHandler",
    "SignatureSchemaType", "StatusNotifType", "SysInfoLinuxDistroType", "SysInfoPackageManagerType",
    "TerminalAnsiGuru", "TerminalEchoType", "TerminalTeeStream",
    "ValidationMethodType", "VariableFieldType", "VersionCompare", "Watchdog", "start"
]
