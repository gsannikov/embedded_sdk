#!/usr/bin/env python3
"""
Script: progress_tracker.py
Author: Intel AutoForge Team

Description:
    This module defines the ProgressTracker class, a utility designed for terminal-based status
    and progress reporting. It facilitates real-time updates of task statuses with dynamic text
    formatting, colorization, and cursor manipulations to enhance readability and user interaction
    during long-running operations.

Features:
    - Dynamic in-place update of status messages in the terminal.
    - Customizable text formatting including color support via Colorama.
    - Time prefixing for entries to track the progression of tasks chronologically.

Note:
    This module depends on the 'colorama' and 'ANSIGuru' (hypothetical) packages for its
    color handling and cursor control capabilities.
"""

import re
import shutil
import sys
import time
from datetime import datetime
from enum import Enum
from typing import Optional
from typing import Tuple

from colorama import init, Fore, Style


class TrackerState(Enum):
    """
    Enum to specify the types of text display for status messages within the ProgressTracker.

    Attributes:
        PRE (int): Represents the initial state for setting up preliminary text, typically used for
                   displaying the initial part of a status message, padded with dots to align the text.
                   This state is used to prepare and format the status message that appears before any actual
                   progress or result is displayed. E.g., "Loading configurations ................."

        BODY (int): Represents the state for displaying ongoing updates or body content. It is used
                    after the preliminary text to update the status dynamically without changing the
                    initial setup. This state is ideal for providing continuous feedback during a process,
                    such as "Loading configurations ................. 50% complete"

    Example:
        PRE - Used to initialize and display the start of a message with formatting.
        BODY - Used to provide real-time updates to the ongoing process or task within the same line.
    """
    UN_INITIALIZES = 0
    PRE = 1
    BODY = 2


class ANSIGuru:
    """
    A utility class for managing terminal output through ANSI escape codes. The class provides methods to
    manipulate the cursor's visibility and position, allowing for dynamic updates to the terminal content
    without disrupting the user's view. This class is particularly useful for applications that require
    fine control over the terminal interface, such as text-based user interfaces, progress bars, and
    interactive command-line tools.
    """

    def __init__(self):
        pass

    @staticmethod
    def set_cursor_visibility(visible: bool):
        """Shows or hides the cursor based on the 'visible' parameter."""
        sys.stdout.write('\033[?25h' if visible else '\033[?25l')
        sys.stdout.flush()

    @staticmethod
    def save_cursor_position():
        """Saves the current cursor position."""
        sys.stdout.write("\033[s")
        sys.stdout.flush()

    @staticmethod
    def restore_cursor_position():
        """Restores the cursor to the last saved position."""
        sys.stdout.write("\033[u")
        sys.stdout.flush()

    def move_cursor(self, row: Optional[int] = None, col: Optional[int] = None):
        """
        Moves the cursor to the specified (row, col). Handles partial parameters by moving
        to the specific row or column while keeping the other coordinate unchanged.
        """
        # Attempt to get the current cursor position
        current_pos = self.get_cursor_position()
        if current_pos is None:
            return  # If the position is unknown, exit early

        row = row or current_pos[0]  # Use current row if not specified
        col = col or current_pos[1]  # Use current column if not specified
        sys.stdout.write(f"\033[{row + 1};{col + 1}H")
        sys.stdout.flush()

    @staticmethod
    def erase_line_to_end():
        """Erases from the current cursor position to the end of the line."""
        sys.stdout.write("\033[K")
        sys.stdout.flush()

    @staticmethod
    def get_cursor_position() -> Optional[Tuple[int, int]]:
        """
        Attempts to query the terminal for the current cursor position. Requires terminal support.
        Returns the cursor position as zero-based indices or None if undetermined.
        """
        sys.stdout.write("\033[6n")
        sys.stdout.flush()
        response = sys.stdin.read(20)  # Read enough characters for typical response
        match = re.search(r"\033\[(\d+);(\d+)R", response)
        return (int(match.group(1)) - 1, int(match.group(2)) - 1) if match else None


