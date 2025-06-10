"""
Script:         registry.py
Author:         AutoForge Team

Description:
    Common module enabling dynamic registration of modules within AutoForge.
    It also provides functionality to resolve class and method names into callable
    objects, allowing AutoForge to dynamically invoke functionality based on
    user-provided JSON files.
"""

import inspect
from abc import ABCMeta
from types import ModuleType
from typing import Any, Optional, cast

# AutoForge imports
from auto_forge import AutoForgeModuleType, CoreModuleInterface, ModuleInfoType, AutoForgCommandType
from auto_forge.common import protocols

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
        self._modules_registry: dict[str, dict[str, Any]] = {}

        # Register self
        self._module_info: ModuleInfoType = (
            self.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.COMMON))

    def _find_record(self, value: str, key: Optional[str] = None) -> Optional[dict[str, Any]]:
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

    @staticmethod
    def _resolve_method_case_insensitive(obj: object, method_name: str) -> Optional[callable]:
        """
        Resolves a method on an object by case-insensitive name.
        Args:
            obj (object): The object to inspect.
            method_name (str): The case-insensitive method name.
        Returns:
            Callable: The resolved method.
        """
        method_name_lower = method_name.lower()
        for attr in dir(obj):
            if attr.lower() == method_name_lower:
                candidate = getattr(obj, attr)
                if callable(candidate):
                    return candidate
                else:
                    raise AttributeError(f"'{attr}' exists on object but is not callable.")

        raise AttributeError(f"no method matching '{method_name}' found (case-insensitive).")

    def _get_module_info(self, module_name: str) -> Optional[ModuleInfoType]:
        """
        Gets a module stored registry record as an ModuleInfoType
        Args:
            module_name (str): The exact name of the module.
        Returns:
            ModuleInfoType: The constructed module info object when the module was found.
        """

        record = self._modules_registry.get(module_name)
        if not record:
            raise RuntimeError(f"module '{module_name}' not found in registry")

        return ModuleInfoType(name=module_name, description=record.get("description"),
                              class_name=record.get("class_name"), class_instance=record.get("class_instance"),
                              auto_forge_module_type=record.get("auto_forge_module_type"),
                              python_module_type=record.get("python_module_type"), version=record.get("version"),
                              class_interface_name=record.get("class_interface_name"),
                              file_name=record.get("file_name"), hidden=record.get("hidden"),
                              command_type=record.get("command_type"))

    def get_modules_list(self, auto_forge_module_type=AutoForgeModuleType.UNKNOWN) -> list[ModuleInfoType]:
        """
        Returns a list of full module info objects that match the specified module type.
        Args:
            auto_forge_module_type (AutoForgeModuleType): The type of modules to filter by.
        Returns:
            list[ModuleInfoType]: A list of full module info entries.
        """
        return [module_info for name, meta in self._modules_registry.items() if (module_info := self._get_module_info(
            name)) is not None and module_info.auto_forge_module_type == auto_forge_module_type]

    def update_module_record(self, module_name: str, **updates: Any) -> Optional[ModuleInfoType]:
        """
        Updates one or more fields of a module record in the registry.
        Args:
            module_name (str): The name of the module to update.
            **updates: Arbitrary key-value pairs to update in the module record.
        Returns:
            ModuleInfoType (optional): the updated record convert to ModuleInfoType, or exception on error.
        """

        record = self._modules_registry.get(module_name)
        if not record:
            raise RuntimeError(f"module '{module_name}' not found in registry")

        # Validate all keys exist before applying any updates
        invalid_keys = [k for k in updates if k not in record]
        if invalid_keys:
            raise RuntimeError(f"invalid update key(s) for module '{module_name}': {invalid_keys}")

        record.update(updates)
        return self._get_module_info(module_name=module_name)

    def get_module_record_by_name(self, module_name: str, case_insensitive: bool = False) -> Optional[dict[str, Any]]:
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

    def get_instance_by_class_name(
            self,
            class_name: str,
            case_insensitive: bool = False,
            return_protocol: bool = False
    ) -> Optional[Any]:
        """
        Searches the registry for a specific class instance by its class name.
        Args:
            class_name (str): Name of class to search for.
            case_insensitive (bool): If True, performs a case-insensitive match.
            return_protocol (bool): If True, cast the result to the corresponding protocol class,
                                    assuming it exists in protocols.py and follows the '<ClassName>Protocol' convention.
        Returns:
            Optional[Any]: The matching class instance, possibly cast to its protocol type, or None if not found.
        """
        class_name = class_name.strip() if isinstance(class_name, str) else class_name
        if not class_name:
            raise ValueError("class name cannot be non-string or empty")

        for module in self._modules_registry.values():
            registered_name = module.get("class_name")
            if not registered_name:
                continue

            match = (
                registered_name.lower() == class_name.lower()
                if case_insensitive else
                registered_name == class_name
            )

            if match:
                instance = module.get("class_instance")
                if not return_protocol:
                    return instance

                try:
                    protocol_name = f"{registered_name}Protocol"
                    protocol_type = getattr(protocols, protocol_name, None)

                    if protocol_type is None:
                        raise ImportError(f"protocol class '{protocol_name}' not found in protocols module")

                    return cast(protocol_type, instance)  # type: ignore[arg-type]
                except Exception as cast_error:
                    raise RuntimeError(f"failed to cast class instance to protocol: {cast_error}") from cast_error

        return None

    def register_module(self, name: str, description: str, class_name: Optional[str] = None,
                        class_instance: Optional[Any] = None, class_interface_name: Optional[str] = None,
                        auto_forge_module_type: Optional[AutoForgeModuleType] = AutoForgeModuleType.UNKNOWN,
                        python_module_type: Optional[ModuleType] = None, version: Optional[str] = None,
                        file_name: Optional[str] = None, hidden: Optional[bool] = False,
                        command_type: Optional[AutoForgCommandType] = AutoForgCommandType.UNKNOWN,
                        auto_inspection: Optional[bool] = True) -> Optional[ModuleInfoType]:
        """
        Registers a module with the AutoForge system using explicit metadata arguments.
        Args:
            name (str): The name of the module.
            description (str): The description of the module.
            class_name (Optional[str]): The name of the class of the module.
            class_instance (Optional[Any]): The class instance of the module.
            class_interface_name (Optional[str], optional): The interface class name of the class if any.
            auto_forge_module_type (Optional[ModuleType], optional): The AutoForge type of the module.
            python_module_type (Optional[ModuleType], optional): The Python type of the module.
            version (Optional[str], optional): The version of the module.
            file_name (Optional[str]): The file name of the module.
            hidden (Optional[bool]): Attributes, applicable for dynamic commands.
            command_type (Optional[AutoForgCommandType], optional): The command type of the module.
            auto_inspection (Optional[bool]): If True, performs auto inspection to get the required info.
        Returns:
            ModuleInfoType: if the module was successfully registered, exception otherwise.
        """

        # Inspect the caller's frame and extract runtime context

        caller_frame = inspect.currentframe().f_back
        caller_class_name = None
        caller_class_instance = None
        caller_class_interfaces: Optional[list] = None
        caller_class_interface_name = None
        caller_module_file_name = None
        caller_python_module_type = None

        if auto_inspection and caller_frame is not None:
            caller_locals = caller_frame.f_locals
            caller_self = caller_locals.get("self")

            if caller_self is not None:
                # Resolve caller class info
                caller_class_instance = cast(object, caller_self)
                caller_class_name = cast(object, caller_self).__class__.__name__
                caller_class_object = cast(object, caller_self).__class__

                # Inspect base classes and filter ABCs
                caller_class_interfaces: Optional[list] = [base for base in inspect.getmro(caller_class_object)[1:]
                                                           # skip the actual class itself
                                                           if isinstance(base, ABCMeta)]

            # Resolve file and module
            if caller_class_interfaces and len(caller_class_interfaces) > 0:
                caller_class_interface = cast(object, caller_class_interfaces[0])
                caller_class_interface_name = cast(object, caller_class_interface).__name__

            caller_module_file_name = inspect.getfile(caller_frame)
            caller_python_module_type = inspect.getmodule(caller_frame)

        # Populate dynamic module info
        auto_forge_module_info: ModuleInfoType = ModuleInfoType(name=name, description=description,
                                                                class_name=class_name or caller_class_name,
                                                                class_instance=class_instance or caller_class_instance,
                                                                auto_forge_module_type=auto_forge_module_type,
                                                                python_module_type=python_module_type or caller_python_module_type,
                                                                version=version or "0.0.0",
                                                                class_interface_name=class_interface_name or caller_class_interface_name,
                                                                file_name=file_name or caller_module_file_name,
                                                                hidden=hidden if hidden is not None else False,
                                                                command_type=command_type, )

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

        self._modules_registry[auto_forge_module_info.name] = {"name_lower": auto_forge_module_info.name.lower(),
                                                               "description": auto_forge_module_info.description,
                                                               "class_name": auto_forge_module_info.class_name,
                                                               "class_name_lower": auto_forge_module_info.class_name.lower() if auto_forge_module_info.class_name is not None else None,
                                                               "class_instance": auto_forge_module_info.class_instance,
                                                               "class_interface_name": auto_forge_module_info.class_interface_name,
                                                               "auto_forge_module_type": auto_forge_module_info.auto_forge_module_type,
                                                               "python_module_type": auto_forge_module_info.python_module_type,
                                                               "version": auto_forge_module_info.version,
                                                               "file_name": auto_forge_module_info.file_name,
                                                               "hidden": auto_forge_module_info.hidden,
                                                               "command_type": auto_forge_module_info.command_type, }
        return auto_forge_module_info

    def find_callable_method(self, flat_method_name: str) -> Optional[callable]:
        """
        Resolves a class and method name expressed as a flat string (e.g., 'some_class.method_name')
        into an actual callable belonging to one of the registered class instances.
        Args:
            flat_method_name (str): A dot-separated string in the form 'class_name.method_name'.
        Returns:
            Optional[callable]: The resolved method if found. Raises an exception on failure.
        """

        parts = flat_method_name.strip().split('.')
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"invalid format: '{flat_method_name}'. expected 'class.method'.")

        class_name, method_name = parts
        class_name = class_name.lower().strip()
        method_name = method_name.lower().strip()

        # Locate the record by matching the normalized class name
        record = self._find_record(value=class_name, key="class_name_lower")
        if record is None:
            raise RuntimeError(f"failed to find class '{class_name}'.")

        class_instance = record.get("class_instance")
        if class_instance is None:
            raise RuntimeError(f"missing instance for class '{class_name}'.")

        method = self._resolve_method_case_insensitive(class_instance, method_name)
        if method is None:
            raise RuntimeError(f"method '{method_name}' not found in class '{class_name}'.")

        return method
