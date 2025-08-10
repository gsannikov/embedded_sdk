"""
Script:         __init__.py
Author:         AutoForge Team

Description:
    Centralized import hub for the AutoForge application, managing the import of
    essential modules and configurations. It is critical not to reorganize the
    import order automatically (e.g., by IDE tools like PyCharm) as the sequence
    may impact application behavior due to dependencies and initialization order
    required by certain components.

--------------------------------------------------------------------------------
Note:
    This file must NOT be optimized and sorted by PyCharm.
    >> Imports order does matter <<
--------------------------------------------------------------------------------

"""
import traceback
from typing import TYPE_CHECKING

# Third-party
import pyperclip

try:

    # Disable clipboard access to prevent pyperclip/cmd2 errors
    # in WSL or headless environments
    pyperclip.determine_clipboard()
    pyperclip.set_clipboard("no")

    from .settings import (PackageGlobals)

    # Common types
    from auto_forge.common.local_types import (
        AIKeyType, AIModelType, AIProviderType, AIProvidersType, AddressInfoType,
        AutoForgCommandType, AutoForgFolderType, AutoForgeModuleType, AutoForgeWorkModeType,
        BuildAnalyzedContextType, BuildAnalyzedEventType, BuildProfileType,
        CommandFailedException, CommandResultType,
        DataSizeFormatter, EventManager, ExceptionGuru, ExecutionModeType, ExpectedVersionInfoType,
        FieldColorType, InputBoxButtonType, InputBoxLineType, InputBoxTextType,
        LinuxShellType, LogHandlersType,
        MessageBoxType, MethodLocationType, ModuleInfoType,
        PromptStatusType, ProxyServerType,
        SDKType, SequenceErrorActionType, SignatureFieldType, SignatureSchemaType,
        SourceFileInfoType, SourceFileLanguageType, StatusNotifType,
        SysInfoLinuxDistroType, SysInfoPackageManagerType,
        TerminalAnsiGuru, TerminalEchoType, TerminalSpinner, TerminalTeeStream,
        VariableFieldType, VariableType,
        XRayStateType,
    )

    # Common modules
    from auto_forge.common.version_compare import (VersionCompare)
    from auto_forge.common.progress_tracker import (ProgressTracker)
    from auto_forge.common.crypto import (Crypto)
    from auto_forge.common.summary_patcher import (SummaryPatcher)

    # Protocols
    from auto_forge.core.protocols.protocols import (
        CoreJSONCProcessorProtocol, CoreVariablesProtocol, CoreLinuxAliasesProtocol, CoreToolBoxProtocol,
        CommandInterfaceProtocol, CoreLoggerProtocol, HasConfigurationProtocol)

    # Context providers
    from auto_forge.core.protocols.context import (CoreContext)

    # Interfaces
    from auto_forge.core.interfaces.core_module_interface import (CoreModuleInterface)
    from auto_forge.core.interfaces.command_interface import (CommandInterface)
    from auto_forge.core.interfaces.builder_interfcae import (BuilderRunnerInterface, BuilderArtifactsValidator,
                                                              BuildLogAnalyzerInterface, BuilderToolChain)

    # Build output analyzers
    from auto_forge.builders.analyzers.gcc_log_analyzer import GCCLogAnalyzer

    # WARNING: Core modules — import order is critical. Do not reorder.
    from auto_forge.core.registry import (CoreRegistry)
    from auto_forge.core.telemetry import (CoreTelemetry, TelemetryTrackedCounter)
    from auto_forge.core.logger import (CoreLogger, LoggerSettingsType)
    from auto_forge.core.system_info import (CoreSystemInfo)
    from auto_forge.core.watchdog import (CoreWatchdog)
    from auto_forge.core.jsonc_processor import (CoreJSONCProcessor)
    from auto_forge.core.toolbox import CoreToolBox
    from auto_forge.core.variables import (CoreVariables)
    from auto_forge.core.ai_bridge import (CoreAIBridge)
    from auto_forge.core.gui import (CoreGUI)
    from auto_forge.core.signatures import (CoreSignatures, SignatureFileHandler, Signature)
    from auto_forge.core.solution import (CoreSolution)
    from auto_forge.core.dynamic_loader import (CoreDynamicLoader)
    from auto_forge.core.linux_aliases import (CoreLinuxAliases)
    from auto_forge.core.platform_tools import (CorePlatform)
    from auto_forge.core.xray import (CoreXRayDB)
    from auto_forge.core.build_shell import (CoreBuildShell)
    from auto_forge.core.mcp_service import (CoreMCPService)

    # Last, AutoForg main class
    if TYPE_CHECKING:
        from auto_forge.auto_forge import AutoForge

except ImportError as import_error:
    print(f"Critical Startup Exception: failed to import: {import_error.name}")
    traceback.print_exc()
    raise import_error from import_error
except Exception as exception:
    print(f"Critical Startup Unexpected error: {exception}")
    raise exception from exception

# Exported symbols
__all__ = [
    "AIKeyType", "AIModelType", "AIProviderType", "AIProvidersType", "AddressInfoType",
    "AutoForgCommandType", "AutoForgFolderType", "AutoForgeModuleType", "AutoForgeWorkModeType",
    "BuildAnalyzedContextType", "BuildAnalyzedEventType", "BuildLogAnalyzerInterface", "BuildProfileType",
    "BuilderArtifactsValidator", "BuilderRunnerInterface", "BuilderToolChain",
    "CommandFailedException", "CommandInterface", "CommandInterfaceProtocol", "CommandResultType",
    "CoreAIBridge", "CoreBuildShell", "CoreContext", "CoreDynamicLoader", "CoreGUI", "CoreJSONCProcessor",
    "CoreJSONCProcessorProtocol", "CoreLinuxAliases", "CoreLinuxAliasesProtocol", "CoreLogger",
    "CoreLoggerProtocol", "CoreMCPService", "CoreModuleInterface", "CorePlatform", "CoreRegistry", "CoreSignatures",
    "CoreSolution", "CoreSystemInfo", "CoreTelemetry", "CoreToolBox", "CoreToolBoxProtocol",
    "CoreVariables", "CoreVariablesProtocol", "CoreWatchdog", "CoreXRayDB", "Crypto",
    "DataSizeFormatter", "EventManager", "ExceptionGuru", "ExecutionModeType", "ExpectedVersionInfoType",
    "FieldColorType", "GCCLogAnalyzer", "HasConfigurationProtocol",
    "InputBoxButtonType", "InputBoxLineType", "InputBoxTextType",
    "LinuxShellType", "LogHandlersType", "LoggerSettingsType",
    "MessageBoxType", "MethodLocationType", "ModuleInfoType",
    "PackageGlobals", "ProgressTracker", "PromptStatusType", "ProxyServerType",
    "SDKType", "SequenceErrorActionType", "Signature", "SignatureFieldType", "SignatureFileHandler",
    "SignatureSchemaType", "SourceFileInfoType", "SourceFileLanguageType", "StatusNotifType",
    "SummaryPatcher", "SysInfoLinuxDistroType", "SysInfoPackageManagerType",
    "TelemetryTrackedCounter", "TerminalAnsiGuru", "TerminalEchoType", "TerminalSpinner",
    "TerminalTeeStream", "VariableFieldType", "VariableType", "VersionCompare",
    "XRayStateType",
]
