"""
Script:         logger.py
Author:         AutoForge Team

Description:
    AutoForge logging module allows for console and file logging, with and without ANSI colors.
"""

import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from enum import IntFlag, auto
from html import unescape
from typing import Optional

from colorama import Fore, Style

AUTO_FORGE_MODULE_NAME = "AutoLogger"
AUTO_FORGE_MODULE_DESCRIPTION = "AutoForge logging module"


class AutoHandlers(IntFlag):
    """
    Bitwise-capable enumeration for supported logger handlers.
    Allows combining multiple handlers using bitwise OR.
    """
    NO_HANDLERS = 0
    CONSOLE_HANDLER = auto()
    FILE_HANDLER = auto()


class _ColorFormatter(logging.Formatter):
    """
    Custom logging formatter that enhances log readability by:

    - Optionally adding ANSI color codes for console output
    - Displaying level names in fixed-width aligned format
    - Supporting consistent timestamp and message formatting
    """

    def __init__(self, fmt=None, datefmt=None, style='%', handler: AutoHandlers = AutoHandlers.NO_HANDLERS):
        super().__init__(fmt, datefmt, style)

        # Store the associated handler with this class
        self._handler: Optional[AutoHandlers] = handler
        self._auto_logger: Optional["AutoLogger"] = AutoLogger()

        # Enable colors only when used with a console handler and the logger allows it
        self._enable_colors = (
                AutoHandlers.CONSOLE_HANDLER in self._handler and
                self._auto_logger.is_console_colors_enabled()
        )

    def formatTime(self, record, date_format=None, base_date_format=None):
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

        # Create a dictionary to map log levels to colors using Colorama
        level_colors = {
            'INFO': Fore.LIGHTBLUE_EX,
            'WARNING': Fore.YELLOW,
            'ERROR': Fore.RED,
            'CRITICAL': Fore.MAGENTA,
        }

        try:

            # Attempt to get the client terminal width
            try:
                if self._enable_colors:
                    terminal_width = os.get_terminal_size().columns
            except OSError:
                pass

            if not self._enable_colors:
                # Bare text mode: remove any ANSI color codes leftovers and maintain clear text
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                formatted_record = ansi_escape.sub('', super().format(record))

            else:
                # This mode is for terminal output; apply color and formatting enhancements for better readability.
                level_name_color = level_colors.get(record.levelname, Fore.WHITE)
                record.levelname = f"{level_name_color}{record.levelname:<8}{Style.RESET_ALL}"

                # Flatten for terminal printouts
                # Replace \r, \n, \t with a single space and then compress multiple spaces to one.
                cleaned_message = re.sub(r'[\r\n\t]+', ' ', record.msg)
                cleaned_message = re.sub(r'\s+', ' ', cleaned_message).strip()
                record.msg = cleaned_message

                # Dynamically trim for the user terminal width
                terminal_width = terminal_width - 40  # Account for log level and date
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
                return f"logger exception: {str(os_error)}"
        except Exception as format_exception:
            # Handle any other exception that is not an OSError
            return f"logger exception {str(format_exception)}"

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
                    formatted_errors = [
                        f"Error {error['status']}: {error['message']}" for error in error_data['errors']
                    ]
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


