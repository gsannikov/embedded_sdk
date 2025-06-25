"""
Script:         logger.py
Author:         AutoForge Team

Description:
    Core logging service for AutoForge. Provides flexible management of multiple handler types
    including memory, console, and file logging. Supports dynamic enabling and disabling of handlers,
    customizable log formatting, and transparent in-memory logging.

    Memory-based logging allows the application to capture logs early—before a final destination
    (e.g., file or console) is known. Logs can later be flushed to any enabled handler, ensuring
    nothing is lost during early-stage initialization.
"""

import json
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from enum import IntFlag, auto
from html import unescape
from typing import Any
from typing import Optional, ClassVar

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (
    CoreModuleInterface, FieldColorType, PROJECT_NAME)

AUTO_FORGE_MODULE_NAME = "CoreLogger"
AUTO_FORGE_MODULE_DESCRIPTION = "AutoForge Logging Provider"


# ------------------------------------------------------------------------------
#
# Note:
#   This module is used during early initialization and must remain self-contained.
#   Avoid importing any project-specific code or third-party libraries to ensure
#   portability and prevent circular import issues.
#
# ------------------------------------------------------------------------------

class LogHandlersTypes(IntFlag):
    """
    Bitwise-capable enumeration for supported logger handlers.
    Allows combining multiple handlers using bitwise OR.
    """
    NO_HANDLERS = 0
    CONSOLE_HANDLER = auto()
    FILE_HANDLER = auto()
    MEMORY_HANDLER = auto()


class _PausableFilter(logging.Filter):
    """
    A logging filter that allows dynamic pausing and resuming of log output.
    When attached to a handler, this filter controls whether log records are
    passed through based on the 'enabled' flag.

    Attributes:
        enabled (bool): If True, log records are allowed through. If False, all
                        records are suppressed by this filter.
    """

    def __init__(self):
        """
        Initialize the filter in the enabled state.
        """
        super().__init__()
        self.enabled = True

    def filter(self, _record: logging.LogRecord) -> bool:
        """
        Determine whether the specified record is to be logged.
        Args:
            _record (logging.LogRecord): The log record to filter.
        Returns:
            bool: True if the record should be processed, False to suppress.
        """
        return self.enabled


