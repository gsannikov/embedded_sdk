"""
Script:         linux_aliases,py
Author:         AutoForge Team

Description:
    Provides safe and structured management of Linux shell aliases within a user's shell
    startup file (typically '.zshrc' or '.bashrc'), supports:
    - Querying the current value of an alias via lexical or live-shell resolution.
    - Adding or updating aliases with optional auto-tagging regions for clarity.
    - Committing batched alias changes as a single tagged block.
    - Deleting aliases either from the tagged region or globally (outside functions).
"""

import datetime
import difflib
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from contextlib import suppress
from pathlib import Path
from typing import Optional, Union

# AutoForge late imports
from auto_forge import (
    AutoForgeModuleType, CoreModuleInterface, CoreRegistry, CoreSystemInfo, CoreTelemetry,
    CoreLogger, LinuxShellType, VersionCompare)

AUTO_FORGE_MODULE_NAME = "LinuxAliases"
AUTO_FORGE_MODULE_DESCRIPTION = "Linux Shell Aliases Management Auxiliary Class"


class CoreLinuxAliases(CoreModuleInterface):
    """
    Manages shell environment state and aliases for a given shell (bash, zsh, etc.).
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        self._pending_alias_updates: dict[str, str] = {}
        self._aliases_to_delete = set()

        super().__init__(*args, **kwargs)

    def _initialize(self, rc_files_backup_path: Optional[Union[str, Path]] = None, prefix_comment: Optional[str] = None,
                    suffix_comment: Optional[str] = None, forced_shell: Optional[str] = None) -> None:
        """
        Initialize a ShellBookmark instance for managing aliases in the user's shell RC file.

        Args:
            rc_files_backup_path (Optional[Union[str, Path]]): Path to store backups of the original RC file
                before applying modifications. If not provided, backups are saved alongside the RC file.
            prefix_comment (Optional[str]): Optional comment line to insert before the alias block.
                Used to mark or identify automated sections.
            suffix_comment (Optional[str]): Optional comment line to insert after the alias block.
                Used to mark the end of the managed section.
            forced_shell (Optional[str]): If specified, forces the use of a particular shell ('bash', 'zsh', etc.).
                If None, the user's default shell is auto-detected.
        """

        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry = CoreRegistry.get_instance()
        self._sys_info: CoreSystemInfo = CoreSystemInfo().get_instance()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()

        # Dependencies check
        if None in (self._core_logger, self._logger, self._registry, self._sys_info, self._telemetry):
            raise RuntimeError("failed to instantiate critical dependencies")

        self._shell_name: Optional[str] = None
        self._shell_version: Optional[str] = None
        self._shell_rc_file: Optional[Path] = None
        self._shell_type: LinuxShellType = LinuxShellType.UNKNOWN
        self._home_dir: Path = Path.home()
        self._rc_files_backup_path: Optional[Path] = Path(rc_files_backup_path) if rc_files_backup_path else None

        if prefix_comment is None:
            # Generate default comments with dynamic date in MM-DD-YY format
            current_date = datetime.datetime.now().strftime("%m-%d-%y")
            dashes = "-" * 21
            prefix_comment = f"{dashes} Section was auto added on {current_date} {dashes} "

        if suffix_comment is None:
            suffix_comment = ("-" * (len(prefix_comment) - 1))

        # Form the comments so will be valid in a shell script file.
        self._prefix_comment: str = self._format_shell_comment(prefix_comment.strip())
        self._suffix_comment: str = self._format_shell_comment(suffix_comment.strip())
        self._env_valid: bool = False

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Auto Probe the environment
        self._probe_env(forced_shell=forced_shell)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)
        return None

    def lookup(self, alias: str) -> Optional[str]:
        """
        Check if a shell alias is defined, first by scanning the shell RC file lexically,
        and then by sourcing the shell environment if necessary.
        Args:
            alias (str): The alias name to check.
        Returns:
            Optional[str]: The alias value (unquoted) if found, or None if not found or an error occurred.
        """
        if not self._env_valid or not self._shell_rc_file or not self._shell_name:
            return None

        # Prioritizes performance : static check: scan the RC file directly
        alias_val = self._alias_lookup_in_rc(alias=alias)
        if alias_val:
            return alias_val

        # Fallback to intrusive shell evaluation
        shell_path = shutil.which(self._shell_name)
        if not shell_path:
            return None

        def _get_clear_text(_text: Optional[str]) -> Optional[str]:
            """ Remove ANSI escape sequences from the input string. """
            if isinstance(_text, str):
                ansi_escape = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
                _cleared: str = ansi_escape.sub('', _text)
                return _cleared.strip()
            return _text

        def _get_alias_definition(raw: str) -> Optional[str]:
            """Extract the value part from a shell alias output line like: alias foo='bar baz'"""
            match = re.match(rf"^alias\s+{re.escape(alias)}=['\"](.*?)['\"]$", raw)
            return match.group(1) if match else None

        def _build_full_alias_query_command(_alias: str) -> Optional[str]:
            """Construct the full non-interactive source command per shell."""
            _source_cmd = self._set_shell_source_command(self._shell_type, self._shell_rc_file)
            return f"{_source_cmd} {shlex.quote(_alias)}" if _source_cmd else None

        command = _build_full_alias_query_command(alias)
        if not command:
            return None
        with suppress(Exception):
            result = subprocess.run([shell_path, "-c", command], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True)
            if result.returncode != 0:
                return None

            output = _get_clear_text(result.stdout.strip())
            return _get_alias_definition(output)

        return None

    def create(self, alias: str, command: str, can_update_existing: bool = True) -> bool:
        """
        Queue an alias definition for update or creation.
        The alias will not be written until `commit_alias_updates()` is called.
        Args:
            alias (str): The alias name to define or update.
            command (str): The command string the alias should invoke.
            can_update_existing (bool): If False and alias already exists in the RC file block, skip queuing.

        Returns:
            bool: True if the alias is queued for update or creation, False otherwise.
        """
        if not self._env_valid or not self._shell_rc_file:
            return False

        if not hasattr(self, "_pending_alias_updates"):
            self._pending_alias_updates = {}

        # Skip if already queued and updates not allowed
        if not can_update_existing and alias in self._pending_alias_updates:
            return False

        # Skip if already exists in the file and updates not allowed
        if not can_update_existing and self._alias_lookup_in_rc(alias):
            return False

        self._pending_alias_updates[alias] = command
        return True

    def delete(self, alias: str) -> bool:
        """
        Queue a top-level alias for deletion. Actual removal occurs on commit.
        Args:
            alias (str): The alias name to delete.
        Returns:
            bool: True if deletion is queued, False otherwise.
        """
        if not self._env_valid or not self._shell_rc_file:
            return False

        # Validate the alias exists
        if not self._alias_lookup_in_rc(alias):
            return False

        if not hasattr(self, "_aliases_to_delete"):
            self._aliases_to_delete = set()

        # Remove it from update queue if it's there
        if hasattr(self, "_pending_alias_updates"):
            self._pending_alias_updates.pop(alias, None)

        self._aliases_to_delete.add(alias)
        return True

    def commit(self) -> bool:
        """
        Write all queued alias definitions into the shell RC file under a single tagged region.
        Returns:
            bool: True if update was successful, False otherwise.
        """
        if not self._env_valid or not self._shell_rc_file:
            return False

        def _look_alike(a: str, b: str, threshold: float = 0.85) -> bool:
            """
            Returns True if two strings are sufficiently similar based on a given threshold.
            Useful for fuzzy matching of comments or labels that may differ slightly in content or formatting.
            Args:
                a (str): First string to compare.
                b (str): Second string to compare.
                threshold (float): Similarity ratio (0.0 to 1.0) above which the strings are considered similar.

            Returns:
                bool: True if similarity ratio >= threshold, False otherwise.
            """
            ratio = difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()
            return ratio >= threshold

        with suppress(Exception):
            rc_path = self._shell_rc_file
            backup_path = (
                self._rc_files_backup_path / rc_path.name if self._rc_files_backup_path else rc_path.with_suffix(
                    rc_path.suffix + ".bak"))
            shutil.copy(rc_path, backup_path)

            with rc_path.open(encoding="utf-8") as f:
                lines_in = f.readlines()

            lines_out = []
            in_block = False
            block_start_idx = None
            block_end_idx = None
            block_lines = []
            old_block_aliases = {}

            for idx, line in enumerate(lines_in):
                stripped = line.strip()

                if self._prefix_comment and _look_alike(stripped, self._prefix_comment):
                    in_block = True
                    block_start_idx = idx
                    block_lines = [line]
                    continue

                if in_block:
                    block_lines.append(line)
                    if self._suffix_comment and _look_alike(stripped, self._suffix_comment):
                        in_block = False
                        block_end_idx = idx
                        # Extract aliases from the old block
                        for bl in block_lines:
                            m = re.match(r"^alias\s+(\w+)=['\"](.*?)['\"]$", bl.strip())
                            if m:
                                old_block_aliases[m.group(1)] = m.group(2)
                        continue  # skip writing old block lines

                lines_out.append(line)

            # Remove aliases marked for deletion
            to_delete = getattr(self, "_aliases_to_delete", set())
            old_block_aliases = {name: cmd for name, cmd in old_block_aliases.items() if name not in to_delete}

            # Merge updates on top of remaining aliases
            for name, cmd in self._pending_alias_updates.items():
                old_block_aliases[name] = cmd

            if not old_block_aliases:
                return True  # nothing to update

            # Build the new block
            new_block = []
            if self._prefix_comment:
                new_block.append(self._prefix_comment + "\n")

            for alias, cmd in self._pending_alias_updates.items():
                new_block.append(f"alias {alias}='{cmd}'\n")

            if self._suffix_comment:
                new_block.append(self._suffix_comment + "\n")

            # Insert the block
            if block_start_idx is not None and block_end_idx is not None:
                lines_out[block_start_idx:block_end_idx + 1] = new_block
            else:
                lines_out.append("\n")
                lines_out.extend(new_block)

            with rc_path.open("w", encoding="utf-8") as f:
                f.writelines(lines_out)

            self._pending_alias_updates.clear()
            self._aliases_to_delete.clear()
            return True

        return False  # Suppressed exceptiom

    def _probe_env(self, forced_shell: Optional[str] = None) -> bool:
        """
        Detect the user shell environment.
        Args:
            forced_shell (Optional[str]): Force probing for a specific shell ('bash', 'zsh', etc.).
                If None, will auto-detect the user's default shell.
        Returns:
            bool: True if a valid shell environment was detected and configured.
        """
        with suppress(Exception):

            # Use user-specified shell or detect from environment
            shell_bin = shutil.which(forced_shell) if forced_shell else self._sys_info.linux_shell
            if not shell_bin:
                return False

            # Get the shell binary base nam, make sure it's a string and normalize it
            shell_name = os.path.basename(shell_bin)
            if not isinstance(shell_name, str) or not shell_name.strip():
                return False

            # Map the string to an enum identifier
            shell_type = self._get_shell_type(shell_name=shell_name)
            if shell_type == LinuxShellType.UNKNOWN:
                return False  # The Shell type could not be resolved

            # Try to extract the numerical porton of the binary response to version request
            shell_version = self._get_shell_version(shell_name)
            shell_rc_file = self._get_rc_file_path(shell_type=shell_type)

            # Lastly, set class globals and state if the .rc file is identified and found.
            if shell_rc_file and shell_rc_file.exists():
                self._shell_rc_file = shell_rc_file
                self._shell_type = shell_type
                self._shell_name = shell_name
                self._shell_version = shell_version  # Note could be none if we failed to parse it.
                self._env_valid = True

                return True

        # Suppressed error has accused
        self._env_valid = False
        return False

    @staticmethod
    def _format_shell_comment(_text: Optional[str], max_line_width: int = 120) -> Optional[str]:
        """
        Formats a given text into a shell comment friendly format,ensuring:
        1. Each output line adheres to max_line_width.
        2. Each output line starts with exactly '# '.
        3. Internal sequences of multiple '#' characters within the content
           are normalized to single spaces.
        4. Multi-line input comments are treated as a single block for wrapping,
           with internal newlines also being normalized.
        Args:
            _text: The input text.
            max_line_width: The maximum desired width for each line, including the '#'.

        Returns:
            The formatted shell comment string.
        """
        if not isinstance(_text, str):
            return None

        comment_prefix = "# "
        # Calculate the effective width for the text content, accounting for the prefix
        effective_max_width = max_line_width - len(comment_prefix)

        # Extract the core content, removing any leading comment markers from input lines ---
        if _text.strip().startswith('#'):
            # If the input already looks like a comment, strip its leading hashes line by line
            extracted_content_parts = []
            for line in _text.splitlines():
                stripped_line = line.lstrip()
                if stripped_line.startswith('#'):
                    # Find the first non-hash character to get the actual content start
                    content_start_index = 0
                    while content_start_index < len(stripped_line) and stripped_line[content_start_index] == '#':
                        content_start_index += 1

                    # Append the content after the leading hashes, stripping its own leading whitespace
                    if content_start_index < len(stripped_line):
                        extracted_content_parts.append(stripped_line[content_start_index:].lstrip())
                    else:
                        extracted_content_parts.append("")  # Line was just hashes or empty after hashes
                else:
                    # If a line in a multi-line input doesn't start with '#', treat it as regular text
                    extracted_content_parts.append(stripped_line)
            text_to_process = "\n".join(extracted_content_parts)
        else:
            # If it's not starting with '#', use the text as is
            text_to_process = _text

        # Normalize internal multiple '#' characters and all whitespace ---
        normalized_content = re.sub(r'#+', ' ', text_to_process)
        # Coalesce all whitespace (including newlines resulting from splitlines) into a single space,
        normalized_content = re.sub(r'\s+', ' ', normalized_content).strip()
        # Wrap the normalized content to fit the max_line_width ---
        wrapped_lines = textwrap.fill(normalized_content, width=effective_max_width,
                                      break_on_hyphens=False).splitlines()
        # Prepend '# ' to each wrapped line ---
        formatted_lines = [comment_prefix + line for line in wrapped_lines]

        return "\n".join(formatted_lines).strip()

    @staticmethod
    def _get_shell_version(shell_name: str) -> Optional[str]:
        """
        Attempt to get the version of the detected shell.
        Args:
            shell_name (Optional[str]): The name of the shell to check.
        Returns:
            str, optional: The version of the detected shell or None if no version is detected.
        """
        version: Optional[str] = None

        with suppress(Exception):
            result = subprocess.run([shell_name, '--version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True)
            if result.returncode == 0:
                binary_response = result.stdout.strip()
                version = VersionCompare().extract_version(text=binary_response)

        return version

    @staticmethod
    def _get_shell_type(shell_name: Optional[str]) -> LinuxShellType:
        """
        Maps the shell binary base name to its enum identifier.
        Args:
            shell_name (str): The name of the shell to check.
        Returns:
            LinuxShellType: The enum identifier for the shell name.
        """
        if not isinstance(shell_name, str):
            return LinuxShellType.UNKNOWN

        _shell_name = shell_name.lower().strip()

        if _shell_name == "bash":
            return LinuxShellType.BASH
        elif _shell_name == "zsh":
            return LinuxShellType.ZSH
        elif _shell_name == "fish":
            return LinuxShellType.FISH
        else:  # Unknown or not supported shell
            return LinuxShellType.UNKNOWN

    # noinspection SpellCheckingInspection
    @staticmethod
    def _set_shell_source_command(shell_type: LinuxShellType, shell_rc_file: Path) -> Optional[str]:
        """
        Construct the correct non-interactive source command line for the given shell type.
        Args:
            shell_type (LinuxShellType): The enum value representing the shell type.
            shell_rc_file (Path): The path to the shell RC file to source.
        Returns:
            Optional[str]: A shell-safe command string to source the RC and enable alias resolution,
                           or None if unsupported.
        """
        rc_path_quoted = shlex.quote(str(shell_rc_file))

        if shell_type == LinuxShellType.BASH:
            return f"source {rc_path_quoted} >/dev/null 2>&1; alias"

        elif shell_type == LinuxShellType.ZSH:
            return (f"autoload -U compinit >/dev/null 2>&1; compinit; "
                    f"setopt aliases; "
                    f"source {rc_path_quoted} >/dev/null 2>&1; alias")
        elif shell_type == LinuxShellType.FISH:
            return f"source {rc_path_quoted}; functions -n"
        else:
            return None

    def _get_rc_file_path(self, shell_type: LinuxShellType) -> Optional[Path]:
        """
        Map shell types to expected rc file paths.
        Args:
            shell_type (LinuxShellType): The id of the shell to check.
        Returns:
            Path, Optional: the executed path to the shell .rc or None if the shell is not recognized by this class.
        """
        if shell_type == LinuxShellType.BASH:
            return self._home_dir / '.bashrc' if (
                    self._home_dir / '.bashrc').exists() else self._home_dir / '.bash_profile'
        elif shell_type == LinuxShellType.ZSH:
            return self._home_dir / '.zshrc'
        elif shell_type == LinuxShellType.FISH:
            return self._home_dir / '.config/fish/config.fish'
        else:  # Unknown or not supported shell
            return None

    def _alias_lookup_in_rc(self, alias: str) -> Optional[str]:
        """
        Lexically scan the shell RC file for a top-level alias definition.
        Ignores aliases inside functions or conditional blocks. Matches alias assignments
        anywhere on a line, including compound statements, as long as they are not nested.
        Args:
            alias (str): The alias name to search for.
        Returns:
            Optional[str]: The alias value if found (unquoted), or None if not found or invalid.
        """
        if not self._env_valid or not self._shell_rc_file:
            return None

        # Match: alias foo='some command'; handles both ' and "
        alias_pattern = re.compile(rf"""(?:^|\s)alias\s+{re.escape(alias)}\s*=\s*(['"])(.*?)\1""")

        brace_level = 0

        with suppress(Exception):
            with self._shell_rc_file.open(encoding="utf-8") as rc_file:
                for line in rc_file:
                    brace_level += line.count("{") - line.count("}")
                    if brace_level > 0:
                        continue  # skip if inside a function or block

                    match = alias_pattern.search(line)
                    if match:
                        return match.group(2)

        return None
