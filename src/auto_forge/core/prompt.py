"""
Script:         prompt.py
Author:         AutoForge Team

Description:
    Core module which defines and manages the PromptEngine class, built on the cmd2 interactive
    shell, to provide SDK build system commands.
"""
import logging
import os
import readline
import subprocess
import sys
from contextlib import suppress
from types import MethodType
from typing import Iterable
from typing import Optional, Any

# Third-party
import cmd2
from cmd2 import Statement, ansi
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# AutoForge imports
from auto_forge import (
    PROJECT_NAME,
    COMMAND_COMPLETION_MAP,
    AutoForgeModuleType,
    AutoLogger,
    CoreEnvironment,
    CoreLoader,
    CoreModuleInterface,
    CoreSolution,
    CoreVariables,
    ExecutionModeType,
    Registry,
    ToolBox,
)

AUTO_FORGE_MODULE_NAME = "Prompt"
AUTO_FORGE_MODULE_DESCRIPTION = "Prompt manager"

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


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
        self.core_prompt = core_prompt
        self._logger = logger

    def _should_fallback_to_path_completion(self, cmd: str, arg_text: str, completer_func: Optional[callable]) -> bool:
        """
        Determines whether path completions should be triggered for a command
        that has no specific completer.

        Args:
            cmd (str): The base command name.
            arg_text (str): The partial argument text (after the command).
            completer_func (Optional[callable]): If a custom completer exists.

        Returns:
            bool: True if fallback to path completion should occur.
        """
        if completer_func:
            return False

        meta = COMMAND_COMPLETION_MAP.get(cmd)
        if meta:
            return meta.get("path_completion", False)  # ← allow even if arg_text is empty

        # Fallback for unlisted known commands
        is_known_command = (
                cmd in self.core_prompt.executable_db
                or cmd in [c.name for c in self.core_prompt.dynamic_cli_commands_list]
        )

        return is_known_command and bool(arg_text.strip())

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        """
        Return completions for the current cursor position and buffer state.

        Supports:
        - Command name completion (built-in, dynamic, executable)
        - Per-command argument completion via `complete_<cmd>()`
        - Fallback to path completion for external commands
        - Prevents fallback on known non-path commands like `alias`, `help`, etc.
        """
        text = document.text_before_cursor
        buffer_ends_with_space = text.endswith(" ")
        tokens = text.strip().split()
        matches = []
        arg_text = ""  # Ensures it's always defined

        # Case 1: Completing the first word (a command)
        if not tokens or (len(tokens) == 1 and not buffer_ends_with_space):
            partial = tokens[0] if tokens else ""

            # System executables
            matches += [
                Completion(cmd, start_position=-len(partial))
                for cmd in self.core_prompt.executable_db
                if cmd.startswith(partial)
            ]

            # Dynamic CLI commands
            if self.core_prompt.loaded_commands:
                for cmd in self.core_prompt.dynamic_cli_commands_list:
                    if cmd.name.startswith(partial):
                        matches.append(Completion(cmd.name, start_position=-len(partial)))

            # Built-in do_* methods
            for name in dir(self.core_prompt):
                if name.startswith("do_") and callable(getattr(self.core_prompt, name)):
                    cmd_name = name[3:]
                    if cmd_name.startswith(partial):
                        matches.append(Completion(cmd_name, start_position=-len(partial)))

        # Case 2: Completing arguments for a command
        elif len(tokens) >= 1:
            cmd = tokens[0]
            arg_text = text[len(cmd) + 1:] if len(text) > len(cmd) + 1 else ""
            begin_idx = len(text) - len(arg_text)
            end_idx = len(text)

            if hasattr(self.core_prompt, f"do_{cmd}"):
                completer_func = getattr(self.core_prompt, f"complete_{cmd}", None)

                # Special case: track build components for coloring prompt
                if cmd == "build":
                    parts = arg_text.split(".")
                    self.core_prompt._parsed_build = {
                        "solution": parts[0] if len(parts) > 0 else None,
                        "project": parts[1] if len(parts) > 1 else None,
                        "config": parts[2] if len(parts) > 2 else None
                    }

                if completer_func:
                    try:
                        results = completer_func(arg_text, text, begin_idx, end_idx)
                        if results:
                            if isinstance(results[0], str):
                                matches = [Completion(r, start_position=-len(arg_text)) for r in results]
                            else:
                                matches = results
                    except Exception as e:
                        self._logger.debug(f"Completer error for '{cmd}': {e}")
                elif self._should_fallback_to_path_completion(cmd=cmd, arg_text=arg_text,
                                                              completer_func=completer_func):
                    meta = COMMAND_COMPLETION_MAP.get(cmd, {})
                    matches = self.core_prompt.gather_path_matches(
                        text=arg_text,
                        only_dirs=meta.get("only_dirs", False)
                    )

            elif self._should_fallback_to_path_completion(cmd=cmd, arg_text=arg_text, completer_func=None):
                meta = COMMAND_COMPLETION_MAP.get(cmd, {})
                matches = self.core_prompt.gather_path_matches(
                    text=arg_text,
                    only_dirs=meta.get("only_dirs", False)
                )

        # Final filtering: ensure no duplicate completions are yielded
        seen = set()
        for m in matches:
            key = m.text if isinstance(m, Completion) else str(m)
            if key not in seen:
                seen.add(key)
                yield m if isinstance(m, Completion) else Completion(key, start_position=-len(arg_text))


