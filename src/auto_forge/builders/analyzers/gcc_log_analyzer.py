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
import json
import re
import textwrap
import threading
from contextlib import suppress
from pathlib import Path
from typing import IO
from typing import Union, Optional, Any

# AutoForge imports
from auto_forge import (AutoForgeModuleType, BuildLogAnalyzerInterface, PromptStatusType, BuildAnalyzedEventType,
                        BuildAnalyzedContextType)

# Third-party

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

        self._last_error_context: Optional[str] = None

        self._ai_prompt_context: str = (
            "The following is a structured diagnostic report from a C code-base. Each entry includes:\n"
            "- File name, line number, column, and diagnostic type (e.g., 'warning', 'error').\n"
            "- A concise GCC message (with the type prefix removed).\n"
            "- The name of the function where the issue occurred.\n"
            "- A compressed source snippet of the full function, preserving line numbers.\n"
            "- (Optional) Toolchain metadata indicating which compilers and tools were used.\n\n"
            "Use this information to identify root causes or propose precise fixes. Be technical and concise  "
            "focus on what a human developer would need to inspect or modify to resolve the issue."
            "Provide fixed code suggestions whenever possible."
        )

        self._module_info = self.sdk.registry.register_module(  # and these lines are handled by the user's setup
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
        # Examples: "/path/to/file.c: In function my_func:"
        # Matches a file path and then "In function 'function_name'".
        # The key is to match the initial file part and allow for the trailing colon on the line
        # not necessarily as part of the captured group.
        self._function_regex = re.compile(
            r'^(?P<file>.*?):\s+In function[ \'"](?P<function>[^\'"]+)'  # Restored working regex, added smart quotes
        )

        # Build system chatter patterns to ignore
        self._build_prefix_re = re.compile(r'^\[\d+/\d+]\s+Building ')  # Ninja/Make building progress
        self._failed_line_re = re.compile(r'^FAILED:')  # Ninja/Make FAILED line
        self._compiler_invocation_re = re.compile(r'^\s*/.+gcc\b.*-c\s+')  # Compiler command line
        self._ninja_summary_re = re.compile(
            r'^ninja:.*stopped:.*$')  # Ninja summary (e.g., "ninja: build stopped: sub-command failed.")

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
            # Replace block comment content with white-spaces/newlines only
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

    def _render_ai_response(self, response: Optional[str], export_markdown_file: Union[str, Path],
                            debug: bool = False) -> bool:
        """
        Render the AI response as a Markdown file for later inspection using a textual viewer.
        Args:
            response (Optional[str]): The AI-generated response.
            export_markdown_file (str | Path): The file path where the Markdown output should be written.
            debug (bool): Store the raw AI response to a text file

        Returns:
            bool: True if the file was successfully written, False otherwise.
        """

        def _clean_snippet(_code: str) -> str:
            """
            Clean and format a C/C++ snippet using the SDK formatter.
            """
            _lines = [_line.rstrip() for _line in _code.strip().splitlines()]
            if not any(_line.strip() for _line in _lines):
                return "// (snippet content was omitted or removed for brevity)"

            compacted = []
            blank_count = 0
            for _line in _lines:
                if line.strip():
                    compacted.append(_line)
                    blank_count = 0
                else:
                    blank_count += 1
                    if blank_count <= 1:
                        compacted.append("")
            try:
                return self.sdk.tool_box.clang_formatter("\n".join(compacted))
            except Exception as formatter_error:
                self._logger.warning(f"Failed to format snippet {formatter_error}")
                return "\n".join(compacted)

        if not isinstance(response, str) or not response.strip():
            self._logger.debug("Failed to export AI response, invalid input")
            return False

        try:
            export_markdown_file = Path(export_markdown_file).expanduser().resolve()
            raw_txt_file = export_markdown_file.with_suffix(".raw.txt")

            if debug:
                raw_txt_file.write_text(response.strip(), encoding="utf-8")
                self._logger.debug(f"AI raw response saved to: {raw_txt_file}")

            md_lines: list[str] = ["# AI Analysis Report", ""]

            # Context Section
            try:
                context_obj = json.loads(self.context) if isinstance(self.context, str) else self.context

                if context_obj:
                    md_lines.append("## 🐞 Analyzed Context")
                    md_lines.append("")

                    events = context_obj.get("events") if isinstance(context_obj, dict) else context_obj
                    if not isinstance(events, list):
                        raise TypeError("Expected 'events' to be a list in context")

                    md_lines.append("| Type | File | Function | Line:Col | Message |")
                    md_lines.append("|------|------|----------|----------|---------|")

                    for entry in events:
                        file = Path(entry.get("file", "None")).name
                        function = entry.get("function", "-")
                        typ = entry.get("type", "")
                        line = entry.get("line", "?")
                        col = entry.get("column", "?")
                        msg = entry.get("message", "None")
                        md_lines.append(f"| {typ} | `{file}` | `{function}` | `{line}:{col}` | {msg} |")

                    md_lines.append("")

                    # Optional toolchain info
                    if isinstance(context_obj, dict) and "toolchain" in context_obj:
                        md_lines.append("### 🛠️ Toolchain Info")
                        md_lines.append("")
                        for k, v in context_obj["toolchain"].items():
                            md_lines.append(f"- **{k}**: {v}")
                        md_lines.append("")

                    for entry in events:
                        snippet = entry.get("snippet", "")
                        if snippet:
                            file = entry.get("file", "")
                            line = entry.get("line", "?")
                            cleaned_snippet = _clean_snippet(snippet)
                            md_lines.append(f"## 🧾 Snippet: {Path(file).name} at line {line}")
                            md_lines.append("")
                            md_lines.append("```c")
                            md_lines.append(f"// From file: {file} at line {line}")
                            md_lines.append(cleaned_snippet)
                            md_lines.append("```")
                            md_lines.append("")

            except Exception as format_error:
                self._logger.warning(f"Failed to format context: {format_error}")

            # AI Prompt Section
            md_lines.append("## ❓ AI Prompt")
            md_lines.append("")
            md_lines.append(self._ai_prompt_context)

            # AI Response Section
            code_block_pattern = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
            code_blocks = code_block_pattern.findall(response)
            response_body = code_block_pattern.sub("[[CODE_BLOCK]]", response)

            if debug:
                self._logger.debug(f"Code blocks found: {len(code_blocks)}")
                self._logger.debug(f"Response body after substitution: {repr(response_body)}")

            parts = re.split(r"\n\s*\n", response_body.strip())
            parts = [p.strip() for p in parts if p.strip()]

            md_lines.append("## 🤖 AI Response")
            md_lines.append("")

            for part in parts:
                if "[[CODE_BLOCK]]" in part:
                    segments = part.split("[[CODE_BLOCK]]")
                    for i, seg in enumerate(segments):
                        if seg.strip():
                            for line in seg.strip().splitlines():
                                md_lines.append(f"> {line.strip()}")
                            md_lines.append("")
                        if i < len(segments) - 1 and code_blocks:
                            code = code_blocks.pop(0).strip()
                            md_lines.append("```c")
                            md_lines.append(code)
                            md_lines.append("```")
                            md_lines.append("")
                else:
                    for line in part.splitlines():
                        md_lines.append(f"> {line.strip()}")
                    md_lines.append("")

            # Fallback for any remaining code blocks
            for leftover in code_blocks:
                md_lines.append("```c")
                md_lines.append(leftover.strip())
                md_lines.append("```")
                md_lines.append("")

            if not md_lines:
                self._logger.debug("No structured content detected; exporting as plain block.")
                md_lines.append("```text")
                md_lines.append(response.strip())
                md_lines.append("```")

            export_markdown_file.write_text("\n".join(md_lines), encoding="utf-8")
            return True

        except Exception as export_error:
            self._logger.error(f"Failed to write AI response to Markdown: {export_error}")
            return False

    def _get_ai_response_background(self, prompt: str, context: str, export_markdown_file: Union[str, Path]):
        """
        Sends an AI query in a background thread and processes the result.
        Args:
            prompt (str): The user prompt sent to the AI.
            context (str): Additional context to guide the AI response.
            export_markdown_file (Union[str, Path]): Path to save the AI response in Markdown format.
        """

        def _runner():

            async def _inner():
                response = await self.sdk.ai_bridge.query(
                    prompt=prompt, context=context, max_tokens=400, timeout=30,
                )
                if self._render_ai_response(response=response, export_markdown_file=export_markdown_file):
                    self.sdk.tool_box.show_status(message="🤖 AI response available, type 'rep' to view", expire_after=3,
                                                  erase_after=True)
                else:
                    raise RuntimeError('AI response could not be retrieved')

            try:
                asyncio.run(_inner())
            except Exception as ai_exception:
                self.sdk.tool_box.show_status(message=f"🤖 AI query failed: {ai_exception}", expire_after=2,
                                              status_type=PromptStatusType.ERROR, erase_after=True)

        threading.Thread(target=_runner, daemon=True).start()

    def _generate_error_context(self, file_path: Union[Path, str], line_number: int, lines_range: int = 10) -> Optional[
        BuildAnalyzedEventType]:
        """
        Generates minimal, AI-optimized context from a source file.
        Returns function if line is inside one, otherwise a range of lines.
        """
        path = Path(file_path)
        if not path.exists() or path.suffix not in {".c", ".h"}:
            raise ValueError(f"Unsupported or missing file: {path}")

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if not (1 <= line_number <= len(lines)):
            raise ValueError(f"Line number {line_number} is out of bounds (1{len(lines)}).")

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
                snippet = textwrap.dedent("\n".join(line.rstrip() for line in raw)).strip()
                compressed_snippet = self._compress_function_body(snippet)
                return BuildAnalyzedEventType(
                    function=func_name,
                    snippet=compressed_snippet,
                    snippet_line=target_idx - start
                )

        # Fallback range (outside any function)
        context_start = max(0, target_idx - lines_range)
        context_end = min(len(lines), target_idx + lines_range + 1)
        raw = lines[context_start:context_end]
        snippet = textwrap.dedent("\n".join(line.rstrip() for line in raw)).strip()
        compressed_snippet = self._compress_function_body(snippet)

        return BuildAnalyzedEventType(
            function=None,
            snippet=compressed_snippet,
            snippet_line=target_idx - context_start
        )

    def _serialize(self, context_data: Union[list[dict[str, Any]], dict[str, Any]],
                   output_path: Union[str, Path]) -> bool:
        """
        Saves parsed diagnostic entries to a JSON file.
        Args:
            context_data: Either:
                - a list of structured diagnostic dictionaries, or
                - a dictionary containing additional metadata (e.g., {"toolchain": ..., "events": [...]})
            output_path: Destination file path (str or Path).

        Returns:
            True if successfully written, False otherwise.
        """

        def _clear_duplicated_entries(_entries: list[dict]) -> list[dict]:
            """Removes exact duplicates based on canonicalized JSON keys."""
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

            # Clean 'None' entries
            cleaned = _remove_none_recursive(context_data)

            # Handle structured dict format: {"toolchain": ..., "events": [...]}
            if isinstance(cleaned, dict) and "events" in cleaned:
                cleaned["events"] = _clear_duplicated_entries(cleaned["events"])
                cleaned["events"].sort(key=lambda e: (e.get("file", ""), e.get("line", 0)))
                json_str = json.dumps(cleaned, indent=4, ensure_ascii=False)
            else:
                # List-only case
                cleaned = _clear_duplicated_entries(cleaned)
                cleaned.sort(key=lambda e: (e.get("file", ""), e.get("line", 0)))
                json_str = json.dumps(cleaned, indent=4, ensure_ascii=False)

            self._last_error_context = json_str

            with output_path.open("w", encoding="utf-8") as f:
                f.write(json_str)

            return True
        return False

    def analyze(self, log_source: Union[Path, str],
                context_file_name: Optional[str] = None,
                ai_response_file_name: Optional[str] = None,
                ai_auto_advise: Optional[bool] = False,
                toolchain: Optional[dict[str, Any]] = None) -> Optional[list[dict]]:
        """
        Analyzes a GCC-based compilation log and extracts structured diagnostic events.

        A parser that identifies and collects warnings, errors, and notes emitted by GCC or Clang,
        grouping them into structured event entries that include source file, line, column,
        type, function context, and a cleaned message string. It handles multi-line diagnostics
        (including caret and source lines), removes duplicated prefixes like "warning:", and
        associates diagnostics with detected function names where available.
        Args:
            log_source: Path to a file or raw log string containing compiler output.
            context_file_name: Path to store structured diagnostics (JSON).
            ai_response_file_name: Path for AI response Markdown (optional).
            ai_auto_advise: Auto forward the error context to an AI
            toolchain: The tool-chain dictionary used to during this build.
.
        Returns:
            List of structured diagnostic dictionaries, or None if no diagnostics found.
        """
        parsed_entries: Optional[list[dict]] = None
        event_info: Optional[BuildAnalyzedEventType] = None
        analyzed_context = BuildAnalyzedContextType(toolchain=toolchain)
        pending_function = None
        current_message_lines = []
        log_lines_iterable: Union[IO, list[str]] = []
        log_source_name: str = ""

        self._logger.debug(f"Context will be stored in '{context_file_name}'")
        self._last_error_context = None

        # Nested helper to finalize and store a diagnostic event
        def _finalize_event():
            nonlocal event_info, current_message_lines
            if event_info is not None:
                # Extract first line and strip type prefix like "warning:"
                full_message = "\n".join(current_message_lines).strip().split('\n', 1)[0]
                type_prefix = f"{event_info.type.lower()}:" if event_info.type else ""
                if full_message.lower().startswith(type_prefix):
                    full_message = full_message[len(type_prefix):].strip()
                event_info.message = full_message or None
                analyzed_context.add_event(event_info)
                event_info = None
                current_message_lines = []

        try:
            # Remove existing context file if present
            if isinstance(context_file_name, str):
                Path(context_file_name).unlink(missing_ok=True)

            # Read input lines from file or string
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

            # Main parsing loop
            for line in log_lines_iterable:
                stripped_line = line.strip()

                # Match diagnostic entry (file:line:col: warning|error|note: ...)
                diag_match = self._diag_regex.match(stripped_line)
                if diag_match:
                    _finalize_event()  # Close previous event if open

                    # Extract components
                    file_name = diag_match.group('file')
                    line_number = int(diag_match.group('line'))

                    # Create new structured diagnostic
                    event_info = self._generate_error_context(file_path=file_name, line_number=line_number)
                    event_info.file = file_name.strip()
                    event_info.line = line_number
                    event_info.column = int(diag_match.group('column')) if diag_match.group('column') else None
                    event_info.type = diag_match.group('type').strip()
                    event_info.function = pending_function if pending_function else event_info.function
                    pending_function = None

                    # Extract and append message portion (e.g., "warning: something")
                    try:
                        msg_index = stripped_line.find(f"{event_info.type}:")
                        message_text = stripped_line[msg_index:].strip() if msg_index != -1 else stripped_line
                    except Exception as e:
                        self._logger.warning(f"Failed to clean diagnostic line: {e}")
                        message_text = stripped_line

                    current_message_lines.append(message_text)
                    continue

                # Match "In function 'foo':"
                func_match = self._function_regex.match(stripped_line)
                if func_match:
                    pending_function = func_match.group('function').strip().rstrip(':')
                    continue

                # Match build system lines (not part of diagnostics)
                is_non_diagnostic_line = any((
                    self._build_prefix_re.match(stripped_line),
                    self._failed_line_re.match(stripped_line),
                    self._compiler_invocation_re.match(stripped_line),
                    self._ninja_summary_re.match(stripped_line),
                    "[ninja" in stripped_line.lower(),
                ))

                if is_non_diagnostic_line:
                    _finalize_event()
                    continue

                # Accumulate message body
                if event_info is not None:
                    current_message_lines.append(stripped_line)

            # Finalize last diagnostic
            _finalize_event()

            # Export parsed diagnostics to JSON
            if analyzed_context.count > 0 and context_file_name is not None:
                context_data = analyzed_context.export_data()
                if not self._serialize(context_data=context_data, output_path=context_file_name):
                    self._logger.error(
                        f"Could not serialize {analyzed_context.count} events into '{context_file_name}'")

            # Trigger background AI analysis
            if isinstance(self._last_error_context, str) and ai_auto_advise:
                self._logger.debug("Starting background AI request")
                self._get_ai_response_background(
                    prompt=self._last_error_context,
                    context=self._ai_prompt_context,
                    export_markdown_file=ai_response_file_name
                )

            return parsed_entries if parsed_entries else None

        except (FileNotFoundError, PermissionError) as file_error:
            raise file_error
        except Exception as exception:
            raise IOError(
                f"Unexpected error while processing log source '{log_source_name}': {exception}") from exception
        finally:
            if isinstance(log_lines_iterable, IO):
                log_lines_iterable.close()

    @property
    def context(self) -> Optional[str]:
        """Get the last context which was exported to JSON as string."""
        return self._last_error_context
