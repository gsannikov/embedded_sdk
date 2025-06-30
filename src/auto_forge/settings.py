"""
Script:         settings.py
Author:         AutoForge Team

Description:
    Populate the 'PackageGlobals' class which holds project-wide constants such as version,
    repository, name, and filesystem paths derived from pyproject.toml and the source layout.
    All attributes are class-level and can be accessed without instantiation.
"""
import os
import re
import sys
import time
from importlib.metadata import metadata, version
from pathlib import Path
from typing import Optional


# Third-party


class PackageGlobals:
    """
    Singleton-style global container for package metadata and paths constants.
    """

    _instance = None  # Singleton enforcement

    VERSION: Optional[str] = None
    PROJ_NAME: Optional[str] = None
    REPO: Optional[str] = None
    NAME: Optional[str] = None
    TEMP_PREFIX: Optional[str] = None

    PACKAGE_PATH: Optional[Path] = None  # Package path
    SOURCE_PATH: Optional[Path] = None  # Package sources (within the package)
    CONFIG_PATH: Optional[Path] = None
    CONFIG_FILE: Optional[Path] = None
    COMMANDS_PATH: Optional[Path] = None
    BUILDERS_PATH: Optional[Path] = None
    RESOURCES_PATH: Optional[Path] = None
    SHARED_PATH: Optional[Path] = None
    SAMPLES_PATH: Optional[Path] = None
    HELP_PATH: Optional[Path] = None
    VIEWERS_PATH: Optional[Path] = None
    SCHEMAS_PATH: Optional[Path] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PackageGlobals, cls).__new__(cls)
            cls.populate()
            cls.export()
        return cls._instance

    # Try to walk up until you find pyproject.toml (workspace mode)
    @staticmethod
    def snake_to_pascal(s: str) -> str:
        return re.sub(r'(?:^|_)(\w)', lambda m: m.group(1).upper(), s)

    @classmethod
    def populate(cls) -> Optional[bool]:
        """Populate class-level project settings from metadata and pyproject.toml."""
        try:
            package_path = Path(__file__).resolve().parent
            package_name = __package__ or sys.modules[__name__].__package__
            cls.PACKAGE_PATH = package_path

            project_data = metadata(package_name)
            cls.VERSION = version(package_name)
            cls.PROJ_NAME = project_data.get("Name")
            cls.REPO = project_data.get("Home-page")
            cls.NAME = cls.snake_to_pascal(s=cls.PROJ_NAME)
            cls.TEMP_PREFIX = f"__{cls.NAME}_" if cls.NAME else None

            base = Path(__file__).resolve().parent
            print(f"PACKAGE_PATH={cls.PACKAGE_PATH}, PROJ_NAME ={cls.PROJ_NAME },NAME={cls.NAME}")
            time.sleep(3)

            cls.SOURCE_PATH = base
            cls.CONFIG_PATH = base / "config"
            cls.CONFIG_FILE = cls.CONFIG_PATH / "auto_forge.jsonc"
            cls.COMMANDS_PATH = base / "commands"
            cls.BUILDERS_PATH = base / "builders"
            cls.RESOURCES_PATH = base / "resources"
            cls.SHARED_PATH = cls.RESOURCES_PATH / "shared"
            cls.SAMPLES_PATH = cls.RESOURCES_PATH / "samples"
            cls.HELP_PATH = cls.RESOURCES_PATH / "help"
            cls.VIEWERS_PATH = cls.SHARED_PATH / "viewers"
            cls.SCHEMAS_PATH = cls.CONFIG_PATH / "schemas"
            return True

        except Exception as e:
            print(f"Failed to populate project globals : {str(e)}", file=sys.stderr)

    @classmethod
    def to_dict(cls) -> dict:
        """
        Export all uppercase global attributes to a dictionary.
        Returns:
            dict: A dictionary containing all global project configuration values.
        """
        return {
            str(k): str(getattr(cls, k)) if getattr(cls, k) is not None else ""
            for k in vars(cls)
            if k.isupper() and not k.startswith("__")
        }

    @classmethod
    def export(cls) -> None:
        """
        Export all uppercase global attributes to the environment as string key-value pairs.
        Existing environment variables with the same name will be overwritten.
        """
        for key, value in cls.to_dict().items():
            os.environ[key] = value


# Singleton instance
_ = PackageGlobals()
