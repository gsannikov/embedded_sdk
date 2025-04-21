"""
Script:         relocator_command.py
Author:         AutoForge Team

Description:
    AutoForge command for automating the process of relocating directories and files from one location to another
    based on specified configuration settings. It supports complex operations like filtering specific file
    types to copy, creating a 'graveyard' for unwanted files, and ensuring directory structure integrity up to a
    defined depth.

"""
import argparse
import logging
import os
import shutil
from typing import Optional, Any, Dict, List

# AutoForge imports
from auto_forge import (Processor, CLICommandInterface, AutoLogger)

AUTO_FORGE_COMMAND_NAME = "relocator"
AUTO_FORGE_COMMAND_DESCRIPTION = "Code restructure assistant"
AUTO_FORGE_COMMAND_VERSION = "1.0"


class _RelocateDefaults:
    """
    Private auxiliary class for managing default relocation related attributes
    """

    def __init__(self, defaults_config: Dict[str, Any]):
        """
        Initialize the RelocateDefaults class with configuration settings.

        Args:
            defaults_config (Dict[str, Any]): A dictionary containing configuration settings.
        Raises:
            TypeError: If the input is not a dictionary.
            ValueError: If essential paths are not defined or file_types is not a list.
            FileNotFoundError: If the base source path does not exist.
            RuntimeError: For other initialization errors.
        """

        if not isinstance(defaults_config, dict):
            raise TypeError("'defaults_config' must be a dictionary")

        self.base_source_path: str = os.path.expandvars(os.path.expanduser(defaults_config.get('base_source_path', "")))
        self.base_destination_path: str = os.path.expandvars(
            os.path.expanduser(defaults_config.get('base_destination_path', "")))

        if not self.base_source_path or not self.base_destination_path:
            raise ValueError("'base_source_path' and 'base_destination_path' must be defined and non-empty")

        # Check if the source path is a real directory
        if not os.path.isdir(self.base_source_path):
            # Try to see if 'base_source_path' is located in our current path
            relative_path = os.path.join(os.getcwd(), self.base_source_path)
            if not os.path.isdir(relative_path):
                raise FileNotFoundError(f"Base source path does not exist: '{self.base_source_path}'")
            else:
                # Update base source and base destination paths to the resolved relative path
                self.base_source_path = relative_path
                self.base_destination_path = os.path.join(os.getcwd(), self.base_destination_path)

        # Validate file_types to ensure it is a list
        file_types = defaults_config.get('file_types', ['*'])
        if not isinstance(file_types, list):
            raise ValueError("file_types must be a list")
        self.file_types: Optional[list] = file_types

        # Reset opf the attributes
        self.delete_destination_on_start: bool = defaults_config.get('delete_destination_on_start', False)
        self.full_debug: bool = defaults_config.get('full_debug', False)
        self.create_grave_yard: bool = defaults_config.get('create_grave_yard', False)
        self.max_copy_depth: int = defaults_config.get('max_copy_depth', -1)
        self.create_empty_cmake_file: bool = defaults_config.get('create_empty_cmake_file', False)


class _RelocatedFolder:
    """
    Private auxiliary class for managing a single relocated folder.
    """

    def __init__(self, defaults: '_RelocateDefaults', folder_config: Dict[str, Any]):
        """
        Initializes a RelocatedFolder instance using default settings and folder-specific overrides.
        Args:
            defaults (_RelocateDefaults): An instance of RelocateDefaults providing default settings.
            folder_config (Dict[str, Any]): A dictionary containing the configuration for a specific folder,
                                            which may override the defaults.
        Raises:
            KeyError: If essential keys like 'source' or 'destination' are missing from the combined configuration.
        """
        # Use defaults if keys are not specified in folder_config
        self.description: Optional[str] = folder_config.get('description', None)
        self.source: str = os.path.join(defaults.base_source_path, folder_config['source'])
        self.destination: str = os.path.join(defaults.base_destination_path, folder_config['destination'])

        # Initialize file_types where if we have '*' , it will be converted to the only item
        file_types = folder_config.get('file_types', defaults.file_types)
        if '*' in file_types:
            self.file_types = ['*']
        else:
            self.file_types = file_types

        # Validate that mandatory fields are provided
        if 'source' not in folder_config or 'destination' not in folder_config:
            raise KeyError("Both 'source' and 'destination' fields must be provided in folder_config.")

        # Initialize other properties from defaults if not overridden
        self.create_grave_yard: bool = folder_config.get('create_grave_yard', defaults.create_grave_yard)
        self.max_copy_depth: int = folder_config.get('max_copy_depth', defaults.max_copy_depth)
        self.create_empty_cmake_file: bool = folder_config.get('create_empty_cmake_file',
                                                               defaults.create_empty_cmake_file)


