"""
Script:         xray.py
Author:         AutoForge Team

Description:
    Provides fast, full-text search indexing and querying for developer source trees.
    The core module uses SQLite with FTS5 (trigram tokenizer) to index source files and
    supports optional content purification to normalize formatting and remove noise.

Features:
    - Multi-threaded file scanning and indexing
    - Content de-duplication using checksums
    - Optional whitespace and encoding normalization ("purify")
    - Live progress reporting with file skip/error counts
    - CLI-friendly interface for structured and ad-hoc SQL queries

"""

import getpass
import hashlib
import os
import platform
import re
import sqlite3
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from queue import Queue
from threading import Thread, Lock
from typing import Optional, Any

from rich import box
from rich.console import Console
from rich.table import Table
# Third-party
from rich.text import Text

# Note: Compatibility bypass - no native "UTC" import in Python 3.9.
UTC = timezone.utc

# AutoForge imports
from auto_forge import (
    AutoForgeModuleType, CoreLogger, CoreModuleInterface, CoreRegistry, CoreSolution, CoreVariables, CoreToolBox,
    CoreTelemetry, PromptStatusType, XRayStateType
)

AUTO_FORGE_MODULE_NAME = "XRayDB"
AUTO_FORGE_MODULE_DESCRIPTION = "Source Tree Data Base"

# Constants
XRAY_NUM_WORKERS = os.cpu_count() or 4
XRAY_NUM_READERS = 4
XRAY_BATCH_SIZE = 50


@dataclass
class _XRayStats:
    """ Internal type used for storing indexing related statics """
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    start_time: float = 0

    def reset(self):
        self.processed = 0
        self.skipped = 0
        self.errors = 0
        self.start_time = time.time()


