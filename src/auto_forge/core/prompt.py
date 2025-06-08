"""
Script:         prompt.py
Author:         AutoForge Team

Description:
    Core module that defines and manages the PromptEngine class, which integrates the cmd2
    interactive shell with prompt_toolkit to provide a rich command-line interface for the
    AutoForge build system.
"""
import fcntl
import logging
import os
import shlex
import stat
import subprocess
import sys
import termios
import textwrap
from collections.abc import Iterable
from contextlib import suppress
from itertools import islice
from pathlib import Path
from types import MethodType
from typing import Optional, Any, Union

# Third-party
import cmd2
from cmd2 import Statement, ansi
from cmd2 import with_argument_list
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, CompleteEvent, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console

# AutoForge imports
from auto_forge import (PROJECT_NAME, PROJECT_VERSION, AutoLogger, AutoForgCommandType, AutoForgeModuleType,
                        BuildProfileType, CoreEnvironment, CoreLoader, CoreModuleInterface, CoreSolution,
                        TerminalEchoType, ModuleInfoType, CoreVariables, ExecutionModeType, Registry, ToolBox)

# Basic types
AUTO_FORGE_MODULE_NAME = "Prompt"
AUTO_FORGE_MODULE_DESCRIPTION = "Prompt manager"


class _CorePathCompleter(Completer):
    """
        A command-specific path completer that delegates to `PathCompleter` when the input
        line begins with the specified command name.
        This is useful for commands like `cd`, `mkdir`, etc., where argument completion
        should offer filesystem paths, optionally limited to directories.

        Attributes:
            _command_name (str): The CLI command this completer is associated with.
            _path_completer (PathCompleter): Internal prompt_toolkit completer for paths.
        """

    def __init__(self, core_prompt: "CorePrompt", command_name: str, only_directories: bool):
        """
        Initialize the completer for a specific command.
        Args:
            command_name (str): The command to match against the beginning of input lines (e.g., "cd").
            only_directories (bool): If True, only suggest directory names (not files).
        """
        self._core_prompt = core_prompt
        self._command_name = command_name
        self._path_completer = PathCompleter(only_directories=only_directories, expanduser=True)

    def get_completions(self, document: Document, complete_event):
        """
        Yield path completions for a shell-like command, skipping flags like '-rf'.
        """
        text = document.text_before_cursor.lstrip()

        try:
            tokens = shlex.split(text)
        except ValueError:
            return  # Unbalanced quotes, etc.

        if not tokens or tokens[0] != self._command_name:
            return

        # Find the token we're completing
        if text.endswith(" "):
            # Cursor is after a space => start new arg
            arg = ""
        else:
            arg = tokens[-1]

        # If the current token is a flag (starts with -), skip completion
        if arg.startswith("-"):
            return

        # Delegate to the internal PathCompleter
        sub_doc = Document(text=arg, cursor_position=len(arg))
        for completion in islice(self._path_completer.get_completions(sub_doc, complete_event),
                                 self._core_prompt.max_completion_results):
            yield completion


class _CoreCompleter(Completer):
    """
    A context-aware tab-completer for shell-like CLI behavior using prompt_toolkit.
    This completer supports:
    - Executable name completion (from PATH)
    - Dynamically loaded CLI command completion
    - Built-in commands with `do_<command>()` methods
    - Custom argument-level completion via `complete_<command>()` functions
    - Project-specific smart completion for `build` with dot-notation
    - Progressive token parsing and prompt coloring support

    It distinguishes between:
    1. First word completion (i.e., command): suggests system, dynamic, and built-in commands
    2. Argument-level completion: delegates to registered per-command completer or generic path matching
    """

    def __init__(self, core_prompt: "CorePrompt", logger: logging.Logger):

        self._core_prompt = core_prompt
        self._logger = logger
        self._loader: Optional[CoreLoader] = CoreLoader.get_instance()

    def _should_fallback_to_path_completion(self, cmd: str, arg_text: str, completer_func: Optional[callable]) -> bool:
        """
        Determines whether path completions should be triggered for a command that has no specific completer.
        Args:
            cmd (str): The base command name.
            arg_text (str): The partial argument text (after the command).
            completer_func (Optional[callable]): If a custom completer exists.
        Returns:
            bool: True if fallback to path completion should occur.
        """
        if completer_func:
            return False

        if not self._core_prompt.path_completion_rules_metadata:
            return True

        meta = self._core_prompt.path_completion_rules_metadata.get(cmd)
        if meta:
            return meta.get("path_completion", False)  # allow even if arg_text is empty

        # Fallback for unlisted known commands
        is_known_command = (cmd in self._core_prompt.executables_metadata or cmd in self._core_prompt.commands_metadata)
        return is_known_command and bool(arg_text.strip())

    def get_completions(  # noqa: C901
            self, document: Document, _complete_event: CompleteEvent) -> Iterable[Completion]:
        """
        Return completions for the current cursor position and buffer state.
        Supports:
        - Command name completion (built-in, dynamic, executable)
        - Per-command argument completion via `complete_<cmd>()`
        - Fallback to path completion for external commands
        - Prevents fallback on known non-path commands like `alias`, `help`, etc.

        NOTE:
            This function exceeds typical complexity limits (C901) by design.
            It encapsulates a critical, tightly-coupled sequence of logic that benefits from being kept together
            for clarity, atomicity, and maintainability. Refactoring would obscure the execution flow.
        """
        try:
            text = document.text_before_cursor
            buffer_ends_with_space = text.endswith(" ")
            tokens = text.strip().split()
            matches = []
            arg_text = ""  # Ensures it's always defined

            # Case 1: Completing the first word (a command)
            if not tokens or (len(tokens) == 1 and not buffer_ends_with_space):
                partial = tokens[0] if tokens else ""

                # First-token path completion support
                if partial.startswith(("/", "./", "../")):
                    matches = self._core_prompt.gather_path_matches(text=os.path.expanduser(partial))
                    for m in matches:
                        yield m
                    return

                # Registered CLI commands
                for cmd in self._core_prompt.commands_metadata:
                    if cmd.startswith(partial):
                        matches.append(Completion(cmd, start_position=-len(partial), style='class:cli_commands'))

                # Built-in do_* methods
                for name, method in vars(self._core_prompt.__class__).items():
                    if name.startswith("do_") and callable(method):
                        cmd_name = name[3:]
                        # Check if its one of the aliases that gets executed through a dynamic do_ implementation.
                        is_known_alias = (cmd_name in self._core_prompt.aliases_metadata)
                        if cmd_name.startswith(partial):
                            if not is_known_alias:
                                matches.append(
                                    Completion(cmd_name, start_position=-len(partial), style='class:builtins'))
                            else:
                                matches.append(
                                    Completion(cmd_name, start_position=-len(partial), style='class:aliases'))

                # System executables (styled if executable)
                matches += [Completion(sys_binary, start_position=-len(partial), style='class:executable') for
                            sys_binary in self._core_prompt.executables_metadata if sys_binary.startswith(partial)]

            # Case 2: Completing arguments for a command
            elif len(tokens) >= 1:

                cmd = tokens[0]
                arg_text = text[len(cmd) + 1:] if len(text) > len(cmd) + 1 else ""
                begin_idx = len(text) - len(arg_text)
                end_idx = len(text)

                completer_func = getattr(self._core_prompt, f"complete_{cmd}", None)
                # Retrieve optional arguments hints for registered CLI commands
                cli_command_args_list = self._loader.get_cli_command_known_args(name=cmd)

                if completer_func:
                    try:
                        results = completer_func(arg_text, text, begin_idx, end_idx)
                        if results:
                            if isinstance(results[0], str):
                                matches = [Completion(r, start_position=-len(arg_text)) for r in results]
                            else:
                                matches = results
                    except Exception as completer_exception:
                        self._logger.debug(f"Completer error for '{cmd}': {completer_exception}")

                elif self._should_fallback_to_path_completion(cmd=cmd, arg_text=arg_text, completer_func=None):
                    meta = self._core_prompt.path_completion_rules_metadata.get(cmd, {})
                    matches = self._core_prompt.gather_path_matches(text=arg_text if arg_text.strip() else ".",
                                                                    only_dirs=meta.get("only_dirs", False))

                elif cli_command_args_list:
                    matches = [Completion(arg, start_position=-len(arg_text)) for arg in cli_command_args_list if
                               arg.startswith(arg_text)]

            # Final filtering: ensure no duplicate completions and max results count are yielded
            seen = set()
            trimmed_matches = []
            for m in matches:
                key = m.text if isinstance(m, Completion) else str(m)
                if key not in seen:
                    seen.add(key)
                    trimmed_matches.append(
                        m if isinstance(m, Completion) else Completion(key, start_position=-len(arg_text)))
                    if len(trimmed_matches) >= self._core_prompt.max_completion_results:
                        break

            for match in trimmed_matches:
                yield match

        except Exception as completer_exception:
            error_message = f"Completer exception {completer_exception}"
            self._core_prompt.command_loop_abort(error_message=error_message)


