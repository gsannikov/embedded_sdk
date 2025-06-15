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
import inspect
import threading
import time
from abc import ABCMeta
from typing import TYPE_CHECKING, Any, ClassVar, Optional, TypeVar, cast

# Third-party
from colorama import Fore, Style

# Import AutoForge only during static type checking to avoid circular import issues at runtime
from auto_forge import ExceptionGuru

if TYPE_CHECKING:
    # noinspection PyUnusedImports
    from auto_forge.auto_forge import AutoForge

# Generic type variable used to represent subclasses of CoreModuleInterface
T = TypeVar("T", bound="CoreModuleInterface")

# Global singleton reference to the root AutoForge instance
_CORE_AUTO_FORGE_ROOT: Optional["AutoForge"] = None
# Helps to guard against nested exceptions
_CORE_EXCEPTIONS_COUNT: Optional[int] = 0


class _SingletonABCMeta(ABCMeta):
    """
    Internal metaclass that enforces singleton behavior for abstract base classes (ABCs).

    Usage Notes:
        This metaclass is intended only for internal use by `CoreModuleInterface` and its subclasses.
        It should not be reused or subclassed directly outside the framework core.
    """

    _instances: ClassVar[dict[type, Any]] = {}
    _init_args: ClassVar[dict[type, tuple[tuple[Any, ...], dict[str, Any]]]] = {}
    _ready_event: ClassVar[dict[type, threading.Event]] = {}

    def __call__(cls, *args, **kwargs):
        """
        Returns the singleton instance for the class, creating it on first call.
        """
        if cls not in cls._instances:
            cls._init_args[cls] = (args, kwargs)
            cls._ready_event[cls] = threading.Event()  # Class readiness event

            # Instantiate, potentially execute implementation '__init__' if exists.
            instance = super().__call__(*args, **kwargs)

            cls._instances[cls] = instance
        return cls._instances[cls]

    @classmethod
    def wait_until_ready(cls, subclass: type[T], timeout: Optional[float] = 5) -> bool:
        """
        Waits for a given subclass (e.g., CoreGUI) to be initialized and ready.
        Args:
            subclass (Type[T]): The subclass to wait for.
            timeout (Optional[float]): Maximum wait time in seconds (default: 5).
        Returns:
            bool: True if ready, False if timeout reached.
        """
        poll_interval: float = 0.1
        deadline = time.time() + timeout if timeout is not None else None

        while True:
            instance = cls._instances.get(subclass)
            if instance:
                ready_event = cls._ready_event.get(subclass)
                if isinstance(ready_event, threading.Event):
                    remaining = (deadline - time.time()) if deadline else None
                    if ready_event.wait(timeout=remaining):
                        return True
                    return False  # Timed out waiting

            if deadline and time.time() >= deadline:
                return False  # Timed out before instance appeared

            time.sleep(poll_interval)

    @classmethod
    def mark_ready(cls, subclass: type[T]) -> None:
        """
        Marks the singleton instance of the given subclass as ready.
        """
        event = cls._ready_event.get(subclass)
        if isinstance(event, threading.Event):
            event.set()

    @classmethod
    def get_instance_for(cls, subclass: type[T]) -> Optional[T]:
        """
        Returns the singleton instance for the given subclass, if initialized.
        Args:
            subclass (Type[T]): The class for which the singleton is requested.
        Returns:
            Optional[T]: The instance, or None if not yet created.
        """
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

        global _CORE_AUTO_FORGE_ROOT, _CORE_EXCEPTIONS_COUNT

        # Register AutoForge root once during its own construction
        core_module_name: str = type(self).__name__
        self._is_initialized = False

        try:
            if core_module_name == "AutoForge":
                _CORE_AUTO_FORGE_ROOT = cast("AutoForge", self)
            else:
                if _CORE_AUTO_FORGE_ROOT is None:
                    raise RuntimeError("AutoForge must be instantiated before any core module.")

            # Properties extracted from the module code
            caller_frame = inspect.stack()[1].frame
            caller_globals = caller_frame.f_globals
            self.module_config_file_name = caller_globals.get("AUTO_FORGE_MODULE_CONFIG_FILE", None)

            # Preform core specific initialization
            self._initialize(*args, **kwargs)

            self.mark_ready()
            self._is_initialized = True

        except Exception as core_exception:
            if _CORE_EXCEPTIONS_COUNT < 1:
                _CORE_EXCEPTIONS_COUNT = _CORE_EXCEPTIONS_COUNT + 1

                # Store the exception context in the exception guru utility class.
                ExceptionGuru()
                raise RuntimeError(f"Core module '{core_module_name}': {core_exception!s}") from core_exception
            else:
                raise

    def _initialize(self, *args, **kwargs) -> None:
        """
        One-time setup hook for singleton instances.
        Override this method in subclasses to perform custom init using arguments passed on first instantiation.
        """
        pass

    @staticmethod
    def who_we_are() -> str:
        """`
        Returns the decorated AutoForge branding string.
        Includes colored segments and an emoji.
        """
        return f"{Fore.LIGHTBLUE_EX}Auto{Fore.LIGHTWHITE_EX}🛠️Forge{Style.RESET_ALL}"

    @property
    def auto_forge(self) -> "AutoForge":
        """
        Provides access to the globally registered AutoForge engine.
        """
        global _CORE_AUTO_FORGE_ROOT
        return _CORE_AUTO_FORGE_ROOT

    @classmethod
    def get_instance(cls: type[T]) -> Optional[T]:
        """
        Returns the existing singleton instance of the class if it was created,
        or `None` if the class was never explicitly instantiated.
        Returns:
            Optional[T]: The existing singleton instance, or None.
        """
        return _SingletonABCMeta.get_instance_for(cls)

    @classmethod
    def wait_until_ready(cls, timeout: Optional[float] = 5) -> bool:
        """
        Waits until this module is fully initialized and ready.
        Delegates to the internal singleton system.
        """
        return _SingletonABCMeta.wait_until_ready(cls, timeout)

    @classmethod
    def mark_ready(cls) -> None:
        """
        Marks this module as fully initialized and ready.
        Delegates to the internal singleton system.
        """
        return _SingletonABCMeta.mark_ready(cls)
