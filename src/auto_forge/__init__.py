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
import sys
import traceback
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import fields
from typing import ClassVar, TYPE_CHECKING, Union
from typing import Optional
from typing import get_origin, get_args, Any, ForwardRef

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
    from auto_forge.core.ai_bridge import (CoreAI)
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


    @dataclass(init=False)
    class SDKType:
        """
        Singleton class that holds all the core service instances of the AutoForge SDK.
        This container provides centralized, type-safe access to shared core components,
        allowing modules to retrieve services like telemetry, logging, toolchains, and
        variable management from a single global instance.
        Each field represents a distinct core module or system service. Instances of these
        services are expected to register themselves via the `auto_register()` method,
        which matches the instance to a field based on its annotated type.

        Notes:
            - Type annotations are written as strings to support forward references.
            - Optional[...] and Union[...] annotations are fully supported and resolved dynamically.
            - Unmatched or unresolved instances are silently ignored.
            - All core modules should inherit from `CoreModuleInterface`, which handles
              calling `sdk.auto_register(self)` automatically during initialization.
        """

        registry: Optional["CoreRegistry"] = None
        telemetry: Optional["CoreTelemetry"] = None
        logger: Optional["CoreLogger"] = None
        watchdog: Optional["CoreWatchdog"] = None
        system_info: Optional["CoreSystemInfo"] = None
        processor: Optional["CoreJSONCProcessor"] = None
        toolbox: Optional["CoreToolBox"] = None
        variables: Optional["CoreVariables"] = None
        ai_bridge: Optional["CoreAI"] = None
        signatures: Optional["CoreSignatures"] = None
        solution: Optional["CoreSolution"] = None
        loader: Optional["CoreDynamicLoader"] = None
        linux_aliases: Optional["CoreLinuxAliases"] = None
        platform: Optional["CorePlatform"] = None
        xray: Optional["CoreXRayDB"] = None
        build_shell: Optional["CoreBuildShell"] = None
        auto_forge: Optional["AutoForge"] = None

        _instance: ClassVar[Optional["SDKType"]] = None
        _type_hint_cache: Optional[dict[type, dict[str, Any]]] = None

        def __new__(cls, *args, **kwargs):
            if cls._instance is None:
                cls._instance = super(SDKType, cls).__new__(cls)
                cls._type_hint_cache = {}
            return cls._instance

        @classmethod
        def get_instance(cls) -> "SDKType":
            return cls()

        def auto_register(self, instance: object) -> None:
            """
            Automatically registers a core instance into the appropriate field of the SDK singleton.
            Handles Optional[...] and forward references like "CoreRegistry".
            Skips fields it cannot resolve safely.
            Args:
                instance: The service instance to register (e.g., CoreRegistry).
            """
            cls = self.__class__
            module_globals = sys.modules[cls.__module__].__dict__

            for field in fields(self):
                field_type = field.type

                # Step 1: Resolve string-based annotations like "CoreRegistry"
                if isinstance(field_type, str):
                    with suppress(Exception):
                        field_type = eval(field_type, module_globals)
                if field_type is None:
                    continue

                # Step 2: Handle Union/Optional
                origin = get_origin(field_type)
                if origin is Union:
                    type_list = [t for t in get_args(field_type) if t is not type(None)]
                else:
                    type_list = [field_type]

                for typ in type_list:
                    # Step 3: Resolve ForwardRef objects
                    if isinstance(typ, ForwardRef):
                        with suppress(Exception):
                            typ = eval(typ.__forward_arg__, module_globals)

                    if typ is Any or (isinstance(typ, type) and isinstance(instance, typ)):
                        setattr(self, field.name, instance)
                        return


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
    "AutoForgeWorkModeType", "SDKType",
    "BuildLogAnalyzerInterface", "BuildProfileType", "BuilderRunnerInterface", "BuilderToolChain",
    "CommandFailedException", "CommandInterface", "CommandInterfaceProtocol", "CommandResultType",
    "CoreAI", "CoreBuildShell", "CoreContext", "CoreDynamicLoader", "CoreGUI", "CoreJSONCProcessor",
    "CoreJSONCProcessorProtocol", "CoreLinuxAliases", "CoreLinuxAliasesProtocol", "CoreLogger", "CoreLoggerProtocol",
    "CoreModuleInterface", "CorePlatform", "CoreRegistry", "CoreSignatures", "CoreSolution", "CoreSystemInfo",
    "CoreTelemetry", "CoreToolBox", "CoreToolBoxProtocol", "CoreVariables", "CoreVariablesProtocol", "CoreWatchdog",
    "CoreXRayDB", "Crypto", "DataSizeFormatter", "EventManager", "ExceptionGuru", "ExecutionModeType",
    "ExpectedVersionInfoType", "FieldColorType", "GCCLogAnalyzer", "HasConfigurationProtocol", "InputBoxButtonType",
    "InputBoxLineType", "InputBoxTextType", "LinuxShellType", "LogHandlersType", "MessageBoxType",
    "MethodLocationType", "ModuleInfoType", "PackageGlobals", "ProgressTracker", "PromptStatusType",
    "SequenceErrorActionType", "Signature", "SignatureFieldType", "SignatureFileHandler", "SignatureSchemaType",
    "StatusNotifType", "SysInfoLinuxDistroType", "SysInfoPackageManagerType", "TelemetryTrackedCounter",
    "TerminalAnsiGuru", "TerminalEchoType", "TerminalSpinner", "TerminalTeeStream", "ValidationMethodType",
    "VariableFieldType", "VariableType", "VersionCompare", "XRayStateType"
]
