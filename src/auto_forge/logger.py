#!/usr/bin/env python3
"""
Script:         logger.py
Author:         Intel AutoForge team

Description:
    AutoForge logging module allows for console and file logging, with and without ANSI colors.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from html import unescape
from typing import Optional

from colorama import Fore, Style, init

# Initialize Colorama (needed for Windows, optional on other platforms)
init(autoreset=True)


class NullLogger:
    """
    A logger that does nothing with log messages.

    This class provides empty implementations for common logging methods,
    allowing it to be used as a drop-in replacement where logging might be optional.
    """

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass

    def critical(self, msg):
        pass


class AutoForgeColorFormatter(logging.Formatter):
    """
    Custom logging formatter class to add color to log levels and maintain
    fixed-width formatting for log messages.
    """

    def __init__(self, fmt=None, datefmt=None, style='%', no_colors=False):
        super().__init__(fmt, datefmt, style, no_colors)

        self.no_colors = no_colors

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
        terminal_width = 1024

        # Create a dictionary to map log levels to colors using Colorama
        level_colors = {
            'DEBUG': Fore.CYAN,
            'INFO': Fore.LIGHTBLUE_EX,
            'WARNING': Fore.YELLOW,
            'ERROR': Fore.RED,
            'CRITICAL': Fore.MAGENTA,
        }

        try:
            # Attempt to get the client terminal width
            try:
                if not self.no_colors:
                    terminal_width = os.get_terminal_size().columns
            except OSError:
                pass

            if self.no_colors:
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
                record.msg = self._logger_message_format(record.getMessage())
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
    def _logger_message_format(message: str):
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


def _logger_initialize():
    """
    Initializes the logging system by disabling the root logger.

    Sets the logging level of the root logger to a value higher than CRITICAL
    to effectively prevent it from processing any logs. Additionally, it removes all existing
    handlers from the root logger to ensure that no logs are inadvertently output to any
    external targets (e.g., console, file).
    """

    # Prevent a git library from accessing our logs
    git_looger = logging.getLogger('git')
    if git_looger is not None:
        git_looger.setLevel(logging.CRITICAL + 10)
        git_looger.handlers.clear()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.CRITICAL + 10)  # Set log level beyond CRITICAL to disable logging
    root_logger.handlers.clear()  # Remove all attached handlers


def logger_close(logger_instance: logging.Logger):
    """
    Close all handlers associated with the provided logger, ensuring that
    resources like file handles are properly released. This is useful when you
    need to programmatically release file handles or other resources tied to the
    logger handlers.

    Parameters:
        logger_instance (logging.Logger): The logger whose handlers will be closed and removed.
    """
    handlers = logger_instance.handlers[:]
    for handler in handlers:
        handler.close()
        logger_instance.removeHandler(handler)


def logger_get_filename(logger_instance: logging.Logger) -> Optional[str]:
    """
    Retrieves the filename of the log file from the specified logger's file handler.
    Iterates through all handlers of the provided logger instance. If a file handler
    is found, returns the filename associated with it. Returns None if no file handler is present.

    Parameters:
        logger_instance (logging.Logger): The logger from which to retrieve the log file name.
    """
    for handler in logger_instance.handlers:
        if isinstance(handler, logging.FileHandler):
            return handler.baseFilename  # Return the path to the log file

    return None


def logger_setup(name='AutoForge', level=logging.DEBUG, log_file=None, no_colors=False):
    """
    Set up the logger with color formatting and a custom TRACE and BUILD levels.

    Args:
        name (str): Name of the logger (default: 'Logging').
        level (int): Logging level (default: DEBUG).
        log_file (str): Optional log file to create
        no_colors(bool): Whether to disable colored output (default: False).

    Returns:
        logging.Logger: Configured logger instance.
    """
    # Define log format with placeholders for custom formatting
    log_format = '[%(asctime)s %(levelname)-8s] %(name)-14s: %(message)s'
    # Define the base date format (without milliseconds)
    base_date_format = '%d-%m %H:%M:%S'

    _logger_initialize()

    auto_forge_logger = logging.getLogger(name)

    # Ensure no duplicate handlers are added
    if not auto_forge_logger.hasHandlers():

        # Create a console handler
        console_handler = logging.StreamHandler(sys.stdout)
        formatter = AutoForgeColorFormatter(log_format, datefmt=base_date_format, no_colors=no_colors)
        console_handler.setFormatter(formatter)
        auto_forge_logger.setLevel(level)

        # Get the root logger and set the handler and level
        auto_forge_logger = logging.getLogger()
        auto_forge_logger.addHandler(console_handler)
        auto_forge_logger.propagate = True

        # Configure log filer handler
        if log_file is not None:
            file_formatter = AutoForgeColorFormatter(log_format, datefmt=base_date_format, no_colors=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(level)
            file_handler.setFormatter(file_formatter)
            auto_forge_logger.addHandler(file_handler)

        auto_forge_logger.name = name
        return auto_forge_logger


# Sets the logger with defaults upon startup
logger = logger_setup()
