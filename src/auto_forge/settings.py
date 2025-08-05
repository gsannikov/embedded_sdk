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
import uuid
from contextlib import suppress
from importlib.metadata import metadata, version
from pathlib import Path
from typing import Optional


class PackageGlobals:
    """
    Singleton-style global container for package metadata and paths constants.
    """
    _instance = None  # Singleton enforcement
    NAME: Optional[str] = None  # Pascal case: 'AutoForge'
    PROJ_NAME: Optional[str] = None  # Snake case: 'auto_forge'
    REPO: Optional[str] = None
    VERSION: Optional[str] = None
    TEMP_PREFIX: Optional[str] = None
    PACKAGE_PATH: Optional[Path] = None  # Package path
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
    EDITABLE: Optional[bool] = True
    SESSION_ID:Optional[str] = None
    SPAWNED: Optional[bool] = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PackageGlobals, cls).__new__(cls)
            cls._populate()
            cls._export()
        return cls._instance

    # Try to walk up until you find pyproject.toml (workspace mode)
    @staticmethod
    def _snake_to_pascal(s: str) -> str:
        """ Convert snake case to pascal case """
        return re.sub(r'(?:^|_)(\w)', lambda m: m.group(1).upper(), s)

    @staticmethod
    def _get_project_url(package_name: str, key: str = "repository") -> Optional[str]:
        """ Extract a URL from the package metadata"""
        with suppress(Exception):
            meta = metadata(package_name)
            project_urls = meta.get_all("Project-URL") or []

            for entry in project_urls:
                if re.match(rf"{key}\s*,", entry, re.IGNORECASE):
                    # Format: "repository, https://url"
                    return entry.split(",", 1)[1].strip()
        return None

    @classmethod
    def _populate(cls) -> Optional[bool]:
        """Populate class-level project settings from metadata and pyproject.toml."""
        try:
            package_path = Path(__file__).resolve().parent
            package_name = __package__ or sys.modules[__name__].__package__
            project_data = metadata(package_name)

            if "site-packages" in str(package_path):
                cls.EDITABLE = False

            # Assume we are spawned by a parent AutoForge instance if the SESSION_ID is already exported
            if os.environ.get('PACKAGE_SESSION_ID'):
                cls.SPAWNED = True

            cls.SESSION_ID = str(uuid.uuid4())
            cls.PACKAGE_PATH = package_path
            cls.VERSION = version(package_name)
            cls.PROJ_NAME = project_data.get("Name")
            cls.REPO = cls._get_project_url("auto_forge", "repository")
            cls.NAME = cls._snake_to_pascal(s=cls.PROJ_NAME)
            cls.TEMP_PREFIX = f"__{cls.NAME}_" if cls.NAME else None
            cls.CONFIG_PATH = cls.PACKAGE_PATH / "config"
            cls.CONFIG_FILE = cls.CONFIG_PATH / "auto_forge.jsonc"
            cls.COMMANDS_PATH = cls.PACKAGE_PATH / "commands"
            cls.BUILDERS_PATH = cls.PACKAGE_PATH / "builders"
            cls.RESOURCES_PATH = cls.PACKAGE_PATH / "resources"
            cls.SHARED_PATH = cls.RESOURCES_PATH / "shared"
            cls.SAMPLES_PATH = cls.RESOURCES_PATH / "samples"
            cls.HELP_PATH = cls.RESOURCES_PATH / "help"
            cls.VIEWERS_PATH = cls.SHARED_PATH / "viewers"
            cls.SCHEMAS_PATH = cls.CONFIG_PATH / "schemas"
            return True

        except Exception as exception:
            print(f"Failed to populate project globals : {str(exception)}", file=sys.stderr)

    @classmethod
    def _export(cls) -> None:
        """
        Export all uppercase global attributes to the environment as string key-value pairs.
        Existing environment variables with the same name will be overwritten.
        """
        for key, value in cls.to_dict().items():
            os.environ[key] = value

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


# Singleton instance
_ = PackageGlobals()