class RelocatorCommand(CLICommandInterface):

    def __init__(self, **kwargs: Any):
        """
        Initializes the RelocatorCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments:
                - raise_exceptions (bool): Whether to raise exceptions on error instead of returning codes.
        """

        self._json_processor: Processor = Processor()  # Class instance
        self._recipe_data: Optional[Dict[str, Any]] = None  # To store processed json data
        self._relocate_defaults: Optional[_RelocateDefaults] = None
        self._relocate_folders_data: Optional[List[str, Any]] = None
        self._relocate_folders_count: Optional[int] = 0

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_COMMAND_NAME)

        # Extract optional parameters
        raise_exceptions: bool = kwargs.get('raise_exceptions', False)

        # Base class initialization
        super().__init__(name=AUTO_FORGE_COMMAND_NAME,
                         description=AUTO_FORGE_COMMAND_DESCRIPTION,
                         version=AUTO_FORGE_COMMAND_VERSION,
                         raise_exceptions=raise_exceptions)

    def _load_recipe(self, recipe_file: str):
        """
        Load parse and validate a relocation recipe from a JSON file using JSONProcessorLib.
        Args:
            recipe_file (str): Path to the JSON recipe file. The file may contain comments, which will be
                removed prior to parsing.
        """

        try:

            # Start fresh
            self._recipe_data: Optional[Dict[str, Any]] = None  # To store processed json data
            self._relocate_defaults: Optional[_RelocateDefaults] = None
            self._relocate_folders_data: Optional[List[str, Any]] = None
            self._relocate_folders_count: Optional[int] = 0

            # JSON preprocess
            self._recipe_data = self._json_processor.preprocess(file_name=recipe_file)

            if 'defaults' not in self._recipe_data:
                raise KeyError("missing 'defaults' section in JSON recipe.")
            self._relocate_defaults = _RelocateDefaults(self._recipe_data['defaults'])

            # Sets the logger to debug if specified
            if self._relocate_defaults.full_debug:
                self._logger.setLevel(logging.DEBUG)

            # Load the folders list and make sure we got something sensible
            self._relocate_folders_data = self._recipe_data.get('folders', None)
            if self._relocate_folders_data is None or not isinstance(self._relocate_folders_data, list) or len(
                    self._relocate_folders_data) < 1:
                raise KeyError("missing or invalid 'folders' section in JSON recipe.")

            # Create a list of RelocatedFolder instances based on the raw dictionary
            self._relocated_folders: List[_RelocatedFolder] = [
                _RelocatedFolder(self._relocate_defaults, folder_config) for folder_config in
                self._relocate_folders_data
            ]

            self._relocate_folders_count = len(self._relocate_folders_data)

            # Be nice
            if self._relocate_defaults.create_empty_cmake_file:
                self._logger.warning("Sorry,'create_empty_cmake_file' is not yet coded :)")

            self._logger.debug(f"Recipe '{recipe_file}' loaded, total {self._relocate_folders_count} folders defined")

        # Forwarded the exception
        except Exception:
            raise

    def _relocate(self, **kwargs: Any) -> bool:
        """
        Follow the loaded recipe file and build a reconstructed tree accordingly.
        Args:
            **kwargs (Any): Optional keyword arguments:
                - recipe_file (str): JSON AutoForge recipe file reqwired for the reconstruction.
        """

        try:

            recipe_file: Optional[str] = kwargs.get("recipe_file", None)
            if not recipe_file or not isinstance(recipe_file, str):
                raise KeyError("reqwired argument 'recipe_file' is missing or invalid")

            # Load and process the
            self._load_recipe(recipe_file)

            if not self._relocated_folders or not self._relocate_folders_count:
                raise RuntimeError("relocate' has not been initialized with any folders")

            graveyard_path: Optional[str] = None

            # Check and handle the deletion of the destination directory tree
            if self._relocate_defaults.delete_destination_on_start:
                # Check if the base destination path exists and is a directory
                if os.path.exists(self._relocate_defaults.base_destination_path):
                    try:
                        # Remove the entire directory tree
                        shutil.rmtree(self._relocate_defaults.base_destination_path)
                        self._logger.debug(
                            f"Deleted existing destination directory: '{self._relocate_defaults.base_destination_path}'")
                    except Exception as clear_path_error:
                        raise RuntimeError(f"failed to delete the destination directory: {str(clear_path_error)}")
            else:
                # Check if the base destination path exists and is a directory
                if os.path.exists(self._relocate_defaults.base_destination_path):
                    raise RuntimeError(f"destination '{self._relocate_defaults.base_destination_path}' already exists.")

            # Ensure the base destination directory is recreated
            os.makedirs(self._relocate_defaults.base_destination_path, exist_ok=True)

            for folder in self._relocated_folders:
                os.makedirs(folder.destination, exist_ok=True)
                self._logger.debug(f"Processing folder from {folder.source} to {folder.destination}")

                # Prepare 'graveyard' directory if needed
                if folder.create_grave_yard:
                    graveyard_path = os.path.join(folder.destination, "grave_yard")
                    os.makedirs(graveyard_path, exist_ok=True)

                max_depth = folder.max_copy_depth
                base_level = folder.source.count(os.sep)

                for root, dirs, files in os.walk(folder.source):
                    current_depth = root.count(os.sep) - base_level
                    if max_depth != -1 and current_depth > max_depth:
                        raise RuntimeError(f"exceeded maximum copy depth ({max_depth}) at '{root}'")

                    for file in files:

                        src_path = os.path.join(root, file)

                        if '*' in folder.file_types or any(file.endswith(ft) for ft in folder.file_types if ft != '*'):
                            dest_path = os.path.join(folder.destination, os.path.relpath(root, folder.source), file)
                            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                            shutil.copy2(src_path, dest_path)
                            self._logger.debug(f"{src_path} -> {dest_path}")

                        elif folder.create_grave_yard:
                            # Move files that do not match file types into the graveyard
                            graveyard_file_path = os.path.join(graveyard_path, os.path.relpath(root, folder.source),
                                                               file)
                            os.makedirs(os.path.dirname(graveyard_file_path), exist_ok=True)
                            shutil.move(src_path, graveyard_file_path)
                            self._logger.debug(f"{src_path} -> {graveyard_file_path}")

            self._logger.debug("All folders processed successfully")
            return True

        # Propagate
        except Exception:
            raise

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        parser.add_argument("-r", "--recipe", type=str, help="Path to a relocator JSON recipe file.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:
            args (argparse.Namespace): The parsed CLI arguments.
        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """
        return_value: int = 0

        # Handle arguments
        if args.recipe:
            self._relocate(recipe_file=args.recipe)
        else:
            return_value = CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_value
