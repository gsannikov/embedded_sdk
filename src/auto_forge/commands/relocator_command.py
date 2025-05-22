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
from pathlib import Path
from typing import Any, Optional

# Third-party
from rich.console import Console

# AutoForge imports
from auto_forge import PROJECT_COMMANDS_PATH, CLICommandInterface, CoreProcessor, PrettyPrinter, ToolBox

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
    base_source_path: str
    base_destination_path: str
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

        # Expand and resolve base paths
        base_source_path = os.path.expandvars(os.path.expanduser(defaults_data.get("base_source_path", "")))
        base_destination_path = os.path.expandvars(os.path.expanduser(defaults_data.get("base_destination_path", "")))

        if not base_source_path or not base_destination_path:
            raise ValueError("'base_source_path' and 'base_destination_path' must be defined and non-empty")

        if not os.path.isdir(base_source_path):
            relative_path = os.path.join(os.getcwd(), base_source_path)
            if not os.path.isdir(relative_path):
                raise FileNotFoundError(f"Base source path does not exist: '{base_source_path}'")
            base_source_path = relative_path
            base_destination_path = os.path.join(os.getcwd(), base_destination_path)

        # Validate file_types
        file_types = defaults_data.get("file_types", ["*"])
        if not isinstance(file_types, list):
            raise ValueError("'file_types' must be a list")

        # Construct dataclass instance
        return _RelocateDefaults(
            base_source_path=base_source_path,
            base_destination_path=base_destination_path,
            file_types=file_types,
            delete_destination_on_start=defaults_data.get("delete_destination_on_start", False),
            full_debug=defaults_data.get("full_debug", False),
            create_grave_yard=defaults_data.get("create_grave_yard", False),
            max_copy_depth=defaults_data.get("max_copy_depth", -1),
            create_empty_cmake_file=defaults_data.get("create_empty_cmake_file", False),
        )


class _RelocateFolderRead:
    """
    Validates a raw folder entry JSON entry and returns a clean _RelocateFolder.
    Applies defaults where needed.
    """

    @staticmethod
    def process(
            defaults: _RelocateDefaults, raw_folder_entry: dict[str, Any]
    ) -> _RelocateFolder:
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

        return _RelocateFolder(
            description=raw_folder_entry.get("description"),
            source=source,
            destination=destination,
            file_types=file_types,
        )


