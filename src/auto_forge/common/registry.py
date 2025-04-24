"""
Script:         registry.py
Author:         AutoForge Team

Description:
    Auxiliary module for managing dynamic module registration within AutoForge.
"""
import inspect
from abc import ABCMeta
from types import ModuleType
from typing import Any, Dict, Optional, cast, List

# AutoForge imports
from auto_forge import (CoreModuleInterface,
                        AutoForgeModuleType, ModuleInfoType, ModuleSummaryType)

AUTO_FORGE_MODULE_NAME = "Registry"
AUTO_FORGE_MODULE_DESCRIPTION = "Modules registry"


class Registry(CoreModuleInterface):
    """
    Core module responsible for tracking and managing dynamically registered modules
    within the AutoForge system.
    """

    def _initialize(self):
        """
        Implements 'CoreModuleInterface' one tine initialization.
        """
        self._modules_registry: Dict[str, Dict[str, Any]] = {}

        # Register self
        self._module_info: ModuleInfoType = (
            self.register_module(name=AUTO_FORGE_MODULE_NAME,
                                 description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.COMMON))

    def _find_record(self, value: str, key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Searches the modules registry for a record matching the given value.
        Args:
            value (str): The value to search for.
            key (Optional[str]): Specific key to search within each module record.
                                 If not provided, all keys are scanned.
        Returns:
            Optional[Dict[str, Any]]: The first matching command record, or None.
        """
        for record in self._modules_registry.values():
            if key:
                if key in record and record[key] == value:
                    return record
            else:
                for _, v in record.items():
                    if v == value or (isinstance(v, list) and value in v):
                        return record
        return None

    from typing import List

    def get_modules_summary_list(self, auto_forge_module_type=AutoForgeModuleType.UNKNOWN) -> List[
        ModuleSummaryType]:
        """
        Returns a list of module summaries (name and description only) that match the specified module type.
        Omits all internal or non-serializable details.
        Args:
            auto_forge_module_type (AutoForgeModuleType): The type of modules to filter by.
        Returns:
            List[ModuleSummaryType]: A list of filtered module summaries.
        """
        return [
            ModuleSummaryType(name, meta.get("description", "description not provided"))
            for name, meta in self._modules_registry.items()
            if meta.get("auto_forge_module_type") == auto_forge_module_type
        ]

    def update_module_record(self, name: str, **updates: Any) -> bool:
        """
        Updates one or more fields of a module record in the registry.
        Args:
            name (str): The name of the module to update.
            **updates: Arbitrary key-value pairs to update in the module record.
        Returns:
            bool: True if the record was updated, False if the module was not found.
        """
        record = self._modules_registry.get(name)

        if not record:
            raise RuntimeError(f"module '{name}' not found in registry")

        for key, value in updates.items():
            record[key] = value

        return True

    def get_module_record_by_name(self, module_name: str,
                                  case_insensitive: bool = False) -> Optional[Dict[str, Any]]:
        """
        Retrieves a module record from the registry by its registered name.
        Args:
            module_name (str): The exact name of the module.
            case_insensitive (bool, optional): If True, performs a case-insensitive match.

        Returns:
            Optional[Dict[str, Any]]: The matching module record, or None if not found.
        """
        module_name = module_name.strip()
        if not module_name:
            raise ValueError("module name cannot be empty or whitespace")

        if case_insensitive:
            # Normalize keys and compare case-insensitively
            lowered = module_name.lower()
            for key, record in self._modules_registry.items():
                if key.lower() == lowered:
                    return record
            return None
        else:
            return self._modules_registry.get(module_name)

    def register_module(self, name: str, description: str,
                        class_name: Optional[str] = None,
                        class_instance: Optional[Any] = None,
                        class_interface: Optional[Any] = None,
                        auto_forge_module_type: Optional[AutoForgeModuleType] = AutoForgeModuleType.UNKNOWN,
                        python_module_type: Optional[ModuleType] = None,
                        version: Optional[str] = None,
                        file_name: Optional[str] = None,
                        auto_inspection: Optional[bool] = True) -> Optional[ModuleInfoType]:
        """
        Registers a module with the AutoForge system using explicit metadata arguments.
        Args:
            name (str): The name of the module.
            description (str): The description of the module.
            class_name (Optional[str]): The name of the class of the module.
            class_instance (Optional[Any]): The class instance of the module.
            class_interface (Optional[Any], optional): The interface instance of the class.
            auto_forge_module_type (Optional[ModuleType], optional): The AutoForge type of the module.
            python_module_type (Optional[ModuleType], optional): The Python type of the module.
            version (Optional[str], optional): The version of the module.
            file_name (Optional[str]): The file name of the module.
            auto_inspection (Optional[bool]): If True, performs auto inspection to get the r requited info.
        Returns:
            ModuleInfoType: if the module was successfully registered, exception otherwise.
        """

        # Inspect the caller's frame and extract runtime context

        caller_frame = inspect.currentframe().f_back
        caller_class_name = None
        caller_class_instance = None
        caller_class_interface = []
        caller_module_file_name = None
        caller_python_module_type = None

        if auto_inspection and caller_frame is not None:
            caller_locals = caller_frame.f_locals
            caller_self = caller_locals.get("self")

            if caller_self is not None:
                # Resolve caller class info
                caller_class_instance = cast(object, caller_self).__class__
                caller_class_name = caller_class_instance.__name__

                # Inspect base classes and filter ABCs
                caller_class_interface = [
                    base for base in inspect.getmro(caller_class_instance)[1:]  # skip the actual class itself
                    if isinstance(base, ABCMeta)
                ]

            # Resolve file and module
            caller_module_file_name = inspect.getfile(caller_frame)
            caller_python_module_type = inspect.getmodule(caller_frame)

        # Populate dynamic module info
        auto_forge_module_info: ModuleInfoType = ModuleInfoType(
            name=name,
            description=description,
            class_name=class_name or caller_class_name,
            class_instance=class_instance or caller_class_instance,
            auto_forge_module_type=auto_forge_module_type,
            python_module_type=python_module_type or caller_python_module_type,
            version=version or "0.0.0",
            class_interface=class_interface or caller_class_interface,
            file_name=file_name or caller_module_file_name,
        )

        return self.register_module_by_info(auto_forge_module_info)

    def register_module_by_info(self, auto_forge_module_info: ModuleInfoType) -> Optional[ModuleInfoType]:
        """
        Registers a module into the registry if it is not already registered.
        Args:
            auto_forge_module_info (ModuleInfoType): The module metadata to register.
        Returns:
            bool: True if registration succeeds or RuntimeError: If a module with the
            same name is already registered (case-insensitive).
        """

        if not auto_forge_module_info.name:
            raise RuntimeError(f"failed to register '{auto_forge_module_info.name}' module, mising module nam")

        if self.get_module_record_by_name(module_name=auto_forge_module_info.name, case_insensitive=True):
            raise RuntimeError(f"module '{auto_forge_module_info.name}' is already registered")

        self._modules_registry[auto_forge_module_info.name] = {
            "name_lower": auto_forge_module_info.name.lower(),
            "description": auto_forge_module_info.description,
            "class_name": auto_forge_module_info.class_name,
            "class_name_lower": auto_forge_module_info.class_name.lower() if auto_forge_module_info.class_name is not None else None,
            "class_instance": auto_forge_module_info.class_instance,
            "class_interface": auto_forge_module_info.class_interface,
            "auto_forge_module_type": auto_forge_module_info.auto_forge_module_type,
            "python_module_type": auto_forge_module_info.python_module_type,
            "version": auto_forge_module_info.version,
            "file_name": auto_forge_module_info.file_name,
        }
        return auto_forge_module_info
