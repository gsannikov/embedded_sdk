"""
Script:         gcc_log_analyzer.py
Author:         AutoForge Team

Description:
    Provides a flexible and robust framework for analyzing compilation logs, specifically designed to parse GCC output.
    Extract structured information about errors and warnings rom both file-based and string-based log sources,
    handling multi-line messages and various diagnostic details. It also includes an interface for extending log
    analysis to different build systems or compilers.
"""
import json
import re
from collections import defaultdict
from contextlib import suppress
from pathlib import Path
from typing import Optional, Union, Literal, IO, Any

# AutoForge imports
from auto_forge import (AutoForgeModuleType, BuildLogAnalyzerInterface)
# Direct internal imports to avoid circular dependencies
from auto_forge.core.registry import CoreRegistry

AUTO_FORGE_MODULE_NAME = "GCCLogAnalyzer"
AUTO_FORGE_MODULE_DESCRIPTION = "GCC Output Analyzer"


# noinspection GrazieInspection
class GCCLogAnalyzer(BuildLogAnalyzerInterface):
    """
    A utility class for analyzing GCC compilation output.
    Can handle GCC outputs with or without Ninja/CMake wrappers.
    """

    def __init__(self):
        super().__init__()
        self._registry = CoreRegistry.get_instance()  # Assuming CoreRegistry and AutoForgeModuleType are external
        self._module_info = self._registry.register_module(  # and these lines are handled by the user's setup
            name=AUTO_FORGE_MODULE_NAME,
            description=AUTO_FORGE_MODULE_DESCRIPTION,
            auto_forge_module_type=AutoForgeModuleType.ANALYZER
        )

        # --- Regex patterns for parsing GCC logs ---
        # Main diagnostic line: file:line:column: type: message
        self._diag_regex = re.compile(
            r"^(?P<file>[^:]+):"  # Capture file path (anything until the first colon, non-greedy)
            r"(?P<line>\d+):"  # Capture line number (digits)
            r"(?:(?P<column>\d+):)?"  # Optionally capture column number (digits, preceded by a colon)
            r"\s*(?P<type>warning|error|note|fatal error):"  # Capture type (more robust types)
            r"(?:\s|:\s*)(?P<message>.*)$"  # Message can start with space or colon-space
        )

        # "In function" line: restored to a working base, adapted for smart quotes.
        # Examples: "/path/to/file.c: In function ‘my_func’:"
        # Matches a file path and then "In function 'function_name'".
        # The key is to match the initial file part and allow for the trailing colon on the line
        # not necessarily as part of the captured group.
        self._function_regex = re.compile(
            r'^(?P<file>.*?):\s+In function[ \'‘"](?P<function>[^\'’"]+)'  # Restored working regex, added smart quotes
        )

        # Build system chatter patterns to ignore
        self._build_prefix_re = re.compile(r'^\[\d+/\d+]\s+Building ')  # Ninja/Make building progress
        self._failed_line_re = re.compile(r'^FAILED:')  # Ninja/Make FAILED line
        self._compiler_invocation_re = re.compile(r'^\s*/.+gcc\b.*-c\s+')  # Compiler command line
        self._ninja_summary_re = re.compile(
            r'^ninja:.*stopped:.*$')  # Ninja summary (e.g., "ninja: build stopped: subcommand failed.")

        # Visual context lines (caret hints, source echoes) - explicitly EXCLUDED from messages
        # Updated to be more comprehensive for various forms of visual context.
        self._visual_line_re = re.compile(
            r"^\s*(?:"  # Start non-capturing group for alternatives
            r"\d+\s*\||"  # Matches "65 |" lines
            r"\|(?:\s|$)|"  # Matches lines starting with "|" (including empty after pipe)
            r"\^+\s*.*$|"  # Matches "^~~~" lines
            r"~+\s*.*$"  # Matches "~~~" lines
            r")"
        )

    @staticmethod
    def collect_diagnostics(
            diagnostics: list[dict],
            group_by: Literal['file', 'type', 'file+type'] = 'file'
    ) -> dict:
        """
        Organizes a list of GCC diagnostic entries into groups.
        Args:
            diagnostics: list of diagnostic dictionaries (as returned by GCCLogAnalyzer.analyze()).
            group_by: How to group the diagnostics. One of:
                - 'file': groups by filename
                - 'type': groups by 'error', 'warning', etc.
                - 'file+type': nested grouping by file, then by type
        Returns:
            A dictionary where keys are file/type combinations and values are lists of diagnostics.
        """
        # Ensure diagnostics is not None before processing
        if diagnostics is None:
            return {}

        grouped = defaultdict(list)

        for entry in diagnostics:
            if group_by == 'file':
                grouped[entry['file']].append(entry)
            elif group_by == 'type':
                grouped[entry['type']].append(entry)
            elif group_by == 'file+type':
                key = (entry['file'], entry['type'])
                grouped[key].append(entry)
            else:
                raise ValueError(f"Invalid group_by value: {group_by}")

        return dict(grouped)

    def analyze(self, log_source: Union[Path, str], json_name: Optional[str] = None) -> Optional[
        list[dict[str, Any]]]:
        """
        Analyzes a GCC compilation log, either from a file or a string, to extract
        structured information about errors and warnings.
        Args:
            log_source: The path to the compilation log file (Path object) or
                        a string containing the full log content.
            json_name: JSON file name to results into.
        Returns:
            A list of dictionaries, where each dictionary represents a warning or error.
            Each dictionary contains the following keys:
            - 'file' (str): The path to the source file where the diagnostic occurred.
            - 'line' (int): The line number in the file.
            - 'column' (int or None): The column number, if available; otherwise None.
            - 'type' (str): 'error', 'warning', 'note', 'fatal error'.
            - 'message' (str): The full diagnostic message, potentially multi-line.
            - 'function' (str or None): The name of the function related to the diagnostic,
                                        if mentioned; otherwise None.

            Returns None if the log file contains no identifiable errors or warnings.
        """
        # Initialize internal variables
        parsed_entries: list[dict[str, Union[str, int, None]]] = []
        current_diagnostic: Optional[dict[str, Union[str, int, None]]] = None
        pending_function: Optional[str] = None  # Function name that might apply to the next diagnostic

        # Internal buffer for multi-line messages before joining
        current_message_lines: list[str] = []

        # Initialize log source related variables to avoid UnboundLocalError
        # Ensure log_lines_iterable is always an iterable (list or file object)
        log_lines_iterable: Union[IO, list[str]] = []
        log_source_name: str = ""  # Ensure log_source_name is always initialized

        try:
            # Determine log source (Path or str) and prepare iterable
            if isinstance(log_source, Path):
                log_source_name = str(log_source)
                if not log_source.is_file():
                    raise FileNotFoundError(f"Compilation log file not found: {log_source_name}")
                log_lines_iterable = open(log_source, 'r', encoding='utf-8', errors='ignore')
            elif isinstance(log_source, str):
                log_source_name = "<string_input>"
                log_lines_iterable = log_source.splitlines()
            else:
                raise TypeError("log_source must be a Path object or a string.")

            for line in log_lines_iterable:
                stripped_line = line.strip()

                # 1. Check for new diagnostic (error/warning/note)
                diag_match = self._diag_regex.match(stripped_line)
                if diag_match:
                    # Finalize the previous diagnostic if one was being built
                    if current_diagnostic:
                        current_diagnostic['message'] = "\n".join(current_message_lines).strip()
                        parsed_entries.append(current_diagnostic)
                        current_message_lines = []  # Reset for new message

                    # Start a new diagnostic entry
                    current_diagnostic = {
                        'file': diag_match.group('file'),
                        'line': int(diag_match.group('line')),
                        'column': int(diag_match.group('column')) if diag_match.group('column') else None,
                        'type': diag_match.group('type'),
                        # Initial message part is now directly taken from the regex match
                        'message': diag_match.group('message').strip(),
                        'function': pending_function  # Apply pending function here
                    }
                    # Add the initial message to buffer for potential multi-line continuations
                    current_message_lines.append(diag_match.group('message').strip())
                    pending_function = None  # Reset pending function after use
                    continue

                # 2. Check for "In function" line (context for the NEXT diagnostic)
                func_match = self._function_regex.match(stripped_line)
                if func_match:
                    # This line is matched. The function group from the regex is extracted.
                    pending_function = func_match.group('function')
                    # This line provides context for the *next* diagnostic, not the current one.
                    # Do not append it to current_diagnostic's message.
                    continue

                # 3. Check for known build system chatter and visual context lines that should be ignored/skipped
                # Crucial order: Check explicit ignorable lines BEFORE general message continuation.
                if (not stripped_line or  # Always skip empty lines
                        self._build_prefix_re.match(stripped_line) or
                        self._failed_line_re.match(stripped_line) or
                        self._compiler_invocation_re.match(stripped_line) or
                        self._ninja_summary_re.match(stripped_line) or
                        self._visual_line_re.match(
                            line)):  # Use original 'line' for visual context to catch leading spaces
                    # If we were in the middle of a diagnostic message, and we hit chatter,
                    # it means the message has ended. Finalize it.
                    if current_diagnostic:
                        current_diagnostic['message'] = "\n".join(current_message_lines).strip()
                        parsed_entries.append(current_diagnostic)
                        current_diagnostic = None  # Mark as no active diagnostic
                        current_message_lines = []  # Clear buffer
                    continue  # Skip this line, do not append to message

                # 4. If an active diagnostic exists and the line is not chatter or visual context,
                #    assume it's a continuation of the diagnostic message.
                if current_diagnostic:
                    # Append other non-empty, unclassified lines to the message buffer
                    # They will be joined later.
                    current_message_lines.append(stripped_line)
                    continue

            # After the loop, finalize any remaining active diagnostic
            if current_diagnostic:
                current_diagnostic['message'] = "\n".join(current_message_lines).strip()
                parsed_entries.append(current_diagnostic)

        except (FileNotFoundError, PermissionError) as e:
            # Re-raise specific file system errors directly
            raise e
        except Exception as e:
            # Wrap other unexpected exceptions in an IOError for clarity
            raise IOError(f"An unexpected error occurred while processing log source '{log_source_name}': {e}") from e
        finally:
            # Ensure the file handle is closed if it was opened
            if isinstance(log_lines_iterable, IO):
                log_lines_iterable.close()

        # Store the result of this analysis
        if parsed_entries:
            self._last_analysis = parsed_entries
            if json_name:
                with suppress(Exception):
                    with open(json_name, 'w', encoding='utf-8') as f:
                        json.dump(self._last_analysis, f, indent=4)

        # Return the parsed entries or None
        return self._last_analysis