class CorePrompt(CoreModuleInterface, cmd2.Cmd):
    """
    Interactive CLI shell for AutoForge with shell-like behavior.
    Provides dynamic prompt updates, path-aware tab completion,
    and passthrough execution of unknown commands via the system shell.
    """

    def _initialize(self, prompt: Optional[str] = None,
                    max_completion_results: Optional[int] = 100,
                    history_file: Optional[str] = None) -> None:
        """
        Initialize the 'Prompt' class and its underlying cmd2 components.
        Args:
            prompt (Optional[str]): Optional custom base prompt string.
                If not specified, the lowercase project name ('autoforge') will be used
                as the base prefix for the dynamic prompt.
            max_completion_results (Optional[int]): Maximum number of completion results.
            history_file (Optional[str]): Optional file to store the prompt history and make it persistent
        """

        self._toolbox = ToolBox.get_instance()
        self._variables = CoreVariables.get_instance()
        self._environment: CoreEnvironment = CoreEnvironment.get_instance()
        self._solution: CoreSolution = CoreSolution.get_instance()
        self._prompt_base: Optional[str] = None
        self._prompt_base = prompt if prompt else PROJECT_NAME.lower()
        self._loader: Optional[CoreLoader] = CoreLoader.get_instance()
        self.loaded_commands: int = 0
        self._history_file: Optional[str] = None
        self._max_completion_results = max_completion_results
        self._last_execution_return_code: Optional[int] = 0
        self._term_width = self._toolbox.get_terminal_width(default_width=100)
        self._project_workspace: Optional[str] = self._variables.get('PROJ_WORKSPACE', quiet=True)

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._registry: Registry = Registry.get_instance()

        # Clear command line buffer
        sys.argv = [sys.argv[0]]
        ansi.allow_ansi = True

        # Get a lis for the dynamically loaded AutoForge commands and inject them to cmd2
        self.dynamic_cli_commands_list = (
            self._registry.get_modules_summary_list(auto_forge_module_type=AutoForgeModuleType.CLI_COMMAND))
        if len(self.dynamic_cli_commands_list) > 0:
            self.loaded_commands = self._add_dynamic_cli_commands()
        else:
            self._logger.warning("No dynamic commands loaded")

        # Build executables dictionary for implementation shell style fast auto completion
        self.executable_db: Optional[dict[str, str]] = None
        if self._environment.execute_with_spinner(message=f"Initializing {PROJECT_NAME}... ",
                                                  command=self._build_executable_index,
                                                  command_type=ExecutionModeType.PYTHON,
                                                  new_lines=1) != 0:
            raise RuntimeError("could not finish initializing")

        # Initialize cmd2 bas class
        cmd2.Cmd.__init__(self)
        self.default_to_shell = True

        # Create persistent history object
        if history_file is not None:
            history_file = self._variables.expand(text=history_file)
            self._history_file = history_file
            self._load_history()

        # Remove unnecessary built-in commands
        for cmd in ['macro', 'edit', 'run_pyscript']:
            self._remove_command(cmd)

        # Dynamically add aliases based on a user defined dictionary in the solution file
        aliases = self._solution.get_arbitrary_item(key='aliases')
        if isinstance(aliases, dict):
            for alias_name, alias_definition in aliases.items():
                self.set_alias(alias_name, alias_definition)
        else:
            self._logger.warning("'aliases' ware not dynamically loaded from the solution")

        # Persist this module instance in the global registry for centralized access
        self._registry.register_module(name=AUTO_FORGE_MODULE_NAME,
                                       description=AUTO_FORGE_MODULE_DESCRIPTION,
                                       auto_forge_module_type=AutoForgeModuleType.CORE)

    def _remove_command(self, command_name: str):
        """
        Hide and disable a built-in cmd2 command by overriding its method.
        """
        # Hide from help
        if command_name not in self.hidden_commands:
            self.hidden_commands.append(command_name)

        # Step 2: Disable functionality
        def disabled_command(_self, _):
            _self.perror(f"The '{command_name}' command is disabled in this shell.")

        setattr(self, f'do_{command_name}', disabled_command)

        # Optionally disable help and completer too
        setattr(self, f'help_{command_name}', lambda _self: None)
        setattr(self, f'complete_{command_name}', lambda *_: [])

    def _add_dynamic_cli_commands(self) -> int:

        """
        Dynamically adds AutoForge dynamically loaded command to the Prompt.
        Each command is dispatched via the loader's `execute()` method using its registered name.
        Returns:
            int: The number of commands added.
        """
        added_commands: int = 0

        for cmd_summary in self.dynamic_cli_commands_list:
            cmd_name = cmd_summary.name
            description = cmd_summary.description

            # Define the function and attach a docstring BEFORE binding
            def make_cmd(name=cmd_name, doc=description):
                # noinspection PyShadowingNames
                def dynamic_cmd(self, arg):
                    try:
                        if isinstance(arg, Statement):
                            args = arg.args
                        elif isinstance(arg, str):
                            args = arg.strip()
                        else:
                            raise RuntimeError(f"command {name} has an unsupported arguments type")

                        result = self._loader.execute(name, args)
                        self._last_execution_return_code = result
                        return 0
                    except Exception as e:
                        self.perror(f"{e}")
                        return 0

                dynamic_cmd.__doc__ = doc
                return dynamic_cmd

            unbound_func = make_cmd(cmd_name, description)
            method_name = f"do_{cmd_name}"
            bound_method = MethodType(unbound_func, self)
            setattr(self, method_name, bound_method)
            self._logger.debug(f"Command '{cmd_name}' was added to the prompt")
            added_commands = added_commands + 1

        return added_commands

    def _load_history(self):
        """Load history from file if available."""
        if self._history_file is not None and os.path.exists(self._history_file):
            try:
                readline.read_history_file(self._history_file)
                self._logger.debug(f"History loaded from {self._history_file}")
            except Exception as exception:
                self._logger.warning(f"Failed to load history: {exception}")

    def _save_history(self):
        """Save history to file if specified"""
        if self._history_file is not None:
            try:
                readline.write_history_file(self._history_file)
                self._logger.debug(f"History saved to {self._history_file}")
            except Exception as exception:
                self._logger.warning(f"Failed to save history: {exception}")

    def _build_executable_index(self) -> None:
        """
        Scan all PATH directories and populate self._executable_db
        with executable names mapped to their full paths.
        """
        self.executable_db = {}
        seen_dirs = set()

        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory or directory in seen_dirs:
                continue
            seen_dirs.add(directory)

            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if (
                                entry.name not in self.executable_db
                                and entry.is_file()
                                and os.access(entry.path, os.X_OK)
                        ):
                            self.executable_db[entry.name] = entry.path
            except OSError:
                continue  # skip unreadable dirs

    # noinspection SpellCheckingInspection
    def _get_colored_prompt_toolkit(self, active_name: Optional[str] = None) -> str:
        """
        Return an HTML-formatted prompt string for prompt_toolkit.
        """
        # Virtual environment / board name section
        solution_name = self._solution.get_solutions_list(primary=True)
        venv = os.environ.get("VIRTUAL_ENV")
        venv_prompt = f"[{active_name}]" if active_name else (f"[{solution_name}]" if solution_name else f"[{venv}]")

        # Current working directory
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        cwd_display = "~" + cwd[len(home):] if cwd.startswith(home) else cwd

        # Git branch name (optional)
        git_branch = ""
        with suppress(Exception):
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=cwd,
                text=True
            ).strip()
            if branch:
                git_branch = f' <ansired>{branch}</ansired>'

        # Prompt symbol (➜)
        arrow = "\u279C"

        # Final HTML-formatted string
        return (
            f"<ansigreen>{venv_prompt}</ansigreen> "
            f"<ansiblue>{cwd_display}</ansiblue>"
            f"{git_branch} <ansiyellow>{arrow}</ansiyellow> "
        )

    def _register_generic_complete(self):
        """
        Registers default path completer for all do_* commands that don’t have a custom completer.
        Used by CoreCompleter in prompt_toolkit.
        """
        special_behavior = {
            "cd": {"only_dirs": True},
        }

        def make_completer(command_name: str, complete_only_dirs: bool):
            # noinspection PyShadowingNames
            def completer(self, text, _line, _being_idx, _end_idx):
                return self._complete_path_with_completions(
                    text=text,
                    only_dirs=complete_only_dirs
                )

            completer.__name__ = f"complete_{command_name}"
            return completer

        for attr in dir(self):
            if not attr.startswith("do_"):
                continue

            cmd_name = attr[3:]
            completer_attr = f"complete_{cmd_name}"

            # Do not override manually defined completer
            if hasattr(self, completer_attr):
                continue

            only_dirs = special_behavior.get(cmd_name, {}).get("only_dirs", False)
            bound_completer = MethodType(make_completer(cmd_name, only_dirs), self)
            setattr(self, completer_attr, bound_completer)

    def _complete_path_with_completions(self, text: str, only_dirs: bool = False) -> list[Completion]:
        """
        Return prompt_toolkit Completion objects for filesystem path completions.
        Supports optional filtering for directories only.
        """
        return self.gather_path_matches(
            text=text,
            only_dirs=only_dirs
        )

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
            is_dir = os.path.isdir(full_path)

            if only_dirs and not is_dir:
                continue
            if partial and not entry.startswith(partial):
                continue
            if not partial and not (raw_text.endswith(os.sep) or raw_text == ""):
                continue

            # Avoid suggesting the same directory again
            with suppress(Exception):
                if os.path.samefile(os.path.normpath(full_path), normalized_input_path):
                    continue

            insertion = entry + (os.sep if is_dir else "")
            display = insertion

            completions.append(
                Completion(
                    text=insertion,
                    start_position=-len(partial),
                    display=display
                )
            )

        return sorted(completions, key=lambda c: c.text.lower())

    def complete_cd(self, text: str, _line: str, _begin_idx: int, _end_idx: int) -> list[Completion]:
        """
        Tab-completion for the `cd` command. Suggests directories only.
        """
        return self.gather_path_matches(
            text=text,
            only_dirs=True
        )

    # noinspection SpellCheckingInspection
    def complete_build(self, text: str, line: str, begin_idx: int, _endidx: int) -> list[Completion]:
        """
        Completes the 'build' command in progressive dot-separated segments:
        build <solution>.<project>.<config>
        The user is expected to type dots manually, not inserted by completions.
        """
        try:
            import shlex
            tokens = shlex.split(line[:begin_idx])
            if not tokens or tokens[0] != "build":
                return []

            completions = []
            dot_parts = text.split(".")

            # Case 1: build + SPACE → suggest solutions (no dot inserted)
            if len(tokens) == 1 and not text:
                for sol in self._solution.get_solutions_list() or []:
                    completions.append(Completion(sol, start_position=0))

            # Case 2: build sol → match solutions
            elif len(dot_parts) == 1:
                partial = dot_parts[0]
                for sol in self._solution.get_solutions_list() or []:
                    if sol.startswith(partial):
                        suffix = sol[len(partial):]
                        completions.append(Completion(suffix, start_position=-len(partial)))

            # Case 3: build sol.proj → match projects (no dot in completion)
            elif len(dot_parts) == 2:
                sol, proj_partial = dot_parts
                for proj in self._solution.get_projects_list(sol) or []:
                    if proj.startswith(proj_partial):
                        suffix = proj[len(proj_partial):]
                        completions.append(Completion(suffix, start_position=-len(proj_partial)))

            # Case 4: build sol.proj.cfg → match configurations (final)
            elif len(dot_parts) == 3:
                sol, proj, cfg_partial = dot_parts
                for cfg in self._solution.get_configurations_list(sol, proj) or []:
                    if cfg.startswith(cfg_partial):
                        suffix = cfg[len(cfg_partial):]
                        completions.append(Completion(suffix, start_position=-len(cfg_partial)))

            return completions

        except Exception as e:
            import traceback
            self._logger.debug(f"Auto-completion error in complete_build(): {e}")
            self._logger.debug(traceback.format_exc())
            return []

    def set_alias(self, alias_name: str, alias_definition: Optional[str]) -> None:
        """
        Add or delete an alias.
        Args:
            alias_name (str): The alias name.
            alias_definition (Optional[str]): The alias definition; if None, the alias will be deleted.
        """
        if alias_definition is None:
            self.aliases.pop(alias_name, None)  # delete safely if exists
        else:
            self.aliases[alias_name] = alias_definition

    def do_cd(self, path: str):
        """
        This method mimics the behavior of the shell 'cd' command and changing the current working directory and
        update the prompt accordingly.
        Args:
            path (str): The target directory path, relative or absolute. Shell-like expansions are supported.
        """
        # Expand
        path = self._variables.expand(text=path)
        try:
            if not os.path.exists(path):
                raise RuntimeError(f"no such file or directory: {path}")

            if not os.path.isdir(path):
                raise RuntimeError(f"'{path}' is not a directory")

            os.chdir(path)

        except Exception as change_path_errors:
            print(f"cd: {change_path_errors}")

    def do_lss(self, args):
        """
        Display a directory listing using the system '/bin/ls' command.
        Args:
            args (str): Arguments passed directly to the 'ls' command (e.g., '-la', '--color=auto').
        Returns:
            str: The output of the 'ls' command as a string.
        """
        try:
            self._environment.execute_shell_command(
                command_and_args=f"ls {args} --color=auto -F",
                shell=True,
                terminal_echo=True,
                expand_command=True,
                check=False,
                use_pty=True
            )
        except Exception as exception:
            self.perror(f"ls: {exception}")

    def do_help(self, arg) -> None:
        """
        Displays a custom, panel-based CLI help screen for AutoForge commands.

        If an argument is provided, defers to the default cmd2 help behavior.
        Otherwise, builds a stylized command list using rich tables and panels,
        including truncated and flattened descriptions.
        """

        console = Console()

        if arg:
            # User typed 'help my_command' --> Show help for that specific command

            # Get the stored help from tye registry
            command_record: Optional[dict[str, Any]] = self._registry.get_module_record_by_name(
                module_name=arg,
                case_insensitive=False
            )

            method = getattr(self, f'do_{arg}', None)
            method_description = (command_record.get(
                'description') if command_record and 'description' in command_record else None
                                  ) or method.__doc__ or "Description not provided."

            if method and method.__doc__:
                command_help_title = "[bold cyan]Auto[/bold cyan][bold white]🛠 Forge[/bold white] Command Help"
                console.print("\n",
                              Panel(f"[bold green]{arg}[/bold green]:\n    {method_description}",
                                    border_style="cyan",
                                    title=command_help_title,
                                    padding=(1, 1), width=self._term_width),
                              "\n")  # Force panel to fit terminal width
            else:
                console.print(f"[bold red]No help available for '{arg}'.[/bold red]")
            return None

        # Reserve some space for panel borders/margins
        max_desc_width = self._term_width - 25  # Approximated value for command column and panel padding

        # Build the commands table
        table = Table(box=box.ROUNDED, highlight=True, expand=True)
        table.add_column("Command", style="bold green", no_wrap=True)
        table.add_column("Description", style="dim", overflow="fold", no_wrap=False)

        commands = sorted(self.get_all_commands())
        for cmd in commands:
            if cmd in self.hidden_commands:
                continue

            method = getattr(self, f'do_{cmd}', None)
            doc = self._toolbox.flatten_text(method.__doc__, default_text="No help available")

            # Truncate description if necessary
            if len(doc) > max_desc_width:
                if max_desc_width > 3:
                    doc = doc[:max_desc_width - 3] + "..."
                else:
                    doc = doc[:max_desc_width]

            table.add_row(cmd, doc)

        # Wrap the table in a Panel
        master_help_title = "[bold cyan]Auto[/bold cyan][bold white]🛠 Forge[/bold white] Available Commands"
        panel = Panel(
            table,
            title=master_help_title,
            border_style="cyan",
            padding=(1, 1), width=self._term_width  # Force panel to fit terminal width
        )

        console.print("\n", panel, "\n")
        return None

    def do_build(self, arg: str):
        """
        Build a specific target via dot-notation:
            build <solution>.<project>.<configuration>
        """
        target = arg.strip()

        if not target or "." not in target:
            self.perror("Expected: <solution>.<project>.<configuration>")
            return

        parts = target.split(".")
        if len(parts) != 3:
            self.perror("Expected exactly 3 parts: <solution>.<project>.<configuration>")
            return

        solution, project, config = parts

        config_data = self._solution.query_configurations(
            solution_name=solution,
            project_name=project,
            configuration_name=config
        )

        if config_data:
            print(f"Building {solution}.{project}.{config}...")
        else:
            self.perror(f"Configuration not found for {solution}.{project}.{config}")

    def default(self, statement: Statement) -> None:
        """
        Fallback handler for unrecognized commands — executes them via the system shell.
        Method is called when a user types a command that is not defined as a `do_*` method.
        Args:
            statement (Any): Either a raw string command or a `cmd2.Statement` object.
        """
        try:
            self._environment.execute_shell_command(
                command_and_args=statement.command_and_args,
                terminal_echo=True,
                expand_command=True)
        except KeyboardInterrupt:
            pass

        except subprocess.CalledProcessError as exception:
            self._logger.warning(f"Command '{exception.cmd}' failed with {exception.returncode}")
            pass

        except Exception as exception:
            print(f"[{self.who_we_are()}]: {format(exception)}")

    def postloop(self) -> None:
        """
        Called once when exiting the command loop.
        """

        # Save history when the session ends
        self._save_history()

        self.poutput("\nClosing prompt..\n")
        self._toolbox.set_terminal_title("Terminal")
        super().postloop()  # Always call the parent

    def cmdloop(self, intro: Optional[str] = None) -> None:
        """
        Custom command loop using prompt_toolkit for colored prompt, autocompletion,
        and key-triggered path completions (e.g., on '/' and '.').
        """
        if intro:
            self.poutput(intro)

        # Create key bindings
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
                # 🩹 Force a "change" so prompt_toolkit updates completions
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

        # Create session with completer and key bindings
        completer = _CoreCompleter(self, logger=self._logger)
        history_path = self._history_file or os.path.expanduser(self._history_file)
        session = PromptSession(
            completer=completer,
            history=FileHistory(history_path),
            key_bindings=kb
        )

        while True:
            try:
                prompt_text = HTML(self._get_colored_prompt_toolkit())
                line = session.prompt(prompt_text)

                stop = self.onecmd_plus_hooks(line)
                if stop:
                    break

            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            except Exception as toolkit_error:
                self.perror(f"Prompt toolkit error {toolkit_error}")

        self.postloop()
