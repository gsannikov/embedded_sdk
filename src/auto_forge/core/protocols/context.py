"""
Script:         context.py
Author:         AutoForge Team

Description:
    Central context manager that, together with protocol classes, facilitates global access to
    methods and properties across the package—without requiring direct instantiation of the data provider class.
"""

from contextvars import ContextVar

# AutoForge imports
from auto_forge import HasConfigurationProtocol


class CoreContext:
    """
    Central context manager for AutoForge runtime state.
    Handles thread/async-safe storage of configuration and other core providers.
    """
    _config_ctx: ContextVar[HasConfigurationProtocol] = ContextVar("_config_ctx")

    @classmethod
    def set_config_provider(cls, provider: HasConfigurationProtocol) -> None:
        cls._config_ctx.set(provider)

    @classmethod
    def get_config_provider(cls) -> HasConfigurationProtocol:
        return cls._config_ctx.get()
