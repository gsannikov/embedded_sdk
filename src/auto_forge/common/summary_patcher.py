"""
Script:         summary_patcher.py
Author:         AutoForge Team

Description:
    Anb module that provides language-aware logic for detecting and patching summary
    blocks in source files, such as module-level docstrings or comment headers.

    It supports multiple programming languages including Python, Shell, Groovy, and C-like
    languages. For high-accuracy parsing, it uses Tree-sitter — a lightweight incremental
    parsing engine capable of generating concrete syntax trees for many languages. Unlike
    traditional regex or line-based parsing, Tree-sitter understands source code structure,
    enabling safe and precise detection of comment blocks, string expressions, and declarations
    across language boundaries.

"""
import re
from pathlib import Path
from typing import Union, Optional

# Tree sitter AST library for Python
import tree_sitter_bash as ts_bash
import tree_sitter_groovy as ts_groovy
import tree_sitter_python as ts_python
# Third-party
from pygments.lexers import guess_lexer_for_filename
from tree_sitter import Parser, Language

# AutoForge imports
from auto_forge import (SourceFileLanguageType, SourceFileInfoType, )

AUTO_FORGE_MODULE_NAME = "SummaryPatcher"
AUTO_FORGE_MODULE_DESCRIPTION = "Language-aware summary detection and patching using AST and Tree-sitter"
AUTO_FORGE_DEFAULT_MAX_REDA_SIZE = (1024 * 128)  # 128Kb max allowed processed source file size