class AutoLogger:
    """
    Singleton logger class for managing application-wide logging with optional
    console and file handlers.

    Supports:
    - Singleton instantiation (only one logger instance per runtime)
    - Bitmask-based handler control via LoggerHandlers
    - Optional console color formatting
    - Exclusive mode to suppress all external loggers

    Example:
        logger = AutoLogger(logging.INFO, enable_console_colors=True)
        logger.set_handlers(LoggerHandlers.CONSOLE_HANDLER | LoggerHandlers.FILE_HANDLER)
    """

    _instance = None
    _is_initialized: bool = False
    _log_level: int = logging.CRITICAL

    def __new__(cls, log_level=logging.WARNING):
        """
        Enforces singleton behavior. If an instance already exists, it returns it;
        otherwise, creates a new instance.

        Args:
            log_level (int): Initial logging level (default to CRITICAL)

        Returns:
            AutoLogger: The singleton logger instance.
        """
        if cls._instance is None:
            cls._instance = super(AutoLogger, cls).__new__(cls)
            cls._log_level = log_level

        return cls._instance

    def __init__(self, log_level=logging.WARNING, enable_console_colors: bool = True, erase_exiting_file: bool = True,
                 exclusive: bool = True):
        """
        Initializes the AutoLogger with default logging level and handler configuration.

        Args:
            log_level (int): Logging level.
            enable_console_colors (bool): Enables ANSI color formatting for console output.
            erase_exiting_file (bool): If True, na exiting log file will be erased.
            exclusive (bool): If True, disables all other non-AutoLogger loggers.
        """
        if not AutoLogger._is_initialized:
            self._logger: Optional[logging.Logger] = None
            self._name: str = 'AutoForge'
            self._log_level: int = log_level
            self._erase_exiting_file: bool = erase_exiting_file
            self._enabled_handlers: AutoHandlers = AutoHandlers.NO_HANDLERS
            self._stream_console_handler: Optional[logging.StreamHandler] = None
            self._stream_file_handler: Optional[logging.FileHandler] = None
            self._enable_console_colors: bool = enable_console_colors
            self._exclusive: bool = exclusive

            self._log_format: str = '[%(asctime)s %(levelname)-8s] %(name)-14s: %(message)s'
            self._date_format: str = '%d-%m %H:%M:%S'

            timestamp = datetime.now().strftime('%d_%m_%H_%M_%S')
            temp_dir = tempfile.gettempdir()
            self._log_file_name = os.path.join(temp_dir, f"{self._name}_{timestamp}.log")

            try:
                self._logger = logging.getLogger(self._name)
                self._logger.setLevel(self._log_level)

                if self._exclusive:
                    self._set_exclusive()

                AutoLogger._is_initialized = True

            except Exception as ex:
                raise RuntimeError("failed to initialize AutoLogger") from ex

    def _set_exclusive(self):
        """
        Make this logger instance exclusive, meaning mute any other loggers that may try
        to piggyback this instance while it's running.
        """

        if self._logger is None or AutoLogger._is_initialized:
            raise RuntimeError(
                f"method not allowed without logger instance or '{self.__class__.__name__}' is already initialized")

        # Disable propagation so logs do not bubble up to ancestor loggers (like root)
        self._logger.propagate = False

        # Mute all other loggers
        for name in list(logging.Logger.manager.loggerDict.keys()):
            if name != self._name:
                other_logger = logging.getLogger(name)
                other_logger.disabled = True

    def _enable_handlers(self, handlers: AutoHandlers):
        """
        Enable the requested handlers if not already enabled.
        No-op for already active handlers.
        """

        if not hasattr(self, "_enabled_handlers"):
            self._enabled_handlers = AutoHandlers.NO_HANDLERS

        self._logger.setLevel(self._log_level)

        if AutoHandlers.CONSOLE_HANDLER in handlers:
            if self._stream_console_handler is None:
                # Create dedicated formatter instance
                formatter: Optional[_ColorFormatter] = (
                    _ColorFormatter(fmt=self._log_format, datefmt=self._date_format,
                                    handler=AutoHandlers.CONSOLE_HANDLER))

                self._stream_console_handler = logging.StreamHandler(sys.stdout)
                self._stream_console_handler.setFormatter(formatter)
                self._logger.addHandler(self._stream_console_handler)
                self._enabled_handlers |= AutoHandlers.CONSOLE_HANDLER

        if AutoHandlers.FILE_HANDLER in handlers:
            if self._stream_file_handler is None:
                if not self._log_file_name:
                    raise RuntimeError("log file name is not defined.")

                # Create dedicated formatter instance
                formatter: Optional[_ColorFormatter] = (
                    _ColorFormatter(fmt=self._log_format, datefmt=self._date_format,
                                    handler=AutoHandlers.FILE_HANDLER))

                self._stream_file_handler = logging.FileHandler(self._log_file_name)
                self._stream_file_handler.setFormatter(formatter)
                self._logger.addHandler(self._stream_file_handler)
                self._enabled_handlers |= AutoHandlers.FILE_HANDLER

        self._logger.propagate = True
        self._logger.name = self._name

    def _disable_handlers(self, handlers: AutoHandlers):
        """
        Disable the requested handlers if currently enabled.
        """

        if not hasattr(self, "_enabled_handlers"):
            self._enabled_handlers = AutoHandlers.NO_HANDLERS

        if AutoHandlers.CONSOLE_HANDLER in handlers and self._stream_console_handler is not None:
            self._logger.removeHandler(self._stream_console_handler)
            self._stream_console_handler.close()
            self._stream_console_handler = None
            self._enabled_handlers &= ~AutoHandlers.CONSOLE_HANDLER

        if AutoHandlers.FILE_HANDLER in handlers and self._stream_file_handler is not None:
            self._logger.removeHandler(self._stream_file_handler)
            self._stream_file_handler.close()
            self._stream_file_handler = None

    def set_log_file_name(self, file_name: Optional[str] = None):
        """
        Sets a new log file name. If a file handler is currently enabled,
        it will be temporarily disabled and re-enabled with the new file name.

        Args:
            file_name (Optional[str]): The new file path. If None, keeps the current path.
        """

        if not AutoLogger._is_initialized or self._logger is None:
            raise RuntimeError("logger is not properly initialized.")

        # Disable file handler if currently active
        was_enabled = AutoHandlers.FILE_HANDLER in self._enabled_handlers
        if was_enabled:
            self._disable_handlers(AutoHandlers.FILE_HANDLER)

        # Expand and set the file name
        self._log_file_name = file_name or self._log_file_name
        expanded_name: str = os.path.expanduser(os.path.expandvars(self._log_file_name))
        expanded_name = os.path.normpath(expanded_name)

        # Optionally remove exiting log file
        if self._erase_exiting_file and os.path.exists(expanded_name):
            os.remove(expanded_name)

        self._log_file_name = expanded_name

        # Re-enable if it was active before
        if was_enabled:
            self._enable_handlers(AutoHandlers.FILE_HANDLER)

    def get_log_filename(self) -> Optional[str]:
        """
        Returns the current log file name used by the file handler.
        Returns:
            Optional[str]: Full path to the active log file, or None if not set.

        """
        if not AutoLogger._is_initialized or self._logger is None:
            raise RuntimeError("logger is not properly initialized.")

        return self._log_file_name

    def set_handlers(self, handlers: AutoHandlers):
        """
        Set up the logger with optional console and file handlers.
        Args:
            handlers (AutoHandlers): A bitmask of handler types to enable.
                                       For example:
                                       LoggerHandlers.CONSOLE_HANDLER | LoggerHandlers.FILE_HANDLER
        """

        if not AutoLogger._is_initialized or self._logger is None:
            raise RuntimeError("logger is not properly initialized.")

        if not hasattr(self, "_enabled_handlers"):
            self._enabled_handlers = AutoHandlers.NO_HANDLERS

        to_disable = self._enabled_handlers & ~handlers
        to_enable = handlers & ~self._enabled_handlers

        if to_disable:
            self._disable_handlers(to_disable)

        if to_enable:
            self._enable_handlers(to_enable)

    def get_logger(self, name: Optional[str] = None, log_level: Optional[int] = None) -> logging.Logger:
        """
        Returns a logger instance. If a name is provided, returns a named logger
        sharing the same handlers and config as the AutoLogger.

        Args:
            name (Optional[str]): Custom display name for the logger (overrides .name).
            log_level (Optional[int]): Override for logger level.

        Returns:
            logging.Logger: Configured logger instance.
        """
        if not AutoLogger._is_initialized or self._logger is None:
            raise RuntimeError("logger is not properly initialized.")

        if name is None:
            # 'name' not provided, return the root logger defined by this class
            return self._logger

        # Get a separate logger instance with its own name
        custom_logger = logging.getLogger(name)
        custom_logger.setLevel(log_level if log_level is not None else self._log_level)
        custom_logger.propagate = False  # Optional: don't double-log via parent

        # Attach same handlers if not already attached
        for handler in self._logger.handlers:
            if handler not in custom_logger.handlers:
                custom_logger.addHandler(handler)

        return custom_logger

    def is_console_colors_enabled(self) -> Optional[bool]:
        """
        Checks whether ANSI console color formatting is enabled.

        Returns:
            Optional[bool]: True if console colors are enabled, False otherwise.
        """
        if not AutoLogger._is_initialized or self._logger is None:
            raise RuntimeError("Logger is not properly initialized.")

        return self._enable_console_colors

    def close(self):
        """
        Close and remove all active logging handlers.
        """
        if not AutoLogger._is_initialized or self._logger is None:
            return  # Already cleaned up or never initialized

        if not hasattr(self, "_enabled_handlers"):
            self._enabled_handlers = AutoHandlers.NO_HANDLERS

        self._disable_handlers(AutoHandlers.CONSOLE_HANDLER | AutoHandlers.FILE_HANDLER)

        # Optional: reset the enabled mask
        self._enabled_handlers = AutoHandlers.NO_HANDLERS
