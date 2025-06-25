"""
Script:         refactor_command.py
Author:         AutoForge Team

Description:
    Automate the process of refactoring path structure by relocating directories and files from one location to another
    based on specified configuration settings. It supports complex operations like filtering specific file
    types to copy, creating a 'graveyard' for unwanted files, and ensuring directory structure integrity up to a
    defined depth.

"""
import argparse
import logging
import os
import shutil
from dataclasses import dataclass
from logging import Logger
from typing import Any, Optional

# AutoForge imports
from auto_forge import (CommandInterface, CoreJSONCProcessor, CoreVariables, AutoForgCommandType, CoreToolBox)

AUTO_FORGE_MODULE_NAME = "refactor"
AUTO_FORGE_MODULE_DESCRIPTION = "Directory tree restructure assistant"
AUTO_FORGE_MODULE_VERSION = "1.2"


@dataclass
class _RefactorFolder:
    """
    Defines the expected JSON stored list of folders (essentially list of dictionaries)
    """
    description: Optional[str]
    source: str
    destination: str
    raw_source: str
    raw_destination: str
    file_types: list[str]
    create_grave_yard: bool
    max_copy_depth: int


@dataclass
class _RefactorDefaults:
    delete_destination_on_start: bool
    full_debug: bool
    file_types: list[str]
    create_grave_yard: bool
    max_copy_depth: int
    create_empty_cmake_file: bool
    break_on_errors: bool
    source_path: Optional[str] = None
    destination_path: Optional[str] = None


class _RefactorDefaultsRead:
    """
    Validates and normalizes raw JSON config for 'defaults' and returns a _RefactorDefaults instance.
    """

    @staticmethod
    def process(defaults_data: dict[str, Any]) -> _RefactorDefaults:
        if not isinstance(defaults_data, dict):
            raise TypeError("'defaults_data' must be a dictionary")

        # Validate file_types
        file_types = defaults_data.get("file_types", ["*"])
        if not isinstance(file_types, list):
            raise ValueError("'file_types' must be a list")

        # Construct dataclass instance
        return _RefactorDefaults(file_types=file_types,
                                 delete_destination_on_start=defaults_data.get("delete_destination_on_start", False),
                                 full_debug=defaults_data.get("full_debug", False),
                                 create_grave_yard=defaults_data.get("create_grave_yard", False),
                                 max_copy_depth=defaults_data.get("max_copy_depth", -1),
                                 create_empty_cmake_file=defaults_data.get("create_empty_cmake_file", False),
                                 break_on_errors=defaults_data.get("break_on_errors", False), )


class _RefactorFolderRead:
    """
    Validates a raw folder entry JSON entry and returns a clean _RefactorFolder.
    Applies defaults where needed.
    """

    @staticmethod
    def process(defaults: _RefactorDefaults, raw_folder_entry: dict[str, Any], logger: Logger) -> _RefactorFolder:
        if not isinstance(raw_folder_entry, dict):
            raise TypeError("Each folder entry must be a dictionary")

        raw_source = raw_folder_entry.get("source")
        raw_destination = raw_folder_entry.get("destination")

        if not isinstance(raw_source, str) or not isinstance(raw_source, str):
            raise KeyError("Both 'source' and 'destination' must be provided in each folder entry.")

        # Combine with the provided base paths
        source = os.path.join(defaults.source_path, raw_source)
        destination = os.path.join(defaults.destination_path, raw_destination)

        if not os.path.isdir(source) or not os.path.exists(source):
            if defaults.break_on_errors:
                raise FileNotFoundError(f"error '{raw_source} -> {raw_destination}': '{source}' does not exist")
            else:
                if logger:
                    logger = logging.getLogger("Refactor")
                    logger.warning(f"'{raw_source} -> {raw_destination}': '{source}' does not exist")

        file_types = raw_folder_entry.get("file_types", defaults.file_types)
        if not isinstance(file_types, list):
            raise ValueError("'file_types' must be a list if provided")

        file_types = ['*'] if '*' in file_types else file_types

        create_grave_yard = raw_folder_entry.get("create_grave_yard", defaults.create_grave_yard)
        max_copy_depth = raw_folder_entry.get("max_copy_depth", defaults.max_copy_depth)

        return _RefactorFolder(description=raw_folder_entry.get("description"), source=source, destination=destination,
                               file_types=file_types, create_grave_yard=create_grave_yard,
                               max_copy_depth=max_copy_depth, raw_source=raw_source, raw_destination=raw_destination, )


