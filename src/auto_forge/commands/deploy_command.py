"""
Script:         deploy_command.py
Author:         AutoForge Team

Description:
    Follows a JSON recipe that defines a table which maps files between a ZIP archive ('archive') and a
    file system ('host'). Each entry specifies how a file should be extracted or archived, enabling structured
    file deployment and collection operations.
"""

import argparse
import zipfile
from datetime import datetime
from enum import Enum, auto
from logging import Logger
from pathlib import Path
from typing import Any
from typing import Optional

# AutoForge imports
from auto_forge import (CommandInterface, CoreProcessor, CoreVariables, ToolBox, AutoLogger, AutoForgCommandType)

AUTO_FORGE_MODULE_NAME = "deploy"
AUTO_FORGE_MODULE_DESCRIPTION = "Recipe Deployer"
AUTO_FORGE_MODULE_VERSION = "1.0"


class _DeployDirectionType(Enum):
    """
    Defines the direction of the deployment operation:
    - ArchiveToHost: Extract files from archive to file system.
    - HostToArchive: Collect files from file system into an archive.
    """
    Unknown = 0
    ArchiveToHost = auto()
    HostToArchive = auto()


class _OverwritePolicy(Enum):
    """
    Specifies how existing files should be handled during extraction or archiving:
    - Unknown: Unrecognized or unset policy.
    - Always: Always overwrite existing files.
    - Never: Never overwrite existing files.
    - WhenNewer: Overwrite only if the source file is newer than the destination.
    """
    Unknown = 0
    Always = auto()
    Never = auto()
    WhenNewer = auto()