class _ColorFormatter(logging.Formatter):
    """
    Custom logging formatter that enhances log readability by:
    - Optionally adding ANSI color codes for console output
    - Displaying level names in fixed-width aligned format
    - Supporting consistent timestamp and message formatting
    """

    # noinspection SpellCheckingInspection
    def __init__(self, fmt=None, datefmt=None, style='%',
                 handler: LogHandlersTypes = LogHandlersTypes.NO_HANDLERS,
                 parent_logger: Optional["CoreLogger"] = None):
        super().__init__(fmt, datefmt, style)

        # Store the associated handler with this class
        self._handler: Optional[LogHandlersTypes] = handler
        if not isinstance(parent_logger, CoreLogger):
            raise ValueError(f"formatter expected 'CoreLogger', got '{type(parent_logger).__name__}' instead.")

        self._auto_logger: CoreLogger = parent_logger
        self._clean_tokens = self._auto_logger.cleanup_patterns_list

        # Enable colors only when used with a console handler and the logger allows it
        self._enable_colors = (
                LogHandlersTypes.CONSOLE_HANDLER in self._handler and self._auto_logger.is_console_colors_enabled())

    def clean_log_line(self, line: str) -> str:
        """
        Applies cleanup patterns to a log line using regular expressions.
        For each pattern in self._clean_tokens (if set), attempts to remove matching parts
        from the line. All exceptions are suppressed to ensure robustness. If a pattern fails,
        the line is returned as-is for that case.

        Args:
            line (str): The input log line.

        Returns:
            str: The cleaned log line.
        """
        if not self._clean_tokens:
            return line

        for pattern in self._clean_tokens:
            with suppress(Exception):
                line = re.sub(pattern, "", line).lstrip()
        return line

    def formatTime(self, record, date_format=None, base_date_format=None):  # noqa: N802
        """
        Format the log timestamp, adding milliseconds to the output.
        """
        if date_format:
            s = datetime.fromtimestamp(record.created).strftime(date_format)
        else:
            s = datetime.fromtimestamp(record.created).strftime(base_date_format)
        return f"{s}:{int(record.msecs):03d}"  # Append milliseconds

    def format(self, record):
        """
        Format the log message for terminal colored mode or as bare text.
        """
        terminal_width: int = 1024

        try:

            # Apply tokens list regex cleanup
            record.msg = self.clean_log_line(record.msg)

            if not self._enable_colors:
                # Bare text mode: remove any ANSI color codes leftovers and maintain clear text
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                formatted_record = ansi_escape.sub('', super().format(record))

            else:
                # Attempt to get the client terminal width, use the default on error
                with suppress(Exception):
                    terminal_width = os.get_terminal_size().columns

                # This mode is for terminal output; apply color and formatting enhancements for better readability.
                level_name_color = self._auto_logger.LOG_LEVEL_COLORS.get(record.levelname, Fore.WHITE)
                # noinspection SpellCheckingInspection
                record.levelname = f"{level_name_color}{record.levelname:<8}{Style.RESET_ALL}"

                # Flatten for terminal printouts
                # Replace \r, \n, \t with a single space and then compress multiple spaces to one.
                cleaned_message = re.sub(r'[\r\n\t]+', ' ', record.msg)
                cleaned_message = re.sub(r'\s+', ' ', cleaned_message).strip()
                record.msg = cleaned_message

                # Dynamically trim for the user terminal width
                terminal_width -= 40  # Account for log level and date
                if len(record.msg) > terminal_width:
                    record.msg = record.msg[:terminal_width]

                # Apply the JSON formatting function to the message
                record.msg = self._message_format(record.getMessage())
                formatted_record = f"\r{Style.RESET_ALL}" + super().format(record)

            return formatted_record

        except OSError as os_error:
            if os_error.errno == 25:  # Inappropriate ioctl for device, colorama?
                return super().format(record)
            else:
                # Handle other OS errors
                return f"logger exception: {os_error!s}"
        except Exception as format_exception:
            # Handle any other exception that is not an OSError
            return f"logger exception {format_exception!s}"

    @staticmethod
    def _message_format(message: str):
        """
        Detect and pretty-print JSON or HTML content within a log message,
        and convert HTML content into a single-line error string.
        Parameters:
            message (str): The original log message that may contain JSON or HTML error content.

        Returns:
            str: The log message with JSON or HTML content formatted, if detected.
        """
        error_parts: list = []  # Build the error message parts and clean each component

        # Check if the message contains JSON-like structure
        try:
            if isinstance(message, str) and '{' in message:
                text_part, json_part = message.split('{', 1)
                json_part = '{' + json_part  # Add the curly brace back

                # Parse and pretty-print the JSON part
                error_data = json.loads(json_part)
                if 'errors' in error_data:
                    formatted_errors = [f"Error {error['status']}: {error['message']}" for error in
                                        error_data['errors']]
                    # Return the combined text and formatted JSON
                    return f"{text_part} {'; '.join(formatted_errors)}"
        except (json.JSONDecodeError, ValueError):
            pass  # If JSON decoding fails, continue to check for HTML content

        # Check if the message contains HTML-like structure
        if isinstance(message, str) and '<html>' in message.lower():
            try:
                # Extract key parts of the HTML error message
                title_match = re.search(r'<title>(.*?)</title>', message, re.IGNORECASE | re.DOTALL)
                h1_match = re.search(r'<h1>(.*?)</h1>', message, re.IGNORECASE | re.DOTALL)
                paragraphs = re.findall(r'<p>(.*?)</p>', message, re.IGNORECASE | re.DOTALL)

                if title_match:
                    error_parts.append(unescape(title_match.group(1)).strip())
                if h1_match:
                    error_parts.append(unescape(h1_match.group(1)).strip())
                if paragraphs:
                    paragraph_texts = [unescape(paragraph).replace("<br />", "").strip() for paragraph in paragraphs]
                    error_parts.extend(paragraph_texts)

                # Join all parts into a single line, removing any extra whitespace
                return " | ".join(str(part) for part in error_parts).replace("\n", " ").replace("\r", " ")

            except re.error:
                pass  # If HTML parsing fails, return the original message

        return message


