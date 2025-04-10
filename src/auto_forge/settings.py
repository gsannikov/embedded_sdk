"""
Script:         settings.py
Author:         Intel AutoForge team
Description:    Configuration script that retrieves project information from the
                pyproject.toml file and sets global variables for project version
                and name.
"""
import importlib.metadata
import os
from contextlib import suppress
from pathlib import Path

import toml

# Determine the base directory of the project
PROJECT_BASE_PATH = Path(__file__).resolve().parent
PROJECT_CONFIG_PATH = PROJECT_BASE_PATH / "config"
PROJECT_RESOURCES_PATH = PROJECT_BASE_PATH / "resources"
PROJECT_SCHEMAS_PATH = PROJECT_CONFIG_PATH / "schemas"
PROJECT_PACKAGE_BASE_PATH = Path(__file__).resolve().parent.parent.parent

# Initialize default values for global variables
PROJECT_VERSION = "1.0"
PROJECT_NAME = "AutoForge"
PROJECT_REPO = "https://github.com/intel-innersource/firmware.ethernet.imcv2/tree/main/scripts/auto_forge"
PROJECT_PACKAGE = "auto_forge"


def auto_forge_get_info(base_path: Path):
    """
    Retrieves project parameters such as version and name from the pyproject.toml file
    located in the base directory of the auto_forge project.
    Args:
        base_path (str): The base directory path where pyproject.toml is located.
    Returns:
        bool: True if the project information was successfully retrieved and globals updated,
              False otherwise.
    """
    global PROJECT_VERSION, PROJECT_NAME, PROJECT_REPO, PROJECT_PACKAGE

    # Suppress all exceptions derived from Exception class to prevent crash
    with suppress(Exception):
        # Construct the full path to the pyproject.toml file
        toml_path = base_path / "pyproject.toml"

        PROJECT_VERSION = importlib.metadata.version(PROJECT_PACKAGE)

        # Export paths so we could use those in our configuration files
        os.environ['AUTO_FORGE_PROJECT_BASE_PATH'] = str(PROJECT_BASE_PATH)
        os.environ['AUTO_FORGE_PROJECT_PACKAGE_BASE_PATH'] = str(PROJECT_PACKAGE_BASE_PATH)
        os.environ['AUTO_FORGE_PROJECT_CONFIG_PATH'] = str(PROJECT_CONFIG_PATH)
        os.environ['AUTO_FORGE_PROJECT_RESOURCES_PATH'] = str(PROJECT_RESOURCES_PATH)
        os.environ['AUTO_FORGE_PROJECT_SCHEMAS_PATH'] = str(PROJECT_SCHEMAS_PATH)

        # Try to open and load the TOML file
        with open(toml_path, "r") as toml_file:
            data = toml.load(toml_file)
            # Update global variables with data from the TOML file
            PROJECT_VERSION = data.get('project', {}).get('version', '0.0')
            PROJECT_PACKAGE = data.get('project', {}).get('name', 'Unknown')
            PROJECT_REPO = (data.get('project', {}).get('urls', {}).get('repository', "Unknown"))
            PROJECT_NAME = (data.get('tool', {}).get('autoforge_metadata', {}).get('fancy_name', "Unknown"))


# Attempt to update global defaults based on the module's TOML file
auto_forge_get_info(PROJECT_PACKAGE_BASE_PATH)