class DeployCommand(CommandInterface):
    """
    Implements the deployment command for syncing files between a ZIP archive and the host file system
    based on a structured recipe.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the EditCommand class.
        Args:
            **_kwargs (Any): Optional keyword arguments, such as:
        """

        self._json_processor: CoreProcessor = CoreProcessor.get_instance()  # JSON preprocessor instance
        self._tool_box: ToolBox = ToolBox.get_instance()
        self._variables: CoreVariables = CoreVariables.get_instance()

        # Get a logger instance
        self._logger: Logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME.capitalize())

        # Type for  essential JSON sections
        self._recipe_defaults_raw: Optional[dict] = None
        self._recipe_files: Optional[list[dict]] = None
        self._recipe_defaults: Optional[dict[str, Any]] = None

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, command_type=AutoForgCommandType.AUTOMATION, hidden=True)

    def _reset(self):
        """
        Resets internal state in preparation for a new deployment operation.
        Clears any previously loaded recipe defaults and file mappings.
        """
        self._recipe_defaults_raw = None
        self._recipe_defaults = None
        self._recipe_files = None

    @staticmethod
    def _parse_overwrite_policy(policy_str: str) -> _OverwritePolicy:
        """
        Normalize a string into a valid OverwritePolicy enum value.
        Args:
            policy_str (str): Input string (e.g., "Always", "never", "new").
        Returns:
            _OverwritePolicy: Matching enum value, or OverwritePolicy.Unknown if invalid.
        """
        normalized = policy_str.strip().lower()

        if normalized == "always":
            return _OverwritePolicy.Always
        elif normalized == "never":
            return _OverwritePolicy.Never
        elif normalized == "new":
            return _OverwritePolicy.WhenNewer
        else:
            return _OverwritePolicy.Unknown

    @staticmethod
    def _parse_direction(direction_str: str) -> _DeployDirectionType:
        """
        Parses a string value into a corresponding _DeployDirectionType enum.
        Args:
            direction_str (str): The direction string to interpret.
        Returns:
            _DeployDirectionType: The parsed direction enum.
        """
        normalized = direction_str.strip().lower()

        if normalized == 'to-host':
            return _DeployDirectionType.ArchiveToHost
        elif normalized == 'to-archive':
            return _DeployDirectionType.HostToArchive
        else:
            return _DeployDirectionType.Unknown

    def _to_archive(self, host_base_path: Path, archive_path: Path) -> Optional[int]:
        """
        Creates a ZIP archive using file entries defined in the loaded recipe.
        For each entry, the 'destination' field is resolved relative to the host base path,
        and the corresponding file is added to the archive under its 'archive' name.

        Respects the following recipe default:
            - overwrite (str): Determines if the archive should be created or updated.
                - 'always': Overwrite or recreate the archive unconditionally.
                - 'never': Skip archive creation if it already exists.
                - 'when_newer': Add files only if they are newer than the archive.
        Args:
            host_base_path (Path): Base directory containing the source files on the host system.
            archive_path (Path): Path to the output ZIP archive.
        Returns:
            Optional[int]: 0 on success, 1 if any error occurred.
        """

        try:
            raw_overwrite_host_files_policy = self._recipe_defaults.get("overwrite_host_files", "always")
            policy = self._parse_overwrite_policy(raw_overwrite_host_files_policy)

            if policy == _OverwritePolicy.Unknown:
                raise ValueError(f"invalid overwrite policy: '{raw_overwrite_host_files_policy}'")

            if archive_path.exists():
                if policy == _OverwritePolicy.Never:
                    self._logger.info(f"Archive already exists, skipping due to overwrite policy: never")
                    return 0
                elif policy == _OverwritePolicy.WhenNewer:
                    archive_mtime = datetime.fromtimestamp(archive_path.stat().st_mtime)
                else:
                    archive_mtime = None
            else:
                archive_mtime = None

            with zipfile.ZipFile(archive_path, mode='w' if policy == _OverwritePolicy.Always else 'a') as archive:
                for entry in self._recipe_files:
                    src_rel = Path(entry["host"])
                    arc_rel = Path(entry["archive"])
                    src_abs = host_base_path / src_rel

                    if not src_abs.exists():
                        self._logger.warning(f"File not found: {src_abs}")
                        continue

                    if policy == _OverwritePolicy.WhenNewer and archive_mtime:
                        file_mtime = datetime.fromtimestamp(src_abs.stat().st_mtime)
                        if file_mtime <= archive_mtime:
                            self._logger.info(f"Skipping {src_abs} (not newer than archive)")
                            continue

                    self._logger.info(f"Adding to archive: {src_abs} as {arc_rel}")
                    archive.write(src_abs, arcname=str(arc_rel))

            return 0

        except Exception as archive_error:
            raise RuntimeError(f"failed to create archive: {archive_error}")

    def _from_archive(self, archive_path: Path, host_base_path: Path) -> Optional[int]:
        """
        Extracts selected files from a ZIP archive to the host file system according to the loaded recipe.
        Each file entry in the recipe specifies an 'archive' (path inside the ZIP) and a 'destination'
        (path relative to the host base). Extraction behavior is governed by recipe defaults.

        Respects the following recipe defaults:
            - create_destination_path (bool): Whether to create missing destination directories.
            - overwrite (str): Overwrite policy, one of: 'always', 'never', 'when_newer'.
        Args:
            archive_path (Path): Path to the ZIP archive containing the source files.
            host_base_path (Path): Base directory on the host where files will be written.
        Returns:
            Optional[int]: 0 on success, 1 if any error occurred.
        """
        try:
            if not archive_path.exists():
                raise FileNotFoundError(f"archive not found: {archive_path}")

            create_dirs = bool(self._recipe_defaults.get("create_host_path", False))
            policy = self._parse_overwrite_policy(self._recipe_defaults.get("overwrite", "always"))

            if policy == _OverwritePolicy.Unknown:
                raise ValueError(f"invalid overwrite policy in recipe: {self._recipe_defaults.get('overwrite')}")

            with zipfile.ZipFile(archive_path, mode='r') as archive:
                archive_contents = {Path(info.filename): info for info in archive.infolist()}

                for entry in self._recipe_files:
                    arc_rel = Path(entry["archive"])
                    dst_rel = Path(entry["host"])
                    dst_abs = host_base_path / dst_rel

                    if arc_rel not in archive_contents:
                        self._logger.warning(f"Archive entry not found: {arc_rel}")
                        continue

                    # Check if we need to create host path
                    if not dst_abs.parent.exists():
                        if create_dirs:
                            dst_abs.parent.mkdir(parents=True, exist_ok=True)
                            self._logger.info(f"Created directory: {dst_abs.parent}")
                        else:
                            self._logger.warning(f"Host path does not exist: {dst_abs.parent}")
                            continue

                    # Overwrite policy enforcement
                    if dst_abs.exists():
                        if policy == _OverwritePolicy.Never:
                            self._logger.info(f"Skipping (exists): {dst_abs}")
                            continue
                        elif policy == _OverwritePolicy.WhenNewer:
                            arc_mtime = datetime(*archive_contents[arc_rel].date_time)
                            fs_mtime = datetime.fromtimestamp(dst_abs.stat().st_mtime)
                            if fs_mtime >= arc_mtime:
                                self._logger.info(f"Skipping (up-to-date): {dst_abs}")
                                continue

                    # Extract to the specified host path
                    with archive.open(str(arc_rel), 'r') as src, open(dst_abs, 'wb') as dst:
                        dst.write(src.read())

                    self._logger.info(f"Extracted {arc_rel} -> {dst_abs}")

        except Exception as extract_error:
            raise RuntimeError(f"failed to extract from archive: {extract_error}")

    def _process(self, recipe_file: str, archive_path: str, host_base_path: str, direction: _DeployDirectionType,
                 verbose: Optional[bool] = False) -> Optional[int]:
        """
        Processes the recipe file and sets up the archive-to-host mapping.
        Args:
            recipe_file (str): Path to the JSON or JSONC recipe file.
            archive_path (str): Path to the ZIP archive, which could be decompressed or created based on the direction.
            host_base_path (str): Path to the directory where files will deploy to or collected from.
            direction (_DeployDirectionType): Determines the operation direction.
            verbose (bool, optional): Enable verbose logging if True.
        Returns:
            Optional[int]: 1 on failure, None on success.
        """
        try:

            # Reset class variable
            self._reset()

            # Allow console logger if verbose was specified
            if verbose:
                AutoLogger().set_output_enabled(logger=self._logger, state=True)

            # Expand variables (environment, etc.)
            recipe_file = self._variables.expand(recipe_file)

            # Expand and convert to path type
            archive_path = Path(self._variables.expand(archive_path))
            host_base_path = Path(self._variables.expand(host_base_path))

            self._logger.debug(f"Recipe: {recipe_file}, archive: {archive_path}, host base: {host_base_path}")

            # Load and preprocess the recipe JSONC/JSON file
            recipe_raw: Optional[dict] = self._json_processor.preprocess(file_name=recipe_file)
            if not isinstance(recipe_raw, dict):
                raise ValueError(f"failed to parse recipe: '{recipe_file}'")

            self._recipe_defaults = recipe_raw.get("defaults", {})
            self._recipe_files = recipe_raw.get("files", [])

            # Validate presence of mandatory fields
            if not isinstance(self._recipe_defaults, dict) or not isinstance(self._recipe_files, list):
                raise ValueError(f"missing or malformed 'defaults' or 'files' section in recipe: '{recipe_file}'")

            if not len(self._recipe_files):
                raise ValueError(f"not files specified in recipe: '{recipe_file}'")

            self._logger.debug(f"Loaded recipe: {len(self._recipe_files)} file entries found")

            if direction == _DeployDirectionType.HostToArchive:
                return self._to_archive(host_base_path=host_base_path, archive_path=archive_path)
            elif direction == _DeployDirectionType.ArchiveToHost:
                return self._from_archive(archive_path=archive_path, host_base_path=host_base_path)
            else:
                raise ValueError(f"unknown deploy direction: {direction}")

        except Exception as deploy_error:
            raise RuntimeError(f"recipe deploy processing failed: {deploy_error}")
        finally:
            if verbose:  # Shutdown console logger
                AutoLogger().set_output_enabled(logger=self._logger, state=False)

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument('-r', '--recipe',
                            required=True, help='Path to the JSONC/JSON recipe describing file mappings.')

        parser.add_argument('-a', '--archive', required=True, help='Path to the ZIP archive file.')

        parser.add_argument('-b', '--host_base_path',
                            required=True, help='Path to the a directory where files will deployed or collected from.')

        parser.add_argument('-d', '--direction',
                            choices=['to-host', 'to-archive'], required=True,
                            help="Operation mode: 'to-host' extracts from archive to host, 'to-archive' creates archive from host files."
                            )

        parser.add_argument("-vv", "--verbose", action="store_true",
                            help="Show more information while running the recipe.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:
            args (argparse.Namespace): The parsed arguments.
        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """
        if args.recipe and args.archive and args.host_base_path and args.direction:

            # Convert the direction into a recognize type
            deploy_direction: _DeployDirectionType = self._parse_direction(args.direction)

            if deploy_direction == _DeployDirectionType.Unknown:
                raise ValueError(f"unknown recipe direction: '{args.direction}'")

            # Process the recipe
            return self._process(recipe_file=args.recipe, archive_path=args.archive, host_base_path=args.host_base_path,
                                 direction=deploy_direction, verbose=args.verbose)
        else:
            return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
