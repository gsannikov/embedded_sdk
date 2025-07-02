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
import re
import traceback
from typing import TYPE_CHECKING, Any, ClassVar, Optional

# Third-party
import pyperclip

try:

    # Disable clipboard access to prevent pyperclip/cmd2 errors in WSL or headless environments
    pyperclip.determine_clipboard()
    pyperclip.set_clipboard("no")

    from .settings import (PackageGlobals)

    # Common types
    from auto_forge.common.local_types import (
        AddressInfoType, AutoForgFolderType, AutoForgeModuleType, AutoForgCommandType, AutoForgeWorkModeType,
        BuildProfileType, CommandResultType, CommandFailedException,
        DataSizeFormatter, EventManager, ExceptionGuru, ExecutionModeType, ExpectedVersionInfoType,
        FieldColorType, InputBoxButtonType, InputBoxLineType, InputBoxTextType, LogHandlersType,
        LinuxShellType, MessageBoxType, MethodLocationType, ModuleInfoType, PromptStatusType,
        SignatureFieldType, SignatureSchemaType, SequenceErrorActionType, StatusNotifType,
        SysInfoLinuxDistroType, SysInfoPackageManagerType, TerminalAnsiGuru, TerminalSpinner,
        TerminalEchoType, TerminalTeeStream, ValidationMethodType, VariableFieldType, VariableType, XRayStateType
    )

    # Common modules
    from auto_forge.common.version_compare import (VersionCompare)
    from auto_forge.common.progress_tracker import (ProgressTracker)
    from auto_forge.common.crypto import (Crypto)

    # Protocols
    from auto_forge.core.protocols.protocols import (
        CoreJSONCProcessorProtocol, CoreVariablesProtocol, CoreLinuxAliasesProtocol, CoreToolBoxProtocol,
        CommandInterfaceProtocol, CoreLoggerProtocol, HasConfigurationProtocol)

    # Context providers
    from auto_forge.core.protocols.context import (CoreContext)

    # Interfaces
    from auto_forge.core.interfaces.core_module_interface import (CoreModuleInterface)
    from auto_forge.core.interfaces.command_interface import CommandInterface
    from auto_forge.core.interfaces.builder_interfcae import (BuilderRunnerInterface, BuildLogAnalyzerInterface,
                                                              BuilderToolChain)

    # Build output analyzers
    from auto_forge.builders.analyzers.gcc_log_analyzer import GCCLogAnalyzer

    # Core / common modules
    from auto_forge.core.registry import (CoreRegistry)
    from auto_forge.core.telemetry import (CoreTelemetry, TelemetryTrackedCounter)
    from auto_forge.core.logger import (CoreLogger)
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

    # Last, AutoForg main class
    if TYPE_CHECKING:
        from auto_forge.auto_forge import AutoForge


    class SDKType:
        """
        Singleton container for all dynamically registered core modules in the AutoForge SDK.
        This class acts as a runtime service locator, allowing core modules (such as telemetry,
        logging, toolchains, and platform tools) to self-register and expose themselves through
        a centralized global instance.

        Unlike traditional dataclasses, this implementation does **not** declare fixed attributes.
        Instead, it dynamically injects fields based on core module class names, which are converted
        to standardized `snake_case` identifiers (e.g., `CoreXRayDB` → `xray_db`). This avoids static
        annotation boilerplate and keeps the class extensible.

        Registration:
            Each core module should inherit from `CoreModuleInterface`, which invokes `SDKType.get_instance().register(self)`
            automatically during its initialization. This mechanism ensures the module becomes accessible
            via the global SDK instance.
        """

        _instance: ClassVar[Optional["SDKType"]] = None

        def __new__(cls):
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

        def __getattr__(self, name: str) -> Any:
            """
            Escape hatch for IDEs to prevent 'Cannot find reference' warnings.
            All dynamically injected core modules go through here if not declared.
            """
            raise AttributeError(f"{name!r} is not defined in SDKType")

        @classmethod
        def get_instance(cls) -> "SDKType":
            return cls()

        def register(self, instance: object) -> None:
            """
            Registers a core module instance into the SDK singleton under a snake_case name
            derived from its class name (e.g., CoreTelemetry → telemetry).
            """

            _ACRONYMS = {"AI", "JSONC", "XRay", "DB", "SDK"}

            def _camel_to_snake(_name: str) -> str:
                """
                Converts CamelCase to snake_case, correctly preserving known acronyms
                and inserting underscores between consecutive acronym groups.
                """
                _name = re.sub(r'^Core', '', _name)
                acr_pattern = '|'.join(sorted(_ACRONYMS, key=len, reverse=True))
                pattern = rf'(?:{acr_pattern})|[A-Z][a-z]*|\d+'
                parts = re.findall(pattern, _name)
                return '_'.join(part.lower() for part in parts)

            if not isinstance(instance, CoreModuleInterface):
                raise TypeError(f"Instance {instance} must inherit from CoreModuleInterface")

            class_name = instance.__class__.__name__
            stripped = class_name[4:] if class_name.startswith("Core") else class_name
            snake_name = _camel_to_snake(stripped)

            if hasattr(self, snake_name):
                raise ValueError(f"SDKType already has a registered core instance named '{snake_name}'")

            setattr(self, snake_name, instance)


except ImportError as import_error:
    print(f"Critical Startup Exception: failed to import: {import_error.name}")
    traceback.print_exc()
    raise import_error from import_error
except Exception as exception:
    print(f"Critical Startup Unexpected error: {exception}")
    raise exception from exception

# Exported symbols
__all__ = [
    "AddressInfoType", "AutoForgCommandType", "AutoForgFolderType", "AutoForgeModuleType",
    "AutoForgeWorkModeType", "BuildLogAnalyzerInterface", "BuildProfileType", "BuilderRunnerInterface",
    "BuilderToolChain", "CommandFailedException", "CommandInterface", "CommandInterfaceProtocol",
    "CommandResultType", "CoreAIBridge", "CoreBuildShell", "CoreContext", "CoreDynamicLoader", "CoreGUI",
    "CoreJSONCProcessor", "CoreJSONCProcessorProtocol", "CoreLinuxAliases", "CoreLinuxAliasesProtocol",
    "CoreLogger", "CoreLoggerProtocol", "CoreModuleInterface", "CorePlatform", "CoreRegistry", "CoreSignatures",
    "CoreSolution", "CoreSystemInfo", "CoreTelemetry", "CoreToolBox", "CoreToolBoxProtocol", "CoreVariables",
    "CoreVariablesProtocol", "CoreWatchdog", "CoreXRayDB", "Crypto", "DataSizeFormatter", "EventManager",
    "ExceptionGuru", "ExecutionModeType", "ExpectedVersionInfoType", "FieldColorType", "GCCLogAnalyzer",
    "HasConfigurationProtocol", "InputBoxButtonType", "InputBoxLineType", "InputBoxTextType", "LinuxShellType",
    "LogHandlersType", "MessageBoxType", "MethodLocationType", "ModuleInfoType", "PackageGlobals",
    "ProgressTracker", "PromptStatusType", "SDKType", "SequenceErrorActionType", "Signature",
    "SignatureFieldType", "SignatureFileHandler", "SignatureSchemaType", "StatusNotifType",
    "SysInfoLinuxDistroType", "SysInfoPackageManagerType", "TelemetryTrackedCounter", "TerminalAnsiGuru",
    "TerminalEchoType", "TerminalSpinner", "TerminalTeeStream", "ValidationMethodType", "VariableFieldType",
    "VariableType", "VersionCompare", "XRayStateType"
]
