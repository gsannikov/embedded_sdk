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
import textwrap
from contextlib import suppress
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import IO
from typing import Union, Optional

# AutoForge imports
from auto_forge import (AutoForgeModuleType, BuildLogAnalyzerInterface)
# Direct internal imports to avoid circular dependencies
from auto_forge.core.registry import CoreRegistry

AUTO_FORGE_MODULE_NAME = "GCCLogAnalyzer"
AUTO_FORGE_MODULE_DESCRIPTION = "GCC Output Analyzer"


@dataclass
class _AIContext:
    function_name: Optional[str] = None  # Name if inside a function
    text: Optional[str] = None  # Snippet text (function or sliced range)
    line_number: int = 0  # Adjusted line index in 'text' (0-based)


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
    def _get_ai_context(file_path: Union[Path, str], line_number: int, lines_range: int = 10) -> Optional[_AIContext]:
        """
        Generates minimal, AI-optimized context from a source file.
        Returns function if line is inside one, otherwise a range of lines.
        """
        path = Path(file_path)
        if not path.exists() or path.suffix not in {".c", ".h"}:
            raise ValueError(f"Unsupported or missing file: {path}")

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if not (1 <= line_number <= len(lines)):
            raise ValueError(f"Line number {line_number} is out of bounds (1–{len(lines)}).")

        target_idx = line_number - 1
        sig_pattern = re.compile(r'^\s*(\w[\w\s*]*?\**)\s+(\w+)\s*\([^)]*\)\s*(\{)?\s*$')

        start = None
        func_name = None

        # Search upward for function signature
        for i in range(target_idx, -1, -1):
            match = sig_pattern.match(lines[i])
            if match:
                start = i
                func_name = match.group(2)
                break

        # If function start found, scan forward for closing brace
        if start is not None:
            brace_depth = 0
            end = None
            for i in range(start, len(lines)):
                brace_depth += lines[i].count("{") - lines[i].count("}")
                if brace_depth > 0 or (brace_depth == 0 and "{" not in "".join(lines[start:i])):
                    continue
                if brace_depth <= 0 and "{" in "".join(lines[start:i]):
                    end = i
                    break

            # Confirm line falls inside function (not after it)
            if end is not None and start <= target_idx <= end:
                raw = lines[start:end + 1]
                text = textwrap.dedent("\n".join(line.rstrip() for line in raw)).strip()
                return _AIContext(
                    function_name=func_name,
                    text=text,
                    line_number=target_idx - start
                )

        # Fallback range (outside any function)
        context_start = max(0, target_idx - lines_range)
        context_end = min(len(lines), target_idx + lines_range + 1)
        raw = lines[context_start:context_end]
        text = textwrap.dedent("\n".join(line.rstrip() for line in raw)).strip()

        return _AIContext(
            function_name=None,
            text=text,
            line_number=target_idx - context_start
        )

    @staticmethod
    def _serialize(parsed_entries: list[dict], output_path: Union[str, Path]) -> bool:
        """
        Saves the list of parsed diagnostic entries to a JSON file.
        Args:
            parsed_entries: List of structured diagnostic dictionaries.
            output_path: Destination file path (str or Path).
        """
        with suppress(Exception):
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with output_path.open("w", encoding="utf-8") as f:
                json.dump(parsed_entries, f, indent=2, ensure_ascii=False)
            return True

        return False

    def analyze(self, log_source: Union[Path, str],
                export_file_name: Optional[str] = None) -> Optional[list[dict]]:
        """
        Analyzes a GCC compilation log, extracting structured error/warning diagnostics.
        Args:
            log_source: Path to the log file or a raw log string.
            export_file_name: If provided, stores the result in a JSON file.
        Returns:
            list of diagnostic dictionaries or None if none found.
        """
        parsed_entries = []
        current_diagnostic = None
        pending_function = None
        current_message_lines = []

        log_lines_iterable: Union[IO, list[str]] = []
        log_source_name: str = ""

        try:

            # Remove current exported file if exists.
            if isinstance(export_file_name, str):
                Path(export_file_name).unlink(missing_ok=True)

            # Open log source
            if isinstance(log_source, Path):
                log_source_name = str(log_source)
                if not log_source.is_file():
                    raise FileNotFoundError(f"Compilation log file not found: {log_source_name}")
                log_lines_iterable = open(log_source, 'r', encoding='utf-8', errors='ignore')
            elif isinstance(log_source, str):
                log_source_name = "<string_input>"
                log_lines_iterable = log_source.splitlines()
            else:
                raise TypeError("log_source must be a Path or a string.")

            for line in log_lines_iterable:
                stripped_line = line.strip()

                diag_match = self._diag_regex.match(stripped_line)
                if diag_match:
                    # Finalize current diagnostic
                    if current_diagnostic:
                        current_diagnostic['gcc']['message'] = "\n".join(current_message_lines).strip()
                        parsed_entries.append(current_diagnostic)
                        current_message_lines = []

                    file_name = diag_match.group('file')
                    line_number = int(diag_match.group('line'))
                    context = self._get_ai_context(file_path=file_name, line_number=line_number)

                    current_diagnostic = {
                        'gcc': {
                            'file': file_name.strip(),
                            'line': line_number,
                            'column': int(diag_match.group('column')) if diag_match.group('column') else None,
                            'type': diag_match.group('type'),
                            'message': diag_match.group('message').strip(),
                            'function': pending_function
                        },
                        'ai': {
                            **asdict(context)
                        }
                    }

                    current_message_lines.append(diag_match.group('message').strip())
                    pending_function = None
                    continue

                func_match = self._function_regex.match(stripped_line)
                if func_match:
                    pending_function = func_match.group('function').strip().rstrip(':')
                    continue

                if (not stripped_line or
                        self._build_prefix_re.match(stripped_line) or
                        self._failed_line_re.match(stripped_line) or
                        self._compiler_invocation_re.match(stripped_line) or
                        self._ninja_summary_re.match(stripped_line) or
                        self._visual_line_re.match(line)):

                    if current_diagnostic:
                        current_diagnostic['gcc']['message'] = "\n".join(current_message_lines).strip()
                        parsed_entries.append(current_diagnostic)
                        current_diagnostic = None
                        current_message_lines = []
                    continue

                if current_diagnostic:
                    current_message_lines.append(stripped_line)

            if current_diagnostic:
                current_diagnostic['gcc']['message'] = "\n".join(current_message_lines).strip()
                parsed_entries.append(current_diagnostic)

            # Export to JSON
            if parsed_entries and export_file_name is not None:
                self._serialize(parsed_entries=parsed_entries, output_path=export_file_name)

            return parsed_entries if parsed_entries else None

        except (FileNotFoundError, PermissionError) as file_error:
            raise file_error
        except Exception as exception:
            raise IOError(f"unexpected error while processing log source "
                          f"'{log_source_name}': {exception}") from exception
        finally:
            if isinstance(log_lines_iterable, IO):
                log_lines_iterable.close()
