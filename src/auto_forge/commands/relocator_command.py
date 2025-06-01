"""
Script:         relocator_command.py
Author:         AutoForge Team

Description:
    Automate the process of relocating directories and files from one location to another
    based on specified configuration settings. It supports complex operations like filtering specific file
    types to copy, creating a 'graveyard' for unwanted files, and ensuring directory structure integrity up to a
    defined depth.

"""
import argparse
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any, Optional

# AutoForge imports
from auto_forge import (CLICommandInterface, CoreProcessor, CoreVariables, AutoForgCommandType, ToolBox)

AUTO_FORGE_MODULE_NAME = "relocator"
AUTO_FORGE_MODULE_DESCRIPTION = "Code restructure assistant"
AUTO_FORGE_MODULE_VERSION = "1.0"


@dataclass
class _RelocateFolder:
    """
    Defines the expected JSON stored list of folders (essentially list of dictionaries)
    """
    description: Optional[str]
    source: str
    destination: str
    file_types: list[str]


@dataclass
class _RelocateDefaults:
    delete_destination_on_start: bool
    full_debug: bool
    file_types: list[str]
    create_grave_yard: bool
    max_copy_depth: int
    create_empty_cmake_file: bool


class _RelocateDefaultsRead:
    """
    Validates and normalizes raw JSON config for 'defaults' and returns a _RelocateDefaults instance.
    """

    @staticmethod
    def process(defaults_data: dict[str, Any]) -> _RelocateDefaults:
        if not isinstance(defaults_data, dict):
            raise TypeError("'defaults_data' must be a dictionary")

        # Validate file_types
        file_types = defaults_data.get("file_types", ["*"])
        if not isinstance(file_types, list):
            raise ValueError("'file_types' must be a list")

        # Construct dataclass instance
        return _RelocateDefaults(file_types=file_types,
                                 delete_destination_on_start=defaults_data.get("delete_destination_on_start", False),
                                 full_debug=defaults_data.get("full_debug", False),
                                 create_grave_yard=defaults_data.get("create_grave_yard", False),
                                 max_copy_depth=defaults_data.get("max_copy_depth", -1),
                                 create_empty_cmake_file=defaults_data.get("create_empty_cmake_file", False), )


class _RelocateFolderRead:
    """
    Validates a raw folder entry JSON entry and returns a clean _RelocateFolder.
    Applies defaults where needed.
    """

    @staticmethod
    def process(defaults: _RelocateDefaults, raw_folder_entry: dict[str, Any]) -> _RelocateFolder:
        if not isinstance(raw_folder_entry, dict):
            raise TypeError("Each folder entry must be a dictionary")

        source = raw_folder_entry.get("source")
        destination = raw_folder_entry.get("destination")

        if not isinstance(destination, str) or not isinstance(source, str):
            raise KeyError("Both 'source' and 'destination' must be provided in each folder entry.")

        file_types = raw_folder_entry.get("file_types", defaults.file_types)
        if not isinstance(file_types, list):
            raise ValueError("'file_types' must be a list if provided")

        file_types = ['*'] if '*' in file_types else file_types

        return _RelocateFolder(description=raw_folder_entry.get("description"), source=source, destination=destination,
                               file_types=file_types, )


