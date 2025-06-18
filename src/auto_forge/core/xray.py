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

import hashlib
import os
import re
import sqlite3
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from queue import Queue
from threading import Thread, Lock
from typing import Optional, Any

# AutoForge imports
from auto_forge import (
    AutoLogger, AutoForgeModuleType, CoreModuleInterface, CoreRegistry, CoreSolution, CoreVariables, XRayStateType
)

AUTO_FORGE_MODULE_NAME = "XRayDB"
AUTO_FORGE_MODULE_DESCRIPTION = "Source Tree Data Base"


@dataclass
class _XRayStats:
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    start_time: float = 0

    def reset(self):
        self.processed = 0
        self.skipped = 0
        self.errors = 0
        self.start_time = time.time()


XRAY_NUM_WORKERS = os.cpu_count() or 4
XRAY_NUM_READERS = 4
XRAY_BATCH_SIZE = 50


# noinspection SqlNoDataSourceInspection
class CoreXRayDB(CoreModuleInterface):

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """

        self._managed_paths: Optional[list[Path]] = None
        self._index_path: Optional[Path] = None
        self._indexed_items: Optional[list[str]] = None
        self._pause_condition = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._sql_connection: Optional[sqlite3.Connection] = None
        self._sql_db_file: Optional[Path] = None
        self._lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._indexing_report_frequency: int = 1000
        self._has_indexed: bool = False
        self._fresh_db: bool = False
        self._rebuild_index_on_start = True  # User arguments to start()
        self._drop_current_index = False  # User arguments to start()
        self._purify_content: bool = True
        self._indexing_start_time: float = 0
        self._state: XRayStateType = XRayStateType.NO_INITIALIZED

        super().__init__(*args, **kwargs)

    def _initialize(self) -> None:
        """
        Initialize CoreXRay.
        """
        try:
            self._variables = CoreVariables.get_instance()
            self._solution = CoreSolution.get_instance()

            # Get a logger instance
            self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
            self._registry: CoreRegistry = CoreRegistry.get_instance()
            self._get_and_validate_paths()

            # Retrieve AutoForge package configuration
            self._configuration = self.auto_forge.get_instance().configuration
            if self._configuration is None:
                raise RuntimeError("package configuration data not available")

            self._indexed_items = self._configuration.get("indexed_items")
            if not isinstance(self._indexed_items, list) or len(self._indexed_items) < 1:
                raise RuntimeError("no indexed items defined")

            self._max_index_size_kb: int = self._configuration.get("max_index_size_kb", 1024)
            self._min_index_size_bytes: int = self._configuration.get("min_index_size_bytes", 2)
            self._non_indexed_path_patterns: list = self._configuration.get("non_indexed_path_patterns", [])
            self._quiet_skipped_file_patterns = self._configuration.get("quiet_skipped_file_patterns", [])
            self._purify_content = bool(self._configuration.get("purify_content", self._purify_content))
            self._extra_indexing_verbosity = bool(self._configuration.get("extra_indexing_verbosity", False))
            self._indexing_report_frequency = int(
                self._configuration.get("indexing_report_frequency", self._indexing_report_frequency))

            # Load excluded paths list from the solution
            self._excluded_paths: Any = self._solution.get_arbitrary_item(key="xray_excluded_path")
            if not isinstance(self._excluded_paths, list) or len(self._excluded_paths) < 1:
                self._logger.warning("Solution's excluded paths are either undefined or incorrectly formatted.")
                self._excluded_paths = []

            # Create regex pattern based on the configuration list
            self._compiled_quiet_patterns = [re.compile(p) for p in self._quiet_skipped_file_patterns]

            self._sql_db_file = self._index_path / "autoforge.db"

            # Registry for centralized access
            registry = CoreRegistry.get_instance()
            registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                     auto_forge_module_type=AutoForgeModuleType.CORE)

            self._state = XRayStateType.INITIALIZED

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
            return sqlite3.connect(f"file:{self._sql_db_file}?mode=ro", uri=True)
        return sqlite3.connect(str(self._sql_db_file))

    # noinspection SpellCheckingInspection
    def _init_sqlite_index(self, db_path: Path, drop_current_index: bool = False) -> None:
        """
        Create or open the SQLite index database.
        If the database file does not exist, a new one is created with the required tables and
        indexing structure (FTS5 for full-text content and metadata table for checksums).
        If `force_new` is True, an existing database will be deleted and rebuilt from scratch.

        Args:
            db_path (Path): Path to the SQLite database file.
            drop_current_index (bool): If True, existing DB will be deleted and recreated.
        """

        if drop_current_index:
            if db_path.exists():
                self._logger.warning(f"Existing SQLite index '{db_path}' deleted")
                db_path.unlink()

        if not db_path.exists():
            self._fresh_db = True
        else:
            self._logger.debug(f"Opening SQLite index: {db_path}")
            try:
                conn = sqlite3.connect(str(db_path))
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
                cursor.execute("""
                               SELECT name
                               FROM sqlite_master
                               WHERE type = 'table'
                                 AND name = 'files';
                               """)
                if not cursor.fetchone():
                    self._fresh_db = True
                else:
                    cursor.execute("SELECT 1 FROM files LIMIT 1;")  # Fast schema check

                # Check if index is being used
                cursor.execute("EXPLAIN QUERY PLAN SELECT checksum FROM file_meta WHERE path = ?", ("some_path",))
                plan = cursor.fetchall()
                self._logger.debug(f"Query plan for metadata lookup: {plan}")
                conn.close()

            except Exception as sql_error:
                self._logger.warning(f"Existing index is invalid or incompatible: {sql_error}")
                try:
                    db_path.unlink(missing_ok=True)
                    self._logger.info(f"Deleted corrupted index file: {db_path}")
                except Exception as delete_error:
                    self._logger.error(f"Failed to delete corrupted index file: {delete_error}")
                self._fresh_db = True

        if self._fresh_db:
            self._logger.info(f"Creating new SQLite index at: {db_path}")
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS files USING fts5(
                    path UNINDEXED,
                    content,
                    modified UNINDEXED,
                    checksum UNINDEXED,
                    tokenize = 'trigram'
                );
            """)

            cursor.execute("""
                           CREATE TABLE IF NOT EXISTS file_meta
                           (
                               path
                               TEXT
                               PRIMARY
                               KEY,
                               modified
                               REAL,
                               checksum
                               TEXT
                           );
                           """)

            cursor.execute("""
                           CREATE INDEX IF NOT EXISTS idx_file_meta_path ON file_meta(path);
                           """)

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
            conn.close()

    def _indexer_thread(self):
        """
        Internal thread entry point for performing the indexing process.
        This function is intended to be executed in a background thread.
        """
        try:
            # Initialize the database
            self._init_sqlite_index(db_path=self._sql_db_file, drop_current_index=self._drop_current_index)

            if self._fresh_db and not self._rebuild_index_on_start:
                raise RuntimeError("Database does not exist and index rebuild is disabled. XRay cannot proceed.")

            # Perform indexing
            if not self._rebuild_index_on_start:
                self._logger.warning("Indexes building was skipped")
                self._has_indexed = True
            else:
                self._has_indexed = self._perform_indexing()

            if self._has_indexed:
                with self._lock:
                    self._logger.info("XRayDB is running")
                    self._state = XRayStateType.RUNNING

        except Exception as indexing_error:
            self._logger.error(f"Indexer error: {indexing_error}")
            with self._lock:
                self._state = XRayStateType.ERROR

    # noinspection SpellCheckingInspection
    @staticmethod
    def _get_purify_content(text: str) -> Optional[str]:
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

        This function is the core of the indexing system. It spawns multiple reader threads to process and
        checksum eligible files, and a single writer thread to insert data into the SQLite database in a
        thread-safe, batched manner.

        Key Features:
        - Uses multiple threads to read files concurrently for improved performance.
        - Employs a dedicated writer thread to avoid SQLite concurrency issues.
        - Files are skipped if they are hidden or match predefined ignore patterns.
        - Files larger than a configurable threshold are skipped (with optional quiet pattern filtering).
        - Supports checksum-based change detection to avoid unnecessary re-indexing.
        - Logger is used sparingly: only warnings and errors are reported during indexing,
          and progress is reported periodically based on `self._report_frequency` (e.g. every N files).

        This method is performance-critical. It is safe to run multiple times, but all relevant state must be
        cleared/reset before reuse. Variables are reset at the beginning of the function to avoid reuse from
        previous runs.

        Note: Ensure that BATCH_SIZE and NUM_READERS are tuned to the system capacity. Extremely high values may
        lead to memory exhaustion or database contention.
        """

        def _should_skip_path(_path: Path) -> bool:
            """
            Determine if a path should be skipped during indexing.

            Skips if:
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
                    _part.startswith(".") or _part in self._non_indexed_path_patterns
                    for _part in _path.parts
            ):
                return True

            # Check against excluded path patterns
            if self._excluded_paths:
                _path_str = str(_path)
                if any(fnmatch(_path_str, pattern) for pattern in self._excluded_paths):
                    if self._extra_indexing_verbosity:
                        self._logger.warning(f"Skipping '{_path_str}' due to excluded paths rule")
                    return True

            return False

        def _matches(_file: Path, _indexed_items: list[str]) -> bool:
            """ Regex callback implementation """
            return _file.name in _indexed_items or _file.suffix.lstrip(".") in _indexed_items

        file_queue = Queue()
        result_queue = Queue()
        count_lock = Lock()
        total_processed: int = 0
        read_stats: _XRayStats = _XRayStats()
        write_stats: _XRayStats = _XRayStats()
        self._indexing_start_time = time.time()
        last_log_time = self._indexing_start_time

        with self._lock:
            paths = list(self._managed_paths or [])
            indexed_items = list(self._indexed_items or [])
            self._state = XRayStateType.INDEXING

        self._logger.info("Starting background indexing...")
        if self._purify_content:
            self._logger.info("Files will be purified prior to indexing")

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
                    if processed_count % self._indexing_report_frequency == 0:
                        elapsed = now - last_log_time
                        last_log_time = now  # Update for next round

                        rate = int(processed_count / elapsed) if elapsed > 0 else 0
                        total_processed += processed_count

                        details = [f"Handled {total_processed:>7,d} files ({rate:>5,d} files/sec)"]
                        if write_stats.skipped:
                            details.append(f"{write_stats.skipped} skipped")
                        if error_count:
                            details.append(f"{error_count} errors")

                        self._logger.debug(" | ".join(details))
                    else:
                        return
                else:
                    # Print summary statics
                    elapsed = now - self._indexing_start_time
                    rate = int(total_processed / elapsed) if elapsed > 0 else 0
                    details = f"Handled {total_processed:>7,d} files ({rate:>5,d} files/sec)"
                    self._logger.info(f"Summary {details}")

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

            while True:
                _file = file_queue.get()
                if _file is None:
                    break
                try:
                    _stat = _file.stat()
                    _size_kb = _stat.st_size / 1024
                    if (_stat.st_size < self._min_index_size_bytes) or (_size_kb > self._max_index_size_kb):
                        if any(_pattern.match(_file.name) for _pattern in self._compiled_quiet_patterns):
                            if self._extra_indexing_verbosity:
                                self._logger.warning(f"Skipping due to excluded path pattern: '{_file.name}'")
                            read_stats.skipped += 1
                            continue
                        read_stats.skipped += 1
                        if self._extra_indexing_verbosity:
                            self._logger.warning(f"Skipping bad file size: '{_file.name}' ({_size_kb:.1f} KB)")
                        continue

                    _content = _file.read_text(encoding='utf-8', errors='ignore')
                    if self._purify_content:
                        _content = self._get_purify_content(_content)

                    if _content is None:
                        read_stats.skipped += 1
                        if self._extra_indexing_verbosity:
                            self._logger.warning(f"Skipping empty or invalid file '{_file.name}' from '{str(_file)}'")
                        continue

                    _checksum = hashlib.blake2b(_content.encode('utf-8'), digest_size=8).hexdigest()
                    result_queue.put((str(_file), _content, _stat.st_mtime, _checksum))
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
            if not self._fresh_db:
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
                    _path, _content, _mtime, _checksum = _item

                    # Skip unchanged files
                    if not self._fresh_db:
                        if _meta_lookup.get(_path) == _checksum:
                            write_stats.skipped += 1
                            continue

                    _batch.append((_path, _content, _mtime, _checksum))
                    _meta_batch.append((_path, _mtime, _checksum))
                    write_stats.processed += 1

                    if len(_batch) >= XRAY_BATCH_SIZE:
                        try:
                            _conn.executemany("""
                                INSERT OR REPLACE INTO files (path, content, modified, checksum)
                                VALUES (?, ?, ?, ?)
                            """, _batch)

                            _conn.executemany("""
                                INSERT OR REPLACE INTO file_meta (path, modified, checksum)
                                VALUES (?, ?, ?)
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
                                INSERT OR REPLACE INTO files (path, content, modified, checksum)
                                VALUES (?, ?, ?, ?)
                            """, _batch)
                    _conn.executemany("""
                                INSERT OR REPLACE INTO file_meta (path, modified, checksum)
                                VALUES (?, ?, ?)
                            """, _meta_batch)
                    _conn.commit()
                except Exception as sql_error:
                    self._logger.error(f"Final batch insert failed: {sql_error}")
                    _conn.rollback()
                    write_stats.errors += 1

            _conn.close()
            _log_stats()
            _batch = []
            _meta_batch = []

        """ Indexer entrypoint """
        readers = [Thread(target=_reader_worker, daemon=True, name="IndexerReader") for _ in range(XRAY_NUM_READERS)]
        writer = Thread(target=_writer_worker, daemon=True, name="IndexerWriter")

        for r in readers:
            r.start()
        writer.start()

        # Exclude paths
        for path in paths:
            for file in path.rglob("*"):
                if file.is_file() and _matches(file, indexed_items) and not _should_skip_path(file):
                    file_queue.put(file)

        file_queue.join()
        for _ in readers:
            file_queue.put(None)
        for r in readers:
            r.join()

        result_queue.join()
        result_queue.put(None)
        writer.join()

        _log_stats(_summarize=True)
        return True

    def query(self, query: str) -> Optional[str]:
        """
        Execute a raw SQL query against the content database and return the result as a string.
        Args:
            query (str): SQL query string.
        Returns:
            Optional[str]: Result rows joined by newlines, or None on failure.
        """
        if self._state != XRayStateType.RUNNING:
            raise RuntimeError("XRayDB is not running")

        conn = self._get_sql_connection(read_only=True)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return None

        return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)

    def query_raw(self, query: str) -> Optional[list[tuple[str, str]]]:
        """
        Execute a raw SQL query and return raw (path, content) tuples.
        This is primarily used for internal analysis or full-text scanning,
        where the full content of matching files is needed for further filtering.
        Args:
            query (str): SQL query to run.
        Returns:
            Optional[list[tuple[str, str]]]: List of (path, content) tuples,
            or None if query fails or returns no results.
        """
        if self._state != XRayStateType.RUNNING:
            raise RuntimeError("XRayDB is not running")

        with suppress(Exception):
            conn = self._get_sql_connection(read_only=True)
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            return rows

        return None

    def start(self, skip_index_refresh: bool = False, drop_current_index: bool = False) -> None:
        """
        Start the background indexing thread.
        Initializes the SQLite index (creating it if needed) and begins
        scanning the workspace for indexable files. The method returns
        immediately while indexing continues in the background.
        Args:
            skip_index_refresh (bool, optional): If True, skip index refresh
            drop_current_index (bool, optional): If True, drop current index and create new
        """
        with self._lock:

            if self._state not in {XRayStateType.INITIALIZED, XRayStateType.STOPPING}:
                raise RuntimeError(f"Cannot start; current state is {self._state.name}")

            if self._thread and self._thread.is_alive():
                self._logger.debug("Indexer thread already running.")
                return

            # Store user arguments
            self._drop_current_index = drop_current_index
            if skip_index_refresh:
                self._rebuild_index_on_start = False

            self._stop_flag.clear()
            self._thread = threading.Thread(target=self._indexer_thread, name="CoreXRayIndexer", daemon=True)
            self._thread.start()
            self._state = XRayStateType.INDEXING

    def stop(self) -> None:
        """
        Stop the background indexing process, if running.
        Gracefully signals all background threads to shut down and waits
        for completion. Does nothing if already stopped or uninitialized.
        """
        with self._lock:
            if self._state not in {XRayStateType.INDEXING, XRayStateType.RUNNING}:
                raise RuntimeError("Stop ignored: Indexer is not running.")

            self._state = XRayStateType.STOPPING
            self._stop_flag.set()
            with self._pause_condition:
                self._pause_condition.notify_all()
            if self._thread:
                self._thread.join()
                self._thread = None

            self._state = XRayStateType.INITIALIZED

    @property
    def state(self) -> XRayStateType:
        return self._state