class RefactorCommand(CommandInterface):

    def __init__(self, **_kwargs: Any):
        """
        Initializes the RefactorCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments.
        """
        self._json_processor: CoreJSONCProcessor = CoreJSONCProcessor.get_instance()  # JSON preprocessor instance
        self._tool_box: CoreToolBox = CoreToolBox.get_instance()
        self._variables: CoreVariables = CoreVariables.get_instance()

        # Raw JSON data
        self._recipe_data: Optional[dict[str, Any]] = None  # Complete JSON raw data
        self._defaults_raw_data: Optional[dict[str, Any]] = None  # Defaults raw JSON data
        self._folders_raw_data: Optional[list[dict]] = None  # List of folders as raw JSON data

        # Deserialize data
        self._defaults: Optional[_RefactorDefaults] = None
        self._folders: Optional[list[_RefactorFolder]] = None
        self._folders_count: Optional[int] = 0

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME,
                         command_type=AutoForgCommandType.AUTOMATION, hidden=True)

    def _reset(self) -> None:
        """
        Resets the internal state of the object to its initial None/default values.
        """
        self._recipe_data = None
        self._defaults_raw_data = None
        self._folders_raw_data = None
        self._defaults = None
        self._folders = None
        self._folders_count = 0

    def _load_recipe(self, recipe_file: str, source_path: str, destination_path: str) -> None:
        """
        Load, parse, and validate the JSON recipe using JSONProcessorLib.
        Args:
            recipe_file (str): Path to the JSON recipe file.
            source_path (str): Path to the source folder.
            destination_path (str): Path to the destination folder.
        """
        try:
            # Reset all internal state before loading new data
            self._reset()

            # If the recipe file isn't found and the input appears to be a base name,
            # attempt to locate it in the local directory where relevant JSON files typically reside.
            if not os.path.exists(recipe_file) and (os.path.basename(recipe_file) == recipe_file):
                alternative_path = f"$SCRIPTS_SOLUTION/{recipe_file}"
                recipe_file = self._variables.expand(key=alternative_path)

            # Preprocess the JSON file (e.g., strip comments)
            self._recipe_data = self._json_processor.render(file_name=recipe_file)

            # Validate and parse 'defaults' section
            if "defaults" not in self._recipe_data:
                raise KeyError("missing 'defaults' section in the JSON recipe")

            self._defaults_raw_data = self._recipe_data["defaults"]
            self._defaults = _RefactorDefaultsRead.process(self._defaults_raw_data)

            # Add source and destination base paths
            self._defaults.source_path = source_path
            self._defaults.destination_path = destination_path

            # Set logger level if full debug is enabled
            if self._defaults.full_debug:
                self._logger.setLevel(logging.DEBUG)

            # Validate and parse 'folders' section
            self._folders_raw_data = self._recipe_data.get("folders", [])
            if not isinstance(self._folders_raw_data, list) or not self._folders_raw_data:
                raise KeyError("missing or invalid 'folders' section in JSON recipe")

            self._folders = [
                _RefactorFolderRead.process(defaults=self._defaults, raw_folder_entry=entry,
                                            logger=self._logger) for entry in self._folders_raw_data]
            self._folders_count = len(self._folders)

            # Inform the user if a feature is not implemented
            if self._defaults.create_empty_cmake_file:
                self._logger.warning("'create_empty_cmake_file' is not implemented")

            self._logger.debug(
                f"Recipe '{recipe_file}' loaded successfully: {self._folders_count} folders defined.")

        except Exception as load_error:
            # Re-raise the exception explicitly for future extension (e.g., logging or wrapping)
            raise load_error from load_error

    def _safe_copy_file(self, src_path: str, dest_path: str, relative_src: str, relative_dest: str,
                        is_source: bool = True, fatal=False, log_level="debug"):
        """
        Attempt to copy a file from src_path to dest_path, creating parent directories if needed.
        Logs the copy operation at the specified level and handles exceptions based on the 'fatal' flag.
        Args:
            src_path (str): Absolute source file path.
            dest_path (str): Absolute destination file path.
            relative_src (str): Source path relative to the base source folder (for logging).
            relative_dest (str): Destination path relative to the base destination folder (for logging).
            is_source (bool): Specifies if it's a source or grave-yard item,
            fatal (bool): If True, raises RuntimeError on failure. Otherwise, logs the error.
            log_level (str): Logging method to use for successful copies (e.g., 'debug', 'info').
        """
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        try:
            shutil.copy2(src_path, dest_path)
            file_name = os.path.basename(src_path)
            if is_source:
                getattr(self._logger, log_level)(f"> Copying '{file_name}'")
        except Exception as copy_error:
            msg = f"Failed to copy '{relative_src}' to '{relative_dest}' {copy_error}"
            if fatal:
                raise RuntimeError(msg)
            else:
                self._logger.error(msg)

    def _refactor(self, recipe_file: str, source_path: str, destination_path: str) -> Optional[int]:
        """
        Execute the recipe and reconstruct the destination tree accordingly.
        Args:
            recipe_file (str): Path to the recipe file.
            source_path (str): Base source path for path we would like to refactor.
            destination_path (str): Base source path the refactored output.
        Returns:
            bool: True if the operation was successful, False otherwise.
        """
        processed_folders_count = 0
        processed_files_count = 0

        try:

            # Check all are non-empty strings
            if not all(isinstance(x, str) for x in (recipe_file, source_path, destination_path)):
                raise ValueError("missing or invalid arguments: expected non-empty strings for "
                                 "recipe_file, source_path, and destination_path.")

            # Expand and validate
            recipe_file = self._variables.expand(recipe_file)
            source_path = self._variables.expand(source_path)
            destination_path = self._variables.expand(destination_path)

            # Validate that source_path is an existing directory
            if not os.path.isdir(source_path):
                raise NotADirectoryError(f"source path must be an existing directory: {source_path}")
            if not self._tool_box.looks_like_unix_path(destination_path):
                raise NotADirectoryError(f"destination path does not appear to look like a directory: {source_path}")
            if destination_path == source_path:
                raise RuntimeError("destination path must be different from source path")

            # Load and validate recipe; raises on error
            self._load_recipe(recipe_file=recipe_file, source_path=source_path, destination_path=destination_path)

            if not self._folders or not self._folders_count:
                raise RuntimeError("No folders found in the recipe to process.")

            # Handle deletion of existing destination directory
            if self._defaults.delete_destination_on_start:
                if os.path.exists(destination_path):
                    try:
                        self._logger.debug(f"Deleting existing destination: '{destination_path}'")
                        # Safe erase
                        self._tool_box.safe_erase_path(target_path=destination_path, force=True)
                    except Exception as exception:
                        raise RuntimeError(f"failed to delete destination directory: {exception}") from exception
            else:
                if os.path.exists(destination_path):
                    raise RuntimeError(f"destination '{destination_path}' already exists.")

            print(f"\nStarting refactoring process for {len(self._folders)} paths..")

            # Recreate destination
            os.makedirs(destination_path, exist_ok=True)

            # Process each folder entry
            for folder in self._folders:
                processed_folders_count += 1
                depth_from_root = 0
                os.makedirs(folder.destination, exist_ok=True)
                self._logger.info(f"Processing '{folder.raw_source}' -> '{folder.raw_destination}'")

                max_depth = folder.max_copy_depth
                base_level = folder.source.count(os.sep)

                for root, _, files in os.walk(folder.source):
                    copied_files_count = 0
                    copied_graveyard_files_count = 0
                    depth_from_root = depth_from_root + 1
                    current_depth = root.count(os.sep) - base_level

                    relative_source_root = os.path.relpath(root, self._defaults.source_path)

                    if depth_from_root > 1:
                        self._logger.info(f"> Processing '{relative_source_root}'")

                    if max_depth != -1 and current_depth > max_depth:
                        raise RuntimeError(f"exceeded maximum copy depth ({max_depth}) at '{root}'")

                    for file in files:
                        src_path = os.path.join(root, file)
                        relative_src = os.path.relpath(src_path, self._defaults.source_path)
                        relative_path = os.path.relpath(root, folder.source)

                        # Files we have to copy
                        if '*' in folder.file_types or any(
                                file.endswith(f".{ft}") for ft in folder.file_types if ft != '*'):
                            dest_path = os.path.join(folder.destination, relative_path, file)
                            relative_dest = os.path.relpath(dest_path, self._defaults.destination_path)
                            self._safe_copy_file(src_path=src_path, dest_path=dest_path, relative_src=relative_src,
                                                 relative_dest=relative_dest, is_source=True,
                                                 fatal=self._defaults.break_on_errors)
                            copied_files_count += 1

                        # Not part of the list, see if we have a garve yard
                        elif folder.create_grave_yard:
                            graveyard_path = os.path.join(folder.destination, "grave_yard", relative_path, file)
                            relative_dest = os.path.relpath(graveyard_path, self._defaults.destination_path)
                            self._safe_copy_file(src_path=src_path, dest_path=graveyard_path, relative_src=relative_src,
                                                 relative_dest=relative_dest, is_source=False,
                                                 fatal=self._defaults.break_on_errors, log_level="debug")
                            copied_graveyard_files_count = copied_graveyard_files_count + 1

                    processed_files_count = processed_files_count + (copied_files_count + copied_graveyard_files_count)
                    self._logger.info(
                        f"Total {copied_files_count} files copied and {copied_graveyard_files_count} sent to graveyard.")

            print(f"Done, total {processed_files_count} files in {processed_folders_count} paths ware processed.\n")
            return 0

        except Exception as refactoring_error:
            # Re-raise for upstream error handling; can be enhanced for logging
            raise RuntimeError(f"{refactoring_error} @ #{processed_folders_count}") from refactoring_error

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """

        parser.add_argument("-r", "--recipe", type=str, help="Path to a refactor JSON recipe file.")
        parser.add_argument("-s", "--source-path", help="Source path to be refactored.")
        parser.add_argument("-d", "--destination", help="Destination path for the structured project.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:
            args (argparse.Namespace): The parsed arguments.
        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """

        refactor_args_group = [args.recipe, args.source_path, args.destination]
        if any(refactor_args_group) and not all(refactor_args_group):
            raise RuntimeError(
                "Incomplete input: if any of the following options are provided --recipe, --source-path, "
                "or --destination, then all of them must be specified."
            )

        # Handle the refactoring operation
        if any(refactor_args_group):
            return self._refactor(recipe_file=args.recipe, source_path=args.source_path,
                                  destination_path=args.destination)
        else:
            return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
