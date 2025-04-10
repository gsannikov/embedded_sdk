#!/usr/bin/env python3
"""
Script:         __init__.py
Author:         Intel AutoForge team

Description:
    This module serves as the centralized import hub for the AutoForge application, managing the import of essential
    modules and configurations. It is critical not to reorganize the import order
    automatically (e.g., by IDE tools like PyCharm) as the sequence may impact application behavior due to
    dependencies and initialization order required by certain components.
"""

# Main module imports must not be optimized by PyCharm, order  does matter here.
# noinspection PyUnresolvedReferences
from .settings import (PROJECT_BASE_PATH, PROJECT_CONFIG_PATH, PROJECT_RESOURCES_PATH, PROJECT_SCHEMAS_PATH,
                       PROJECT_VERSION, PROJECT_NAME, PROJECT_REPO, PROJECT_PACKAGE)

from auto_forge.logger import logger, logger_setup, logger_get_filename, logger_close, NullLogger
from auto_forge.common.progress_tracker import ProgressTracker
from auto_forge.common.toolbox import ToolBox
from auto_forge.core.json_processor import JSONProcessor
from auto_forge.core.variables import Variables
from auto_forge.core.binary_signatures import (Signatures, SignatureFileHandler, Signature,
                                               SignatureField, SignatureSchema)
from auto_forge.core.relocate import Relocator
from auto_forge.core.solution_processor import SolutionProcessor
from auto_forge.core.west_world import WestWorld
from auto_forge.core.setup_tools import (SetupTools)


from auto_forge.auto_forge import AutoForge, auto_forge_main as main

# Exported symbols
__all__ = [
    "ToolBox",
    "ProgressTracker",
    "JSONProcessor",
    "Variables",
    "SolutionProcessor",
    "SetupTools",
    "Relocator",
    "Signatures",
    "WestWorld",
    "SignatureFileHandler",
    "Signature",
    "SignatureField",
    "SignatureSchema",
    "AutoForge",
    "PROJECT_BASE_PATH",
    "PROJECT_CONFIG_PATH",
    "PROJECT_RESOURCES_PATH",
    "PROJECT_SCHEMAS_PATH",
    "PROJECT_VERSION",
    "PROJECT_NAME",
    "PROJECT_REPO",
    "PROJECT_PACKAGE",
    "NullLogger",
    "logger",
    "logger_setup",
    "logger_close",
    "logger_get_filename",
    "main"
]