# noinspection SqlNoDataSourceInspection
class CoreXRayDB(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """

        self._managed_paths: Optional[list[Path]] = None
        self._index_path: Optional[Path] = None
        self._db_indexed_file_types: Optional[list[str]] = None
        self._db_connection: Optional[sqlite3.Connection] = None
        self._db_file: Optional[Path] = None
        self._manage_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._db_indexing_log_frequency: int = 1000
        self._db_row_count: int = 0
        self._clean_slate: bool = False
        self._db_meta_data: Optional[dict[str, Any]] = None  # 'meta' table loaded key values pairs
        self._db_filter_files_content: bool = True
        self._serving_query: bool = False
        self._state: XRayStateType = XRayStateType.NO_INITIALIZED
        self._console = Console(force_terminal=True)

        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initializes the CoreXRay class.
        """

        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)  # Get a logger instance
        self._variables = CoreVariables.get_instance()
        self._solution = CoreSolution.get_instance()
        self._tool_box = CoreToolBox.get_instance()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()
        self._registry: CoreRegistry = CoreRegistry.get_instance()

        try:

            self._get_and_validate_paths()

            # Retrieve AutoForge package configuration
            self._configuration = self.auto_forge.get_instance().configuration
            if self._configuration is None:
                raise RuntimeError("package configuration data not available")

            self._db_indexed_file_types = self._configuration.get("db_indexed_file_types")
            if not isinstance(self._db_indexed_file_types, list) or len(self._db_indexed_file_types) < 1:
                raise RuntimeError("no indexed items defined")

            # Required configuration property: 'db_version' must be a string.
            # It indicates the expected schema version for the XRay engine-compatible database.
            self._db_version: Optional[str] = self._configuration.get("db_version")
            if not isinstance(self._db_version, str):
                raise RuntimeError("Configuration error: 'db_version' must be specified as a string.")

            # Required configuration property: 'db_meta_schema' must be a dictionary.
            # This defines the metadata schema expected in the database's 'meta' table.
            self._db_meta_schema: Optional[dict] = self._configuration.get("db_meta_schema")
            if not isinstance(self._db_meta_schema, dict):
                raise RuntimeError("Configuration error: 'db_meta_schema' must be specified as a dictionary.")

            self._db_max_indexed_file_size_kb: int = self._configuration.get("db_max_indexed_file_size_kb", 1024)
            self._db_min_indexed_file_size_bytes: int = self._configuration.get("db_min_indexed_file_size_bytes", 8)
            self._db_non_indexed_path_patterns: list = self._configuration.get("db_non_indexed_path_patterns", [])
            self._db_non_indexed_file_patterns = self._configuration.get("db_non_indexed_file_patterns", [])
            self._db_filter_files_content = bool(
                self._configuration.get("db_filter_files_content", self._db_filter_files_content))
            self._db_extra_log_verbosity = bool(self._configuration.get("db_extra_log_verbosity", False))
            self._db_indexing_log_frequency = int(
                self._configuration.get("db_indexing_log_frequency", self._db_indexing_log_frequency))

            # Number of days after which existing index data is considered stale
            self._db_max_index_age_days: int = self._configuration.get("db_max_index_age_days", 30)

            # We will get that from the 'meta' table
            self._db_last_indexed_date: Optional[datetime] = None
            self._db_last_indexed_age_days: Optional[int] = None

            # Load excluded paths list from the solution
            self._solution_excluded_paths: Any = self._solution.get_arbitrary_item(key="xray_excluded_path")
            if not isinstance(self._solution_excluded_paths, list) or len(self._solution_excluded_paths) < 1:
                self._logger.warning("Solution's excluded paths are either undefined or incorrectly formatted.")
                self._solution_excluded_paths = []

            # Create regex pattern based on the configuration list
            self._db_file = self._index_path / "autoforge.db"

            # Force 'clean slate' when the SQLite file is missing
            if not self._db_file.exists():
                self._logger.warning(f"Existing SQLite file '{str(self._db_file)}' not found")
                self._clean_slate = True

            # Register this module with the package registry
            registry = CoreRegistry.get_instance()
            registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                     auto_forge_module_type=AutoForgeModuleType.CORE)

            # Inform telemetry that the module is up & running
            self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

        # Forward exceptions
        except Exception as exception:
            self._state = XRayStateType.ERROR
            raise exception

    def _get_and_validate_paths(self):
        """" Retrieve paths from variables which could be a single str or alist and force the results into a list """
        managed_paths: Any = self._variables.get_by_folder_type(folder_type="SOURCES")

        # Validate we got something we can work with
        if not isinstance(managed_paths, (list, str)):
            raise RuntimeError("No managed paths provided")

        if isinstance(managed_paths, str):
            managed_paths = [managed_paths]
        elif not isinstance(managed_paths, list) or len(managed_paths) <= 1:
            raise ValueError("Managed paths are not defined by the system")

        self._managed_paths = [Path(p) for p in managed_paths if Path(p).exists()]
        if not self._managed_paths:
            raise ValueError("None of the specified managed paths exist on the filesystem")

        index_path: Any = self._variables.get_by_folder_type(folder_type="INDEX")
        # Validate we got something we can work with
        if not isinstance(index_path, (list, str)):
            raise ValueError("Indexes path not defined by the system")

        # Normalize the indexes path to a string (first item if it's a list)
        if isinstance(index_path, list):
            if not index_path:
                raise ValueError("Index path can't be an empty list")
            index_path = index_path[0]

        # Convert to Path and validate
        index_path = Path(index_path)
        if not index_path.exists():
            raise FileNotFoundError(f"Index path does not exist: '{index_path}'")

        self._index_path = index_path

    def _get_sql_connection(self, read_only: bool = False) -> sqlite3.Connection:
        """
        Returns a connection to the SQLite database.
        Args:
            read_only (bool): If True, opens the database in read-only mode.
        Returns:
            sqlite3.Connection: SQLite connection object.
        """
        if read_only:
            return sqlite3.connect(f"file:{self._db_file}?mode=ro", uri=True)
        return sqlite3.connect(str(self._db_file))

    def _initialize_database(self) -> None:
        """
        Create or open the SQLite database file.
        If the database file does not exist, a new one is created with the required tables and
        indexing structure (FTS5 for full-text content and metadata table for checksums).
        If `force_new` is True, an existing database will be deleted and rebuilt from scratch.

        """
        with self._lock:
            # We are allowed to initialize the database only when not yet initialized.
            if self._state != XRayStateType.NO_INITIALIZED:
                raise RuntimeError(f"XRay Can't perform initialization on state '{self._state.name}'")

        # Normalize 'clean_slate' variable based on SQLite file existence.
        if self._clean_slate:
            if self._db_file.exists():
                self._logger.warning(f"Existing SQLite file '{str(self._db_file)}' will be deleted")
                self._db_file.unlink(missing_ok=True)
        else:
            if not self._db_file.exists():
                self._logger.warning(f"Existing SQLite file '{str(self._db_file)}' not found, creating it")
                self._clean_slate = True

        if not self._clean_slate:

            # ------------------------------------------------------------------
            #
            # Open and validate an existing SQLite database.
            #
            # ------------------------------------------------------------------

            self._logger.debug(f"Opening SQLite file: {str(self._db_file)}")
            conn: Optional[sqlite3.Connection] = None
            try:
                conn = sqlite3.connect(str(self._db_file))
                cursor = conn.cursor()

                # Fast settings for read-heavy use
                cursor.executescript("""
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = OFF;
                    PRAGMA temp_store = MEMORY;
                    PRAGMA cache_size = -512000;
                    PRAGMA mmap_size = 536870912;
                    PRAGMA foreign_keys = OFF;
                """)

                # Detect presence of existing tables
                # @formatter:off
                cursor.execute("""
                               SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'files';
                               """)
                # @formatter:on
                if not cursor.fetchone():
                    self._logger.warning(f"Unable to fetch data from exiting an SQLite database '{str(self._db_file)}'")
                    self._clean_slate = True
                else:
                    cursor.execute("SELECT 1 FROM files LIMIT 1;")  # Fast schema check

                    # Validate 'meta' table
                    self._validate_meta_table()

            except Exception as sql_error:
                self._logger.warning(f"Existing index is invalid or incompatible: {sql_error}")
                try:
                    self._logger.info(f"Delinting corrupted SQLite database, deleting '{str(self._db_file)}'")
                    self._db_file.unlink(missing_ok=True)
                    self._clean_slate = True

                except Exception as delete_error:
                    raise RuntimeError(
                        f"Error deleting corrupted SQLite database file '{str(self._db_file)}'") from delete_error
            finally:
                if conn:
                    conn.close()

        # ----------------------------------------------------------------------
        #
        # Creating SQLite tables:
        # Data table: 'files', fields { path , content }
        # Files metadata table:  'file_meta', fields { path, modified, checksum }
        # General metadata Key/Value table: 'meta', fields { db_version, db_creation_date .. }
        #
        # ----------------------------------------------------------------------

        if self._clean_slate:
            conn: Optional[sqlite3.Connection] = None
            # noinspection SpellCheckingInspection
            try:
                self._logger.info(f"Creating new SQLite database file at: {str(self._db_file)}")
                conn = sqlite3.connect(str(str(self._db_file)))
                cursor = conn.cursor()

                # Main paths and files content table
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS files USING fts5(
                        path UNINDEXED,
                        content,
                        tokenize = 'trigram'
                    );
                """)

                # Per file metadata table
                # @formatter:off
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS file_meta(
                        path TEXT PRIMARY KEY,
                        modified REAL,
                        checksum TEXT,
                        ext TEXT,
                        base TEXT
                    );
                """)
                # @formatter:on

                cursor.execute("""
                               CREATE INDEX IF NOT EXISTS idx_file_meta_path ON file_meta(path);
                               """)

                # Persistanr meta table
                # @formatter:off
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS meta(
                        key TEXT PRIMARY KEY,
                        value TEXT
                    );
                """)
                # @formatter:on

                # PRAGMAs for new DB
                cursor.executescript("""
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = OFF;
                    PRAGMA temp_store = MEMORY;
                    PRAGMA cache_size = -512000;
                    PRAGMA mmap_size = 536870912;
                    PRAGMA foreign_keys = OFF;
                """)
                conn.commit()

                # Populate the new 'meta' table with initial values
                self._update_meta_table(clean_slate=True)

            except Exception as sql_error:
                self._logger.warning(f"Existing SQLite database is invalid or incompatible: {sql_error}")
            finally:
                if conn:
                    conn.close()

    def _validate_meta_table(self) -> Optional[bool]:
        """
        Validates the persistent 'meta' table against the preloaded schema and the expected db_version field.
        - Ensures all required keys are present.
        - Confirms all values match the expected types (str, datetime, int, float).
        - Date fields are also double-checked and verified to be in the past and no older than one year.
        - Raises an error if the stored db_version is incompatible with the current engine version.
        Returns:
            bool: True if an existing 'meta' table was found and validated.
        """
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = self._get_sql_connection()
            cursor = conn.cursor()
            now = datetime.now(UTC)
            one_year_ago = now - timedelta(days=365)

            # Load existing meta values
            cursor.execute("SELECT key, value FROM meta")
            self._db_meta_data = dict(cursor.fetchall())

            if not isinstance(self._db_meta_data, dict):
                raise RuntimeError("'meta' table could not be interpreted as a key-value dictionary")

            for key, schema_entry in self._db_meta_schema.items():
                is_required = schema_entry.get("required", False)
                expected_type = schema_entry.get("type")

                if is_required and key not in self._db_meta_data:
                    raise RuntimeError(f"Missing required meta key: '{key}' in 'meta' table")

                if key in self._db_meta_data:
                    value = self._db_meta_data[key]

                    if expected_type == "datetime":
                        try:
                            dt_value = datetime.fromisoformat(value)
                        except ValueError:
                            raise RuntimeError(f"Invalid datetime format for meta key '{key}': {value}")

                        # Ensure date is within 1 year and not in the future
                        if dt_value > now:
                            raise RuntimeError(f"Datetime value for '{key}' is in the future: {value}")
                        if dt_value < one_year_ago:
                            raise RuntimeError(f"Datetime value for '{key}' is older than 1 year: {value}")

                    elif expected_type == "str":
                        if not isinstance(value, str):
                            raise RuntimeError(f"Key '{key}' in 'meta' table must be a string")

                    elif expected_type == "int":
                        try:
                            int(value)
                        except (ValueError, TypeError):
                            raise RuntimeError(
                                f"Key '{key}' in 'meta' table must be an integer (coercible from string)")

                    elif expected_type == "float":
                        try:
                            float(value)
                        except (ValueError, TypeError):
                            raise RuntimeError(f"Key '{key}' in 'meta' table must be a float (coercible from string)")

                    else:
                        raise RuntimeError(f"Unsupported type '{expected_type}' for meta key '{key}'")

            # Check db_version compatibility
            existing_db_version = self._db_meta_data.get("db_version", "0.0")
            if existing_db_version != self._db_version:
                raise RuntimeError(
                    f"'meta' table has an unsupported db_version '{existing_db_version}', expected '{self._db_version}'")

            # Validate the last indexing date as ISO string from meta table when we have it.
            raw_date = self._db_meta_data.get("db_last_indexed_date", None)
            if raw_date is not None:
                self._db_last_indexed_date = None
                self._db_last_indexed_age_days = None
                if isinstance(raw_date, str):
                    try:
                        parsed_date = datetime.fromisoformat(raw_date)
                        if parsed_date.tzinfo is None:
                            parsed_date = parsed_date.replace(tzinfo=UTC)  # assume UTC if not present
                        self._db_last_indexed_date = parsed_date
                        self._db_last_indexed_age_days = (datetime.now(UTC) - parsed_date).days
                        self._logger.info(f"Database was last indexed {self._db_last_indexed_age_days} days ago")
                    except ValueError:
                        raise RuntimeError(f"invalid datetime format for 'db_last_indexed_date': {raw_date}")

            # Log few of the 'meta' table properties.
            self._logger.info(f"DB Metadata: engine v{existing_db_version}, "
                              f"created by AutoForge v{self._db_meta_data.get('auto_forge_version')} "
                              f"for solution '{self._db_meta_data.get('solution_name')}'")

            # Get current records (indexed files) count in the 'files' table
            cursor.execute("SELECT COUNT(*) FROM files")
            self._db_row_count = cursor.fetchone()[0]
            self._logger.info(f"DB row count in 'files': {self._db_row_count}")
            if not self._db_row_count:
                self._logger.warning(f"Empty 'files', forcing clean slate")
                self._clean_slate = True

            return True

        except Exception as validation_error:
            raise RuntimeError(f"Failed to validate meta table: {validation_error}") from validation_error
        finally:
            if conn:
                conn.close()

    def _update_meta_table(self, clean_slate: bool = False):
        """
        Updates or optionally creates from scratch the persistent 'meta' table.
        Args:
            clean_slate (bool): If True, remove all metadata and insert fresh values.
                                If False, only update the 'db_last_indexed_date' field.
        """
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = self._get_sql_connection()
            cursor = conn.cursor()
            now = datetime.now(UTC).isoformat()

            if not clean_slate:
                # Partial update: only update 'db_last_indexed_date'
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("db_last_indexed_date", now)
                )
                conn.commit()
                return

            # Full reset
            cursor.execute("DELETE FROM meta")

            meta_items = [
                ("db_version", self._db_version),
                ("db_creation_date", now),
                ("solution_name", self._solution.solution_name),
                ("user_name", getpass.getuser()),
                ("host_name", platform.node()),
                ("platform", platform.platform()),
                ("xray_version", platform.python_version()),
                ("auto_forge_version", self.auto_forge.version)
            ]

            cursor.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                meta_items
            )
            conn.commit()

        except Exception as update_error:
            raise RuntimeError(f"Failed to update 'meta' table: {update_error}") from update_error

        finally:
            if conn:
                conn.close()

    # noinspection SpellCheckingInspection
    @staticmethod
    def _filter_file_content(text: str) -> Optional[str]:
        """
        Sanitizes decoded text content for consistent and searchable indexing.
        - Rejects text containing binary markers (e.g., NUL bytes)
        - Normalizes line endings to '\n'
        - Removes UTF-8 BOM if present
        - Strips trailing spaces/tabs/vtabs/formfeeds
        - Replaces tabs with 4 spaces
        - Collapses internal repeated spaces/tabs
        - Removes trailing empty lines
        Args:
            text (str): Input decoded file content.
        Returns:
            Optional[str]: Sanitized content, or None if deemed invalid.
        """
        with suppress(Exception):
            # Binary content detection
            if '\x00' in text:
                return None

            if text.startswith('\ufeff'):
                text = text.lstrip('\ufeff')

            text = text.replace('\r\n', '\n').replace('\n\r', '\n').replace('\r', '\n')

            lines = []
            for line in text.split('\n'):
                line = line.rstrip(" \t\v\f")
                line = line.replace('\t', '    ')

                # Collapse internal whitespace (but preserve leading indent)
                line = re.sub(r'(?<=\S)[ \t]+(?=\S)', ' ', line)
                lines.append(line)

            while lines and not lines[-1].strip():
                lines.pop()

            return '\n'.join(lines) if lines else None

        return None  # Error

    def _perform_indexing(self) -> Optional[bool]:
        """
        Perform multithreaded indexing of managed file paths into a SQLite database.
        This is the core of the indexing system. It spawns multiple reader threads to process and
        checksum eligible files, and a single writer thread to insert data into the SQLite database in a
        thread-safe, batched manner.

        Key Features:
        - Uses multiple threads to read files concurrently for improved performance.
        - Employs a dedicated writer thread to avoid SQLite concurrency issues.
        - Files are skipped if they are hidden or match predefined ignore patterns.
        - Files larger than a configurable threshold are skipped (with optional quiet pattern filtering).
        - Supports checksum-based change detection to avoid unnecessary re-indexing.

        Note: Ensure that 'BATCH_SIZE' and 'NUM_READERS' are tuned to the system capacity.
            Extremely high values may lead to memory exhaustion or database contention.
        """

        file_queue = Queue()
        result_queue = Queue()
        count_lock = Lock()
        total_processed: int = 0
        read_stats: _XRayStats = _XRayStats()
        write_stats: _XRayStats = _XRayStats()
        indexing_start_time: float = time.time()
        last_log_time = indexing_start_time
        paths = list(self._managed_paths or [])
        indexed_items = list(self._db_indexed_file_types or [])

        def _should_skip_path(_path: Path) -> bool:
            """
            Determine if a path should be skipped during indexing, skips if:
            - Any part starts with '.' (hidden files/dirs)
            - Any part matches `self._non_indexed_path_patterns`
            - Full path matches any pattern in `self._excluded_paths`
            Args:
                _path (Path): The path to evaluate.
            Returns:
                bool: True if the path should be skipped, False otherwise.
            """
            # Check hidden parts and known non-indexed path names
            if any(
                    _part.startswith(".") or _part in self._db_non_indexed_path_patterns
                    for _part in _path.parts
            ):
                return True

            # Check against excluded path patterns
            if self._solution_excluded_paths:
                _path_str = str(_path)
                if any(fnmatch(_path_str, pattern) for pattern in self._solution_excluded_paths):
                    if self._db_extra_log_verbosity:
                        self._logger.warning(f"Skipping '{_path_str}' due to excluded paths rule")
                    return True

            return False

        def _add_files_to_queue() -> int:
            """
            Traverse configured paths and enqueue files that pass initial filters.
            Returns:
                int: Number of files added to the queue.
            """

            def _matches(_file: Path, _indexed_items: list[str]) -> bool:
                """ Regex callback implementation """
                return _file.name in _indexed_items or _file.suffix.lstrip(".") in _indexed_items

            count = 0
            try:
                for path in paths:
                    for file in path.rglob("*"):
                        if file.is_file() and _matches(file, indexed_items) and not _should_skip_path(file):
                            file_queue.put(file)
                            count += 1
            except Exception as e:
                self._logger.warning(f"File traversal failed: {e}")

            return count

        def _log_stats(_summarize: bool = False):
            """
            Periodically or finally log indexing statistics.
            Logs the number of files handled, the rate of processing,
            and optionally the number of skipped and error files.
            Args:
                _summarize (bool): If True, forces a final summary log regardless of frequency.
            """
            nonlocal read_stats, write_stats, total_processed, last_log_time

            with count_lock:
                processed_count = write_stats.processed + write_stats.skipped
                error_count = read_stats.errors + write_stats.errors

                if not processed_count:
                    return

                now = time.time()
                if not _summarize:
                    if processed_count % self._db_indexing_log_frequency == 0:
                        elapsed = now - last_log_time
                        last_log_time = now  # Update for next round

                        rate = int(processed_count / elapsed) if elapsed > 0 else 0
                        total_processed += processed_count

                        details = [f"Indexed {total_processed:>7,d} files ({rate:>6,d} files/sec)"]
                        if write_stats.skipped:
                            details.append(f"{write_stats.skipped} skipped")
                        if error_count:
                            details.append(f"{error_count} errors")

                        self._logger.debug(" | ".join(details))
                    else:
                        return
                else:
                    # Print summary statics
                    elapsed = now - indexing_start_time
                    rate = int(total_processed / elapsed) if elapsed > 0 else 0
                    total_processed += processed_count
                    details = f"{total_processed:>7,d} files indexed ({rate:>6,d} files/sec)"
                    self._logger.info(f"Summary: {details}")

                # Reset counters but not the start time
                read_stats.processed = read_stats.errors = 0
                write_stats.processed = write_stats.skipped = write_stats.errors = 0

        def _reader_worker():
            """
            Thread worker that reads and optionally purifies file content from the queue.
            - Skips large, binary, or excluded files.
            - Computes checksum.
            - Passes valid results to the writer queue.
            """
            nonlocal read_stats
            read_stats.start_time = time.time()
            _compiled_non_indexed_files_patterns = [re.compile(p) for p in self._db_non_indexed_file_patterns]

            while True:
                _file = file_queue.get()
                if _file is None:
                    break
                try:
                    _stat = _file.stat()
                    _size_kb = _stat.st_size / 1024
                    if (_stat.st_size < self._db_min_indexed_file_size_bytes) or (
                            _size_kb > self._db_max_indexed_file_size_kb):
                        if any(_pattern.match(_file.name) for _pattern in _compiled_non_indexed_files_patterns):
                            if self._db_extra_log_verbosity:
                                self._logger.warning(f"Skipping due to excluded path pattern: '{_file.name}'")
                            read_stats.skipped += 1
                            continue
                        read_stats.skipped += 1
                        if self._db_extra_log_verbosity:
                            self._logger.warning(f"Skipping bad file size: '{_file.name}' ({_size_kb:.1f} KB)")
                        continue

                    _content = _file.read_text(encoding='utf-8', errors='ignore')
                    if self._db_filter_files_content:
                        _content = self._filter_file_content(_content)

                    if _content is None:
                        read_stats.skipped += 1
                        if self._db_extra_log_verbosity:
                            self._logger.warning(f"Skipping empty or invalid file '{_file.name}' from '{str(_file)}'")
                        continue

                    _checksum = hashlib.blake2b(_content.encode('utf-8'), digest_size=8).hexdigest()

                    # Get file extension
                    _file_ext = _file.suffix
                    if _file_ext:
                        _file_ext = _file_ext[1:].lower()  # remove dot and lowercase
                    else:
                        _file_ext = _file.name.lower()  # fallback to full name like "makefile"

                    # Add the queue
                    result_queue.put((str(_file), _content, _stat.st_mtime, _checksum, _file_ext, _file.name))
                    read_stats.processed += 1

                except Exception as reader_error:
                    read_stats.errors += 1
                    self._logger.error(f"Failed to read '{_file.name}': {reader_error}")
                finally:
                    file_queue.task_done()

        def _writer_worker():
            """
            Thread worker that consumes parsed file data and writes it to the SQLite index.
            - Skips unchanged files based on checksum.
            - Commits batched inserts.
            - Updates statistics.
            """
            _conn = self._get_sql_connection()
            _conn.execute("BEGIN")
            _batch = []
            _meta_batch = []
            _meta_lookup = {}
            _path = "<unknown>"
            nonlocal write_stats
            write_stats.start_time = time.time()

            # Before loop starts
            if not self._clean_slate:
                self._logger.debug(f"Preloading metadata..")
                _meta_lookup = {
                    row[0]: row[1]
                    for row in _conn.execute("SELECT path, checksum FROM file_meta")
                }
                self._logger.debug(f"Metadata preloaded size {len(_meta_lookup)}")

            while True:
                _item = result_queue.get()
                if _item is None:
                    self._logger.debug(f"Queue is empty, consumer thread stopped")
                    break

                _log_stats()

                try:
                    _path, _content, _mtime, _checksum, _file_ext, _file_base = _item

                    # Skip unchanged files
                    if not self._clean_slate:
                        if _meta_lookup.get(_path) == _checksum:
                            write_stats.skipped += 1
                            continue

                    _batch.append((_path, _content))
                    _meta_batch.append((_path, _mtime, _checksum, _file_ext, _file_base))
                    write_stats.processed += 1

                    if len(_batch) >= XRAY_BATCH_SIZE:
                        try:
                            _conn.executemany("""
                                INSERT OR REPLACE INTO files (path, content)
                                VALUES (?, ?)
                            """, _batch)

                            _conn.executemany("""
                                INSERT OR REPLACE INTO file_meta (path, modified, checksum, ext, base)
                                VALUES (?, ?, ?, ?, ?)
                            """, _meta_batch)

                            _conn.commit()
                            _batch.clear()
                            _meta_batch.clear()

                        except Exception as sql_error:
                            self._logger.error(f"Batch insert failed at '{_path}': {sql_error}")
                            _conn.rollback()

                except Exception as write_error:
                    self._logger.error(f"Failed to process '{os.path.basename(_path)}': {write_error}")
                    write_stats.errors += 1
                finally:
                    result_queue.task_done()

            # Final flush
            if _batch:
                try:
                    _conn.executemany("""
                                INSERT OR REPLACE INTO files (path, content)
                                VALUES (?, ?)
                            """, _batch)
                    _conn.executemany("""
                                INSERT OR REPLACE INTO file_meta (path, modified, checksum, ext, base)
                                VALUES (?, ?, ?, ?, ?)
                            """, _meta_batch)
                    _conn.commit()
                    write_stats.processed += len(_batch)

                except Exception as sql_error:
                    self._logger.error(f"Final batch insert failed: {sql_error}")
                    _conn.rollback()

            # Refresh current records (indexed files) count in the 'files' table post our indexing operation.
            self._db_row_count = _conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            self._logger.info(f"DB row count in 'files': {self._db_row_count}")

            _conn.close()
            _batch = []
            _meta_batch = []
            _log_stats(_summarize=True)

            # Update 'meta' table with the last indexed timestamp
            self._update_meta_table(clean_slate=False)

        # ----------------------------------------------------------------------
        #
        # Indexing logic entry-point.
        #
        # ----------------------------------------------------------------------

        queued_files = _add_files_to_queue()
        if not queued_files:
            self._logger.debug("No files matched indexing criteria — queue is empty.")
            return True  # Nothing to do

        # Set indexing state
        with self._lock:
            if self._state not in (XRayStateType.INITIALIZED, XRayStateType.RUNNING):
                raise RuntimeError(f"XRay Can't perform indexing on state '{self._state.name}'")
            self._state = XRayStateType.INDEXING

        self._logger.info(f"Starting background indexing of approximately {queued_files} files..")
        if self._db_filter_files_content:
            self._logger.info("Files will be filtered prior to indexing")

        # Create worker threads.
        readers = [Thread(target=_reader_worker, daemon=True, name="IndexerReader") for _ in range(XRAY_NUM_READERS)]
        writer = Thread(target=_writer_worker, daemon=True, name="IndexerWriter")

        # Start all readers and writer thread
        for reader in readers:
            reader.start()
        writer.start()

        # Wait for all files to be processed
        file_queue.join()
        for _ in readers:
            file_queue.put(None)
        for r in readers:
            r.join()

        # Wait for the writer to complete
        result_queue.join()
        result_queue.put(None)
        writer.join()
        return True

    def _management_thread(self):
        """
        Internal thread entry point for managing internal states, initializing and performing the indexing process.
        This function is intended to be executed in a background thread.
        """

        time.sleep(1)  # Minimal delay before starting

        try:

            # Initialize the database and set the state
            self._initialize_database()
            with self._lock:
                self._state = XRayStateType.INITIALIZED

            # If the database is recent enough, perform indexing and transition to the running state upon success.
            has_recently_indexed = self._tool_box.is_recent_event(event_date=self._db_last_indexed_date,
                                                                  days_back=self._db_max_index_age_days)
            if has_recently_indexed:
                self._logger.info("Database was recently indexed, skipping")
            else:
                self._tool_box.show_status(message="XRayDB starting background indexing", expire_after=2,
                                           erase_after=True)
                has_recently_indexed = self._perform_indexing()

            if has_recently_indexed:
                with self._lock:
                    self._logger.info("Database initialized")
                    self._state = XRayStateType.RUNNING
                    self._tool_box.show_status(message="XRayDB is up and running.", expire_after=2, erase_after=True)

        except Exception as indexing_error:
            # Any exception leads to error state from which there is no recovery.
            self._logger.error(f"Indexer error: {indexing_error}")
            with self._lock:
                self._state = XRayStateType.ERROR
                self._tool_box.show_status("XrayDB Error, check logs.", status_type=PromptStatusType.ERROR,
                                           expire_after=2,
                                           erase_after=True)

    @staticmethod
    def _format_cell(value: Any, col_name: str) -> str | Text:
        """
        Format individual cell values based on column name and type.
        - Converts Unix timestamps to human-readable dates for 'modified'
        - Makes paths clickable using Rich's hyperlink-safe Text object
        """
        if value is None:
            return ""

        if isinstance(value, float) and "modified" in col_name.lower():
            try:
                return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                return str(value)

        if "path" in col_name.lower() and isinstance(value, str) and value.startswith("/"):
            text = Text(value, style="cyan")
            text.stylize(f"link file://{value}")
            return text

        return str(value)

    def query(self, query: str) -> Optional[str]:
        """
        Execute a raw SQL query against the content database and return the result as a string.
        Args:
            query (str): SQL query string.
        Returns:
            Optional[str]: Result rows joined by newlines, or None on failure.
        """

        conn: Optional[sqlite3.Connection] = None

        with self._lock:
            if self._state != XRayStateType.RUNNING:
                raise RuntimeError(f"XRay Can't execute query on state '{self._state.name}'")
            if self._serving_query:
                raise RuntimeError("Another query is already running")
            self._serving_query = True

        try:
            conn = self._get_sql_connection(read_only=True)
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            if not rows:
                return None

            return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)

        except Exception as db_error:
            raise db_error from db_error

        finally:
            if conn:
                conn.close()
            with self._lock:
                self._serving_query = False

    def query_raw(self, query: str, params: Optional[tuple[Any, ...]] = None, print_table: bool = False) -> Optional[
        list[tuple]]:
        """
        Execute a raw SQL query and optionally display the result as a Rich table.

        Args:
            query (str): SQL query to run.
            params (Optional[tuple]): Optional SQL parameters for safe substitution.
            print_table (bool): If True, print the result as a Rich table.

        Returns:
            Optional[list[tuple]]: List of result tuples, or None if query fails.
        """
        conn: Optional[sqlite3.Connection] = None

        with self._lock:
            if self._state != XRayStateType.RUNNING:
                raise RuntimeError(f"Can't execute query on state '{self._state.name}'")
            if self._serving_query:
                raise RuntimeError("Another query is already running")
            self._serving_query = True

        try:
            conn = self._get_sql_connection(read_only=True)
            cursor = conn.cursor()

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            rows = cursor.fetchall()

            if print_table:
                columns = [desc[0] for desc in cursor.description]
                table = Table(title="Query Results", header_style="bold magenta", show_lines=False, box=box.ROUNDED)
                for col in columns:
                    table.add_column(str(col).title(), overflow="ellipsis", style="white")

                for row in rows:
                    formatted = [self._format_cell(cell, col) for cell, col in zip(row, columns)]
                    table.add_row(*formatted)

                self._console.print(table)
            return rows

        except Exception as db_error:
            raise db_error from db_error

        finally:
            if conn:
                conn.close()
            with self._lock:
                self._serving_query = False

    def start(self, force_clean_slate: Optional[bool] = None, quiet: bool = False) -> None:
        """
        Starts the background management thread responsible for initializing and managing the database.
        Args:
            force_clean_slate (bool, optional): If True, forcibly recreate the database from scratch.
                                                If False or None, current database will be reused if valid.
            quiet (bool): If False, expansion misses will result in raising an exception.
        """
        try:
            with self._lock:
                if self._state != XRayStateType.NO_INITIALIZED:
                    raise RuntimeError(f"Cannot start from state '{self._state.name}'")

            if self._manage_thread and self._manage_thread.is_alive():
                raise RuntimeError(f"Cannot start, Indexer thread is already running")

            # Respect caller's request to start with a clean slate
            if force_clean_slate:
                self._clean_slate = True

            self._stop_flag.clear()
            self._manage_thread = threading.Thread(
                target=self._management_thread,
                name="XRayManager",
                daemon=True
            )
            self._manage_thread.start()

        except Exception as exception:
            exception_message: Optional[str] = f"XRayDB Exception: {exception}"
            if quiet:
                self._logger.error(exception_message)
            else:
                raise RuntimeError(exception_message) from exception

    @property
    def state(self) -> XRayStateType:
        with self._lock:
            return self._state