class _LanguageAnalysis:
    """
    Helper class that analyzes a source file to identify its programming language and
    extract structural information relevant to AI summarization.

    This class uses Tree-sitter with language-specific grammars to:
      - Parse the source code.
      - Detect the programming language.
      - Locate any existing summary blocks (e.g., docstrings or header comments).
      - Suggest an appropriate insertion point for a new summary.

    Returns its results in a structured SourceFileInfoType dataclass, including:
      - Source file content and metadata.
      - Detected language.
      - Suggested line index for injecting summaries.
    """

    def __init__(self, max_read_size_bytes: Optional[int] = None):
        self._max_read_size_bytes: int = (
            max_read_size_bytes if isinstance(max_read_size_bytes,
                                              int) and max_read_size_bytes > 0 else AUTO_FORGE_DEFAULT_MAX_REDA_SIZE
        )

    def _read_content(self, filename: Union[str, Path]) -> Optional[str]:
        if not isinstance(filename, (str, Path)):
            raise TypeError("'filename' must be a string or pathlib.Path object")

        path = Path(filename)
        if not path.is_file():
            raise FileNotFoundError(f"file does not exist: {path}")

        file_size = path.stat().st_size
        if file_size > self._max_read_size_bytes:
            raise ValueError(f"file too large: {file_size} bytes (limit is {self._max_read_size_bytes} bytes)")

        content = path.read_bytes()
        if b"\x00" in content:
            raise ValueError("file appears to be binary (contains null bytes)")

        return content.decode("utf-8", errors="replace")

    @staticmethod
    def _detect_shell_summary(file_info: SourceFileInfoType):
        """
        Detects the existing top-level comments block in shell scripts.
        """

        def _normalize_lines(_lines: list[str]) -> list[str]:
            """
            Normalize a block of text as a valid shell script docstring.
            - If a 'Description:' section is found, extract and return it.
            - Otherwise, reformat all lines to ensure each starts with exactly '# '.

            Any un-commented or improperly commented lines are fixed.
            """

            def _normalize_comment(_line: str) -> str:
                _match = re.sub(r"^\s*#\s?", "", _line.rstrip())  # remove leading comment if present
                return f"# {_match}" if _match else "#"

            # Try to find a 'description:' section in any form
            description_block = []
            in_description = False
            for idx, line in enumerate(_lines):
                _content = re.sub(r"^\s*#\s?", "", line.rstrip())

                if re.match(r"(?i)^description\s*:", _content):
                    in_description = True
                    after_colon = _content.split(":", 1)[1].strip()
                    description_block.append(_normalize_comment(after_colon))
                    continue

                if in_description:
                    if _content.strip() == "":
                        description_block.append("#")
                    elif re.match(r"^\s", line):  # indented = continuation
                        description_block.append(_normalize_comment(_content.strip()))
                    else:
                        break  # End of description block

            if description_block:
                return description_block

            # Fallback: normalize entire block
            return [_normalize_comment(line) for line in _lines]

        def _suggest_patch_start_line(_lines: list[str]) -> int:
            """
            Suggests the insertion point for a summary:
            - After shebang if found
            - Else top of file
            """
            for _i, _line in enumerate(_lines):
                stripped = _line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#!") and "sh" in stripped.lower():
                    return _i + 1
                return 0
            return 0

        file_info.suggested_start_line = _suggest_patch_start_line(file_info.source_code_lines)

        SHELL_LANGUAGE = Language(ts_bash.language())
        parser = Parser(SHELL_LANGUAGE)
        tree = parser.parse(file_info.source_code)
        root = tree.root_node
        summary_start = None
        summary_end = None

        # Find top-most contiguous block of comment nodes starting at top or after shebang
        for i, node in enumerate(root.children):
            if node.type == "comment":
                content = file_info.source_code_lines[node.start_point[0]].strip()

                # Skip shebang-style comment at the top
                if node.start_point[0] == 0 and content.startswith("#!") and "sh" in content.lower():
                    continue

                if summary_start is None:
                    summary_start = node.start_point[0]
                summary_end = node.end_point[0]
            elif summary_start is not None:
                break  # stop at first non-comment after comment block

        if summary_start is not None and summary_end is not None:
            file_info.summary_start_line = summary_start
            file_info.summary_end_line = summary_end

            comment_block = file_info.source_code_lines[summary_start:summary_end + 1]
            file_info.summary_exiting_content = _normalize_lines(comment_block)

    @staticmethod
    def _detect_groovy_summary(file_info: SourceFileInfoType):
        """
        Detects the existing top-level comment block in Groovy source files.
        Supports both `//` single-line and `/* ... */` block comments.
        """

        def _suggest_patch_start_line(lines: list[str]) -> int:
            """
            Suggest insertion point: after shebang or top of file
            """
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#!") and "groovy" in stripped.lower():
                    return i + 1
                return 0
            return 0

        file_info.suggested_start_line = _suggest_patch_start_line(file_info.source_code_lines)

        GROOVY_LANGUAGE = Language(ts_groovy.language())
        parser = Parser(GROOVY_LANGUAGE)
        tree = parser.parse(file_info.source_code)
        root = tree.root_node
        summary_start = None
        summary_end = None

        for node in root.children:
            if node.type == "line_comment" or node.type == "block_comment":
                content = file_info.source_code_lines[node.start_point[0]].strip()

                # Exclude shebang line if parsed as comment
                if node.start_point[0] == 0 and content.startswith("#!") and "groovy" in content.lower():
                    continue

                if summary_start is None:
                    summary_start = node.start_point[0]
                summary_end = node.end_point[0]
            elif summary_start is not None:
                break  # End of contiguous block

        if summary_start is not None and summary_end is not None:
            file_info.summary_start_line = summary_start
            file_info.summary_end_line = summary_end
            comment_block = file_info.source_code_lines[summary_start:summary_end + 1]
            file_info.summary_exiting_content = comment_block

    @staticmethod
    def _detect_python_summary(file_info: SourceFileInfoType):
        """
        Detects the existing top-level docstring (if any) and suggests the appropriate
        insertion line for a summary block in a Python source file.
        """

        def _normalize_lines(_lines: list[str]) -> list[str]:
            """
            Normalize a block of text as a python script docstring.
            If a 'description' section exists in the lines, return its indented block as a cleaned list of strings.
            Otherwise, return the input list as-is.
            """
            description_start = None
            description_lines = []

            for idx, line in enumerate(_lines):
                if re.match(r'^[ \t]*description[ \t]*:', line, re.IGNORECASE):
                    description_start = idx
                    break

            if description_start is not None:
                # Handle inline text on same line
                desc_header = _lines[description_start]
                after_colon = desc_header.split(":", 1)[1].strip()
                if after_colon:
                    description_lines.append(after_colon)

                # Add indented block
                for line in _lines[description_start + 1:]:
                    if line.strip() == "":
                        description_lines.append("")
                    elif re.match(r'^[ \t]+', line):
                        description_lines.append(line.strip())
                    else:
                        break

                return description_lines

            # No description section → fallback: return all non-blank lines stripped of indentation
            return [line.strip() for line in _lines if line.strip()]

        def _suggest_patch_start_line(_lines: list[str]) -> int:
            """
            Suggests the line index for inserting a summary.
            Skips leading empty lines and places it after a shebang if present.
            """
            for i, line in enumerate(_lines):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#!") and "python" in stripped.lower():
                    return i + 1  # insert after shebang
                return 0  # first non-empty line is not a shebang → insert at top
            return 0  # all lines are blank

        # Default suggestion: after shebang or at top
        file_info.suggested_start_line = _suggest_patch_start_line(file_info.source_code_lines)

        PY_LANGUAGE = Language(ts_python.language())
        parser = Parser(PY_LANGUAGE)
        tree = parser.parse(file_info.source_code)
        root = tree.root_node
        summary_start = None
        summary_end = None

        for node in root.children:
            if node.type == "expression_statement" and node.child_count == 1:
                child = node.children[0]
                if child.type == "string":
                    if summary_start is None:
                        summary_start = node.start_point[0]
                    summary_end = node.end_point[0]
                elif summary_start is not None:
                    break  # End of contiguous block

                if summary_start is not None and summary_end is not None:
                    file_info.summary_start_line = summary_start
                    file_info.summary_end_line = summary_end
                    comment_block = file_info.source_code_lines[summary_start:summary_end + 1]
                    file_info.summary_exiting_content = _normalize_lines(comment_block)

    def analyze(self, filename: Union[str, Path]) -> Optional[SourceFileInfoType]:
        """
        Analyzes a source file to determine its programming language and extract summary-related metadata.
        - Reads and validates the file content (UTF-8, text-only, size-limited).
        - Uses Pygments to guess the programming language based on filename and content.
        - Maps the detected language to a supported enum (`SourceFileLanguageType`).
        - Dispatches language-specific logic (e.g., using Tree-sitter or AST) to:
            - Detect an existing summary block (docstring or comment header).
            - Suggest an appropriate insertion line for a new or replacement summary.
        Args:
            filename: Path to the source file to analyze.
        Returns:
            A populated `SourceFileInfoType` object with language, content, and summary metadata,
            or `None` if the file was unreadable or unsuitable.

        """
        try:
            file_info = SourceFileInfoType()
            file_info.filename = Path(filename)

            file_info.file_content = self._read_content(file_info.filename)
            if file_info.file_content is None:
                return None

            file_info.file_size = len(file_info.file_content)
            file_info.source_code = file_info.file_content.encode("utf-8")
            file_info.source_code_lines = file_info.file_content.splitlines()

            lexer = guess_lexer_for_filename(str(file_info.filename), file_info.file_content)
            language_name = lexer.name.lower()

            mapping = {
                "python": SourceFileLanguageType.PYTHON,
                "c": SourceFileLanguageType.C,
                "json": SourceFileLanguageType.JSON,
                "json5": SourceFileLanguageType.JSON,
                "groovy": SourceFileLanguageType.GROOVY,
                "bash": SourceFileLanguageType.SHELL,
                "sh": SourceFileLanguageType.SHELL,
                "shell session": SourceFileLanguageType.SHELL,
                "yaml": SourceFileLanguageType.YAML,
                "ansible": SourceFileLanguageType.YAML,
            }

            file_info.programming_language = mapping.get(language_name, SourceFileLanguageType.UNKNOWN)
            if file_info.programming_language == SourceFileLanguageType.UNKNOWN:
                raise RuntimeError(f"language mapping returned unknown languge")

            # Handle the source file based onb the detected language
            if file_info.programming_language == SourceFileLanguageType.PYTHON:
                self._detect_python_summary(file_info)
            elif file_info.programming_language == SourceFileLanguageType.SHELL:
                self._detect_shell_summary(file_info)
            elif file_info.programming_language == SourceFileLanguageType.GROOVY:
                self._detect_groovy_summary(file_info)
            else:
                raise RuntimeError(
                    f"programming language '{file_info.programming_language.name.title()}' not yet supported")

            return file_info

        except Exception as analyzer_error:
            raise RuntimeError(f"unable to detect language: {analyzer_error}")


class SummaryPatcher:
    """ Implements the SummaryPatcher class """

    def __init__(self, max_read_size_bytes: Optional[int] = None) -> None:
        self._analyzer = _LanguageAnalysis(max_read_size_bytes=max_read_size_bytes)

    def get_analysis(self, filename: Union[str, Path]) -> Optional[SourceFileInfoType]:
        return self._analyzer.analyze(filename)