class CoreLogger(CoreModuleInterface):
    """
    Singleton logger class for managing application-wide logging with optional
    console and file handlers.

    Supports:
    - Singleton instantiation (only one logger instance per runtime)
    - Bitmask-based handler control via LoggerHandlers
    - Optional console color formatting
    - Exclusive mode to suppress all external loggers

    Example:
        logger = CoreLogger(logging.INFO, enable_console_colors=True)
        logger.set_handlers(LoggerHandlers.CONSOLE_HANDLER | LoggerHandlers.FILE_HANDLER)
    """

    # Dictionary to map log levels to colors using Colorama
    LOG_LEVEL_COLORS: ClassVar[dict[str, str]] = {'DEBUG': Fore.LIGHTCYAN_EX, 'INFO': Fore.LIGHTBLUE_EX,
                                                  'WARNING': Fore.YELLOW, 'ERROR': Fore.RED, 'CRITICAL': Fore.MAGENTA, }

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """

        self._log_file_name: Optional[str] = None
        self._enabled_handlers: LogHandlersTypes = LogHandlersTypes.NO_HANDLERS

        super().__init__(*args, **kwargs)

    def _initialize(self, log_level=logging.ERROR, console_enable_colors: bool = True,
                    console_output_state: bool = True,
                    erase_exiting_file: bool = True, exclusive: bool = True,
                    enable_memory_logger: bool = True,
                    configuration_data: Optional[dict[str, Any]] = None) -> None:

        """
        Initializes the CoreLogger with default logging level and handler configuration.
        Args:
            log_level (int): Logging level.
            console_enable_colors (bool): Enables ANSI color formatting for console output.
            console_output_state (bool): Controls whether console output is enabled.
            erase_exiting_file (bool): If True, na exiting log file will be erased.
            exclusive (bool): If True, disables all other non-CoreLogger loggers.
            enable_memory_logger (bool): If True, enables memory logger.
            configuration_data (dict, optional): Global AutoForge JSON configuration data.
        """

        self._logger: Optional[logging.Logger] = None
        self._internal_logger: Optional[logging.Logger] = None
        self._name: str = PROJECT_NAME
        self._log_level: int = log_level
        self._erase_exiting_file: bool = erase_exiting_file
        self._stream_console_handler: Optional[logging.StreamHandler] = None
        self._stream_file_handler: Optional[logging.FileHandler] = None
        self._stream_memory_handler: Optional[logging.StreamHandler] = None
        self._memory_logs_buffer = []  # List[str] of formatted log lines
        self._enable_console_colors: bool = console_enable_colors
        self._output_stdout: bool = console_output_state
        self._exclusive: bool = exclusive

        # Attempt to get the cleanup regex pattern from the configuration object
        self.cleanup_patterns_list: Optional[list] = None
        if configuration_data is not None:
            self.cleanup_patterns_list = configuration_data.get('log_cleanup_patterns', [])

        # noinspection SpellCheckingInspection
        self._log_format: str = '[%(asctime)s %(levelname)-8s] %(name)-14s: %(message)s'
        self._date_format: str = '%d-%m %H:%M:%S'

        try:
            self._logger = logging.getLogger(self._name)
            self._logger.setLevel(self._log_level)

            if self._exclusive:
                self._set_exclusive()

            # Auto enable memory logging if specified
            if enable_memory_logger:
                self._enable_handlers(LogHandlersTypes.MEMORY_HANDLER)

            # Create logger for the logger
            self._internal_logger = self.get_logger(name="Logger")

        except Exception as logger_exception:
            raise RuntimeError(f"Logger exception {str(logger_exception)}") from logger_exception

    def _set_exclusive(self):
        """
        Make this logger instance exclusive, meaning mute any other loggers that may try
        to piggyback this instance while it's running.
        """

        if self._logger is None:
            raise RuntimeError(
                f"method not allowed without logger instance or '{self.__class__.__name__}' is already initialized")

        # Disable propagation so logs do not bubble up to ancestor loggers (like root)
        self._logger.propagate = False

        # Mute all other loggers
        for name in list(logging.Logger.manager.loggerDict.keys()):
            if name != self._name:
                other_logger = logging.getLogger(name)
                other_logger.disabled = True

    def _get_stream_handler(self, handler_type: LogHandlersTypes) -> Any:
        """ Return the stream handler associated with the provided handler type """
        if handler_type == LogHandlersTypes.MEMORY_HANDLER:
            return self._stream_memory_handler
        elif handler_type == LogHandlersTypes.FILE_HANDLER:
            return self._stream_file_handler
        elif handler_type == LogHandlersTypes.CONSOLE_HANDLER:
            return self._stream_console_handler
        else:
            raise RuntimeError(f'invalid handler {handler_type.name}')

    def _enable_handlers(self, handlers: LogHandlersTypes):
        """
        Enable the requested handlers if not already enabled.
        No-op for already active handlers.
        """

        self._logger.setLevel(self._log_level)

        if LogHandlersTypes.MEMORY_HANDLER in handlers and self._stream_memory_handler is None:
            # ------------------------------------------------------------------------------
            #
            #  Create logger memory handler
            #
            # ------------------------------------------------------------------------------

            formatter: Optional[_ColorFormatter] = (_ColorFormatter(fmt=self._log_format, datefmt=self._date_format,
                                                                    parent_logger=self,
                                                                    handler=LogHandlersTypes.MEMORY_HANDLER))
            # noinspection PyTypeChecker
            self._stream_memory_handler = logging.StreamHandler(self)
            self._stream_memory_handler.setFormatter(formatter)
            self._logger.addHandler(self._stream_memory_handler)
            self._enabled_handlers |= LogHandlersTypes.MEMORY_HANDLER

        if LogHandlersTypes.CONSOLE_HANDLER in handlers and self._stream_console_handler is None:
            # ------------------------------------------------------------------------------
            #
            #  Create logger console handler
            #
            # ------------------------------------------------------------------------------

            formatter: Optional[_ColorFormatter] = (_ColorFormatter(fmt=self._log_format, datefmt=self._date_format,
                                                                    parent_logger=self,
                                                                    handler=LogHandlersTypes.CONSOLE_HANDLER))
            pause_filer = _PausableFilter()
            pause_filer.enabled = self._output_stdout

            self._stream_console_handler = logging.StreamHandler(sys.stdout)
            self._stream_console_handler.setFormatter(formatter)
            self._stream_console_handler.addFilter(pause_filer)
            self._logger.addHandler(self._stream_console_handler)
            self._enabled_handlers |= LogHandlersTypes.CONSOLE_HANDLER

        if LogHandlersTypes.FILE_HANDLER in handlers and self._stream_file_handler is None:
            # ------------------------------------------------------------------------------
            #
            #  Create logger file handler
            #
            # ------------------------------------------------------------------------------

            if not self._log_file_name:
                raise RuntimeError("file name is not defined while enabling file handler")

            formatter: Optional[_ColorFormatter] = (_ColorFormatter(fmt=self._log_format, datefmt=self._date_format,
                                                                    parent_logger=self,
                                                                    handler=LogHandlersTypes.FILE_HANDLER))
            self._stream_file_handler = logging.FileHandler(self._log_file_name)
            self._stream_file_handler.setFormatter(formatter)
            self._logger.addHandler(self._stream_file_handler)
            self._enabled_handlers |= LogHandlersTypes.FILE_HANDLER

        self._logger.propagate = True
        self._logger.name = self._name

    def _disable_handlers(self, handlers: LogHandlersTypes):
        """
        Disable and release the specified handlers if its currently enabled.
        No-op for already non-active handlers.
        """

        if not hasattr(self, "_enabled_handlers"):
            self._enabled_handlers = LogHandlersTypes.NO_HANDLERS

        if LogHandlersTypes.MEMORY_HANDLER in handlers and self._stream_memory_handler is not None:
            self._stream_memory_handler.flush()
            self._stream_memory_handler.close()
            self._logger.removeHandler(self._stream_memory_handler)
            self._stream_memory_handler = None
            self._enabled_handlers &= ~LogHandlersTypes.MEMORY_HANDLER

        if LogHandlersTypes.CONSOLE_HANDLER in handlers and self._stream_console_handler is not None:
            self._stream_console_handler.flush()
            self._stream_console_handler.close()
            self._logger.removeHandler(self._stream_console_handler)
            self._console_filter = None  # Invalidate console filer
            self._stream_console_handler = None
            self._enabled_handlers &= ~LogHandlersTypes.CONSOLE_HANDLER

        if LogHandlersTypes.FILE_HANDLER in handlers and self._stream_file_handler is not None:
            self._stream_file_handler.flush()
            self._stream_file_handler.close()
            self._logger.removeHandler(self._stream_file_handler)
            self._stream_file_handler = None
            self._enabled_handlers &= ~LogHandlersTypes.FILE_HANDLER

    def flush_memory_logs(self, destination_handler: LogHandlersTypes):
        """
        Flushes buffered memory logs to the specified destination handler and disables
        the memory handler afterward.
        Args:
            destination_handler (LogHandlersTypes): The target log stream type to flush into.
            Must be a single handler bit and already enabled.
        Notes:
            - Once flushed, the memory buffer is cleared and the memory handler is disabled.
        """

        # Prevent self-flushing (makes no sense)
        if destination_handler == LogHandlersTypes.MEMORY_HANDLER:
            raise ValueError("Cannot flush memory logs into MEMORY_HANDLER.")

        # Ensure only a single bit is set (i.e., a single handler type)
        value = int(destination_handler)  # preferred over .value
        if value & (value - 1) != 0:
            raise ValueError("Expected a single handler flag, got multiple.")

        if destination_handler not in self._enabled_handlers:
            raise ValueError(f"Destination handler {destination_handler.name} is not enabled.")

        if LogHandlersTypes.MEMORY_HANDLER not in self._enabled_handlers:
            raise RuntimeError("Memory handler is not enabled — nothing to flush.")

        destination_stream_handler = self._get_stream_handler(handler_type=destination_handler)
        if destination_stream_handler is None:
            raise RuntimeError(
                f"No active stream handler found for {destination_handler.name}, "
                f"even though it is marked as enabled."
            )

        self._internal_logger.debug(
            f"Memory logger flushing {len(self._memory_logs_buffer)} recods to destination logger")

        # Flush memory buffer to the destination stream
        for entry in self._memory_logs_buffer:
            destination_stream_handler.stream.write(entry + "\n")

        destination_stream_handler.flush()
        self._memory_logs_buffer.clear()

        # Disable memory handler after flush
        self._disable_handlers(LogHandlersTypes.MEMORY_HANDLER)

    def write(self, message: str):
        """
        Called exclusively by the memory StreamHandler.
        Captures formatted log output and stores it in memory for deferred flushing.
        """
        if message.strip():
            self._memory_logs_buffer.append(message.strip())

    def flush(self):
        """Required by StreamHandler interface — safe no-op."""
        pass

    def set_log_file_name(self, file_name: Optional[str] = None):
        """
        Sets a new log file name. If a file handler is currently enabled,
        it will be temporarily disabled and re-enabled with the new file name.
        Args:
            file_name (Optional[str]): The new file path. If None, keeps the current path.
        """

        # Disable file handler if currently active
        was_enabled = LogHandlersTypes.FILE_HANDLER in self._enabled_handlers
        if was_enabled:
            self._disable_handlers(LogHandlersTypes.FILE_HANDLER)

        if file_name is None:
            # Generate a name in temp path of file nam was not specified
            timestamp = datetime.now().strftime('%d_%m_%H_%M_%S')
            temp_dir = tempfile.gettempdir()
            log_file_name = os.path.join(temp_dir, f"{self._name}_{timestamp}.log")
        else:
            # Expand and set the file name
            expanded_name: str = os.path.expanduser(os.path.expandvars(file_name))
            expanded_name = os.path.normpath(expanded_name)
            if not os.path.isabs(expanded_name):
                expanded_name = os.path.abspath(expanded_name)
            log_file_name = expanded_name

        # Optionally remove exiting log file
        if self._erase_exiting_file and os.path.exists(log_file_name):
            os.remove(log_file_name)

        self._log_file_name = log_file_name

        # Re-enable if it was active before
        if was_enabled:
            self._enable_handlers(LogHandlersTypes.FILE_HANDLER)

    def get_log_filename(self) -> Optional[str]:
        """
        Returns the current log file name used by the file handler.
        Returns:
            Optional[str]: Full path to the active log file, or None if not set.

        """
        return self._log_file_name

    def set_handlers(self, handlers: LogHandlersTypes):
        """
        Enables the specified log handlers and disables all others.
        Args:
            handlers (LogHandlersTypes): A bitmask of handler types to enable.
                                         Use bitwise OR to combine multiple handlers.
                                         Example:
                                             LogHandlersTypes.CONSOLE_HANDLER | LogHandlersTypes.FILE_HANDLER
        """
        to_disable = self._enabled_handlers & ~handlers
        to_enable = handlers & ~self._enabled_handlers

        if to_disable:
            self._disable_handlers(to_disable)

        if to_enable:
            self._enable_handlers(to_enable)

    def show(self, cheerful: bool = False, field_colors: Optional[Sequence[FieldColorType]] = None) -> None:
        """
        Display the contents of the log file, either plainly or with colorized formatting.
        """
        if not self._log_file_name or not os.path.isfile(self._log_file_name):
            print(f"{Fore.RED}No log file available.{Style.RESET_ALL}")
            return

        max_width = shutil.get_terminal_size((80, 20)).columns - 5
        level_pattern = re.compile(r"\[(.*?)\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+] (\S+\s*): (.*)")
        print()

        with open(self._log_file_name) as f:
            for line in f:
                line = line.rstrip('\n')

                if not cheerful:
                    # Adjust to the terminal width formal
                    if len(line) > max_width:
                        line = line[:max_width - 3] + '...'
                    print(line)
                    continue

                # Adjust to the terminal width (cheerfully)
                if len(line) > max_width:
                    line = line[:max_width - 3] + f"{Fore.LIGHTYELLOW_EX}...{Style.RESET_ALL}"

                match = level_pattern.match(line)
                if not match:
                    print(line)
                    continue

                timestamp, level, module, message = match.groups()

                # Apply level color
                level_color = self.LOG_LEVEL_COLORS.get(level, Fore.WHITE)
                colored_level = f"{level_color}{level:<8}{Style.RESET_ALL}"

                # Apply module color if matched
                color = Fore.WHITE
                clear_module = module.strip()
                if field_colors:
                    for fc in field_colors:
                        if clear_module == fc.field_name:
                            color = fc.color
                            break
                colored_module = f"{color}{module:<14}{Style.RESET_ALL}"

                # Process message
                if message.startswith("> "):
                    message = f"{Fore.MAGENTA}> {Fore.LIGHTBLACK_EX}{message[2:]}{Style.RESET_ALL}"
                else:
                    # Highlight keywords
                    def _highlight(word: str) -> str:
                        if re.search(r"\bwarning\b", word, re.IGNORECASE):
                            return f"{Fore.YELLOW}{Style.BRIGHT}{word}{Style.RESET_ALL}"
                        elif re.search(r"\berror\b", word, re.IGNORECASE):
                            return f"{Fore.RED}{Style.BRIGHT}{word}{Style.RESET_ALL}"
                        return word

                    message = ''.join(_highlight(w) for w in re.split(r'(\W+)', message))

                # Final line
                print(f"[{timestamp} {colored_level}] {colored_module}: {message}")
            print()

    @staticmethod
    def get_base_logger() -> Optional[logging.Logger]:
        """
        Returns the logger instance the base logger instance.
        Returns:
            Logger: The base logger instance or None if the base logger was not initialized.
        """
        local_instance = CoreLogger.get_instance()
        return local_instance._logger if (local_instance and local_instance._logger) else None

    @staticmethod
    def set_output_enabled(logger: Optional[logging.Logger] = None, state: bool = True) -> None:
        """
        Enable or disable log output for any logger by toggling _PausableFilter instances
        attached to its handlers.

        Args:
            logger (logging.Logger): The logger whose handlers will be inspected, if None use base logger.
            state (bool): True to enable output, False to disable.
        """
        target_logger: Optional[logging.Logger] = CoreLogger.get_base_logger() if logger is None else logger
        if target_logger is None:
            raise ValueError("Logger instance is required.")

        # Iterate and adjust the pause filter everywhere
        for handler in target_logger.handlers:
            for pause_filter in handler.filters:
                if isinstance(pause_filter, _PausableFilter):
                    pause_filter.enabled = state

        # Update the base logger session state flag if we're working with the base logger.
        if target_logger == CoreLogger.get_base_logger():
            local_instance = CoreLogger.get_instance()
            if local_instance is not None:
                local_instance._output_stdout = state

    def get_logger(self, name: Optional[str] = None, log_level: Optional[int] = None,
                   console_stdout: Optional[bool] = None) -> logging.Logger:
        """
        Returns a logger instance. If a name is provided, returns a named logger
        sharing the same handlers and config as the CoreLogger.

        Args:
            name (Optional[str]): Custom display name for the logger (overrides .name).
            log_level (Optional[int]): Override for logger level.
            console_stdout (Optional[bool]): Sets the initial state of the console streamer.

        Returns:
            logging.Logger: Configured logger instance.
        """
        if name is None:
            # 'name' not provided, return the root logger defined by this class
            returned_instance: logging.Logger = self._logger

        else:
            # Gets a separate logger instance with its own name
            custom_logger = logging.getLogger(name)
            custom_logger.setLevel(log_level if log_level is not None else self._log_level)
            custom_logger.propagate = False  # Pass log records up the logger hierarchy
            custom_logger._autologger = self  # Note: hacking the logger, not guttered to work

            # Attach same handlers if not already attached
            for handler in self._logger.handlers:
                if handler not in custom_logger.handlers:
                    custom_logger.addHandler(handler)

            returned_instance: logging.Logger = custom_logger

        # Use the base logger console state flag if not specified
        if console_stdout is None:
            console_stdout = self._output_stdout

        # Set initial output state
        self.set_output_enabled(logger=returned_instance, state=console_stdout)
        return returned_instance

    def is_console_colors_enabled(self) -> Optional[bool]:
        """
        Checks whether ANSI console color formatting is enabled.
        Returns:
            Optional[bool]: True if console colors are enabled, False otherwise.
        """
        return self._enable_console_colors

    def close(self):
        """
        Close and remove all active logging handlers.
        """
        if self._logger is None:
            return  # Already cleaned up or never initialized

        if not hasattr(self, "_enabled_handlers"):
            self._enabled_handlers = LogHandlersTypes.NO_HANDLERS

        self._disable_handlers(LogHandlersTypes.CONSOLE_HANDLER | LogHandlersTypes.FILE_HANDLER)

        # Optional: reset the enabled mask
        self._enabled_handlers = LogHandlersTypes.NO_HANDLERS