class CorePrompt(CoreModuleInterface, cmd2.Cmd):
    """
    Interactive CLI shell for AutoForge with shell-like behavior.
    Provides dynamic prompt updates, path-aware tab completion,
    and passthrough execution of unknown commands via the system shell.
    """

    def __init__(self, *args, **kwargs):

        self._prompt_session: Optional[PromptSession] = None
        self._loop_stop_flag = False
        super().__init__(*args, **kwargs)

    def _initialize(self, prompt: Optional[str] = None) -> None:
        """
        Initialize the 'Prompt' class and its underlying cmd2 / prompt toolkit components.
        Args:
            prompt (Optional[str]): Optional custom base prompt string instead of the solution name.
        """

        self._tool_box = ToolBox.get_instance()
        self._variables = CoreVariables.get_instance()
        self._environment: CoreEnvironment = CoreEnvironment.get_instance()
        self._solution: CoreSolution = CoreSolution.get_instance()
        self._prompt_base: Optional[str] = prompt
        self._loader: Optional[CoreLoader] = CoreLoader.get_instance()
        self._history_file_name: Optional[str] = None
        self._aliases_metadata: dict[str, Any] = {}
        self._path_completion_rules_metadata: dict[str, Any] = {}
        self._cli_commands_metadata: dict[str, Any] = {}
        self._executables_metadata: Optional[dict[str, Any]] = {}
        self._package_configuration_data: Optional[dict[str, Any]] = None
        self._max_completion_results = 100
        self._builtin_commands = set(self.get_all_commands())  # Ger cmd2 builtin commands
        self._project_workspace: Optional[str] = self._variables.get('PROJ_WORKSPACE', quiet=True)

        # Retrieve AutoForge package configuration
        self._package_configuration_data = self.auto_forge.get_instance().package_configuration
        if self._package_configuration_data is None:
            raise RuntimeError("package configuration data not available")

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry: Registry = Registry.get_instance()

        # Clear command line buffer
        sys.argv = [sys.argv[0]]
        ansi.allow_ansi = True

        # Get the active loaded solution
        self._loaded_solution_name = self._solution.get_loaded_solution(name_only=True)

        # Build executables dictionary for implementation shell style fast auto completion
        if self._environment.execute_with_spinner(message=f"Initializing {PROJECT_NAME}... ",
                                                  command=self._build_executable_index,
                                                  command_type=ExecutionModeType.PYTHON, new_lines=1) != 0:
            raise RuntimeError("could not finish initializing")

        # Allow to override maximum completion results
        self._max_completion_results = self._package_configuration_data.get('prompt_max_completion_results',
                                                                            self._max_completion_results)

        # Use the project configuration to retrieve a dictionary that maps commands to their completion behavior.
        self._path_completion_rules_metadata = self._package_configuration_data.get('path_completion_rules', )
        if not self._path_completion_rules_metadata:
            self._logger.warning("No path completion rules loaded")

        # Use the primary solution name as the path base text
        self._prompt_base = self._loaded_solution_name if self._prompt_base is None else self._prompt_base

        # Restore keyboard and flush any residual user input
        self._tool_box.set_terminal_input(state=True)

        # Perper history file
        if not self._init_history_file():
            self._logger.warning("No history file loaded")

        # Initialize cmd2 bas class
        cmd2.Cmd.__init__(self, persistent_history_file=self._history_file_name)

        #
        # Post 'cmd2' instantiation setup
        #

        self.default_to_shell = True
        self.last_result = 0

        # Adding dynamic 'build' commands based on the loaded solution tree.
        for proj, cfg, cmd in self._solution.iter_menu_commands_with_context() or []:
            self._add_dynamic_build_command(project=proj, configuration=cfg, description=cmd['description'],
                                            command_name=cmd['name'])

        # Adding dynamically registered CLI commands.
        self._add_dynamic_cli_commands()

        # Adding built-in aliases based on a dictionary from the package configuration file, and then
        # solution proprietary aliases.
        self._add_dynamic_aliases(self._package_configuration_data.get('builtin_aliases'))
        self._add_dynamic_aliases(self._solution.get_arbitrary_item(key="aliases"))

        # Exclude built-in cmd2 commands from help display without disabling their functionality
        if self._package_configuration_data.get('hide_cmd2_native_commands', False):
            for cmd in ['macro', 'edit', 'run_pyscript', 'run_script', 'shortcuts', 'history', 'shell', 'set', 'alias',
                        'quit', 'help']:
                self._remove_command(command_name=cmd)

        # Persist this module instance in the global registry for centralized access
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Export dynamic md based help file
        self._help_md_file = self._export_help_file(exported_file=f"$BUILD_LOGS/help.md")

    def _init_history_file(self) -> bool:
        """
        Initializes the history file path from the package configuration.
        - Expands any environment or user variables.
        - Validates that the file (if present) appears to be a valid compressed JSON archive,
          such as one used by cmd2 for persistent history.
        - Deletes the file if it's invalid.
        - Ensures the parent directory exists.
        Returns:
            bool: True if a history file name is defined (even if not yet created), False otherwise.
        """
        self._history_file_name = self._package_configuration_data.get('prompt_history_file')
        if not self._history_file_name:
            return False

        self._history_file_name = self._variables.expand(self._history_file_name)

        with suppress(Exception):
            if os.path.exists(self._history_file_name):
                if not self._tool_box.is_valid_compressed_json(self._history_file_name):
                    self._logger.warning(f"Invalid history file '{self._history_file_name}' was deleted")
                    with suppress(Exception):
                        os.chmod(self._history_file_name, stat.S_IWRITE)  # Best-effort unlock
                    os.remove(self._history_file_name)
            else:
                # Ensure parent directory exists so cmd2 can write to it later
                os.makedirs(os.path.dirname(self._history_file_name), exist_ok=True)
            self._logger.info(f"Using history file from '{self._history_file_name}'")
            return True
        return False  # Probably suppressed exception

    def _remove_command(self, command_name: str, disable_functionality: bool = False,
                        disable_help: bool = False) -> None:
        """
        Hides and optionally disables a built-in cmd2 command.
        Args:
            command_name (str): The name of the command to hide or disable (e.g., 'quit').
            disable_functionality (bool): If True, replaces the command with a stub that prints an error.
            disable_help (bool): If True, removes help and completion support for the command.
        """
        # Mark the command as hidden for custom help menus
        if not hasattr(self, "hidden_commands"):
            self.hidden_commands = []
        if command_name not in self.hidden_commands:
            self.hidden_commands.append(command_name)

        method_name = f"do_{command_name}"

        if disable_functionality:
            def disabled_command(_self, _):
                """ Override the command with a disabled stub if requested """
                _self.perror(f"The '{command_name}' command is disabled in this shell.")

            setattr(self, method_name, disabled_command)

        # Optionally suppress help and tab completion
        if disable_help:
            setattr(self, f"help_{command_name}", lambda _self: None)
            setattr(self, f"complete_{command_name}", lambda *_: [])

    def _is_command_exist(self, command_or_alias: str) -> bool:
        """
        Check if the given command or alias exists in either alias or CLI command metadata.
        Args:
            command_or_alias (str): The name to check.
        Returns:
            bool: True if the command or alias exists, False otherwise.
        """
        if command_or_alias in self._aliases_metadata:
            return True
        if command_or_alias in self._cli_commands_metadata:
            return True
        return False

    def _set_command_metadata(self, command_name: str, description: Optional[str] = None,
                              command_type: AutoForgCommandType = AutoForgCommandType.UNKNOWN, hidden: bool = False,
                              patch_doc: bool = False, is_alias: bool = False) -> None:
        """
        Sets or updates metadata for any cmd2 command, including dynamic aliases.
        Args:
            command_name (str): Name of the command (e.g., 'hello', 'build').
            description (str, optional): Help text to associate with the command.
            command_type (AutoForgCommandType): Logical category of the command.
            hidden (bool): Whether the command should be hidden from help menus.
            patch_doc (bool): Whether to update the command's __doc__ string.
            is_alias (bool): Whether this is a dynamically created alias.
        """

        # Try to get the bound method from the instance
        method = getattr(self, f"do_{command_name}", None)
        func = None

        # Get the original function (not the bound method) to attach attributes
        if method and hasattr(method, "__func__"):
            func = method.__func__
        elif hasattr(self.__class__, f"do_{command_name}"):
            func = getattr(self.__class__, f"do_{command_name}")

        if func:
            if not hasattr(func, "command_metadata"):
                func.command_metadata = {}

            func.command_metadata.update(
                {"description": description, "command_type": command_type, "hidden": hidden, "is_alias": is_alias, })

            if patch_doc and description:
                with suppress(AttributeError, TypeError):
                    func.__doc__ = description

    def _add_alias_with_description(self, alias_name: str, description: str, target_command: str,
                                    cmd_type: AutoForgCommandType = AutoForgCommandType.UNKNOWN,
                                    hidden: bool = False) -> None:
        """
        Adds a dynamically defined alias command to the cmd2 application with metadata used for help 
        display and categorization. If alias maps directly to a known builtin command (e.g., 'q' -> 'quit'), 
        it will register via the native cmd2 alias system to avoid collisions.
        it will be registered via the native cmd2 alias system to avoid collisions.
        """
        # Extract the root command from the target (first word)
        target_cmd_root = target_command.split()[0] if target_command else ""

        # If this alias maps 1-to-1 to a builtin command, use cmd2's alias system
        if (
                alias_name not in self._builtin_commands and target_command == target_cmd_root and target_cmd_root in self._builtin_commands):
            self.aliases[alias_name] = target_command
            return

        @with_argument_list
        def alias_func(cmd_instance, args):
            """ Otherwise, define a custom dynamic command """
            cmd_instance.onecmd_plus_hooks(f"{target_command} {' '.join(args)}")

        alias_func.__name__ = f"do_{alias_name}"
        alias_func.__doc__ = description
        setattr(self.__class__, alias_func.__name__, alias_func)

        existing = self._aliases_metadata.get(alias_name, {})

        # Set metadata
        self._set_command_metadata(alias_name, description=description, command_type=cmd_type, hidden=hidden,
                                   patch_doc=True, is_alias=True)
        # Hide if specified
        if hidden and alias_name not in self.hidden_commands:
            self.hidden_commands.append(alias_name)

        # Register in the global aliases metadata registry
        self._aliases_metadata[alias_name] = {
            "description": description or existing.get("description", "No help available"), "command_type": cmd_type,
            "target_command": existing.get("target_command"), "hidden": hidden, }

    def _export_help_file(self, exported_file: Optional[str] = None, export_hidden: bool = False) -> Optional[str]:
        """
        Export all available commands into a formatted Markdown file.
        Args:
            exported_file (Optional[str]): Desired path for the output. If None, a unique file will be created under
            the system temp directory, with a name starting with a common prefix.
            export_hidden(bool): Whether to export commands hidden from the cmd2 application.

        Returns:
            Optional[str]: Path to the created file if successful, else None.
        """
        command_width = 14
        description_width = 80
        use_backticks = True

        try:
            if exported_file:
                exported_file = self._variables.expand(exported_file)
                output_path = Path(exported_file)
            else:
                output_path = Path(self._tool_box.get_temp_filename())

            # Ensure the extension is '.md'
            if output_path.suffix.lower() != ".md":
                output_path = output_path.with_suffix(".md")

            commands_by_type = {}

            # Collect alias-based commands
            for cmd, metadata in self._aliases_metadata.items():
                if not export_hidden and cmd in self.hidden_commands:
                    continue
                cmd_type = metadata.get("command_type", AutoForgCommandType.BUILTIN)
                doc = metadata.get("description", "No help available")
                # Minor string touch up
                doc = doc.replace('\t', ' ').strip()
                if not doc.endswith('.'):
                    doc += '.'

                # Append if not exist
                if cmd not in [c for c, _ in commands_by_type.setdefault(cmd_type, [])]:
                    commands_by_type[cmd_type].append((cmd, doc))

            # Collect docstring-based commands
            for cmd in sorted(self.get_all_commands()):
                method = getattr(self, f'do_{cmd}', None)
                if not method:
                    continue

                metadata = getattr(method, "command_metadata", {})
                if metadata.get("hidden", False) and cmd not in self.hidden_commands:
                    self.hidden_commands.append(cmd)

                # Append if not exist
                if not export_hidden and (cmd in self.hidden_commands or cmd in self._aliases_metadata):
                    continue

                cmd_type = metadata.get("command_type", AutoForgCommandType.BUILTIN)
                doc = self._tool_box.flatten_text(method.__doc__, default_text="No help available")
                if cmd not in [c for c, _ in commands_by_type.setdefault(cmd_type, [])]:
                    commands_by_type[cmd_type].append((cmd, doc))

            # Write to Markdown file
            with output_path.open("w", encoding="utf-8") as f:
                f.write("<!-- Auto-generated by AutoForge. Do not edit manually. -->\n")
                f.write("# AutoForge Command Menu\n\n")

                for cmd_type in sorted(commands_by_type.keys(), key=lambda t: t.value):
                    f.write(f"## {cmd_type.name.title()} Commands\n\n")

                    # Header
                    f.write(f"| {'Commands':<{command_width}} | {'Description':<{description_width}} |\n")
                    f.write(f"| {'-' * command_width} | {'-' * description_width} |\n")

                    # Table Rows
                    for cmd, desc in sorted(commands_by_type[cmd_type]):
                        safe_desc = desc.replace("|", "\\|")
                        wrapped_lines = textwrap.wrap(safe_desc, width=description_width - 2, break_long_words=False)

                        cmd_str = f"`{cmd}`" if use_backticks else cmd
                        padded_cmd_str = f"{cmd_str:<{command_width}}"

                        # First line with command
                        first_line_str = f"`{wrapped_lines[0]}`" if use_backticks else wrapped_lines[0]
                        f.write(f"| {padded_cmd_str} | {first_line_str:<{description_width}} |\n")

                        # Remaining wrapped lines without command
                        for line in wrapped_lines[1:]:
                            line_str = f"`{line}`" if use_backticks else line
                            f.write(f"| {'':<{command_width}} | {line_str:<{description_width}} |\n")

                    f.write("\n")

            self._logger.debug(f"Dynamic help file generated in {output_path.name}")
            return str(output_path)

        except Exception as export_error:
            self._logger.error(f"Could not export help help file {export_error}")
            return None

    def _add_dynamic_aliases(self, aliases: Optional[Union[dict, list[dict]]]) -> Optional[int]:
        """
        Registers dynamically defined aliases from a dictionary or list of dictionaries.
        Returns:
            Optional[int]: Number of aliases successfully registered, or None on failure.
        """

        added_aliases_count = 0

        if isinstance(aliases, dict):  # Legacy support
            for alias_name, target_command in aliases.items():
                try:
                    # Filter existing
                    if self._is_command_exist(command_or_alias=alias_name):
                        self._logger.warning(f"Duplicate alias '{alias_name}' already exists.")
                        continue

                    self._add_alias_with_description(alias_name=alias_name, target_command=target_command,
                                                     description="No description provided",
                                                     cmd_type=AutoForgCommandType.ALIASES)
                    added_aliases_count += 1
                except Exception as alias_add_error:
                    self._logger.warning(f"Failed to add legacy alias '{alias_name}': {alias_add_error}")

        elif isinstance(aliases, list):
            for alias in aliases:
                try:
                    alias_name = alias["alias_name"]
                    target_command = alias["target_command"]
                    description = alias.get("description", "No description provided")
                    cmd_type_str = alias.get("command_type", "ALIASES")
                    hidden = alias.get("hidden", False)

                    # Filter existing
                    if self._is_command_exist(command_or_alias=alias_name):
                        self._logger.warning(f"Duplicate alias '{alias_name}' already exists.")
                        continue

                    try:
                        cmd_type = AutoForgCommandType[cmd_type_str.upper()]
                    except KeyError:
                        self._logger.warning(
                            f"Unknown command_type '{cmd_type_str}' for alias '{alias_name}', defaulting to ALIASES")
                        cmd_type = AutoForgCommandType.ALIASES

                    self._add_alias_with_description(alias_name=alias_name, target_command=target_command,
                                                     description=description, cmd_type=cmd_type, hidden=hidden)
                    added_aliases_count += 1
                except Exception as alias_add_error:
                    self._logger.warning(f"Failed to add alias from entry {alias}: {alias_add_error}")

        else:
            self._logger.warning("'aliases' must be a dictionary or list of dictionaries")

        if added_aliases_count == 0:
            self._logger.warning("No aliases registered")

        return added_aliases_count

    def _add_dynamic_cli_commands(self):
        """
        Dynamically adds AutoForge dynamically loaded command to the Prompt.
        Each command is dispatched via the loader's `execute()` method using its registered name.
        Returns:
            int: The number of commands added or exception.
        """
        added_commands: int = 0

        # Get the loaded commands list from registry
        cli_commands_list: list[ModuleInfoType] = self._registry.get_modules_list(
            auto_forge_module_type=AutoForgeModuleType.CLI_COMMAND)

        existing_commands = len(cli_commands_list) if cli_commands_list else 0
        if existing_commands == 0:
            self._logger.warning("No dynamic commands loaded")
            return 0

        for cmd_info in cli_commands_list:
            command_name = cmd_info.name
            command_type = cmd_info.command_type
            description = "Description not provided" if cmd_info.description is None else cmd_info.description
            hidden = cmd_info.hidden

            def make_cmd(name: str, doc: str):
                """ Define the function and attach a docstring BEFORE binding """

                # noinspection PyShadowingNames
                def dynamic_cmd(self, arg):
                    """ Command wrapper """
                    try:
                        if isinstance(arg, Statement):
                            args = arg.args
                        elif isinstance(arg, str):
                            args = arg.strip()
                        else:
                            raise RuntimeError(f"command {name} has an unsupported arguments type")

                        result = self._loader.execute_command(name, args)
                        self.last_result = result if isinstance(result, int) else 0
                    except Exception as cli_execution_error:
                        self.perror(f"{cli_execution_error}")
                        self.last_result = 1

                dynamic_cmd.__doc__ = doc
                return dynamic_cmd

            unbound_func = make_cmd(command_name, description)
            method_name = f"do_{command_name}"
            bound_method = MethodType(unbound_func, self)
            setattr(self, method_name, bound_method)

            self._set_command_metadata(command_name=command_name, description=description, command_type=command_type,
                                       hidden=hidden)
            added_commands += 1
            self._logger.debug(f"Command '{command_name}' was added to the prompt")

            # Register in the global commands metadat registry
            self._cli_commands_metadata[command_name] = {"description": description, "command_type": command_type,
                                                         "target_command": method_name, "hidden": hidden, }

            added_commands += 1

        if added_commands == 0:
            self._logger.warning("No dynamic commands loaded")
        return added_commands

    def _add_dynamic_build_command(self, project: str, configuration: str, command_name: str,
                                   description: Optional[str] = None):
        """
        Registers a user-friendly build command alias.
        Here we create a new cmd2 command alias that triggers a specific build configuration
        for the given solution, project, and configuration name.
        Args:
            project (str): The project name within the solution.
            configuration (str): The specific build configuration.
            command_name (str): The alias command name to be added.
            description (Optional[str]): A description of the command to be shown in help.
                                                 Defaults to a generated description if not provided.
        """
        target_command = f"build {project}.{configuration}"
        if not description:
            description = f"Build {project}/{configuration}"

        self._add_alias_with_description(alias_name=command_name, description=description,
                                         target_command=target_command, cmd_type=AutoForgCommandType.BUILD)

    def _build_executable_index(self) -> None:
        """
        Scan all directories in the search path and populate self.executable_db
        with executable names mapped to their full paths.
        """

        seen_dirs = set()

        # Retrieve search path from package configuration or fall back to $PATH
        search_path = self._package_configuration_data.get('prompt_search_path')
        if not search_path:
            search_path = os.environ.get("PATH", "").split(os.pathsep)

        # Expand each path
        for directory in search_path:
            directory = self._variables.expand(key=directory, quiet=True)
            if not directory or directory in seen_dirs:
                continue
            seen_dirs.add(directory)

            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        # Only add executable files that haven't been added already
                        if (entry.is_file() and os.access(entry.path,
                                                          os.X_OK) and entry.name not in self._executables_metadata):

                            # Optional: filter out Windows executables when on Linux
                            if not entry.name.endswith('.exe') or os.name == 'nt':
                                self._executables_metadata[entry.name] = entry.path
            except OSError:
                # Skip directories that can't be accessed
                continue

    # noinspection SpellCheckingInspection
    def _print_colored_prompt_intro(self, cheerful: bool = True):
        """
        Print the prompt welcome message with optional colorful effects.
        Parameters:
            cheerful (bool): If True, display the solution name with rainbow colors.
        """
        solution_name = self._solution.solution_name.capitalize()
        sys.stdout.write("🛠️  Welcome to the '")

        self._tool_box.print_lolcat(solution_name) if cheerful else sys.stdout.write(solution_name)
        sys.stdout.write("' solution!\n👉 Type \033[1mhelp\033[0m or \033[1m?\033[0m to list available commands.\n\n")
        sys.stdout.flush()

    # noinspection SpellCheckingInspection
    def _get_colored_prompt_toolkit(self, active_name: Optional[str] = None) -> str:
        """
        Return an HTML-formatted prompt string for prompt_toolkit.
        Emulates zsh-style prompt with:
        - virtualenv or project name
        - home-relative or workspace-relative path
        - git branch (if present)
        """
        func = type(self)._get_colored_prompt_toolkit
        if not hasattr(func, "_last_cwd"):
            func._last_cwd = self._tool_box.get_expanded_path("~")

        # Prompt base: active name or fallback
        prompt_base = self._prompt_base
        venv = os.environ.get("VIRTUAL_ENV")
        venv_prompt = f"[{active_name}]" if active_name else (
            f"[{prompt_base}]" if prompt_base else f"[{Path(venv).name}]" if venv else "[?]")

        # Get current working directory
        try:
            cwd = os.getcwd()
            func._last_cwd = cwd
        except FileNotFoundError:
            if os.path.exists(func._last_cwd):
                os.chdir(func._last_cwd)
            else:
                os.chdir(self._tool_box.get_expanded_path("~"))
            cwd = os.getcwd()
            func._last_cwd = cwd

        # Compute display path
        home = self._tool_box.get_expanded_path("~")
        workspace = getattr(self, "_project_workspace", None)

        if workspace and cwd.startswith(workspace):
            rel_path = os.path.relpath(cwd, workspace)
            prefix = "<ansigreen>$</ansigreen>/" if rel_path != "." else "<ansigreen>$</ansigreen>"
            cwd_display = f"{prefix}{rel_path}" if rel_path != "." else prefix
        elif cwd.startswith(home):
            rel_path = os.path.relpath(cwd, home)
            prefix = "<ansimagenta>~</ansimagenta>/" if rel_path != "." else "<ansimagenta>~</ansimagenta>"
            cwd_display = f"{prefix}{rel_path}" if rel_path != "." else prefix
        else:
            cwd_display = cwd

        # Git branch
        git_branch = ""
        with suppress(Exception):
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL,
                                             cwd=cwd, text=True).strip()
            if branch:
                git_branch = f' <ansired>{branch}</ansired>'

        # Determine venv_prompt color: bright red if outside workspace, else bright cyan
        if workspace and not cwd.startswith(workspace):
            venv_color = "ansibrightred"
        else:
            venv_color = "ansibrightcyan"

        # Final prompt ➜
        arrow = "\u279C"
        prompt_text = (f"<{venv_color}>{venv_prompt}</{venv_color}> "
                       f"<ansiblue>{cwd_display}</ansiblue>{git_branch} "
                       f"<ansiyellow>{arrow}</ansiyellow> ")

        return prompt_text

    def _inject_generic_path_complete_hooks(self):
        """
        Automatically injects generic path completer methods for CLI commands that support
        path completion but do not already have a custom `complete_<command>()` method.
        The commands and their completion behavior are defined in `self.command_completion`.
        For each command with `"path_completion": true` and no pre-defined `complete_<cmd>`,
        this function dynamically generates and binds a completer that uses `_CorePathCompleter`.

        This avoids repetitive manual completer definitions for commands like `cd`, `mkdir`, `rm`, etc.
        Expected `self.command_completion` format:
            {
                "cd": {
                    "path_completion": true,
                    "only_dirs": true
                },
                "rm": {
                    "path_completion": true,
                    "only_dirs": false
                }
            }
        """
        for cmd, meta in self._path_completion_rules_metadata.items():
            if not meta.get("path_completion"):
                continue

            completer_name = f"complete_{cmd}"
            if hasattr(self, completer_name):
                continue

            only_dirs = meta.get("only_dirs", False)

            def make_completer(command_name: str, only_directories: bool):
                """
                Factory for creating a `complete_<cmd>()`-style method that delegates
                to a `_CorePathCompleter` instance.
                Args:
                    command_name (str): The command name to match (e.g., 'cd').
                    only_directories (bool): Whether to limit suggestions to directories only.

                Returns:
                    function: A completer method compatible with prompt_toolkit's expectations.
                """

                def completer(_self, _text, line, _begin_idx, end_idx):
                    """ Toolkit completer implement """
                    event = CompleteEvent(completion_requested=True)
                    comp = _CorePathCompleter(core_prompt=self, command_name=command_name,
                                              only_directories=only_directories)
                    return list(comp.get_completions(Document(text=line, cursor_position=end_idx), event))

                return completer

            # Bind the generated completer to `self` under the expected name (e.g., complete_cd)
            setattr(self, completer_name, MethodType(make_completer(cmd, only_dirs), self))

    def gather_path_matches(self, text: str, only_dirs: bool = False) -> list[Completion]:
        """
        Generate Completion objects for filesystem path suggestions.
        - Supports directory-only filtering
        - Uses display and display_meta for UI clarity
        - Prevents repeated completions of the same folder
        """
        raw_text = text.strip().strip('"').strip("'")

        # Not normalizing here; we want raw trailing slashes to preserve user intent
        dirname, partial = os.path.split(raw_text)
        dirname = dirname or "."

        # Normalize separately for safe comparisons
        normalized_input_path = ""
        with suppress(Exception):
            normalized_input_path = os.path.normpath(os.path.expanduser(self._variables.expand(raw_text)))

        try:
            entries = os.listdir(os.path.expanduser(dirname))
        except OSError:
            return []

        completions = []
        for entry in entries:
            full_path = os.path.join(os.path.expanduser(dirname), entry)

            try:
                is_dir = os.path.isdir(full_path)
                is_exec = os.path.isfile(full_path) and os.access(full_path, os.X_OK)
            except FileNotFoundError:
                continue

            if only_dirs and not is_dir:
                continue
            if partial and not entry.startswith(partial):
                continue
            if not partial and not (raw_text.endswith(os.sep) or raw_text == ""):
                continue

            try:
                if os.path.samefile(os.path.normpath(full_path), normalized_input_path):
                    continue
            except FileNotFoundError:
                continue

            insertion = entry + (os.sep if is_dir else "")
            display = insertion
            style = "class:executable" if is_exec else ("class:directory" if is_dir else "class:file")

            completions.append(Completion(text=insertion, start_position=-len(partial), display=display, style=style))

        return sorted(completions, key=lambda c: c.text.lower())

    def complete_build(self, text: str, line: str, begin_idx: int, _end_idx: int) -> list[Completion]:
        """
        Completes the 'build' command in progressive dot-separated segments:
        build <project>.<config>
        The user is expected to type dots manually, not inserted by completions.
        """
        try:
            import shlex
            tokens = shlex.split(line[:begin_idx])
            if not tokens or tokens[0] != "build":
                return []

            completions = []
            dot_parts = text.split(".")

            # Case 1: build + SPACE → suggest projects (no dot inserted)
            if len(tokens) == 1 and not text:
                for proj in self._solution.get_projects_names() or []:
                    completions.append(Completion(proj))

            # Case 2: build sol → match projects
            elif len(dot_parts) == 1:
                partial = dot_parts[0]
                for proj in self._solution.get_projects_names() or []:
                    if proj.startswith(partial):
                        suffix = proj[len(partial):]
                        completions.append(Completion(suffix, start_position=-len(partial)))

            # Case 3: build proj → match projects (no dot in completion)
            elif len(dot_parts) == 2:
                proj, cfg_partial = dot_parts
                for cfg in self._solution.get_configurations_names(project_name=proj) or []:
                    if cfg.startswith(cfg_partial):
                        suffix = cfg[len(cfg_partial):]
                        completions.append(Completion(suffix, start_position=-len(cfg_partial)))

            return completions

        except Exception as completer_error:
            import traceback
            self._logger.debug(f"Auto-completion error in complete_build(): {completer_error}")
            return []

    # noinspection PyMethodMayBeStatic
    def do_version(self, _arg: str):
        """
        Show package version information.
        """
        print(f"\n{PROJECT_NAME} ver. {PROJECT_VERSION}")
        print(f"cmd2: {cmd2.__version__}\n")

    def do_echo(self, arg: str):
        """
        Smart override of shell 'echo':
        - Intercepts `echo $?` to show last_result from cmd2.
        - Defers all other echo invocations to the system shell.
        """
        arg = arg.strip()

        if arg == "$?":
            self.poutput(str(self.last_result))
            self.last_result = 0  # echo succeeded
            return None

        stmt = self.statement_parser.parse(f"echo {arg}")
        return self.default(stmt)

    def do_cd(self, path: str):
        """
        This method mimics the behavior of the shell 'cd' command and changing the current working directory and
        update the prompt accordingly.
        Args:
            path (str): The target directory path, relative or absolute. Shell-like expansions are supported.
        """
        # Expand
        path = self._variables.expand(key=path, quiet=True)
        try:
            if not os.path.exists(path):
                raise RuntimeError(f"no such file or directory: {path}")
            if not os.path.isdir(path):
                raise RuntimeError(f"'{path}' is not a directory")

            os.chdir(path)
            self.last_result = 0  # Set return code explicitly

        except Exception as change_dir_error:
            self.perror(f"cd: {change_dir_error}")
            self.last_result = 1  # Set return code explicitly

    def do_motd(self, _arg: Any) -> None:
        """
        Display the AutoForge greeting file ("motd").
        This is the equivalent of a classic "message of the day" - a friendly introduction
        for developers and CI engineers.
        """
        self._tool_box.show_help_file("motd/motd.md")

    def do_help(self, arg: Any) -> None:
        """
        Show the autogenerated menu markdown through textual when not args provided,
        else, format and display help for the specified command
        Args:
            arg (str): The command to for which we should show help.
        """

        console = Console()
        term_width = self._tool_box.get_terminal_width()

        if not arg:
            # No arguments, try to show the package commands menu using the textual app.
            if self._help_md_file:
                self._tool_box.show_help_file(self._help_md_file)
            return None

        # Normal flow, showing help for a  specific command.
        command_name = str(arg.arg_list[0]) if isinstance(arg, Statement) else str(arg)

        # User typed 'help my_command' --> Show help for that specific command
        # Get the stored help from tye registry
        command_record: Optional[dict[str, Any]] = self._registry.get_module_record_by_name(module_name=command_name)

        # Extract man page help if it's not an internal registered command
        man_description: Optional[str] = None
        if command_record is None:
            man_description = self._tool_box.get_man_description(command_name)

        command_method = getattr(self, f'do_{arg}', None)

        # Try to retrieve help either from the command's docstring or by checking if it has a man page entry.
        if command_record and 'description' in command_record:
            command_method_description = command_record.get('description', None)
        elif command_method and command_method.__doc__:
            command_method_description = self._tool_box.normalize_docstrings(doc=command_method.__doc__,
                                                                             wrap_term_width=term_width - 8)
        elif man_description:
            command_method_description = self._tool_box.normalize_docstrings(doc=man_description,
                                                                             wrap_term_width=term_width - 8)
        else:
            command_method_description = None

        if command_method_description:
            console.print("\n", f"[bold green]{arg}[/bold green]:\n    {command_method_description}",
                          width=term_width), "\n"
        else:
            console.print(f"[bold red]No help available for '{arg}'[/bold red]")

        print()
        return None

    def do_build(self, arg: str):
        """
        Executes a build based on the dot-separated target notation:
            build <solution>.<project>.<configuration>

        This command extracts essential build information by querying the solution structure
        using the user-specified target. Since a builder instance requires both configuration
        and toolchain data, the solution is queried to retrieve the relevant configuration.
        """

        try:
            build_profile = BuildProfileType()
            target = arg.strip()

            if not target or "." not in target:
                self.perror("Expected: <project>.<configuration>")
                return

            parts = target.split(".")
            if len(parts) != 2:
                self.perror("Expected exactly 2 parts: <project>.<configuration>")
                return
            build_profile.solution_name = self._loaded_solution_name
            build_profile.project_name, build_profile.config_name = parts
            build_profile.build_dot_notation = (f"{build_profile.solution_name}."
                                                f"{build_profile.project_name}.{build_profile.config_name}")

            # Fetch build configuration data from the solution
            build_profile.config_data = self._solution.query_configurations(project_name=build_profile.project_name,
                                                                            configuration_name=build_profile.config_name)
            if build_profile.config_data:
                project_data: Optional[dict[str, Any]] = (
                    self._solution.query_projects(project_name=build_profile.project_name))
                if project_data:
                    build_profile.tool_chain_data = project_data.get("tool_chain")
                    build_profile.build_system = (
                        build_profile.tool_chain_data.get("build_system")) if build_profile.tool_chain_data else None

            # The tool china field 'build_system' will be used to pick the registered builder for this specific solution branch.
            if build_profile.build_system:

                self._logger.debug(f"Building {build_profile.build_dot_notation}, using '{build_profile.build_system}'")
                self._loader.execute_build(build_profile=build_profile)
            else:
                self.perror(f"Solution configuration not found for '{build_profile.build_dot_notation};")

        except Exception as build_error:
            self.perror(f"Build Exception: {build_error}")
            self._logger.exception(build_error)

    def default(self, statement: Statement) -> None:
        """
        Fallback handler for unrecognized commands — executes them via the system shell.
        Method is called when a user types a command that is not defined as a `do_*` method.
        Args:
            statement (Any): Either a raw string command or a `cmd2.Statement` object.
        """
        try:
            # Export local variables to an environment mapping
            var_env = self._variables.export(as_env=True)

            results = self._environment.execute_shell_command(command_and_args=statement.command_and_args, env=var_env,
                                                              echo_type=TerminalEchoType.LINE)
            self.last_result = results.return_code

        except KeyboardInterrupt:
            pass

        except subprocess.CalledProcessError as exception:
            self._logger.warning(f"Command '{exception.cmd}' failed with {exception.returncode}")
            self.last_result = exception.returncode
            pass

        except Exception as exception:
            print(f"[{self.who_we_are()}]: {format(exception)}")

    def postloop(self) -> None:
        """
        Called once when exiting the command loop.
        """

        self.poutput("\nClosing session..")
        self._tool_box.set_terminal_title("Terminal")
        super().postloop()  # Always call the parent

        # Remove residual MD help file
        if self._help_md_file and os.path.isfile(self._help_md_file):
            with suppress(Exception):
                os.remove(self._help_md_file)

        formated_delta = self.auto_forge.telemetry.format_timedelta(self.auto_forge.telemetry.get_session_time())
        print(f"Total time: {formated_delta}\n")

    @property
    def path_completion_rules_metadata(self) -> {}:
        """ Get path completion rules metadata """
        return self._path_completion_rules_metadata

    @property
    def executables_metadata(self) -> {}:
        """ Get executables metadata """
        return self._executables_metadata

    @property
    def commands_metadata(self) -> {}:
        """ Get commands metadata """
        return self._cli_commands_metadata

    @property
    def aliases_metadata(self) -> {}:
        """ Get aliases metadata """
        return self._aliases_metadata

    @property
    def max_completion_results(self) -> int:
        """ Get max allowed completion results """
        return self._max_completion_results

    def command_loop_abort(self, error_message: Optional[str] = None) -> None:
        """
        Request immediate termination of the interactive prompt loop.
        This triggers a KeyboardInterrupt in the prompt loop, causing it to exit gracefully.
        Args:
            error_message (Optional[str]): An optional error message to display.
        Note:
            This method only works on POSIX systems (Linux/macOS).
        """
        self._loop_stop_flag = True

        try:
            fd = sys.stdin.fileno()
            fcntl.ioctl(fd, termios.TIOCSTI, b'\x03')  # Simulate Ctrl-C
        except Exception as abort_error:
            self._logger.warning(f"Failed to inject Ctrl-C to abort prompt: {abort_error}")

        if error_message is not None:
            self.perror(error_message)

    def cmdloop(self, intro: Optional[str] = None) -> None:
        """
        Custom command loop using prompt_toolkit for colored prompt, autocompletion,
        and key-triggered path completions (e.g., on '/' and '.'),
        with full cmd2 command history integration.
        """

        if intro:
            self.poutput(intro)
        else:
            self._print_colored_prompt_intro()

        # Initialize key bindings
        kb = KeyBindings()

        @kb.add('/')
        def _(event):
            """
            Handle '/' keypress:
            - Avoid duplicate slashes
            - Always trigger completion even if '/' is already present
            """
            buffer = event.app.current_buffer
            cursor_pos = buffer.cursor_position
            text = buffer.text

            if cursor_pos == 0 or text[cursor_pos - 1] != '/':
                buffer.insert_text('/')
            else:
                # Force a "change" so prompt_toolkit updates completions
                buffer.insert_text(' ')  # insert temp space
                buffer.delete_before_cursor(1)  # immediately remove it

            buffer.start_completion(select_first=True)

        @kb.add('.')
        def _(event):
            """
            Trigger completion after '.' — helpful for build command hierarchy.
            """
            buffer = event.app.current_buffer
            buffer.insert_text('.')
            buffer.start_completion(select_first=True)

        # Define style for different token categories (e.g., executable names, files)
        style = Style.from_dict(
            {'executable': 'fg:green bold', 'builtins': 'fg:blue bold', 'cli_commands': 'fg:magenta italic',
             'aliases': 'fg:purple italic', 'file': 'fg:gray'})

        # Set up the custom completer
        completer = _CoreCompleter(core_prompt=self, logger=self._logger)

        # Create the prompt-toolkit history object
        pt_history = InMemoryHistory()
        for item in self.history:
            raw = item.statement.raw.strip()
            if raw:
                pt_history.append_string(raw)

        #  Automatically injects generic path completer methods for CLI commands
        self._inject_generic_path_complete_hooks()

        # Create the session
        self._prompt_session = PromptSession(completer=completer, history=pt_history, key_bindings=kb, style=style,
                                             complete_while_typing=self._package_configuration_data.get(
                                                 'complete_while_typing', True), auto_suggest=AutoSuggestFromHistory())

        # Start Prompt toolkit custom loop
        while not self._loop_stop_flag:
            try:
                prompt_text = HTML(self._get_colored_prompt_toolkit())
                line = self._prompt_session.prompt(prompt_text)
                stop = self.onecmd_plus_hooks(line)
                if stop:
                    break

            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            except Exception as toolkit_error:
                self.perror(f"Prompt toolkit error: {toolkit_error}")
                break

        self.postloop()
