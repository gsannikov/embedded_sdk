"""
Script:         prompt.py
Author:         AutoForge Team

Description:
    Core module which defines and manages the PromptEngine class, built on the cmd2 interactive
    shell, to provide SDK build system commands.
"""

import os
import readline
import subprocess
import sys
from contextlib import suppress
from types import MethodType
from typing import Optional, Dict

import cmd2
from cmd2 import ansi, CustomCompletionSettings, Statement
from colorama import Fore, Style
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# AutoForge imports
from auto_forge import (CoreModuleInterface, CoreLoader, CoreEnvironment, CoreVariables, CoreSolution,
                        AutoForgeModuleType, ExecutionModeType,
                        Registry, AutoLogger, ToolBox,
                        PROJECT_NAME)

AUTO_FORGE_MODULE_NAME = "Prompt"
AUTO_FORGE_MODULE_DESCRIPTION = "Prompt manager"


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
        self._executable_db: Optional[Dict[str, str]] = None
        self._loaded_commands: int = 0
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

        # Get a lis for the dynamically lodaed AutoForge commands and inject them to cmd2
        self._dynamic_cli_commands_list = (
            self._registry.get_modules_summary_list(auto_forge_module_type=AutoForgeModuleType.CLI_COMMAND))
        if len(self._dynamic_cli_commands_list) > 0:
            self._loaded_commands = self._add_dynamic_cli_commands()
        else:
            self._logger.warning("No dynamic commands loaded")

        # Build executables dictionary for implementation shell style fast auto completion
        if self._environment.execute_with_spinner(message=f"Initializing {PROJECT_NAME}... ",
                                                  command=self._build_executable_index,
                                                  command_type=ExecutionModeType.PYTHON,
                                                  new_lines=1) != 0:
            raise RuntimeError("could not finish initializing")

        # Modify readline behaviour to allow for single TAB when auto completing binary name
        readline.parse_and_bind("set show-all-if-ambiguous on")
        readline.parse_and_bind("TAB: complete")

        # Initialize cmd2 bas class
        cmd2.Cmd.__init__(self)
        self.default_to_shell = True

        # Assign path_complete to the complete_cd and complete_ls methods
        self._register_generic_complete()

        # Create persistent history object
        if history_file is not None:
            history_file = self._variables.expand(text=history_file)
            self._history_file = history_file
            self._load_history()

        # Remove unnecessary built-in commands
        for cmd in ['macro', 'edit', 'run_pyscript']:
            self._remove_command(cmd)

        # Add several basic aliases
        self.set_alias('..', 'cd ..')
        self.set_alias('~', 'cd $HOME')
        self.set_alias('gw', f'cd {self._project_workspace}')
        self.set_alias('ls', 'lsd -g')
        self.set_alias('ll', 'lss -la')
        self.set_alias('l', 'ls')
        self.set_alias('exit', 'quit')
        self.set_alias('x', 'quit')
        self.set_alias('q', 'quit')
        self.set_alias('cln', 'clear')
        self.set_alias('gs', 'git status')
        self.set_alias('ga', 'git add .')
        self.set_alias('gc', 'git commit -m')
        self.set_alias('gp', 'git push')

        self._update_prompt()

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

        for cmd_summary in self._dynamic_cli_commands_list:
            cmd_name = cmd_summary.name
            description = cmd_summary.description

            # Define the function and attach a docstring BEFORE binding
            def make_cmd(name, doc):
                # noinspection PyShadowingNames
                def dynamic_cmd(self, arg):
                    try:
                        if isinstance(arg, Statement):
                            args = arg.args
                        elif isinstance(arg, str):
                            args = arg.strip()
                        else:
                            raise RuntimeError(f"command {cmd_name} has an unsupported argumnets type")

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
        self._executable_db = {}
        seen_dirs = set()

        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory or directory in seen_dirs:
                continue
            seen_dirs.add(directory)

            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if (
                                entry.name not in self._executable_db
                                and entry.is_file()
                                and os.access(entry.path, os.X_OK)
                        ):
                            self._executable_db[entry.name] = entry.path
            except OSError:
                continue  # skip unreadable dirs

    def _update_prompt(self, active_name: Optional[str] = None):
        """
        Dynamically update the cmd2 prompt to mimic a modern Zsh-style shell prompt.
        The prompt includes:
            - The active virtual environment if provided.
            - The current working directory, using `~` when within the home folder.
            - Git branch name (if in a Git repo).
            - A rightward Unicode arrow symbol as a prompt indicator.
        Args:
            active_name (Optional[str]): A board name to display in the prompt. If not provided,
                                        falls back to the VIRTUAL_ENV or '(unknown)'.
        """
        # Virtual environment / board name section
        solution_name = self._solution.get_primary_solution_name()

        venv = os.environ.get("VIRTUAL_ENV")
        venv_prompt = f"[{active_name}]" if active_name else (f"[{solution_name}]" if solution_name else f"[{venv}]")

        # Current working directory, abbreviated with ~ if under home
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        cwd_display = "~" + cwd[len(home):] if cwd.startswith(home) else cwd

        # Git branch name (if in a repo), suppressed to avoid crashes outside Git context
        git_branch = ""
        with suppress(Exception):
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=cwd,
                text=True
            ).strip()
            if branch:
                git_branch = f" {Fore.RED}{branch}{Style.RESET_ALL}"

        # Prompt symbol (unicode arrow ➜)
        arrow = "\u279C"

        # Construct and apply final prompt
        self.prompt = (
            f"{Fore.GREEN}{venv_prompt}{Style.RESET_ALL} "
            f"{Fore.BLUE}{cwd_display}{Style.RESET_ALL}"
            f"{git_branch} {Fore.YELLOW}{arrow}{Style.RESET_ALL} "
        )

    def _print_prompt(self):
        """
        Immediately re-display the current prompt after an interrupt.
        """
        with suppress(Exception):
            self.stdout.write(self.prompt)
            self.stdout.flush()

    def _register_generic_complete(self):
        """
        Dynamically create generic complete for all do_* commands.
        Completes path names by default. Can customize behavior for specific commands.
        """
        special_behavior = {
            "cd": {"only_dirs": True},
        }

        def make_completer(complete_only_dirs: bool):
            # noinspection PyShadowingNames,SpellCheckingInspection
            def completer(self, text, line, begidx, endidx):
                return self._complete_for_do_commands(text, line, begidx, endidx, only_dirs=complete_only_dirs)

            return completer

        for attr_name in dir(self):
            if attr_name.startswith('do_') and callable(getattr(self, attr_name)):
                cmd_name = attr_name[3:]  # Remove 'do_' prefix to get command name
                completer_name = f"complete_{cmd_name}"

                # Skip if already manually defined
                if hasattr(self, completer_name):
                    continue

                # Look for special behavior
                only_dirs = special_behavior.get(cmd_name, {}).get("only_dirs", False)
                completer_func = make_completer(complete_only_dirs=only_dirs)

                # Correctly bind the completer function to self
                setattr(self, completer_name, completer_func.__get__(self))

    # noinspection SpellCheckingInspection
    def _complete_for_do_commands(self, text: str, line: str, _begidx: int, _endidx: int,
                                  only_dirs: bool = False) -> list[str]:
        """
        Generic path completer for do_* commands.
        Args:
            text (str): Partial word being completed.
            line (str): Full input line.
            _begidx (int): Start index of completion text (unused).
            _endidx (int): End index of completion text (unused).
            only_dirs (bool): Whether to complete only directories.
        Returns:
            list[str]: Completion matches.
        """

        matches = []
        state = 0
        buffer_ends_with_space = (len(line) > 0 and line[-1].isspace())

        while True:
            match = self._path_complete(text, state, only_dirs=only_dirs,
                                        complete_entire_directory=buffer_ends_with_space)
            if match is None:
                break
            matches.append(match)
            state += 1

        return matches

    def _path_complete(self, text: str, state: int, only_dirs: bool = False,
                       complete_entire_directory: bool = False) -> Optional[str]:
        """
        Retrieve a single path match based on the current completion state.
        This function is called repeatedly by the completion engine, passing increasing `state` values starting
        from 0, until it returns None to signal no more matches.

        Args:
            text (str): The input text to complete, typically the partially typed path.
            state (int): The match index to return; 0 for the first match, 1 for the second, etc.
            only_dirs (bool, optional): If True, only directory matches are considered.
            complete_entire_directory (bool, optional): If True, completes the full path even if
                only one directory is partially typed.
        Returns:
            Optional[str]: The matching path at the given state, or None if no match exists.
        """
        matches = self._gather_path_matches(text, only_dirs=only_dirs,
                                            complete_entire_directory=complete_entire_directory)
        try:
            return matches[state]
        except IndexError:
            return None

    def _gather_path_matches(self, text: str, only_dirs: bool = False,
                             complete_entire_directory: bool = False) -> list[str]:
        """
        Gather filesystem path matches based on the provided text input.
        Args:
            text (str): The input text representing a partial or full filesystem path.
            only_dirs (bool, optional): If True, only directories are included in the matches.
            complete_entire_directory (bool, optional): If True, allows listing even if the partial text is empty.
        Returns:
            list[str]: A list of matching entries, sorted with directories listed after files.
        """
        text = text.strip()

        if text.endswith(os.sep):
            complete_entire_directory = True

        expanded_text = self._variables.expand(text=text)

        if not expanded_text:
            directory = "."
            partial = ""
        else:
            if os.path.isdir(expanded_text):
                directory = expanded_text
                partial = ""
            else:
                directory, partial = os.path.split(expanded_text)
                if not directory:
                    directory = "."

        try:
            entries = os.listdir(directory)
        except (OSError, FileNotFoundError):
            return []

        # Set up prefix to remove from matches
        prefix = directory
        if prefix and not prefix.endswith(os.sep):
            prefix += os.sep

        matches = []

        for entry in entries:
            full_path = os.path.join(directory, entry)
            is_dir = os.path.isdir(full_path)

            if only_dirs and not is_dir:
                continue

            if partial and not entry.startswith(partial):
                continue
            elif not complete_entire_directory and not partial:
                continue

            suffix = entry[len(partial):] if partial else entry

            # Decide what to display
            if partial:
                display_entry = text + suffix
            else:
                display_entry = suffix

            if is_dir:
                display_entry += os.sep

            matches.append(display_entry)

        matches = sorted(set(matches), key=lambda x: (not x.endswith(os.sep), x.lower()))

        if len(matches) > self._max_completion_results:
            matches = matches[:self._max_completion_results]

        return matches

    def complete(self, text: str, state: int,
                 custom_settings: Optional[CustomCompletionSettings] = None) -> Optional[str]:
        """
        Global tab-completion handler for commands and file paths.
        Handles completion based on the current input context:
        - Suggests executable shell commands.
        - Suggests dynamically loaded CLI commands.
        - Suggests built-in commands (methods starting with do_).
        - Suggests filesystem entries (files/directories) when completing arguments.

        Args:
            text (str): The partial word to complete.
            state (int): The completion attempt index (0 for the first match, 1 for the second, etc.).
            custom_settings (Optional[CustomCompletionSettings]): Optional custom settings for completion behavior.

        Returns:
            Optional[str]: The matching completion string for the given state, or None if no match is available.
        """

        buffer = readline.get_line_buffer()
        buffer_ends_with_space = buffer.endswith(' ')
        tokens = buffer.strip().split()

        if not tokens:
            return super().complete(text, state, custom_settings)

        matches = []

        if len(tokens) == 1 and not buffer_ends_with_space:
            partial = tokens[0]

            # System executables
            matches.extend(cmd for cmd in self._executable_db if cmd.startswith(partial))

            # Dynamically loaded CLI commands
            if self._loaded_commands:
                dynamic_cmds = [cmd.name for cmd in self._dynamic_cli_commands_list]
                matches.extend(cmd for cmd in dynamic_cmds if cmd.startswith(partial))

            # Built-in do_* methods
            builtin_cmds = [name[3:] for name in dir(self) if name.startswith('do_') and callable(getattr(self, name))]
            matches.extend(cmd for cmd in builtin_cmds if cmd.startswith(partial))

            matches = sorted(set(matches))

            if len(matches) > self._max_completion_results:
                matches = matches[:self._max_completion_results]
        else:
            # Completing arguments after the first command
            cmd = tokens[0]
            if cmd in self._executable_db or cmd in [c.name for c in self._dynamic_cli_commands_list]:
                # External binaries or loaded dynamic commands (cat, nano, etc.)
                matches = self._gather_path_matches(text, complete_entire_directory=buffer_ends_with_space)
            elif hasattr(self, f"do_{cmd}"):
                # Built-in command (e.g., do_cd, do_ls)
                completer_func = getattr(self, f"complete_{cmd}", None)
                if completer_func:
                    try:
                        return completer_func(text, buffer, len(buffer) - len(text), len(buffer))[state]
                    except (AttributeError, TypeError, IndexError):
                        return None
                else:
                    # Default fallback for built-in commands
                    matches = self._gather_path_matches(text, complete_entire_directory=buffer_ends_with_space)
            else:
                return super().complete(text, state, custom_settings)

        try:
            return matches[state]
        except IndexError:
            return None

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

            os.chdir(path)
            self._update_prompt()

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
            if arg:
                # User typed 'help my_command' --> Show help for that specific command
                method = getattr(self, f'do_{arg}', None)
                if method and method.__doc__:
                    command_help_title = "[bold cyan]Auto[/bold cyan][bold white]🛠 Forge[/bold white] Command Help"
                    console.print("\n",
                                  Panel(f"[bold green]{arg}[/bold green]: {method.__doc__}",
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

    def default(self, statement: Statement) -> None:
        """
        Fallback handler for unrecognized commands — executes them via the system shell.
        Method is called when a user types a command that is not defined as a `do_*` method.
        Args:
            statement (Any): Either a raw string command or a `cmd2.Statement` object.
        """

        try:
            if statement.command in {"htop", "top", "btop", "vim", "less", "nano", "vi", "clear"}:
                # Full TTY handoff for interactive apps
                full_command = statement.command_and_args
                self._environment.execute_fullscreen_shell_command(command_and_args=full_command)
            else:
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

    def sigint_handler(self, signum, frame):
        """
        Handles SIGINT signals (e.g., Ctrl+C or Ctrl+Break) while idling at the command prompt.
        Behavior:
            - If a KeyboardInterrupt is raised (idle interrupt), it is caught and silently ignored.
            - If any other unexpected exception occurs during interrupt handling, it is re-raised to propagate normally.
        Args:
            signum (int): The signal number received (typically SIGINT).
            frame (FrameType): The current stack frame when the signal was received.
        Raises:
            Exception: Any non-KeyboardInterrupt exceptions encountered during interrupt handling are re-raised.
        """
        try:
            self._raise_keyboard_interrupt()
        except KeyboardInterrupt:
            # Handle clean idle interrupts nicely
            sys.argv = [sys.argv[0]]  # Clean command line buffer
            sys.stderr.write('\n')
            self._print_prompt()
            pass

        # Propagate all others
        except Exception:
            raise

    def postloop(self) -> None:
        """
        Called once when exiting the command loop.
        """

        # Save history when the session ends
        self._save_history()

        self.poutput("\nClosing prompt..\n")
        self._toolbox.set_terminal_title("Terminal")
        super().postloop()  # Always call the parent
