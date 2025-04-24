"""
Script:         local_types,py
Author:         AutoForge Team

Description:
    Auxiliary module defining common types, enumerations, and simple classes
    shared across multiple components of the project. Includes reusable structures
    such as icon mappings, CLI data wrappers, and standardized formatting helpers.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from types import ModuleType
from typing import NamedTuple, TextIO, Any, Optional, Dict, Tuple

from colorama import Fore

AUTO_FORGE_MODULE_NAME: str = "LocalTypes"
AUTO_FORGE_MODULE_DESCRIPTION: str = "Project shared types"


class AutoForgeModuleType(Enum):
    """
    Enumeration of known AutoForge module types.

    Members:
        CORE (int): Built-in core module, part of the AutoForge runtime.
        CLI_COMMAND (int): Dynamically loaded command module, provided either by AutoForge or external extensions.
    """
    UNKNOWN = 0
    CORE = 1
    COMMON = 2
    CLI_COMMAND = 3


class ModuleInfoType(NamedTuple):
    """
    Define a named tuple type for a Python module information retrieve.
    """
    name: str
    description: Optional[str] = None
    class_name: Optional[str] = None
    class_instance: Optional[Any] = None
    class_interface: Optional[Any] = None
    auto_forge_module_type: AutoForgeModuleType = AutoForgeModuleType.UNKNOWN
    python_module_type: Optional[ModuleType] = None
    file_name: Optional[str] = None
    version: Optional[str] = None


class ModuleSummaryType(NamedTuple):
    """
    Represents a minimal summary subset of 'ModuleInfo'.
    """
    name: str
    description: str


class ValidationMethodType(Enum):
    """
    Enumeration for supported validation methods.

    Attributes:
        EXECUTE_PROCESS (int): Run a shell command and validate based on its return code and/or output.
        READ_FILE (int): Read specific lines from a file and validate expected content.
        SYS_PACKAGE (int): Checks if a specific packge ('apt', 'dnf' is installed)
    """
    EXECUTE_PROCESS = 1
    READ_FILE = 2
    SYS_PACKAGE = 3


class ExecutionModeType(Enum):
    """
    Defines how a command should be executed.

    Attributes:
        SHELL: Execute using a shell subprocess (e.g., bash, cmd).
        PYTHON: Execute as a direct Python callable.
    """
    SHELL = "shell"
    PYTHON = "python"


class MessageBoxType(Enum):
    """
    Enum representing supported message box types.
    These types define the set of buttons and dialog styles used
    when displaying a message box to the user.
    """
    MB_OK = 1  # OK button only
    MB_OKCANCEL = 2  # OK and Cancel buttons
    MB_RETRYCANCEL = 3  # Retry and Cancel buttons
    MB_YESNO = 4  # Yes and No buttons
    MB_YESNOCANCEL = 5  # Yes, No, and Cancel buttons
    MB_ERROR = 6  # Error message style
    MB_WARNING = 7  # Warning message style


class InputBoxTextType(Enum):
    INPUT_TEXT = auto()
    INPUT_PASSWORD = auto()


class InputBoxButtonType(Enum):
    INPUT_MB_OK = auto()
    INPUT_CANCEL = auto()


@dataclass
class InputBoxLineType:
    label: str
    input_text: str = ""
    text_type: InputBoxTextType = InputBoxTextType.INPUT_TEXT
    length: int = 0  # 0 = auto width


@dataclass
class SignatureSchemaType:
    """
    A data class to facilitate handling signature schema data.
    """
    name: Optional[str] = None
    description: Optional[str] = None
    dictionary: Dict[str, Any] = field(default_factory=dict)
    size: Optional[int] = None
    search_pattern: Optional[re.Pattern] = None
    format_string: Optional[str] = None
    is_default: bool = False


@dataclass
class SignatureFieldType:
    """
    A data class to facilitate reading and writing specific fields from
    signature binary data.
    """
    name: Optional[str] = None
    type: Optional[str] = None
    size: Optional[int] = None
    data: Optional[Any] = None
    read_only: Optional[bool] = False  # If True, modifying the field raises an error
    is_integrity: Optional[bool] = None  # True if this field holds a CRC or integrity value
    offset: Optional[int] = None  # Offset relative to the signature start address
    type_info: Optional[Dict[str, Any]] = None  # Type definition metadata for this field


@dataclass
class VariableFieldType:
    """
    A data class to manage a single managed variable.
    """
    name: Optional[str] = None
    base_name: Optional[Any] = None
    description: Optional[str] = None
    value: Optional[Any] = None
    path_must_exist: Optional[bool] = None
    create_path_if_not_exist: Optional[bool] = None
    kwargs: Optional[Dict[str, Any]] = field(default_factory=dict)


class TerminalTeeStream:
    """
    A simple output stream duplicator that writes data to multiple target streams.
    """

    def __init__(self, *targets: TextIO):
        """
        Initialize the _TeeStream with one or more target streams.
        Args:
            *targets (TextIO): Output streams to write to (e.g., sys.stdout, StringIO).
        """
        self._targets = targets

    def write(self, data: str) -> int:
        """
        Write data to all registered target streams.
        Args:
            data (str): The string data to write.
        Returns:
            int: The number of characters written (equal to len(data)).
        """
        for target in self._targets:
            target.write(data)
        return len(data)

    def flush(self) -> None:
        """
        Flush all target streams that support flushing.
        """
        for target in self._targets:
            if hasattr(target, "flush"):
                target.flush()


@dataclass(frozen=True)
class TerminalAnsiCodes:
    """
    Provides ANSI color codes.
    """

    RESET: str = "\033[0m"
    BOLD: str = "\033[1m"
    DIM: str = "\033[2m"
    ITALIC: str = "\033[3m"
    UNDERLINE: str = "\033[4m"
    BLINK: str = "\033[5m"
    INVERT: str = "\033[7m"
    HIDDEN: str = "\033[8m"
    STRIKETHROUGH: str = "\033[9m"

    # Foreground colors
    FG_BLACK: str = "\033[30m"
    FG_RED: str = "\033[31m"
    FG_GREEN: str = "\033[32m"
    FG_YELLOW: str = "\033[33m"
    FG_BLUE: str = "\033[34m"
    FG_MAGENTA: str = "\033[35m"
    FG_CYAN: str = "\033[36m"
    FG_WHITE: str = "\033[37m"
    FG_DEFAULT: str = "\033[39m"

    # Background colors
    BG_BLACK: str = "\033[40m"
    BG_RED: str = "\033[41m"
    BG_GREEN: str = "\033[42m"
    BG_YELLOW: str = "\033[43m"
    BG_BLUE: str = "\033[44m"
    BG_MAGENTA: str = "\033[45m"
    BG_CYAN: str = "\033[46m"
    BG_WHITE: str = "\033[47m"
    BG_DEFAULT: str = "\033[49m"

    # Bright variants
    FG_BRIGHT_BLACK: str = "\033[90m"
    FG_BRIGHT_RED: str = "\033[91m"
    FG_BRIGHT_GREEN: str = "\033[92m"
    FG_BRIGHT_YELLOW: str = "\033[93m"
    FG_BRIGHT_BLUE: str = "\033[94m"
    FG_BRIGHT_MAGENTA: str = "\033[95m"
    FG_BRIGHT_CYAN: str = "\033[96m"
    FG_BRIGHT_WHITE: str = "\033[97m"

    BG_BRIGHT_BLACK: str = "\033[100m"
    BG_BRIGHT_RED: str = "\033[101m"
    BG_BRIGHT_GREEN: str = "\033[102m"
    BG_BRIGHT_YELLOW: str = "\033[103m"
    BG_BRIGHT_BLUE: str = "\033[104m"
    BG_BRIGHT_MAGENTA: str = "\033[105m"
    BG_BRIGHT_CYAN: str = "\033[106m"
    BG_BRIGHT_WHITE: str = "\033[107m"


@dataclass(frozen=True)
class TerminalFileIconInfo:
    icon: str
    description: str
    color: str


# File extension or name to icon metadata mapping
TERMINAL_ICONS_MAP: Dict[str, TerminalFileIconInfo] = {
    # Source Code
    ".py": TerminalFileIconInfo("", "Python source file", Fore.YELLOW),
    ".c": TerminalFileIconInfo("", "C source file", Fore.LIGHTBLUE_EX),
    ".cpp": TerminalFileIconInfo("", "C++ source file", Fore.LIGHTBLUE_EX),
    ".h": TerminalFileIconInfo("", "C/C++ header", Fore.LIGHTBLUE_EX),
    ".hpp": TerminalFileIconInfo("", "C++ header", Fore.LIGHTBLUE_EX),
    ".java": TerminalFileIconInfo("", "Java source file", Fore.RED),
    ".js": TerminalFileIconInfo("", "JavaScript file", Fore.YELLOW),
    ".ts": TerminalFileIconInfo("", "TypeScript file", Fore.CYAN),
    ".go": TerminalFileIconInfo("", "Go source file", Fore.CYAN),
    ".rs": TerminalFileIconInfo("", "Rust source file", Fore.RED),
    ".swift": TerminalFileIconInfo("", "Swift source file", Fore.MAGENTA),

    # Scripts & Shell
    ".sh": TerminalFileIconInfo("", "Shell script", Fore.GREEN),
    ".bash": TerminalFileIconInfo("", "Bash script", Fore.GREEN),
    ".zsh": TerminalFileIconInfo("", "Zsh script", Fore.GREEN),
    ".ps1": TerminalFileIconInfo("", "PowerShell script", Fore.CYAN),

    # Config & Markup
    ".json": TerminalFileIconInfo("", "JSON file", Fore.CYAN),
    ".jsonc": TerminalFileIconInfo("", "JSON with comments", Fore.CYAN),
    ".yaml": TerminalFileIconInfo("", "YAML config file", Fore.CYAN),
    ".yml": TerminalFileIconInfo("", "YAML config file", Fore.CYAN),
    ".toml": TerminalFileIconInfo("", "TOML config file", Fore.CYAN),
    ".ini": TerminalFileIconInfo("", "INI config file", Fore.CYAN),
    ".conf": TerminalFileIconInfo("", "Configuration file", Fore.CYAN),
    ".env": TerminalFileIconInfo("", "Environment settings", Fore.GREEN),

    # Markup & Docs
    ".md": TerminalFileIconInfo("", "Markdown file", Fore.BLUE),
    ".txt": TerminalFileIconInfo("", "Text file", Fore.LIGHTBLACK_EX),
    ".rst": TerminalFileIconInfo("", "reStructuredText", Fore.BLUE),
    ".html": TerminalFileIconInfo("", "HTML document", Fore.MAGENTA),
    ".xml": TerminalFileIconInfo("謹", "XML document", Fore.MAGENTA),
    ".pdf": TerminalFileIconInfo("", "PDF document", Fore.RED),

    # Logs
    ".log": TerminalFileIconInfo("", "Log file", Fore.LIGHTBLACK_EX),

    # Archives
    ".zip": TerminalFileIconInfo("", "ZIP archive", Fore.MAGENTA),
    ".tar": TerminalFileIconInfo("", "TAR archive", Fore.MAGENTA),
    ".gz": TerminalFileIconInfo("", "GZ archive", Fore.MAGENTA),
    ".bz2": TerminalFileIconInfo("", "BZIP2 archive", Fore.MAGENTA),
    ".7z": TerminalFileIconInfo("", "7-Zip archive", Fore.MAGENTA),

    # Media
    ".jpg": TerminalFileIconInfo("", "JPEG image", Fore.YELLOW),
    ".jpeg": TerminalFileIconInfo("", "JPEG image", Fore.YELLOW),
    ".png": TerminalFileIconInfo("", "PNG image", Fore.YELLOW),
    ".gif": TerminalFileIconInfo("", "GIF image", Fore.YELLOW),
    ".svg": TerminalFileIconInfo("ﰟ", "SVG vector image", Fore.CYAN),
    ".mp3": TerminalFileIconInfo("", "MP3 audio file", Fore.MAGENTA),
    ".wav": TerminalFileIconInfo("", "WAV audio file", Fore.MAGENTA),
    ".mp4": TerminalFileIconInfo("", "MP4 video file", Fore.BLUE),
    ".mkv": TerminalFileIconInfo("", "MKV video file", Fore.BLUE),

    # Compiled
    ".exe": TerminalFileIconInfo("", "Windows executable", Fore.RED),
    ".out": TerminalFileIconInfo("", "Compiled binary", Fore.RED),
    ".class": TerminalFileIconInfo("", "Java class file", Fore.LIGHTBLACK_EX),
    ".o": TerminalFileIconInfo("", "Object file", Fore.LIGHTBLACK_EX),

    # Special filenames
    "Makefile": TerminalFileIconInfo("", "Makefile", Fore.CYAN),
    "Dockerfile": TerminalFileIconInfo("", "Dockerfile", Fore.BLUE),
    "LICENSE": TerminalFileIconInfo("", "License file", Fore.WHITE),
    "README": TerminalFileIconInfo("", "README file", Fore.BLUE),

    # Fallbacks
    "default_dir": TerminalFileIconInfo("", "Directory", Fore.BLUE),
    "default_file": TerminalFileIconInfo("", "Generic file", Fore.WHITE),
}


class TerminalAnsiGuru:
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

    @staticmethod
    def restore_cursor_position_and_erase_line_to_end():
        """
        Restores the cursor to the last saved position,
        erases from the cursor to the end of the line,
        and restores the cursor back to the same position again.
        """
        sys.stdout.write("\033[u")
        sys.stdout.write("\033[K")
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


class ExceptionGuru:
    """
    A singleton utility class for capturing and exposing the origin (filename and line number)
    of the innermost frame where the most recent exception occurred bty ensuring the exception context
    is captured only once.
    """

    _instance: Optional["ExceptionGuru"] = None
    _context_stored: bool = False

    def __new__(cls) -> "ExceptionGuru":
        """
        Overrides object instantiation to implement the singleton pattern.
        Returns:
            ExceptionGuru: The singleton instance of the class.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """
        Initializes the exception context (filename and line number).
        The context is captured only once during the lifetime of the singleton instance.
        """
        if not self.__class__._context_stored:
            self._file_name: Optional[str] = "<unknown>"
            self._line_number: Optional[int] = -1
            self._store_context()
            self.__class__._context_stored = True

    def get_context(self) -> Tuple[str, int]:
        """
        Retrieves the exception origin information.
        Returns:
            Tuple[str, int]: A tuple containing the base filename and the line number
                             where the exception originally occurred.
        """
        return self._file_name, self._line_number

    def _store_context(self) -> None:
        """
        Captures the filename and line number of the innermost frame where the most recent
        exception occurred. If no exception context is found, defaults to '<unknown>' and -1.
        """
        exc_type, exc_obj, exc_tb = sys.exc_info()

        if exc_tb is None:
            return

        # Traverse to the innermost (deepest) frame
        tb = exc_tb
        while tb.tb_next:
            tb = tb.tb_next

        self._file_name = os.path.basename(tb.tb_frame.f_code.co_filename)
        self._line_number = tb.tb_lineno