class RelocatorCommand(CLICommandInterface):

    def __init__(self, **kwargs: Any):
        """
        Initializes the RelocatorCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments:
                - raise_exceptions (bool): Whether to raise exceptions on error instead of returning codes.
        """

        self._json_processor: CoreProcessor = CoreProcessor.get_instance()  # JSON preprocessor instance
        self._tool_box: ToolBox = ToolBox.get_instance()
        self._variables: CoreVariables = CoreVariables.get_instance()

        # Raw JSON data
        self._recipe_data: Optional[dict[str, Any]] = None  # Complete JSON raw data
        self._relocate_defaults_data: Optional[dict[str, Any]] = None  # Defaults raw JSON data
        self._relocate_folders_data: Optional[list[dict]] = None  # List of folders as raw JSON data

        # Deserialize data
        self._relocate_defaults: Optional[_RelocateDefaults] = None
        self._relocated_folders: Optional[list[_RelocateFolder]] = None
        self._relocate_folders_count: Optional[int] = 0

        # Extract optional parameters
        raise_exceptions: bool = kwargs.get('raise_exceptions', False)

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, raise_exceptions=raise_exceptions,
                         command_type=AutoForgCommandType.AUTOMATION)

    def _reset(self) -> None:
        """
        Resets the internal state of the object to its initial None/default values.
        """
        self._recipe_data = None
        self._relocate_defaults_data = None
        self._relocate_folders_data = None
        self._relocate_defaults = None
        self._relocated_folders = None
        self._relocate_folders_count = 0

    def _load_recipe(self, recipe_file: str) -> None:
        """
        Load, parse, and validate a relocation recipe from a JSON file using JSONProcessorLib.
        Args:
            recipe_file (str): Path to the JSON recipe file. The file may contain comments,
                               which will be removed prior to parsing.
        """
        try:
            # Reset all internal state before loading new data
            self._reset()

            # Preprocess the JSON file (e.g., strip comments)
            self._recipe_data = self._json_processor.preprocess(file_name=recipe_file)

            # Validate and parse 'defaults' section
            if "defaults" not in self._recipe_data:
                raise KeyError("missing 'defaults' section in JSON recipe")

            self._relocate_defaults_data = self._recipe_data["defaults"]
            self._relocate_defaults = _RelocateDefaultsRead.process(self._relocate_defaults_data)

            # Set logger level if full debug is enabled
            if self._relocate_defaults.full_debug:
                self._logger.setLevel(logging.DEBUG)

            # Validate and parse 'folders' section
            self._relocate_folders_data = self._recipe_data.get("folders", [])
            if not isinstance(self._relocate_folders_data, list) or not self._relocate_folders_data:
                raise KeyError("missing or invalid 'folders' section in JSON recipe")

            self._relocated_folders = [_RelocateFolderRead.process(self._relocate_defaults, entry) for entry in
                                       self._relocate_folders_data]
            self._relocate_folders_count = len(self._relocated_folders)

            # Inform the user if a feature is not implemented
            if self._relocate_defaults.create_empty_cmake_file:
                self._logger.warning("'create_empty_cmake_file' is not implemented")

            self._logger.debug(
                f"Recipe '{recipe_file}' loaded successfully: {self._relocate_folders_count} folders defined.")

        except Exception as load_error:
            # Re-raise the exception explicitly for future extension (e.g., logging or wrapping)
            raise load_error from load_error

    def _relocate(self, **kwargs: Any) -> bool:
        """
        Execute the loaded relocation recipe and reconstruct the destination tree accordingly.
        Args:
            **kwargs: Optional keyword arguments:
                - recipe_file (str): Path to the JSON recipe file.
                - source_path (str): Source path to process.
                - destination path (str): Destination path to generate.
        Returns:
            bool: True if relocation was successful, False otherwise.
        """
        try:
            source_path: Optional[str] = kwargs.get("source_path")
            destination_path: Optional[str] = kwargs.get("recipe_file")
            recipe_file: Optional[str] = kwargs.get("recipe_file")

            # Check all are non-empty strings
            if not all(isinstance(x, str) for x in (recipe_file, source_path, destination_path)):
                raise ValueError("missing or invalid arguments: expected non-empty strings for "
                                 "recipe_file, source_path, and destination_path.")

            # Expand
            source_path = self._variables.expand(source_path)
            destination_path = self._variables.expand(destination_path)
            recipe_file = self._variables.expand(recipe_file)

            # Validate that source_path is an existing directory
            if not os.path.isdir(source_path):
                raise NotADirectoryError(f"source path must be an existing directory: {source_path}")
            if not self._tool_box.looks_like_unix_path(destination_path):
                raise NotADirectoryError(f"destination path does not appear to look like a directory: {source_path}")

            # Load and validate recipe; raises on error
            self._load_recipe(recipe_file)

            if not self._relocated_folders or not self._relocate_folders_count:
                raise RuntimeError("No folders found in the recipe to process.")

            graveyard_path: Optional[str] = None

            # Handle deletion of existing destination directory
            if self._relocate_defaults.delete_destination_on_start:
                if os.path.exists(destination_path):
                    try:
                        shutil.rmtree(destination_path)
                        self._logger.debug(f"deleted existing destination directory: '{destination_path}'")
                    except Exception as exception:
                        raise RuntimeError(f"failed to delete destination directory: {exception}") from exception
            else:
                if os.path.exists(destination_path):
                    raise RuntimeError(f"destination '{destination_path}' already exists.")

            # Recreate destination
            os.makedirs(destination_path, exist_ok=True)

            # Process each folder entry
            for folder in self._relocated_folders:
                os.makedirs(folder.destination, exist_ok=True)
                self._logger.debug(f"Processing folder from {folder.source} to {folder.destination}")

                # Create graveyard directory if needed
                if folder.create_grave_yard:
                    graveyard_path = os.path.join(folder.destination, "grave_yard")
                    os.makedirs(graveyard_path, exist_ok=True)

                max_depth = folder.max_copy_depth
                base_level = folder.source.count(os.sep)

                for root, _dirs, files in os.walk(folder.source):
                    current_depth = root.count(os.sep) - base_level
                    if max_depth != -1 and current_depth > max_depth:
                        raise RuntimeError(f"exceeded maximum copy depth ({max_depth}) at '{root}'")

                    for file in files:
                        src_path = os.path.join(root, file)
                        relative_path = os.path.relpath(root, folder.source)

                        # Determine if file matches allowed file types
                        if '*' in folder.file_types or any(file.endswith(ft) for ft in folder.file_types if ft != '*'):
                            dest_path = os.path.join(folder.destination, relative_path, file)
                            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                            shutil.copy2(src_path, dest_path)
                            self._logger.debug(f"{src_path} -> {dest_path}")

                        elif folder.create_grave_yard:
                            # Move unmatched file to graveyard
                            graveyard_file_path = os.path.join(graveyard_path, relative_path, file)
                            os.makedirs(os.path.dirname(graveyard_file_path), exist_ok=True)
                            shutil.move(src_path, graveyard_file_path)
                            self._logger.debug(f"{src_path} -> {graveyard_file_path}")

            self._logger.debug("All folders processed successfully.")
            return True

        except Exception as relocate_error:
            # Re-raise for upstream error handling; can be enhanced for logging
            raise relocate_error from relocate_error

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        parser.add_argument("-r", "--recipe", type=str, help="Path to a relocator JSON recipe file.")
        parser.add_argument("-s", "--source_path",
                            help="Source path containing the structure we would like to refactor")
        parser.add_argument("-d", "--destination",
                            help="Destination path which the new structured project will be created in")

        parser.add_argument("-t", "--tutorial", action="store_true", help="Show the relocator tool tutorial.")

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
        if args.tutorial:
            self._tool_box.show_help_file(help_file_relative_path='commands/relocator.md')
        else:

            # Check if all three required arguments are present
            missing = [arg for arg, value in {'--recipe': args.recipe, '--source_path': args.source_path,
                                              '--destination': args.destination}.items() if value is None]

            if missing:
                print(f"\nError: missing required arguments: {', '.join(missing)}")
            else:
                print("OK")

        return return_value
