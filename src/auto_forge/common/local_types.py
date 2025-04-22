"""
Script:         local_types,py
Author:         AutoForge Team

Description:
    Auxiliary module defining many common types, enums, data classes which aare
    shared across many modules in this project.
"""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, TextIO, Any, Optional, Dict

from colorama import Fore

AUTO_FORGE_MODULE_NAME: str = "LocalTypes"
AUTO_FORGE_MODULE_DESCRIPTION: str = "Project shared types"


class CLICommandInfo(NamedTuple):
    """
    Define a named tuple type for a CLI command related information.
    """
    name: str
    description: str
    version: str
    class_name: str
    class_instance: Any


class CLICommandSummary(NamedTuple):
    """
    Represents a minimal summary subset of 'CLICommandInfo'.
    """
    name: str
    description: str


class ValidationMethod(Enum):
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


class ExecutionMode(Enum):
    """
    Defines how a command should be executed.

    Attributes:
        SHELL: Execute using a shell subprocess (e.g., bash, cmd).
        PYTHON: Execute as a direct Python callable.
    """
    SHELL = "shell"
    PYTHON = "python"


@dataclass
class SignatureSchema:
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
class SignatureField:
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
class VariableField:
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
