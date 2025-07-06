"""
Script:         gcc_log_analyzer.py
Author:         AutoForge Team

Description:
    Provides a flexible and robust framework for analyzing compilation logs, specifically designed to parse GCC output.
    Extract structured information about errors and warnings rom both file-based and string-based log sources,
    handling multi-line messages and various diagnostic details. It also includes an interface for extending log
    analysis to different build systems or compilers.
"""
import asyncio
import io
import json
import re
import textwrap
import threading
from contextlib import suppress
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import IO
from typing import Union, Optional

# Third-party
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

# AutoForge imports
from auto_forge import (AutoForgeModuleType, BuildLogAnalyzerInterface, PromptStatusType)
# Direct internal imports to avoid circular dependencies
from auto_forge.core.registry import CoreRegistry

AUTO_FORGE_MODULE_NAME = "GCCLogAnalyzer"
AUTO_FORGE_MODULE_DESCRIPTION = "GCC Output Analyzer"


@dataclass
class _AIContext:
    function_name: Optional[str] = None  # Name if inside a function
    function_body: Optional[str] = None  # Snippet text (function or sliced range)
    function_line_number: int = 0  # Adjusted line index in 'text' (0-based)


# noinspection GrazieInspection
class GCCLogAnalyzer(BuildLogAnalyzerInterface):
    """
    A utility class for analyzing GCC compilation output.
    Can handle GCC outputs with or without Ninja/CMake wrappers.
    """

    def __init__(self):
        super().__init__()
        self._last_json: Optional[str] = None
        self._last_rendered_ai_response: Optional[str] = None
        self._console = Console(force_terminal=True)

        self._ai_context_info: str = (
            "The following is a list of structured diagnostic entries from a C codebase, each containing a GCC error or warning message and its corresponding source-level context.\n"
            "- The file name, line, column, and error/warning message from GCC.\n"
            "- An optional `ai` field with the full function source where the error occurred, compressed to reduce size while preserving line numbers.\n"
            "Use this information to assist in identifying the root cause of the error or suggest a potential fix.\n"
        )

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
    def _compress_function_body(function_body: str) -> str:
        """
        Compresses a C-like function body while preserving line count and semantic relevance.
        - Keeps empty lines (as '\n')
        - Removes tabs
        - Trims leading/trailing spaces
        - Collapses multiple spaces outside of string literals
        - Removes all comments (// and /* */), preserving line count
        """

        def _collapse_spaces_outside_strings(_line: str) -> str:
            result = []
            in_string = False
            last_char = ''
            i = 0
            while i < len(_line):
                c = _line[i]
                if c == '"' and last_char != '\\':
                    in_string = not in_string
                if not in_string:
                    if c == ' ':
                        result.append(' ')
                        while i + 1 < len(_line) and _line[i + 1] == ' ':
                            i += 1
                    else:
                        result.append(c)
                else:
                    result.append(c)
                last_char = c
                i += 1
            return ''.join(result)

        def _remove_block_comments_preserve_lines(_text: str) -> str:
            # Replace block comment content with whitespace/newlines only
            def _replacer(_match):
                return re.sub(r'[^\n]', '', _match.group())  # keep \n, strip everything else

            return re.sub(r'/\*.*?\*/', _replacer, _text, flags=re.DOTALL)

        def _remove_inline_comment(_line: str) -> str:
            _in_string = False
            _result = []
            _i = 0
            while _i < len(_line):
                if _line[_i] == '"' and (_i == 0 or _line[_i - 1] != '\\'):
                    _in_string = not _in_string
                if not _in_string and _line[_i:_i + 2] == '//':
                    break  # start of comment outside string
                _result.append(_line[_i])
                _i += 1
            return ''.join(_result)

        # Remove block comments but preserve line count
        function_body = _remove_block_comments_preserve_lines(function_body)

        # Process line by line
        compressed_lines = []
        for line in function_body.splitlines():
            line = line.replace('\t', '')  # Remove tabs
            line = _remove_inline_comment(line)
            line = line.strip()
            if line == '':
                compressed_lines.append('')  # Preserve blank line
            else:
                line = _collapse_spaces_outside_strings(line)
                compressed_lines.append(line)

        return '\n'.join(compressed_lines)

    @staticmethod
    def _render_ai_response(response: Optional[str], width: int = 100) -> Optional[str]:
        """
        Render AI response using rich panels into a string buffer.
        """
        if not isinstance(response, str) or not response.strip():
            return "⚠️ No response received."

        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=width, record=True)

        # Match outermost code blocks
        code_block_pattern = re.compile(r"```c?\s*\n(.*?)```", re.DOTALL)
        code_blocks = code_block_pattern.findall(response)
        response_body = code_block_pattern.sub("[[CODE_BLOCK]]", response)

        paragraphs = [p.strip() for p in response_body.strip().split("\n\n") if p.strip()]
        before_code, after_code = [], []
        code_inserted = False

        for para in paragraphs:
            if para == "[[CODE_BLOCK]]" and code_blocks:
                if before_code:
                    combined = "\n\n".join(before_code)
                    console.print(Panel(Text(combined, justify="left"), border_style="cyan", width=width))
                    before_code.clear()

                code = code_blocks.pop(0).strip()
                console.print(Panel(
                    Syntax(code, "c", line_numbers=True, word_wrap=True),
                    border_style="green", title="Suggested Fix"
                ))
                code_inserted = True
            elif not code_inserted:
                before_code.append(para)
            else:
                after_code.append(para)

        if after_code:
            combined = "\n\n".join(after_code)
            console.print(Panel(Text(combined, justify="left"), border_style="cyan", width=width))

        for leftover in code_blocks:
            console.print(Panel(
                Syntax(leftover.strip(), "c", line_numbers=True, word_wrap=True),
                border_style="green", title="Additional Code"
            ))

        return buffer.getvalue()

    def _get_ai_advise_background(self, prompt: str, context: str):
        """
        Executes the AI query in a background thread, updates self._last_rendered_ai_response.
        """

        def _runner():
            self._last_rendered_ai_response = None

            async def _inner():
                response = await self.sdk.ai_bridge.query(
                    prompt=prompt, context=context, max_tokens=400, timeout=30,
                )
                self._last_rendered_ai_response = self._render_ai_response(response=response)

            try:
                asyncio.run(_inner())
            except Exception as ai_exception:
                self.sdk.tool_box.show_status(message=f"❌ AI query failed: {ai_exception}", expire_after=2,
                                              status_type=PromptStatusType.ERROR, erase_after=True)

            if isinstance(self._last_rendered_ai_response, str):
                self.sdk.tool_box.show_status(message="🤖 AI Advise available", expire_after=2, erase_after=True)

        threading.Thread(target=_runner, daemon=True).start()

    def _get_ai_context(self, file_path: Union[Path, str], line_number: int, lines_range: int = 10) -> Optional[
        _AIContext]:
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
                compressed = self._compress_function_body(text)
                return _AIContext(
                    function_name=func_name,
                    function_body=compressed,
                    function_line_number=target_idx - start
                )

        # Fallback range (outside any function)
        context_start = max(0, target_idx - lines_range)
        context_end = min(len(lines), target_idx + lines_range + 1)
        raw = lines[context_start:context_end]
        text = textwrap.dedent("\n".join(line.rstrip() for line in raw)).strip()
        compressed = self._compress_function_body(text)

        return _AIContext(
            function_name=None,
            function_body=compressed,
            function_line_number=target_idx - context_start
        )

    def _serialize(self, parsed_entries: list[dict], output_path: Union[str, Path]) -> bool:
        """
        Saves the list of parsed diagnostic entries to a JSON file.
        Args:
            parsed_entries: List of structured diagnostic dictionaries.
            output_path: Destination file path (str or Path).
        """

        def _clear_duplicated_entries(_entries: list[dict]) -> list[dict]:
            """
            Removes exact duplicate context entries based on their JSON structure.
            This helps eliminate repeated errors/warnings (e.g., from static/shared builds).
            """
            _seen = set()
            _unique = []
            for _entry in _entries:
                key = json.dumps(_entry, sort_keys=True)
                if key not in _seen:
                    _seen.add(key)
                    _unique.append(_entry)
            return _unique

        def _remove_none_recursive(_obj):
            """Recursively remove keys with None values from dicts/lists."""
            if isinstance(_obj, dict):
                return {k: _remove_none_recursive(v) for k, v in _obj.items() if v is not None}
            elif isinstance(_obj, list):
                return [_remove_none_recursive(item) for item in _obj]
            else:
                return _obj

        with suppress(Exception):
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Clean 'null' entries recursively
            cleaned_entries = _remove_none_recursive(parsed_entries)
            # Remove duplicates and sort the remaining entries
            unique_entries = _clear_duplicated_entries(cleaned_entries)
            unique_entries.sort(key=lambda e: (e['gcc']['file'], e['gcc']['line']))

            # Serialize to JSON string and store in instance
            json_str = json.dumps(unique_entries, indent=4, ensure_ascii=False)
            self._last_json = json_str

            # Save to file
            with output_path.open("w", encoding="utf-8") as f:
                f.write(json_str)

            return True
        return False

    def show_last_advise(self):
        """ Print the last stored rendered AI asynchronous response """
        if isinstance(self._last_json, str):
            print(self._last_rendered_ai_response, end="")

    def analyze(self, log_source: Union[Path, str],
                export_file_name: Optional[str] = None, ask_ai_for_help: bool = False) -> Optional[list[dict]]:
        """
        Analyzes a GCC compilation log, extracting structured error/warning diagnostics.
        Args:
            log_source: Path to the log file or a raw log string.
            export_file_name: If provided, stores the result in a JSON file.
            ask_ai_for_help: If true, a background asynchronous request to an AI will be made.
        Returns:
            list of diagnostic dictionaries or None if none found.
        """
        parsed_entries = []
        current_diagnostic = None
        pending_function = None
        current_message_lines = []

        log_lines_iterable: Union[IO, list[str]] = []
        log_source_name: str = ""

        # Purge last analysis results
        self._last_json = None

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
                            'function_name': pending_function
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

            # Auto trigger AI request
            if isinstance(self._last_json, str) and ask_ai_for_help:
                self._get_ai_advise_background(prompt=self._last_json, context=self.ai_context)

            return parsed_entries if parsed_entries else None

        except (FileNotFoundError, PermissionError) as file_error:
            raise file_error
        except Exception as exception:
            raise IOError(f"unexpected error while processing log source "
                          f"'{log_source_name}': {exception}") from exception
        finally:
            if isinstance(log_lines_iterable, IO):
                log_lines_iterable.close()

    @property
    def ai_context(self) -> str:
        """Get the AI context for this log source."""
        return self._ai_context_info

    @property
    def last_json(self) -> Optional[str]:
        """Get the last context as JSON string."""
        return self._last_json