class ProgressTracker:
    def __init__(self, title_length: int = 80, add_time_prefix: bool = False,
                 min_update_interval_ms: int = 250, hide_cursor: bool = True):
        """
        Initializes the ProgressTracker instance.

        Args:
            title_length (int): Maximum length of the status message title.
            add_time_prefix (bool): Whether to prefix messages with the current time.
            min_update_interval_ms (int): Minimum interval in milliseconds between updates to prevent flickering.
        """
        self._state = TrackerState.UN_INITIALIZES
        self._add_time_prefix = add_time_prefix
        self._title_length = title_length
        self._terminal_width = shutil.get_terminal_size().columns
        self._ansi_term = ANSIGuru()
        self._pre_text: Optional[str] = None
        self._min_update_interval_ms = min_update_interval_ms
        self._last_update_time = 0  # Epoch time of the last update
        self._state = TrackerState.PRE

        # Hide the cursor
        if hide_cursor:
            self._ansi_term.set_cursor_visibility(False)

        init()

    @staticmethod
    def _normalize_text(text: Optional[str], allow_empty: bool = False) -> str:
        """
        Normalize the input string by stripping leading and trailing whitespace.

        Args:
            text (Optional[str]): The string to be normalized.
            allow_empty (bool): No exception if the output is an empty string.

        Returns:
            str: A normalized string with no leading or trailing whitespace.
        """
        if text is None or not isinstance(text, str):
            raise ValueError("input must be a non-empty string.")

        normalized_string = text.strip()
        if not allow_empty and not normalized_string:
            raise ValueError("input string cannot be empty after stripping")

        return normalized_string

    def _pre_format(self, text: str) -> str:
        """
        Formats the preliminary status message to include a time prefix (if enabled) and ensures
        it fits within the defined title length by truncating if necessary and padding with dots.

        Args:
            text (str): The preliminary status message to display.

        Returns:
            str: The formatted string ready for display.
        """
        time_string = datetime.now().strftime("%H:%M:%S ") if self._add_time_prefix else ""
        text_length = len(time_string) + len(text)
        dots_count = self._title_length - text_length - 2
        dots = "." * max(0, dots_count)  # Ensure non-negative count of dots

        if text_length > self._title_length:
            truncate_length = self._title_length - len(time_string) - 4  # space for dots and spacing
            text = text[:max(0, truncate_length)]  # Truncate text if necessary

        if self._add_time_prefix:
            formatted_text = f"{Fore.LIGHTBLUE_EX}{time_string}{Style.RESET_ALL}{text} {dots} "
        else:
            formatted_text = f"{text} {dots} "

        return formatted_text

    def set_pre(self, text: str, new_line: bool = True):
        """
        Sets the preliminary message, preparing the display format in the console.

        Args:
            text (str): The preliminary status message to display.
            new_line (bool): Whether or star the message in a new line.
        """

        if self._state != TrackerState.PRE:
            return

        text = self._normalize_text(text, allow_empty=True)
        formatted_text = self._pre_format(text)
        if len(formatted_text) >= self._terminal_width:
            return  # formatted text is too wide for the terminal

        self._ansi_term.erase_line_to_end()
        sys.stdout.write(('\n' if new_line else '\r') + formatted_text)

        self._ansi_term.save_cursor_position()
        self._pre_text = text
        self._state = TrackerState.BODY

    def set_body_in_place(self, text: str, pre_text: Optional[str] = None, update_clock: bool = True):
        """
        Updates the message body in place, optionally updating the timestamp (clock)
        to reflect the current time when the update occurs.

        Args:
            text (str): The message body to display.
            pre_text (str, optional): Adjust the preliminary status message to display.
            update_clock (bool): Whether to update the message clock.
        """
        if self._state != TrackerState.BODY:
            return

        current_time = time.time() * 1000  # Get current time in milliseconds
        if current_time - self._last_update_time < self._min_update_interval_ms:
            return  # Exit if the minimum interval has not passed

        # Move the cursor to the beginning of the line to potentially update the whole line
        self._ansi_term.restore_cursor_position()

        # Optionally update the ptr-text section
        if pre_text is not None:
            self._pre_text = pre_text

        # Update the clock and text if specified
        if update_clock and self._pre_text is not None:
            # Format the preliminary text with the updated clock
            formatted_pre_text = self._pre_format(self._pre_text)
            sys.stdout.write('\r' + formatted_pre_text)  # Use carriage return to overwrite the line
            # After updating the prefix and clock, adjust cursor position to after the prefix
            self._ansi_term.save_cursor_position()

        # Write the new body text ensuring it does not overflow the terminal width
        body_start_pos = len(
            self._pre_format(self._pre_text).strip())  # Calculate end position of the formatted pre text
        max_body_length = self._terminal_width - body_start_pos
        sys.stdout.write(text[:max_body_length])
        self._ansi_term.erase_line_to_end()
        sys.stdout.flush()

        # Update the last update time
        self._last_update_time = current_time

    def set_result(self, text: str, status_code: Optional[int] = None):
        """
        Sets the result message with an optional status code and decides whether to add a new line.

        Args:
            text (str): The result message to display.
            status_code (Optional[int]): The status code to determine message color.
        """
        if self._state != TrackerState.BODY:
            return

        self._ansi_term.restore_cursor_position()
        color = Fore.GREEN if status_code == 0 else Fore.RED
        text = f"{color}{text}{Style.RESET_ALL}" if status_code is not None else text

        sys.stdout.write(text)
        self._ansi_term.erase_line_to_end()
        self._pre_text = None
        self._state = TrackerState.PRE

    def close(self):
        """
        Closes the ProgressTracker instance by making the cursor visible again and marking
        the state as uninitialized.
        """
        self._ansi_term.set_cursor_visibility(True)
        self._state = TrackerState.UN_INITIALIZES
