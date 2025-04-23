"""
Script:         core_module_interface.py
Author:         AutoForge Team

Description:
    Abstract base class for singleton-style core modules with structured
    one-time initialization logic via `_initialize()`.

Developer Guidelines:
    Subclasses should define their own `__init__()` if they need to:
      - Declare instance attributes for clarity and type-checking tools
      - Pre-bind fields to `None` (or defaults) to avoid "attribute-defined-outside-init" warnings
      - Maintain consistent and readable object structure

    __init__()` in this base class ensures `_initialize()` is called only once, even if the class is instantiated
        multiple times via `get_instance()`.

    Use `_initialize(*args, **kwargs)` to:
      - Implement one-time setup logic that should only happen during the first instantiation
      - Accept and process constructor arguments (e.g., configuration paths, modes)
      - Assign runtime values to attributes declared earlier in `__init__()`

    Summary:
        - Use `__init__()` for *attribute declaration (optional)*
        - Use `_initialize()` for *first-time setup logic*

    Example:
        class MyModule(CoreModuleInterface):
            def __init__(self, *args, **kwargs):
                self._solution: Optional[Solution] = None
                super().__init__(*args, **kwargs)

            def _initialize(self, config_path: str):
                self._solution = load_solution(config_path)

"""

from abc import ABCMeta
from typing import Optional, cast, TypeVar, Type, TYPE_CHECKING, Dict, Any, Tuple

# Import AutoForge only during static type checking to avoid circular import issues at runtime
if TYPE_CHECKING:
    from auto_forge.auto_forge import AutoForge

# Generic type variable used to represent subclasses of CoreModuleInterface
T = TypeVar("T", bound="CoreModuleInterface")

# Global singleton reference to the root AutoForge instance
_ENGINE_ROOT: Optional["AutoForge"] = None


class _SingletonABCMeta(ABCMeta):
    """
    Internal metaclass that enforces singleton behavior for abstract base classes (ABCs).

    Usage Notes:
        This metaclass is intended only for internal use by `CoreModuleInterface` and its subclasses.
        It should not be reused or subclassed directly outside the framework core.
    """

    _instances: Dict[Type, Any] = {}
    _init_args: Dict[Type, Tuple[Tuple[Any, ...], Dict[str, Any]]] = {}
    _is_initialized: bool = False

    def __call__(cls, *args, **kwargs):
        """
        Returns the singleton instance for the class, creating it on first call.
        """
        if cls not in cls._instances:
            cls._init_args[cls] = (args, kwargs)
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]

    @classmethod
    def get_instance_for(cls, subclass: Type[T]) -> Optional[T]:
        return cls._instances.get(subclass)


class CoreModuleInterface(metaclass=_SingletonABCMeta):
    """
    Abstract base class that enforces singleton pattern and provides a clean
    interface for core module implementations in AutoForge.
    """

    def __init__(self, *args, **kwargs) -> None:
        """
        Ensures `_initialize()` is only called once and validates that the
        AutoForge engine is already initialized for dependent modules.
        """
        if getattr(self, "_is_initialized", False):
            return

        global _ENGINE_ROOT

        # Register AutoForge root once during its own construction
        if type(self).__name__ == "AutoForge":
            _ENGINE_ROOT = cast("AutoForge", self)
        else:
            if _ENGINE_ROOT is None:
                raise RuntimeError("AutoForge must be instantiated before any core module.")

        self._initialize(*args, **kwargs)
        self._is_initialized = True

    def _initialize(self, *args, **kwargs) -> None:
        """
        One-time setup hook for singleton instances.
        Override this method in subclasses to perform custom init using arguments passed on first instantiation.
        """
        pass

    @property
    def auto_forge(self) -> "AutoForge":
        """
        Provides access to the globally registered AutoForge engine.
        """
        global _ENGINE_ROOT
        return _ENGINE_ROOT

    @classmethod
    def get_instance(cls: Type[T]) -> Optional[T]:
        """
        Returns the existing singleton instance of the class if it was created,
        or `None` if the class was never explicitly instantiated.
        Returns:
            Optional[T]: The existing singleton instance, or None.
        """
        return _SingletonABCMeta.get_instance_for(cls)
