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
from logging import Logger
from typing import Any, Optional

# AutoForge imports
from auto_forge import (CLICommandInterface, CoreProcessor, CoreVariables, AutoForgCommandType, ToolBox, AutoLogger)

AUTO_FORGE_MODULE_NAME = "relocator"
AUTO_FORGE_MODULE_DESCRIPTION = "Code restructure assistant"
AUTO_FORGE_MODULE_VERSION = "1.1"


@dataclass
class _RelocateFolder:
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
class _RelocateDefaults:
    delete_destination_on_start: bool
    full_debug: bool
    file_types: list[str]
    create_grave_yard: bool
    max_copy_depth: int
    create_empty_cmake_file: bool
    break_on_errors: bool
    source_path: Optional[str] = None
    destination_path: Optional[str] = None


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
                                 create_empty_cmake_file=defaults_data.get("create_empty_cmake_file", False),
                                 break_on_errors=defaults_data.get("break_on_errors", False), )


class _RelocateFolderRead:
    """
    Validates a raw folder entry JSON entry and returns a clean _RelocateFolder.
    Applies defaults where needed.
    """

    @staticmethod
    def process(defaults: _RelocateDefaults, raw_folder_entry: dict[str, Any], logger: Logger) -> _RelocateFolder:
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
                    logger = logging.getLogger("Relocator")
                    logger.warning(f"'{raw_source} -> {raw_destination}': '{source}' does not exist")

        file_types = raw_folder_entry.get("file_types", defaults.file_types)
        if not isinstance(file_types, list):
            raise ValueError("'file_types' must be a list if provided")

        file_types = ['*'] if '*' in file_types else file_types

        create_grave_yard = raw_folder_entry.get("create_grave_yard", defaults.create_grave_yard)
        max_copy_depth = raw_folder_entry.get("max_copy_depth", defaults.max_copy_depth)

        return _RelocateFolder(description=raw_folder_entry.get("description"), source=source, destination=destination,
                               file_types=file_types, create_grave_yard=create_grave_yard,
                               max_copy_depth=max_copy_depth, raw_source=raw_source, raw_destination=raw_destination, )


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

        # Get a logger instance
        self._logger: Logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME.capitalize())

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

    def _load_recipe(self, recipe_file: str, source_path: str, destination_path: str) -> None:
        """
        Load, parse, and validate a relocation recipe from a JSON file using JSONProcessorLib.
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
            self._recipe_data = self._json_processor.preprocess(file_name=recipe_file)

            # Validate and parse 'defaults' section
            if "defaults" not in self._recipe_data:
                raise KeyError("missing 'defaults' section in relocator JSON recipe")

            self._relocate_defaults_data = self._recipe_data["defaults"]
            self._relocate_defaults = _RelocateDefaultsRead.process(self._relocate_defaults_data)

            # Add source and destination base paths
            self._relocate_defaults.source_path = source_path
            self._relocate_defaults.destination_path = destination_path

            # Set logger level if full debug is enabled
            if self._relocate_defaults.full_debug:
                self._logger.setLevel(logging.DEBUG)

            # Validate and parse 'folders' section
            self._relocate_folders_data = self._recipe_data.get("folders", [])
            if not isinstance(self._relocate_folders_data, list) or not self._relocate_folders_data:
                raise KeyError("missing or invalid 'folders' section in JSON recipe")

            self._relocated_folders = [
                _RelocateFolderRead.process(defaults=self._relocate_defaults, raw_folder_entry=entry,
                                            logger=self._logger) for entry in self._relocate_folders_data]
            self._relocate_folders_count = len(self._relocated_folders)

            # Inform the user if a feature is not implemented
            if self._relocate_defaults.create_empty_cmake_file:
                self._logger.warning("'create_empty_cmake_file' is not implemented")

            self._logger.debug(
                f"Recipe '{recipe_file}' loaded successfully: {self._relocate_folders_count} folders defined.")

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
                getattr(self._logger, log_level)(f"Copying '{file_name}'")
        except Exception as copy_error:
            msg = f"Failed to copy '{relative_src}' to '{relative_dest}' {copy_error}"
            if fatal:
                raise RuntimeError(msg)
            else:
                self._logger.error(msg)

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
        processed_folders_count = 0
        processed_files_count = 0
        verbose: bool = kwargs.get("verbose", False)

        try:
            source_path: Optional[str] = kwargs.get("source_path")
            destination_path: Optional[str] = kwargs.get("destination_path")
            recipe_file: Optional[str] = kwargs.get("recipe_file")

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

            if not self._relocated_folders or not self._relocate_folders_count:
                raise RuntimeError("No folders found in the recipe to process.")

            # Handle deletion of existing destination directory
            if self._relocate_defaults.delete_destination_on_start:
                if os.path.exists(destination_path):
                    try:
                        shutil.rmtree(destination_path)
                        self._logger.debug(f"Deleting existing destination: '{destination_path}'")
                    except Exception as exception:
                        raise RuntimeError(f"failed to delete destination directory: {exception}") from exception
            else:
                if os.path.exists(destination_path):
                    raise RuntimeError(f"destination '{destination_path}' already exists.")

            print(f"\nStarting relocation process for {len(self._relocated_folders)} paths..")

            # Recreate destination
            os.makedirs(destination_path, exist_ok=True)

            if verbose:
                AutoLogger().set_output_enabled(logger=self._logger, state=True)

            # Process each folder entry
            for folder in self._relocated_folders:
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

                    relative_source_root = os.path.relpath(root, self._relocate_defaults.source_path)

                    if depth_from_root > 1:
                        self._logger.info(f"> Processing '{relative_source_root}'")

                    if max_depth != -1 and current_depth > max_depth:
                        raise RuntimeError(f"exceeded maximum copy depth ({max_depth}) at '{root}'")

                    for file in files:
                        src_path = os.path.join(root, file)
                        relative_src = os.path.relpath(src_path, self._relocate_defaults.source_path)
                        relative_path = os.path.relpath(root, folder.source)

                        # Files we have to copy
                        if '*' in folder.file_types or any(
                                file.endswith(f".{ft}") for ft in folder.file_types if ft != '*'):
                            dest_path = os.path.join(folder.destination, relative_path, file)
                            relative_dest = os.path.relpath(dest_path, self._relocate_defaults.destination_path)
                            self._safe_copy_file(src_path=src_path, dest_path=dest_path, relative_src=relative_src,
                                                 relative_dest=relative_dest, is_source=True,
                                                 fatal=self._relocate_defaults.break_on_errors)
                            copied_files_count += 1

                        # Not part of the list, see if we have a garve yard
                        elif folder.create_grave_yard:
                            graveyard_path = os.path.join(folder.destination, "grave_yard", relative_path, file)
                            relative_dest = os.path.relpath(graveyard_path, self._relocate_defaults.destination_path)
                            self._safe_copy_file(src_path=src_path, dest_path=graveyard_path, relative_src=relative_src,
                                                 relative_dest=relative_dest, is_source=False,
                                                 fatal=self._relocate_defaults.break_on_errors, log_level="debug")
                            copied_graveyard_files_count = copied_graveyard_files_count + 1

                    processed_files_count = processed_files_count + (copied_files_count + copied_graveyard_files_count)
                    self._logger.info(
                        f"Total {copied_files_count} files copied and {copied_graveyard_files_count} sent to graveyard.")

                if verbose:
                    print()

            print(f"Done, total {processed_files_count} files in {processed_folders_count} paths ware processed.\n")
            return True

        except Exception as relocate_error:
            # Re-raise for upstream error handling; can be enhanced for logging
            raise RuntimeError(f"{relocate_error} @ #{processed_folders_count}") from relocate_error
        finally:
            if verbose:
                AutoLogger().set_output_enabled(logger=self._logger, state=False)

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        parser.add_argument("-r", "--recipe", type=str, help="Path to a relocator JSON recipe file.")
        parser.add_argument("-s", "--source_path",
                            help="Source path containing the structure we would like to refactor.")
        parser.add_argument("-d", "--destination",
                            help="Destination path which the new structured project will be created in.")

        parser.add_argument("-vv", "--verbose", action="store_true",
                            help="Show more information while running the recipe.")
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
                self._relocate(recipe_file=args.recipe, source_path=args.source_path, destination_path=args.destination,
                               verbose=args.verbose)

        return return_value