class RelocatorCommand(CLICommandInterface):

    def __init__(self, **kwargs: Any):
        """
        Initializes the RelocatorCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments:
                - raise_exceptions (bool): Whether to raise exceptions on error instead of returning codes.
        """

        self._json_processor: CoreProcessor = CoreProcessor.get_instance()  # JSON preprocessor instance
        self._toolbox: ToolBox = ToolBox.get_instance()

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
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME,
                         raise_exceptions=raise_exceptions)

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

            self._relocated_folders = [
                _RelocateFolderRead.process(self._relocate_defaults, entry)
                for entry in self._relocate_folders_data
            ]
            self._relocate_folders_count = len(self._relocated_folders)

            # Inform the user if a feature is not implemented
            if self._relocate_defaults.create_empty_cmake_file:
                self._logger.warning("'create_empty_cmake_file' is not implemented")

            self._logger.debug(
                f"Recipe '{recipe_file}' loaded successfully: {self._relocate_folders_count} folders defined."
            )

        except Exception as load_error:
            # Re-raise the exception explicitly for future extension (e.g., logging or wrapping)
            raise load_error from load_error

    def _relocate(self, **kwargs: Any) -> bool:
        """
        Execute the loaded relocation recipe and reconstruct the destination tree accordingly.

        Args:
            **kwargs: Optional keyword arguments:
                - recipe_file (str): Path to the JSON AutoForge recipe file.

        Returns:
            bool: True if relocation was successful, False otherwise.
        """
        try:
            recipe_file: Optional[str] = kwargs.get("recipe_file")
            if not recipe_file or not isinstance(recipe_file, str):
                raise KeyError("Required argument 'recipe_file' is missing or invalid.")

            # Load and validate recipe; raises on error
            self._load_recipe(recipe_file)

            if not self._relocated_folders or not self._relocate_folders_count:
                raise RuntimeError("No folders found in the recipe to process.")

            graveyard_path: Optional[str] = None
            destination_root = self._relocate_defaults.base_destination_path

            # Handle deletion of existing destination directory
            if self._relocate_defaults.delete_destination_on_start:
                if os.path.exists(destination_root):
                    try:
                        shutil.rmtree(destination_root)
                        self._logger.debug(f"deleted existing destination directory: '{destination_root}'")
                    except Exception as exception:
                        raise RuntimeError(f"failed to delete destination directory: {exception}") from exception
            else:
                if os.path.exists(destination_root):
                    raise RuntimeError(f"destination '{destination_root}' already exists.")

            # Recreate destination root
            os.makedirs(destination_root, exist_ok=True)

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
                        if '*' in folder.file_types or any(
                                file.endswith(ft) for ft in folder.file_types if ft != '*'
                        ):
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

    def print_relocation_recipe_help(self) -> None:
        """
        Prints dynamic help explaining how to create a .jsonc relocation recipe file,
        based on an example file located in PROJECT_COMMANDS_PATH/help/relocate_example.jsonc.
        """
        console = Console(force_terminal=True, color_system="truecolor")
        help_dir: Path = PROJECT_COMMANDS_PATH / "help"
        example_file: Path = help_dir / "relocate_example.jsonc"

        if not help_dir.exists() or not help_dir.is_dir():
            console.print(f"[bold red][ERROR][/bold red] Help directory not found: {help_dir}")
            return

        if not example_file.exists() or not example_file.is_file():
            console.print(f"[bold red][ERROR][/bold red] Example recipe file not found: {example_file}")
            return

        try:
            json_data = self._json_processor.preprocess(str(example_file))
        except Exception as json_error:
            console.print(f"[bold red][ERROR][/bold red] Failed to load or preprocess example recipe: {json_error}")
            return

        print()
        console.print("[bold cyan]RELOCATION RECIPE HELP (.jsonc)[/bold cyan]", style="bold underline")
        console.print("""
    This guide explains how to write a relocation recipe file for AutoForge.

    A recipe is a JSONC (JSON with comments) file that defines:
      - Global settings (in the [bold]defaults[/bold] section)
      - Folder-specific source/destination mappings (in the [bold]folders[/bold] list)

    [bold]Key Sections[/bold]
    ────────────────────────────────────────────

    [green]▶ defaults[/green]
      Global settings applied to all folders unless overridden.

        • base_source_path: str (Required)
        • base_destination_path: str (Required)
        • file_types: list[str] (Optional, default = ["*"])
        • delete_destination_on_start: bool (Optional)
        • full_debug: bool (Optional)
        • create_grave_yard: bool (Optional)
        • max_copy_depth: int (Optional, default = -1)
        • create_empty_cmake_file: bool (Optional)

    [green]▶ folders[/green]
      Folder-specific mapping list.

        • source: str (Required)
        • destination: str (Required)
        • file_types: list[str] (Optional, overrides global)
        • description: str (Optional)

    """)

        console.print("\n[bold]Parsed JSON version (after stripping comments):[/bold]\n")
        PrettyPrinter(indent=4).render(json_data)

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds the command-line arguments supported by this command.
        Args:
            parser (argparse.ArgumentParser): The parser to extend.
        """
        parser.add_argument("-r", "--recipe", type=str, help="Path to a relocator JSON recipe file.")
        parser.add_argument("-x", "--recipe_example", action="store_true", help="Show precipice file example.")

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
        elif args.recipe_example:
            self.print_relocation_recipe_help()
        else:
            return_value = CLICommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        return return_value
