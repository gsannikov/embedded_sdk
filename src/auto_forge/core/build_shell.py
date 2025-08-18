"""
Script:         build_shell.py
Author:         AutoForge Team

Description:
    Core module that defines and manages the BuildShell class, which integrates the 'cmd2'
    interactive shell with prompt_toolkit to provide a rich command-line interface for the
    AutoForge build system.
"""
import contextlib
import fcntl
import fnmatch
import io
import logging
import os
import random
import shlex
import stat
import subprocess
import sys
import termios
import textwrap
from collections.abc import Iterable
from contextlib import suppress, contextmanager, nullcontext
from datetime import datetime
from itertools import islice
from pathlib import Path
from types import MethodType
from typing import Optional, Any, Union, Callable

# Third-party
import cmd2
from cmd2 import Statement, ansi, Settable
from cmd2 import with_argument_list
# Telemetry
from opentelemetry.sdk.metrics import Meter
# Prompt toolkit
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, CompleteEvent, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.styles import Style
# Rich
from rich.console import Console

# AutoForge imports
from auto_forge import (
    AutoForgCommandType, AutoForgeModuleType, AutoForgeWorkModeType, BuildProfileType,
    CommandFailedException, CommandResultType, CoreDynamicLoader, CoreLogger, CoreModuleInterface,
    CorePlatform, CoreRegistry, CoreSolution, CoreSystemInfo, CoreTelemetry, CoreToolBox,
    CoreVariables, ModuleInfoType, PackageGlobals, TelemetryTrackedCounter, TerminalEchoType,
    VariableFieldType,
)

# Basic types
AUTO_FORGE_MODULE_NAME = "Shell"
AUTO_FORGE_MODULE_DESCRIPTION = "Build Shell for AutoForge"


class _CorePathCompleter(Completer):
    """
        A command-specific path completer that delegates to `PathCompleter` when the input
        line begins with the specified command name.
        This is useful for commands like `cd`, `mkdir`, etc., where argument completion
        should offer filesystem paths, optionally limited to directories.

        Attributes:
            _command_name (str): The command this completer is associated with.
            _path_completer (PathCompleter): Internal prompt_toolkit completer for paths.
        """

    def __init__(self, build_shell: "CoreBuildShell", command_name: str, only_directories: bool):
        """
        Initialize the completer for a specific command.
        Args:
            command_name (str): The command to match against the beginning of input lines (e.g., "cd").
            only_directories (bool): If True, only suggest directory names (not files).
        """
        self._build_shell = build_shell
        self._variables = CoreVariables.get_instance()
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

        meta = self._build_shell.path_completion_rules_metadata.get(self._command_name, {})
        raw_arg = arg.strip().strip('"').strip("'")
        raw_arg = self._variables.expand(raw_arg) if raw_arg else raw_arg  # Expand
        arg_to_complete = raw_arg if raw_arg else "."
        matches = self._build_shell.gather_path_matches(
            text=arg_to_complete,
            only_dirs=meta.get("only_dirs", False),
            allowed_names=meta.get("allowed_names"),
            filter_glob=meta.get("filter_glob"),
        )

        for m in islice(matches, self._build_shell.max_completion_results):
            yield m


class _CoreCompleter(Completer):
    """
    A context-aware tab-completer for shell-like behavior using prompt_toolkit.
    This completer supports:
    - Executable name completion (from PATH)
    - Dynamically loaded command completion
    - Built-in commands with `do_<command>()` methods
    - Custom argument-level completion via `complete_<command>()` functions
    - Project-specific smart completion for `build` with dot-notation
    - Progressive token parsing and prompt coloring support

    It distinguishes between:
    1. First word completion (i.e., command): suggests system, dynamic, and built-in commands
    2. Argument-level completion: delegates to registered per-command completer or generic path matching
    """

    def __init__(self, build_shell: "CoreBuildShell", logger: logging.Logger):

        self._build_shell = build_shell
        self._logger = logger
        self._loader = CoreDynamicLoader.get_instance()
        self._tool_box = CoreToolBox.get_instance()

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

        if not self._build_shell.path_completion_rules_metadata:
            return True

        meta = self._build_shell.path_completion_rules_metadata.get(cmd)
        if meta:
            return meta.get("path_completion", False)  # allow even if arg_text is empty

        # Fallback for unlisted known commands
        is_known_command = (cmd in self._build_shell.executables_metadata or cmd in self._build_shell.commands_metadata)
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

        def _trim_long_text(_text: str, _length: int = 60) -> str:
            """
            Trim the input string to a maximum length, appending '...' if truncated.
            Args:
                _text: The string to trim.
                _length: The maximum allowed length of the result, including ellipsis if added.
            Returns:
                A string no longer than _length, with '...' appended if truncation occurred.
            """
            if len(_text) <= _length:
                return _text
            if _length <= 3:
                return '.' * _length  # Not enough room for any text, just dots
            return _text[:_length - 3] + '...'

        try:

            text = document.text_before_cursor
            buffer_ends_with_space = text.endswith(" ")
            tokens = text.strip().split()
            matches = []
            arg_text = ""  # Ensures it's always defined

            # Case 1: Suggest all settable names on `set <space><TAB>`
            if tokens and tokens[0] == "set" and (
                    len(tokens) == 1 or (len(tokens) == 2 and not buffer_ends_with_space)
            ):

                # Get the current partial word being typed
                partial = document.get_word_before_cursor()

                completions = self._build_shell.get_settable_param(include_doc=True)

                for name, (value, doc) in completions.items():
                    if not partial or name.startswith(partial):
                        yield Completion(
                            text=name,
                            start_position=-len(partial),
                            display=name,
                            display_meta=_trim_long_text(doc)
                        )

                return  # early exit

            # Case 2: Completing the first word (a command)
            if not tokens or (len(tokens) == 1 and not buffer_ends_with_space):
                partial = tokens[0] if tokens else ""

                # First-token path completion support
                if partial.startswith(("/", "./", "../")):
                    matches = self._build_shell.gather_path_matches(text=os.path.expanduser(partial))
                    for m in matches:
                        yield m
                    return

                # Registered dynamic commands
                for cmd in self._build_shell.commands_metadata:

                    # Retrieve the command description from the metadata, flatten the text, and trim it to 80 characters.
                    # This ensures it fits nicely in the small completion popup box.
                    cmd_description = self._build_shell.commands_metadata[cmd].get("description",
                                                                                   "Description not provided")
                    doc = _trim_long_text(_text=self._tool_box.flatten_text(cmd_description), _length=80)
                    if cmd.startswith(partial):
                        matches.append(Completion(cmd, start_position=-len(partial),
                                                  display_meta=doc,
                                                  style=self._build_shell.get_safe_style('commands')))

                # Built-in do_* methods
                for name, method in vars(self._build_shell.__class__).items():
                    if name.startswith("do_") and callable(method):
                        cmd_name = name[3:]
                        # Check if its one of the aliases that gets executed through a dynamic do_ implementation.
                        is_known_alias = (cmd_name in self._build_shell.commands_metadata)
                        if cmd_name.startswith(partial):
                            if not is_known_alias:
                                matches.append(
                                    Completion(cmd_name, start_position=-len(partial),
                                               style=self._build_shell.get_safe_style('builtins')))
                            else:
                                matches.append(
                                    Completion(cmd_name, start_position=-len(partial),
                                               style=self._build_shell.get_safe_style('aliases')))

                # System executables (styled if executable)
                sys_bin_style = self._build_shell.get_safe_style('executable')
                matches += [Completion(sys_binary, start_position=-len(partial), style=sys_bin_style) for
                            sys_binary in self._build_shell.executables_metadata if sys_binary.startswith(partial)]

            # Case 3: Completing arguments for a command
            elif len(tokens) >= 1:

                cmd = tokens[0]
                arg_text = text[len(cmd) + 1:] if len(text) > len(cmd) + 1 else ""
                begin_idx = len(text) - len(arg_text)
                end_idx = len(text)

                completer_func = getattr(self._build_shell, f"complete_{cmd}", None)
                # Retrieve optional arguments hints for registered commands
                command_args_list = self._loader.get_command_known_args(name=cmd)

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
                    meta = self._build_shell.path_completion_rules_metadata.get(cmd, {})
                    matches = self._build_shell.gather_path_matches(
                        text=arg_text if arg_text.strip() else ".",
                        only_dirs=meta.get("only_dirs", False),
                        allowed_names=meta.get("allowed_names"),
                        filter_glob=meta.get("filter_glob")
                    )

                elif command_args_list:
                    matches = [Completion(arg, start_position=-len(arg_text)) for arg in command_args_list if
                               arg.startswith(arg_text)]

            # Final filtering: ensure no duplicate completions and max results count are yielded
            seen = {}
            for match in matches:
                if isinstance(match, Completion):
                    key = match.text
                else:
                    key = str(match)
                    match = Completion(key, start_position=-len(arg_text))  # wrap plain string

                # Keep the richer Completion data if possible
                if key not in seen:
                    seen[key] = match
                else:
                    old = seen[key]
                    # Prefer the one with display_meta or more attributes
                    if (not isinstance(old, Completion)) or (
                            not old.display_meta and match.display_meta
                    ):
                        seen[key] = match

                if len(seen) >= self._build_shell.max_completion_results:
                    break

            # Sort: hidden entries (text starts with ".") appear last
            deduplicated = list(seen.values())
            deduplicated.sort(key=lambda comp: comp.text.lstrip().startswith("."))

            # Yield final completions
            for completion in deduplicated:
                yield completion

        except Exception as completer_exception:
            error_message = f"Completer exception {completer_exception}"
            self._build_shell.command_loop_abort(error_message=error_message)


