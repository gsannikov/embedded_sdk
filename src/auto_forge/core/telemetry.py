"""
Script:         telemetry.py
Author:         AutoForge Team

Description:
    CoreTelemetry is responsible for initializing and managing application-level telemetry.
    It sets up OpenTelemetry tracing, records application startup time, and provides a
    shared tracer for use across all modules.
"""
import threading
import time
from typing import Optional, Any

from opentelemetry import metrics
# Third-party
# Telemetry
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider, Meter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Tracer, TracerProvider as SDKTracerProvider

# AutoForge imports
from auto_forge import (AutoForgeModuleType, CoreModuleInterface, CoreRegistry)
# Borrowing just the bare minium rom the package
from auto_forge.settings import PROJECT_NAME

AUTO_FORGE_MODULE_NAME = "Telemetry"
AUTO_FORGE_MODULE_DESCRIPTION = "Package telemetry and tracer provider"


# ------------------------------------------------------------------------------
#
# Note:
#   This module is used during early initialization and must remain self-contained.
#   Avoid importing any project-specific code or third-party libraries to ensure
#   portability and prevent circular import issues.
#
# ------------------------------------------------------------------------------


class TelemetryTrackedCounter:
    """
    A wrapper around an OpenTelemetry counter that tracks its cumulative value in-process
    for local diagnostics (e.g., CLI, show panel).
    """

    def __init__(self, counter, name: str, unit: str = "1", description: str = ""):
        self._counter = counter
        self._value = 0
        self.name = name
        self.unit = unit
        self.description = description

    def add(self, amount: int = 1):
        self._counter.add(amount)
        self._value += amount

    @property
    def value(self) -> int:
        return self._value


class CoreTelemetry(CoreModuleInterface):
    """
    CoreTelemetry initializes and manages application-level telemetry:
    - Sets up OpenTelemetry tracing
    - Records initial start timestamp
    - Provides shared access to tracer
    - Allows future expansion to metrics/exporters
    Note : CoreModuleInterface: forces single tone and exposes the .get_instance() method to any interested module.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        self._lock: threading.Lock = threading.Lock()
        self._tracing_started: bool = False
        self._tracer: Optional[Tracer] = None
        self._start_perf: float = time.perf_counter()
        self._start_unix: float = time.time()
        self._service_name: Optional[str] = None

        # Metrics providers
        self._metrics_started: bool = False
        self._meter: Optional[Meter] = None

        # Allow to better monitor counters from external module
        self._registered_counters = []
        self._registered_boot_events = {}

        super().__init__(*args, **kwargs)

    def _initialize(self, service_name: Optional[str] = None) -> None:
        """
        Initializes the CoreTelemetry service.
        This method should be called once during startup.
        """
        self._service_name = service_name if service_name else PROJECT_NAME

        # Register this module with the package registry
        registry = CoreRegistry.get_instance()
        registry.register_module(
            name=AUTO_FORGE_MODULE_NAME,
            description=AUTO_FORGE_MODULE_DESCRIPTION,
            auto_forge_module_type=AutoForgeModuleType.CORE,
        )

        # Start tracing and metrics
        self._init_tracing()
        self._init_metrics()

        # Inform ourself that we're up & running.
        self.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

    def _init_tracing(self):
        """
        Initializes the OpenTelemetry tracer with an in-memory setup.
        No span exporters are registered, so spans are created and consumed internally only.
        """
        with self._lock:
            if self._tracing_started:
                return

            # Set up the tracer provider with a service identity
            provider = SDKTracerProvider(
                resource=Resource.create({
                    "service.name": self._service_name or PROJECT_NAME
                })
            )

            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self._service_name or PROJECT_NAME)
            self._tracing_started = True

    def _init_metrics(self):
        """ In-memory reader (we won't export; it's just required by the API) """

        with self._lock:
            if self._metrics_started:
                return

            reader = InMemoryMetricReader()
            provider = MeterProvider(metric_readers=[reader])
            metrics.set_meter_provider(provider)
            self._meter = metrics.get_meter(self._service_name or PROJECT_NAME)
            self._metrics_started = True

    def _register_counter(self, counter: Any):
        """
        Registers a telemetry counter for tracking or display purposes.
        Raises:
            ValueError: If a counter with the same name has already been registered.
        """

        counter_name = getattr(counter, "name", None)
        if not counter_name:
            raise ValueError("Invalid counter object: missing 'name' attribute.")

        for existing in self._registered_counters:
            if getattr(existing, "name", None) == counter_name:
                raise ValueError(f"A counter named '{counter_name}' has already been registered.")

        self._registered_counters.append(counter)

    def create_counter(self, name: str, unit: str = "1", description: str = "") -> TelemetryTrackedCounter:
        """
        Creates a 'TelemetryTrackedCounter'' (in-memory + OpenTelemetry counter) and registers it.
        Args:
            name (str): Counter name (must be unique).
            unit (str): Optional unit of measure.
            description (str): Optional human-readable description.
        Returns:
            TelemetryTrackedCounter: A wrapped counter with local state and .add() support.
        """
        if not self._meter:
            raise RuntimeError("Telemetry meter is not initialized.")

        counter = self._meter.create_counter(name=name, unit=unit, description=description)
        tracked = TelemetryTrackedCounter(counter, name=name, unit=unit, description=description)
        self._register_counter(tracked)
        return tracked

    def get_counter_value(self, name: str) -> Optional[int]:
        """
        Returns the current value of a registered counter by name.
        Args:
            name (str): The name of the counter to query.
        Returns:
            int or None: The counter's current value, or None if not found.
        """
        for counter in self._registered_counters:
            if getattr(counter, "name", None) == name:
                return getattr(counter, "value", None)
        return None

    def elapsed_since_start(self) -> float:
        """Returns elapsed time in seconds since CoreTelemetry was created (high-resolution)."""
        return time.perf_counter() - self._start_perf

    def start_span(self, name: str, **attributes):
        """
        Starts a new telemetry span with optional key/value attributes.
        Args:
            name (str): Name of the span.
            **attributes: Key/value tags to add to the span.
        Returns:
            A context manager that ends the span automatically on exit.
        """
        span = self._tracer.start_span(name)
        for key, value in attributes.items():
            span.set_attribute(key, value)
        return trace.use_span(span, end_on_exit=True)

    def mark_module_boot(self, module_name: str):
        """
        Records a span for a module boot, tagged with the delay from application start.
        Args:
            module_name (str): Identifier of the module that finished booting.
        """
        delay = self.elapsed_since_start()
        self._registered_boot_events[module_name] = delay

        with self.start_span(f"startup.{module_name}", delay=self.elapsed_since_start()):
            pass

    @property
    def tracer(self) -> Optional[Tracer]:
        """Returns the shared OpenTelemetry tracer instance."""
        return self._tracer

    @property
    def meter(self) -> Optional[Meter]:
        """Returns the shared OpenTelemetry meter instance."""
        return self._meter

    @property
    def start_perf(self) -> float:
        """Returns the high-resolution timer at telemetry initialization."""
        return self._start_perf

    @property
    def service_name(self) -> Optional[str]:
        """ Returns telemetry configured service name. """
        return self._service_name

    @property
    def start_unix(self) -> float:
        """Returns the UNIX timestamp at telemetry initialization."""
        return self._start_unix

    @property
    def registered_counters(self) -> list:
        """Returns a list of all registered counters (externally added)."""
        return self._registered_counters

    @property
    def registered_boot_events(self) -> dict:
        """ Returns registered monitored boot events """
        return self._registered_boot_events