# noinspection DuplicatedCode
class CoreBuildShell(CoreModuleInterface, cmd2.Cmd):
    """
    Interactive shell for with shell-like behavior.
    Provides dynamic prompt updates, path-aware tab completion,
    and pass-through execution of unknown commands via the system shell.
    """

    def __init__(self, *args, **kwargs):
        """
        Extra initialization required for assigning runtime values to attributes declared
        earlier in `__init__()` See 'CoreModuleInterface' usage.
        """
        self._prompt_session: Optional[PromptSession] = None
        self._prompt_styles: Optional[Style] = None
        self._loop_stop_flag: bool = False
        self._productivity_assist: bool = False
        self._keyboard_hook_activated: bool = False

        # Telemetry counters
        self._build_success_counter: Optional[TelemetryTrackedCounter] = None
        self._build_failure_counter: Optional[TelemetryTrackedCounter] = None
        self._productivity_events_count: Optional[TelemetryTrackedCounter] = None

        super().__init__(*args, **kwargs)

    def _initialize(self, prompt: Optional[str] = None) -> None:
        """
        Initialize the 'Prompt' class and its underlying cmd2 / prompt toolkit components.
        Args:
            prompt (Optional[str]): Optional custom base prompt string instead of the solution name.
        """

        # Required Autoforge modules
        self._core_logger = CoreLogger.get_instance()
        self._logger = self._core_logger.get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry: CoreRegistry = CoreRegistry.get_instance()
        self._system_info = CoreSystemInfo.get_instance()
        self._telemetry = CoreTelemetry.get_instance()
        self._tool_box = CoreToolBox.get_instance()
        self._variables = CoreVariables.get_instance()
        self._platform = CorePlatform.get_instance()
        self._solution = CoreSolution.get_instance()
        self._loader = CoreDynamicLoader.get_instance()

        # Dependencies check
        if None in (self._core_logger, self._logger, self._registry, self._system_info, self._telemetry,
                    self._tool_box, self._variables, self._platform, self._solution, self._loader,
                    self.auto_forge.configuration):
            raise RuntimeError("failed to instantiate critical dependencies")

        self._prompt_base: Optional[str] = prompt
        self._history_file_name: Optional[str] = None
        self._path_completion_rules_metadata: dict[str, Any] = {}
        self._commands_metadata: dict[str, Any] = {}
        self._hidden_commands: list[str] = []
        self._executables_metadata: Optional[dict[str, Any]] = {}
        self._max_completion_results = 100
        self._builtin_commands = set(self.get_all_commands())  # Ger cmd2 builtin commands
        self._project_workspace: Optional[str] = self._variables.get('PROJ_WORKSPACE', quiet=True)
        self._work_mode: Optional[AutoForgeWorkModeType] = self.auto_forge.work_mode
        self._configuration: Optional[dict[str, Any]] = self.auto_forge.get_instance().configuration

        # Disable user input until the prompt is active
        self._tool_box.set_terminal_input()

        # Clear command line buffer
        sys.argv = [sys.argv[0]]
        ansi.allow_ansi = True

        # Get the active loaded solution
        self._loaded_solution_name = self._solution.get_loaded_solution(name_only=True)

        # Build executables dictionary for implementation shell style fast auto completion
        self._build_executable_index()

        # Allow to override maximum completion results
        self._max_completion_results = self._configuration.get('prompt_max_completion_results',
                                                               self._max_completion_results)

        # Use the project configuration to retrieve a dictionary that maps commands to their completion behavior.
        self._path_completion_rules_metadata = self._configuration.get('path_completion_rules', )
        if not self._path_completion_rules_metadata:
            self._logger.warning("No path completion rules loaded")

        # Use the primary solution name as the path base text
        self._prompt_base = self._loaded_solution_name if self._prompt_base is None else self._prompt_base

        # Restore keyboard and flush any residual user input
        self._tool_box.set_terminal_input(state=True)

        # Perper history file
        if not self._init_history_file():
            self._logger.warning("No history file loaded")

        # Initialize module specific counters
        self._init_counters()

        # Initialize cmd2 bas class
        cmd2.Cmd.__init__(self, persistent_history_file=self._history_file_name)

        # ----------------------------------------------------------------------
        #
        # Post 'cmd2' instantiation setup
        #
        # ----------------------------------------------------------------------

        # Greetings, earthlings!'
        # Show the solution banner when the solution specified 'banner' in its json.
        if self._work_mode == AutoForgeWorkModeType.INTERACTIVE:
            banner = self._solution.get_arbitrary_item(key="banner")
            banner_text = banner if isinstance(banner, str) and banner else None
            if banner_text is not None:
                self._tool_box.print_banner(text=f"{banner_text.title()}", clear_screen=True,
                                            terminal_title=f"AutoForge: {self._loaded_solution_name}")

        self.default_to_shell = True
        self.last_result = 0

        # Adding dynamic 'build' commands based on the loaded solution tree.
        for proj, cfg, cmd in self._solution.iter_menu_commands_with_context() or []:
            self._add_dynamic_build_command(project=proj, configuration=cfg, description=cmd['description'],
                                            name=cmd['name'])

        # Allow to hide specific commands from the user menu.
        self._hidden_commands = self._configuration.get('hidden_commands', [])

        # Adding dynamically registered commands.
        self._add_dynamic_commands()

        # Add various common settable parameters
        self._add_common_settable_params()

        # Adding built-in aliases based on a dictionary from the package configuration file, and then
        # solution proprietary aliases.
        self._add_dynamic_aliases(self._configuration.get('builtin_aliases'))
        self._add_dynamic_aliases(self._solution.get_arbitrary_item(key="aliases", resolve_external_file=True))

        # Register this module with the package registry
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

        # Export dynamic md based help file
        self._help_md_file = self._export_commands_to_markdown(exported_file=f"$BUILD_LOGS/help.md")

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
        self._history_file_name = self._configuration.get('prompt_history_file')
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
            self._logger.debug(f"Using history file from '{self._history_file_name}'")
            return True
        return False  # Probably suppressed exception

    def _init_counters(self) -> Optional[bool]:
        """
        Initializes module-specific telemetry counters for build success/failure tracking.
        Returns:
            bool, optional: True if counters were initialized successfully, exception otherwise.
        """
        meter: Optional[Meter] = self._telemetry.meter if self._telemetry else None
        if not isinstance(meter, Meter):
            raise RuntimeError("Telemetry meter is not available or improperly initialized")

        def _keyboard_hook(_key):
            """ PyInput listener callable on event """
            self._productivity_events_count.add(1)

        # Get productivity flag from configuration
        self._productivity_assist = bool(self._configuration.get('productivity_assist', False))
        try:
            self._build_success_counter = self._telemetry.create_counter(
                name="build_success_total", unit="1", description="Number of successful builds")

            self._build_failure_counter = self._telemetry.create_counter(
                name="build_failure_total", unit="1", description="Number of failed builds")

            if self._productivity_assist:
                self._logger.info("Productivity assist activated")
                self._productivity_events_count = self._telemetry.create_counter(
                    name="productivity.total_events", unit="1", description="Cumulative number of user keystrokes")

                # Attempt to start the global PyInput listener first.
                # If unavailable or unsupported, we will fall back to prompt_toolkit key bindings,
                # which are limited to this terminal session and won't capture global keyboard activity.
                if self._tool_box.safe_start_keyboard_listener(_keyboard_hook):
                    self._keyboard_hook_activated = True

            return True

        except Exception as telemetry_error:
            raise RuntimeError(f"Failed to initialize module counters: {telemetry_error}")

    def _add_common_settable_params(self):
        """
        Initialize general-purpose settable parameters.

        This method defines default parameters that control core behavior.
        Other commands or modules may extend this list as needed.
        """
        # Add a custom user-defined parameter
        self.add_settable_param(
            name="cheerful_logger",
            default=True,
            doc="Enable ANSI color output when displaying logs"
        )

        # Attempt to update the built-in 'editor' parameter by querying the 'edit' module.
        cmd_record = self._registry.get_module_record_by_name("edit")
        cls_instance = cmd_record.get("class_instance") if isinstance(cmd_record, dict) else None
        if cls_instance is not None:
            editor_path: Optional[Path] = cls_instance.get_editor_path()
            if isinstance(editor_path, Path) and editor_path.is_file():
                # Set the 'editor' parameter in cmd2
                self.set_settable_param("editor", str(editor_path))
                self._logger.debug(f"Editor settable parameter set to: {editor_path}")

    def _get_command_metadata(self, name: str) -> Optional[dict]:
        """
        Retrieves metadata for a given cmd2 command, if available.
        Args:
            name (str): Name of the command (e.g., 'build', 'hello') without the 'do_' prefix.
        Returns:
            Optional[dict]: Dictionary containing metadata (e.g., description, type, etc.), or None if not found.
        """
        method = getattr(self, f"do_{name}", None)

        # Try to access the unbound function object
        func = None
        if method and hasattr(method, "__func__"):
            func = method.__func__
        elif hasattr(self.__class__, f"do_{name}"):
            func = getattr(self.__class__, f"do_{name}")

        if func and hasattr(func, "metadata"):
            return func.metadata

        return None

    def _set_command_metadata(self, name: str, patch_doc: bool = False, **metadata: Any) -> None:
        """
        Sets or updates metadata for any cmd2 command, including dynamic aliases.
        Args:
            name (str): Name of the command (e.g., 'hello', 'build').
            patch_doc (bool): If True and 'description' is present, also updates the function's docstring.
            **metadata: Arbitrary keyword arguments to store in the command's metadata.
                       Common keys include: 'description', 'cmd_type', 'hidden', 'is_alias', etc.
        """
        # Try to get the bound method from the instance
        method = getattr(self, f"do_{name}", None)
        func = getattr(method, "__func__", None) if method and hasattr(method, "__func__") else None
        if not func and hasattr(self.__class__, f"do_{name}"):
            func = getattr(self.__class__, f"do_{name}")

        if func:

            # Create empty metadata property if it does not exist
            if not hasattr(func, "metadata"):
                func.metadata = {}

            func.metadata.update(metadata)
            if patch_doc and 'description' in metadata:
                with suppress(AttributeError, TypeError):
                    func.__doc__ = metadata['description']

    def _add_alias(self,
                   name: str,
                   command: Union[list, str],
                   **metadata: Any) -> None:
        """
        Adds a dynamically defined alias using required positional arguments and flexible keyword metadata.
        Args:
            - name (str)
            - command (str | list)
            - **metadata: Arbitrary keyword arguments to store in the command's metadata.
        All metadata is passed through to _set_command_metadata.
        """

        # Patch metadata with several mandatory fields
        cmd_type = metadata.get('cmd_type', None)
        if cmd_type is None:
            metadata["cmd_type"] = "ALIASES"

        #  metadata["name"] = name
        metadata["command"] = command
        metadata["is_alias"] = True

        handler = self._make_dynamic_alias_handler(name, command)
        setattr(self.__class__, handler.__name__, handler)

        self._set_command_metadata(name=name, patch_doc=True, **metadata)

        if metadata.get("hidden", False) and name not in self._hidden_commands:
            self._hidden_commands.append(name)

        # Store the alias and it's metadat also in a global dictionary
        self._commands_metadata[name] = metadata

    def _make_dynamic_alias_handler(self, name: str, command: Optional[Union[str, list]]) -> Optional[
        Callable[[Any, Any], None]]:
        """
        Implements a dynamic function which will be executed when an alias is invoked by the prompt.
        Args:
            name (str): Name of the alias (e.g., 'q').
            command (Union[str,list]): Target command(s) to execute. If a list is specified, each command will be
                executed in order. Execution stops on the first non-zero result.
        Returns:
            Callable: Function to be attached as do_<name> for cmd2.
        """

        @with_argument_list
        def _alias_dynamic_func(cmd_instance: CoreBuildShell, args: Any):
            """Generic dynamic alias handler"""
            metadata = cmd_instance._get_command_metadata(name)

            if metadata is None:
                cmd_instance.perror(f"Could not retrieve command metadata for alias '{name}'")
                cmd_instance.last_result = 1

            cmd_str = f"{name} {' '.join(args)}"
            cmd_instance.history.append(Statement(cmd_str))

            def _run_commands() -> bool:
                """ Alias execution dynamic handler """
                try:

                    suppress_output = (
                            self.sdk.auto_forge.work_mode == AutoForgeWorkModeType.NON_INTERACTIVE_AUTOMATION)
                    if suppress_output:
                        devnull = io.StringIO()
                        redirect_ctx = contextlib.redirect_stdout(devnull)
                    else:
                        redirect_ctx = contextlib.nullcontext()  # no-op

                    with redirect_ctx:
                        if isinstance(command, str):
                            stop = cmd_instance.onecmd_plus_hooks(f"{command} {' '.join(args)}")
                            if stop:
                                return True
                        elif isinstance(command, list):
                            for cmd in command:
                                full_cmd = f"{cmd} {' '.join(args)}"
                                stop = cmd_instance.onecmd_plus_hooks(full_cmd)
                                if stop:
                                    return True

                                result = getattr(cmd_instance, "last_result", None)
                                if result not in (None, 0):
                                    break
                        else:
                            cmd_instance.perror("Invalid target_command type")
                            cmd_instance.last_result = 1

                    return False

                except Exception as command_execution_error:
                    raise command_execution_error from command_execution_error

            ctx = cmd_instance.restore_cwd() if metadata.get("restore_cwd") else nullcontext()
            with ctx:
                return _run_commands()

        _alias_dynamic_func.__name__ = f"do_{name}"
        _alias_dynamic_func.__doc__ = f"Alias: {name}"
        _alias_dynamic_func._alias_name = name
        return _alias_dynamic_func

    def _make_dynamic_command_handler(self, name: str, doc: str) -> Callable:
        """
        Creates a dynamic command handler bound to the command loader.
        Args:
            name (str): The command's registered name.
            doc (str): The docstring to assign to the command function.
        Returns:
            Callable: A function object (unbound) to be turned into a cmd2 command.
        """

        def _run_command(cmd_instance: CoreBuildShell, arg: Any):
            """Dynamic command dispatcher."""
            try:
                if isinstance(arg, Statement):
                    args = arg.args
                elif isinstance(arg, str):
                    args = arg.strip()
                else:
                    raise RuntimeError(f"command {name} has an unsupported argument type: {type(arg)}")

                result = self._loader.execute_command(name, args)
                if self._work_mode not in (AutoForgeWorkModeType.INTERACTIVE, AutoForgeWorkModeType.MCP_SERVICE):
                    # Retrieve the executed command raw output from the  core loader module
                    # and log it when running in non-interactive / non MCP modes. This ensures that
                    # internal AutoForge commands are also captured in logs during automated runs..
                    command_output = self._loader.get_last_output()
                    if isinstance(command_output, str):
                        self._logger.debug(
                            f"'{name}{f' {args}' if args else ''}' output:\n{command_output}")

                cmd_instance.last_result = result if isinstance(result, int) else 0

            except Exception as command_runtime_error:
                cmd_instance.perror(str(command_runtime_error))
                cmd_instance.last_result = 1

        _run_command.__doc__ = doc
        _run_command._command_name = name
        return _run_command

    def _add_dynamic_aliases(self, aliases: Optional[Union[dict, list[dict]]]) -> Optional[int]:
        """
        Registers dynamically defined aliases from a dictionary or list of dictionaries.
        Returns:
            Optional[int]: Number of aliases successfully registered, or None on failure.
        """
        added_aliases_count: int = 0

        # We allow anonymous list of dictionaries or named list, either way will flatten converted to a proper list.
        aliases: Optional[list] = self._tool_box.extract_bare_list(aliases, "aliases")

        # Aliases must be a list of dictionaries
        if not isinstance(aliases, list):
            self._logger.warning("No aliases registered provided typ is a 'list'")
            return 0

        for alias in aliases:
            try:
                if not isinstance(alias, dict):
                    self._logger.error(f"Invalid alias entry: expected dict, got {type(alias).__name__}")
                    continue

                name = alias.get("name")
                command = alias.get("command")

                # Validate required fields and their types
                if not isinstance(name, str) or not isinstance(command, (str, list)):
                    self._logger.warning(f"Alias entry must have 'name' as str and 'command' as str or list: {alias}")
                    continue

                if name in self._commands_metadata:
                    self._logger.warning(f"Duplicate alias '{name}' already exists.")
                    continue

                # Extract optional metadata
                known_keys = {"name", "command"}
                metadata = {k: v for k, v in alias.items() if k not in known_keys}

                self._add_alias(
                    name=name,
                    command=command,
                    **metadata,
                )
                added_aliases_count += 1

            except Exception as alias_add_error:
                self._logger.warning(f"Failed to add alias '{alias.get('name', '?')}': {alias_add_error}")

        return added_aliases_count

    def _add_dynamic_commands(self) -> int:
        """
        Retrieves all dynamically loaded commands from the registry and registers them with cmd2.
        Returns:
            int: The number of successfully added commands.
        """

        added_commands: int = 0

        # Get the loaded commands list from registry
        commands_list: list[ModuleInfoType] = self._registry.get_modules_list(
            auto_forge_module_type=AutoForgeModuleType.COMMAND)

        existing_commands = len(commands_list) if commands_list else 0
        if existing_commands == 0:
            self._logger.warning("No dynamic commands loaded")
            return 0

        for cmd_info in commands_list:

            name: Optional[str] = cmd_info.name
            cmd_type: str = cmd_info.command_type.name
            doc: str = cmd_info.description or "Description not provided"
            hidden: bool = cmd_info.hidden
            metadata: dict = cmd_info.metadata or {}

            # Make sure we got the essentials
            if not isinstance(name, str):
                logging.warning(f"'name' was not specified for dynamic command, skipping to next command")
                continue

            # Get the dynamic command function
            handler = self._make_dynamic_command_handler(name=name, doc=doc)

            # Bind and attach
            method_name = f"do_{name}"
            bound_method = MethodType(handler, self)
            setattr(self, method_name, bound_method)

            # Register metadata
            metadata = {
                "command": method_name,
                "description": doc,
                "hidden": hidden,
                "cmd_type": cmd_type,
                **metadata
            }

            self._set_command_metadata(name, patch_doc=True, **metadata)
            if hidden and name not in self._hidden_commands:
                self._hidden_commands.append(name)

            self._commands_metadata[name] = metadata
            added_commands += 1

        return added_commands

    def _add_dynamic_build_command(self, project: str, configuration: str, name: str,
                                   description: Optional[str] = None):
        """
        Registers a user-friendly build command alias.
        Here we create a new cmd2 command alias that triggers a specific build configuration
        for the given solution, project, and configuration name.
        Args:
            project (str): The project name within the solution.
            configuration (str): The specific build configuration.
            name (str): The alias command name to be added.
            description (Optional[str]): A description of the command to be shown in help.
                                                 Defaults to a generated description if not provided.
        """
        command = f"build {project}.{configuration}"
        if not description:
            description = f"Build {project}/{configuration}"

        self._add_alias(name=name, command=command,
                        description=description, cmd_type=AutoForgCommandType.BUILD.name)

    def _build_executable_index(self) -> None:
        """
        Scan all directories in the search path and populate self.executable_db
        with executable names mapped to their full paths.
        """

        seen_dirs = set()

        # Retrieve search path from package configuration or fall back to $PATH
        search_path = self._configuration.get('prompt_search_path')
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

                            # Optional: filter out Windows executables when running on Linux
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
        sys.stdout.write("🛠️  Welcome to '")

        self._tool_box.print_lolcat(solution_name) if cheerful else (
            sys.stdout.write(f"\033[1m{solution_name}\033[0m"))
        sys.stdout.write("'\n👉 Type \033[1mhelp\033[0m or \033[1m?\033[0m to list available commands.\n\n")
        sys.stdout.flush()

    def _get_dynamic_goodbye(self) -> str:
        """
        Returns a randomly selected goodbye message from the configuration list.
        Falls back to "Goodbye" if the list is missing, malformed, or a selection fails.
        This method is fail-safe and guaranteed to return a valid string.
        """
        with suppress(Exception):
            terminal_sign_out_message: Optional[list[dict[str, Any]]] = self._configuration.get(
                "terminal_sign_out_message", []
            )
            entry = random.choice(terminal_sign_out_message) if terminal_sign_out_message else None
            if isinstance(entry, dict):
                return entry.get("phrase", "Goodbye")

        return "Goodbye"

    def _get_dynamic_styles(self) -> Style:
        """
        Load various styles definitions from configuration.
        Falls back to default background and empty styles if not defined.
        Returns:
            Style: The style definitions.
        """

        # Attempt configuration first
        json_styles: Optional[dict] = self._configuration.get('dynamic_prompt_styles', None)
        if not isinstance(json_styles, dict):
            self._logger.warning("Dynamic prompt styles ('dynamic_prompt_styles') was not specified in configuration")
            json_styles = {}  # Initialize as empty dictionary if missing from configuration

        bg_color = json_styles.get("background", "ffffff")  # Default to white background
        token_styles: dict = json_styles.get("tokens", {})
        configured_styles: int = 0

        style_dict = {}
        for token_name, style_value in token_styles.items():
            # Add background if not explicitly set in the token style
            if "bg:" not in style_value:
                combined_style = f"bg:#{bg_color} {style_value}"
            else:
                combined_style = style_value
            style_dict[token_name] = combined_style
            configured_styles += 1

        self._logger.debug(f"Total configured styles: {configured_styles}")
        return Style.from_dict(style_dict)

    # noinspection SpellCheckingInspection
    def _get_colored_prompt_toolkit(self, active_name: Optional[str] = None) -> str:
        """
        Return an HTML-formatted prompt string for prompt_toolkit.
        Emulates zsh-style prompt with:
        - Payton virtual environment or project name
        - Home-relative or workspace-relative path
        - Git branch (if present)
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
        Automatically injects generic path completer methods for commands that support
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
                    comp = _CorePathCompleter(build_shell=self, command_name=command_name,
                                              only_directories=only_directories)
                    return list(comp.get_completions(Document(text=line, cursor_position=end_idx), event))

                return completer

            # Bind the generated completer to `self` under the expected name (e.g., complete_cd)
            setattr(self, completer_name, MethodType(make_completer(cmd, only_dirs), self))

    def _export_commands_to_markdown(self, exported_file: Optional[str] = None, export_hidden: bool = False) -> \
            Optional[
                str]:
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
        description_width = 100
        use_backticks = True

        def _refresh_hidden_commands():
            """ Refresh commands hidden from the cmd2 application """
            for _cmd in sorted(self.get_all_commands()):
                _do_method = getattr(self, f'do_{_cmd}', None)
                if not _do_method:
                    continue

                _do_metadata = getattr(_do_method, "metadata", {})
                if _do_metadata.get("hidden", False) and _cmd not in self._hidden_commands:
                    self._hidden_commands.append(_cmd)

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
            _refresh_hidden_commands()

            # Collect commands from global metadata table
            for cmd, metadata in self._commands_metadata.items():
                if not export_hidden and cmd in self._hidden_commands:
                    continue
                cmd_type = AutoForgCommandType.from_str(metadata.get("cmd_type", "BUILTIN")).name
                doc = metadata.get("description", "No help available")

                # Append if not exist
                if cmd not in [c for c, _ in commands_by_type.setdefault(cmd_type, [])]:
                    commands_by_type[cmd_type].append((cmd, doc))

            # Collect all cmd2 builtin commands
            for cmd in sorted(self.get_all_commands()):
                method = getattr(self, f'do_{cmd}', None)
                if not method:
                    continue

                metadata = getattr(method, "metadata", {})

                # Append if not exist
                if not export_hidden and (cmd in self._hidden_commands):
                    continue

                cmd_type = AutoForgCommandType.from_str(metadata.get("cmd_type", "BUILTIN")).name
                doc = self._tool_box.flatten_text(method.__doc__, default_text="No help available")
                if cmd not in [c for c, _ in commands_by_type.setdefault(cmd_type, [])]:
                    commands_by_type[cmd_type].append((cmd, doc))

            # Write to Markdown file
            with output_path.open("w", encoding="utf-8") as f:

                # Create auto-generated + time stamp header
                timestamp = datetime.now().strftime("%b %-d, %H:%M")
                f.write(f"<!-- File was auto-generated by AutoForge on {timestamp}. Do not edit. -->\n")

                f.write(f"# Commands Menu\n\n")

                for cmd_type in sorted(commands_by_type.keys(), key=lambda t: t):
                    f.write(f"## {cmd_type.title()} Commands\n\n")

                    # Header
                    f.write(f"| {'Commands':<{command_width}} | {'Description':<{description_width}} |\n")
                    f.write(f"|{'-' * (command_width + 2)}|{'-' * (description_width + 2)}|\n")

                    # Table Rows
                    for cmd, desc in sorted(commands_by_type[cmd_type]):
                        # Add command description adjusted to the terminal width
                        safe_desc = desc.replace("|", "\\|")
                        wrapped_lines = []
                        for para in safe_desc.splitlines():
                            wrapped_lines.extend(
                                textwrap.wrap(para, width=description_width - 2, break_long_words=False) or [""])

                        cmd_str = f"`{cmd}`" if use_backticks else cmd
                        padded_cmd_str = f"{cmd_str:<{command_width}}"

                        # First line with command
                        first_line_str = wrapped_lines[0]
                        f.write(f"| {padded_cmd_str} | {first_line_str:<{description_width}} |\n")

                        # Remaining wrapped lines without command
                        for line in wrapped_lines[1:]:
                            line_str = line
                            f.write(f"| {'':<{command_width}} | {line_str:<{description_width}} |\n")

                    f.write("\n")

                # Add collected system info
                f.write(f"## System Info\n")
                f.write(f"### Package\n")
                f.write(f"- Version: {self.auto_forge.version}\n")
                f.write(f"- Solution: {self._solution.solution_name}\n")
                f.write(self._system_info.to_markdown(as_table=False, heading_level=3))

            self._logger.debug(f"Dynamic help file generated in '{output_path.name}'")
            return str(output_path)

        except Exception as export_error:
            self._logger.error(f"Could not export help help file {export_error}")
            return None

    @staticmethod
    @contextmanager
    def restore_cwd():
        original = os.getcwd()
        try:
            yield
        finally:
            os.chdir(original)

    def get_safe_style(self, name: str) -> str:
        """
    `   Return 'class:<name>' if it's defined in the current style.
        If not, return an empty string so fallback/default style is used.
        """

        style_key = f"class:{name}"

        # Styles should have been configured by now.
        if not isinstance(self._prompt_styles, Style):
            return ""
        try:
            self._prompt_styles.get_attrs_for_style_str(style_key)
            return style_key
        except KeyError:
            return ""

    def gather_path_matches(self, text: str, only_dirs: bool = False,
                            allowed_names: Optional[list[str]] = None,
                            filter_glob: Optional[Union[str, list]] = None) -> list[Completion]:
        """
        Generate Completion objects for filesystem path suggestions.
        - Supports directory-only filtering
        - Supports name filtering via exact list or wildcard globs
        - Handles ".", "./", and "" gracefully as path roots
        """
        raw_text = text.strip().strip('"').strip("'")

        # Not normalizing here; we want raw trailing slashes to preserve user intent
        dirname, partial = os.path.split(raw_text)
        dirname = dirname or "."

        # Special case: if input is exactly ".", treat it like empty string for matching
        if raw_text in (".", "./"):
            partial = ""

        allowed_names_set = set(allowed_names) if allowed_names else None
        glob_list = [filter_glob] if isinstance(filter_glob, str) else (filter_glob or [])

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
            is_hidden = entry.startswith(".")

            try:
                is_dir = os.path.isdir(full_path)
                is_exec = os.path.isfile(full_path) and os.access(full_path, os.X_OK)
            except FileNotFoundError:
                continue

            if only_dirs and not is_dir:
                continue
            if allowed_names_set and entry not in allowed_names_set:
                continue

            # Note: first check partial
            if partial and not entry.startswith(partial):
                continue

            if glob_list and not any(fnmatch.fnmatch(entry, g) for g in glob_list):
                continue

            if not partial and not (
                    raw_text.endswith(os.sep) or
                    raw_text in (".", "./", "")):
                continue

            with suppress(Exception):
                if os.path.exists(full_path) and os.path.exists(normalized_input_path):
                    if os.path.samefile(os.path.normpath(full_path), normalized_input_path):
                        continue

            insertion = entry + (os.sep if is_dir else "")
            display = insertion
            # Apply style
            if is_hidden:
                style = "class:hidden-file-path"
            elif is_exec:
                style = "class:executable"
            elif is_dir:
                style = "class:directory"
            else:
                style = "class:file"

            completions.append(Completion(text=insertion, start_position=-len(partial), display=display, style=style))

        # When a glob list was specified which result ino matches
        if glob_list and not completions:
            msg = f"No match for {', '.join(glob_list)}"
            return [Completion(
                text=" ",  # Note: dummy to force rendering of special info messages
                display=msg,
                style="class:alert",
                start_position=0
            )]

        return sorted(completions, key=lambda c: c.text.lower())

    def complete_cd(self, _text: str, line: str, _begin_idx: int, end_idx: int) -> list[Completion]:
        """
        Auto-completion handler for the `cd` command.
        1. Environment-like variable completions: If the argument contains a `$` character,
           it will suggest variable names from the internal variable registry (not system env).
           Matching is done based on prefix (e.g., typing `$AF_` will suggest `$AF_BASE`, etc.).
        2. Filesystem path completions (fallback) : If no variable is detected or in addition to variable
           suggestions, standard path completions are appended using `_CorePathCompleter`.
        Args:
            _text (str): The current word being completed (ignored, we use `line` instead).
            line (str): The full command line entered so far.
            _begin_idx (int): The beginning index of the word being completed (unused).
            end_idx (int): The current cursor position in the input line.

        Returns:
            list[Completion]: A list of Completion objects with suggestions for variable
                              and/or filesystem paths.
        """

        completions = []

        # Check if we’re trying to complete a variable (starts with $ or part of it)
        dollar_start = line.rfind('$', 0, end_idx)
        if dollar_start != -1:
            prefix = line[dollar_start + 1:end_idx]
            clue = f"{prefix.upper()}*"
            matching_vars: list[VariableFieldType] = self._variables.get_matching_keys(clue=clue)
            for var in matching_vars:
                completions.append(Completion(
                    text=f"${var.key}",
                    start_position=dollar_start - end_idx,
                    display_meta=var.description or ""  # Show description if available
                ))

            # If user explicitly typed `$`, do not return paths
            if line[dollar_start:end_idx].startswith("$") and prefix:
                return completions

        # Fallback to injected generic path completer
        comp = _CorePathCompleter(build_shell=self, command_name='cd', only_directories=True)
        event = CompleteEvent(completion_requested=True)
        path_completions = comp.get_completions(Document(text=line, cursor_position=end_idx), event)
        completions.extend(path_completions)

        return completions

    def complete_build(self, text: str, line: str, begin_idx: int, _end_idx: int) -> list[Completion]:
        """
        Completes the 'build' command in progressive dot-separated segments:
        build <project>.<config>
        The user is expected to type dots manually, not inserted by completions.
        """

        if self.auto_forge.bare_solution:
            return []  # Build command is disabled in bare solution mode

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

    def add_settable_param(self, name: str, default: Any, doc: str, quiet: bool = True):
        """
        Register a new settable parameter with cmd2.
        This method creates a user-modifiable parameter that is exposed to the
        built-in `set` and `show` commands. The value is stored directly as an
        attribute on `self`, and the Settable is registered using positional
        arguments (required by older cmd2 versions).

        Args:
            name (str): The name of the parameter (must be a valid attribute name).
            default (Any): The default value to assign and expose.
            doc (str): A brief description shown in `show` and `help set`.
            quiet (bool): If True, silently skip if the parameter already exists;
                          if False, raise ValueError on conflict.
        """
        # Check if parameter already exists using unified method
        if name in self.get_settable_param():
            if quiet:
                return
            raise ValueError(f"Settable parameter '{name}' already exists.")

        setattr(self, name, default)

        self.add_settable(Settable(
            name,
            type(default),
            doc,
            self
        ))

    def get_settable_param(
            self,
            name: Optional[str] = None,
            default: Optional[Any] = None,
            include_doc: bool = False) -> Union[dict[str, Any], dict[str, tuple[Any, str]], Any, None]:
        """
        Retrieve the value or definition of a settable parameter.
        Args:
            name (str, optional): The name of the parameter to retrieve.
                                  If None, returns a dict of all settable parameters.
            default (Any, optional): Value to return if the parameter is not found. Ignored when name is None.
            include_doc (bool): If True and name is None, returns (value, doc) tuples instead of just values.
        Returns:
            - Any: Value of the requested parameter, or `default` if not found.
            - dict[str, Any]: All settable parameters and their values if `name is None` and `include_doc=False`.
            - dict[str, tuple[Any, str]]: If `include_doc=True`, returns value + doc string pairs.
            - None: If the parameter is not found and no default is provided.
        """

        def _get_all_settable_params():
            # noinspection SpellCheckingInspection
            if hasattr(self, "settable"):
                return self.settable  # cmd2 >= 2.4
            elif hasattr(self, "_settables"):
                return list(self._settables.values())  # cmd2 <= 2.3.x
            return []

        if name is None:
            settable_params = sorted(_get_all_settable_params(), key=lambda s: s.name.lower())
            if include_doc:
                return {
                    s.name: (getattr(self, s.name, None), s.description)
                    for s in settable_params
                }
            else:
                return {
                    s.name: getattr(self, s.name, None)
                    for s in settable_params
                }

        return getattr(self, name, default)

    def set_settable_param(self, name: str, value: Any):
        """
        Safely assign a value to a previously registered settable parameter.
        Args:
            name (str): The name of the settable parameter to update.
            value (Any): The new value to assign to the parameter.
        """
        all_params = self.get_settable_param()
        if name not in all_params:
            raise KeyError(f"Unknown settable parameter: {name}")
        setattr(self, name, value)

    # noinspection PyMethodMayBeStatic
    def do_version(self, _arg: str):
        """
        Show package version information.
        """
        print(f"\n{PackageGlobals.NAME} ver. {PackageGlobals.VERSION}")
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
        Mimics the behavior of the shell 'cd' command and changing the current working directory and
        update the prompt accordingly.
        Args:
            path (str): The target directory path, relative or absolute. Shell-like expansions are supported.
        """
        if not path:
            return

        # Expand ann normalize
        path = self._variables.expand(key=path, quiet=True)
        path = os.path.normpath(path)

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

    def do_help(self, arg: Any) -> None:
        """
        Show the auto-generated menu markdown through textual when not args provided,
        else, format and display help for the specified command
        Args:
            arg (str): The command to for which we should show help.
        """

        console = Console()
        term_width = self._tool_box.get_terminal_width()

        if not arg:
            # No arguments, try to show the package commands menu using the textual app.
            if self._help_md_file:
                self._tool_box.show_markdown_file(self._help_md_file)
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
            command_method_description = (
                self._tool_box.normalize_docstrings(doc=command_method.__doc__, wrap_term_width=term_width - 8))
        elif man_description:
            command_method_description = (
                self._tool_box.normalize_docstrings(doc=man_description, wrap_term_width=term_width - 8))
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
        Executes a build based on the dot-separated target notation.
        This command extracts essential build information by querying the solution structure
        and execute the build using its specific toolchain handler.
        """

        self.last_result = 1

        if self.auto_forge.bare_solution:
            self.perror("Build is disabled when running bare solution mode")
            return

        try:
            args = shlex.split(arg)
            if not args:
                self.perror("Expected: <project>.<configuration> [--flags]")
                return

            target = args[0]
            extra_args = args[1:]

            if "." not in target:
                self.perror("Expected: <project>.<configuration>")
                return

            parts = target.split(".")
            if len(parts) != 2:
                self.perror("Expected exactly 2 parts: <project>.<configuration>")
                return

            self._tool_box.show_status(message="🔧 Building project...")
            # self._tool_box.print()

            # Construct 'build profile' object
            build_profile = BuildProfileType()
            build_profile.solution_name = self._loaded_solution_name
            build_profile.project_name, build_profile.config_name = parts
            build_profile.build_dot_notation = f"{build_profile.solution_name}.{target}"
            build_profile.extra_args = extra_args  # optionally use this in the builder

            # Fetch build configuration
            build_profile.config_data = self._solution.query_configurations(
                project_name=build_profile.project_name,
                configuration_name=build_profile.config_name
            )

            if build_profile.config_data:
                project_data: Optional[dict[str, Any]] = (
                    self._solution.query_projects(project_name=build_profile.project_name))
                if project_data:
                    build_profile.tool_chain_data = project_data.get("tool_chain")
                    build_profile.build_system = (build_profile.tool_chain_data.get("build_system")
                                                  if build_profile.tool_chain_data else None)

            if build_profile.build_system:
                self._logger.debug(
                    f"Building {build_profile.build_dot_notation}, "
                    f"using '{build_profile.build_system}' "
                    f"with extra args: {extra_args}"
                )

                exit_code = self._loader.execute_build(build_profile=build_profile)
                self.last_result = exit_code
            else:
                self.perror(f"Solution configuration not found for '{build_profile.build_dot_notation}'")
                self.last_result = 1

        except Exception as build_error:
            self.perror(f"Build Exception: {build_error}")
            self.last_result = 1
            self._logger.exception(build_error)
        finally:
            self._tool_box.show_status()
            # Update telemetry build  counters
            if self.last_result:
                self._build_failure_counter.add(1)
            else:
                self._build_success_counter.add(1)
            self._tool_box.print()

    def default(self, statement: Statement) -> Optional[bool]:
        """
        Fallback handler for unrecognized commands — executes them via the system shell.
        Method is called when a user types a command that is not defined as a `do_*` method.
        Args:
            statement (Any): Either a raw string command or a `cmd2.Statement` object.
        """
        try:

            results = self._platform.execute_shell_command(
                command_and_args=statement.command_and_args, echo_type=TerminalEchoType.LINE)

            self.last_result = results.return_code if results else 0
            return None

        # Expected exception when using 'execute_shell_command'
        except CommandFailedException as execution_error:
            results = execution_error.results

            if isinstance(results, CommandResultType):
                self.last_result = results.return_code
                self._logger.warning(
                    f"Command '{results.command}' returned {results.return_code} "
                    f"{results.message if results.message else ''}")
            else:
                self._logger.error(f"caught execution exception with no data")
            return None

        # Break (Ctrl/C) signaled
        except KeyboardInterrupt:
            return None

        # Anything else, unexpected
        except Exception as exception:
            self.last_result = 1
            self._logger.exception(f"Caught unexpected exception: {exception}")
            return None

    def postloop(self) -> None:
        """
        Called once when exiting the command loop.
        """

        self._tool_box.set_terminal_title("Terminal")
        super().postloop()  # Always call the parent

        # Remove residual MD help file
        if self._help_md_file and os.path.isfile(self._help_md_file):
            with suppress(Exception):
                os.remove(self._help_md_file)

        # Print average productivity events for the duration of the session
        total_events: Optional[int] = self._telemetry.get_counter_value(name="productivity.total_events")
        productivity_message: Optional[str] = None
        if isinstance(total_events, int):
            elapsed_seconds = self._telemetry.elapsed_since_start()
            if elapsed_seconds > 0:
                events_per_minute = total_events / (elapsed_seconds / 60)
                productivity_message = self._tool_box.format_productivity(events_per_minute=events_per_minute,
                                                                          total_seconds=elapsed_seconds)

        # Use telemetry to tell how long we've been running
        formatted_work_time = self._tool_box.format_duration(seconds=self._telemetry.elapsed_since_start(),
                                                             add_ms=False)
        # Say goodbye
        print(f"\nTotal session time: {formatted_work_time}" + (
            f"\n{productivity_message}" if productivity_message else ""))
        sys.stdout.write("Goodbye, ")
        self._tool_box.print_lolcat(f"{self._get_dynamic_goodbye()}!\n\n")

    @property
    def path_completion_rules_metadata(self) -> dict[str, Any]:
        """ Get path completion rules metadata """
        return self._path_completion_rules_metadata

    @property
    def executables_metadata(self) -> dict[str, Any]:
        """ Get executables metadata """
        return self._executables_metadata

    @property
    def commands_metadata(self) -> dict[str, Any]:
        """ Get commands metadata """
        return self._commands_metadata

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
        Custom command loop using prompt_toolkit for colored prompt, auto-completion,
        and key-triggered path completions (e.g., on '/' and '.'),
        with full cmd2 command history integration.
        """

        if intro:
            self.poutput(intro)
        else:
            self._print_colored_prompt_intro(cheerful=False)

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
            """ Trigger completion after '.' — helpful for build command hierarchy. """
            buffer = event.app.current_buffer
            buffer.insert_text('.')
            buffer.start_completion(select_first=True)

        @kb.add("<any>", filter=Condition(lambda: self._productivity_assist
                                                  and not self._keyboard_hook_activated))
        def _keystroke_hook(event: KeyPressEvent):
            """ User productivity assist helper """
            self._productivity_events_count.add(1)
            # Push back everything printable or otherwise back to the keyboard buffer
            event.current_buffer.insert_text(event.key_sequence[0].key)

        @kb.add("enter", filter=Condition(lambda: self._productivity_assist
                                                  and not self._keyboard_hook_activated))
        def _on_enter(event: KeyPressEvent):
            """ User productivity assist helper """
            self._productivity_events_count.add(1)
            event.current_buffer.validate_and_handle()

        # Retrieve styles either from pre-defined defaults or from configuration
        self._prompt_styles = self._get_dynamic_styles()

        # Set up the custom completer
        completer = _CoreCompleter(build_shell=self, logger=self._logger)

        # Create the prompt-toolkit history object
        pt_history = InMemoryHistory()
        for item in self.history:
            raw = item.statement.raw.strip()
            if raw:
                pt_history.append_string(raw)

        #  Automatically injects generic path completer methods for dynamic commands
        self._inject_generic_path_complete_hooks()

        # This could speedup completion time
        complete_while_typing = self._configuration.get('complete_while_typing', True)

        # Create the session
        self._prompt_session = PromptSession(completer=completer, history=pt_history, key_bindings=kb,
                                             style=self._prompt_styles,
                                             complete_while_typing=complete_while_typing,
                                             auto_suggest=AutoSuggestFromHistory())

        # Inform telemetry that the module is up & running.
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

        # Start Prompt toolkit custom loop
        while not self._loop_stop_flag:
            try:
                prompt_text = HTML(self._get_colored_prompt_toolkit())
                line = self._prompt_session.prompt(prompt_text)
                stop = self.onecmd_plus_hooks(line)
                if stop:
                    break

            except SystemExit:
                break
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            except Exception as toolkit_error:
                self.perror(f"Prompt toolkit error: {toolkit_error}")
                break

        self.postloop()
