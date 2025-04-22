"""
Script:         prompt.py
Author:         AutoForge Team

Description:
    Core module which defines and manages the PromptEngine class, built on the cmd2 interactive
    shell, to provide SDK build system commands.
"""

import os
import readline
import shlex
import subprocess
import sys
from contextlib import suppress
from types import MethodType
from typing import Any, Optional, Dict

import cmd2
from cmd2 import ansi, CustomCompletionSettings
from colorama import Fore, Style
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# AutoForge imports
from auto_forge import (PROJECT_NAME, Environment, ExecutionMode, CommandsLoader, AutoLogger, ToolBox)

AUTO_FORGE_MODULE_NAME = "Prompt"
AUTO_FORGE_MODULE_DESCRIPTION = "SDK Prompt Manager"


class Prompt(cmd2.Cmd):
    """
    Interactive CLI shell for AutoForge with shell-like behavior.

    Provides dynamic prompt updates, path-aware tab completion,
    and passthrough execution of unknown commands via the system shell.

    Args:
        parent (Any): Our parent AutoForge class instance.e.
        prompt (Optional[str]): Optional custom base prompt string.
            If not specified, the lowercase project name ('autoforge') will be used
            as the base prefix for the dynamic prompt.
    """
    _instance: "Prompt" = None
    _is_initialized: bool = False

    def __new__(cls, *args, **kwargs) -> "Prompt":
        """
        Create a new instance if one doesn't exist, or return the existing instance.
        Returns:
            Prompt: The singleton instance of this class.
        """
        if cls._instance is None:
            cls._instance = super(Prompt, cls).__new__(cls)

        return cls._instance

    def __init__(self, parent: Any, prompt: Optional[str] = None) -> None:
        """
        Initialize the 'Prompt' class and its underlying cmd2 components.
        """

        if not self._is_initialized:
            try:
                if parent is None:
                    raise RuntimeError("AutoForge instance must be specified when initializing core module")
                self._autoforge = parent  # Store parent' AutoForge' class instance.

                self._toolbox = ToolBox.get_instance()
                self._environment: Environment = Environment.get_instance()
                self._prompt_base = prompt if prompt else PROJECT_NAME.lower()
                self._executable_db: Optional[Dict[str, str]] = None
                self._commands_loader: Optional[CommandsLoader] = CommandsLoader.get_instance()
                self._last_execution_return_code: Optional[int] = 0

                # Get a logger instance
                self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

                # Clear command line buffer
                sys.argv = [sys.argv[0]]
                ansi.allow_ansi = True

                # Add dynamically lodaed AutoForge style commands
                self._add_auto_commands()

                # Build executables dictionary for implementation shell style fast auto completion
                if self._environment.execute_with_spinner(message=f"Initializing {PROJECT_NAME}... ",
                                                          command=self._build_executable_index,
                                                          command_type=ExecutionMode.PYTHON,
                                                          new_lines=1) != 0:
                    raise RuntimeError("could not finish initializing")

                # Modify readline behaviour to allow for single TAB when auto completing binary name
                readline.parse_and_bind("set show-all-if-ambiguous on")
                readline.parse_and_bind("TAB: complete")

                # Initialize cmd2 core
                super().__init__()

                # Remove unnecessary built-in commands
                for cmd in ['macro', 'edit', 'run_pyscript']:
                    self._remove_command(cmd)

                # Assign path_complete to the complete_cd and complete_ls methods
                self.complete_cd = self.path_complete
                self.complete_lss = self.path_complete

                # Add several basic aliases
                self.set_alias('..', 'cd ..')
                self.set_alias('~', 'cd $HOME')
                self.set_alias('gw', f'cd {Environment.get_instance().get_workspace_path()}')
                self.set_alias('ls', 'lsd -g')
                self.set_alias('ll', 'lss -la')
                self.set_alias('l', 'ls')
                self.set_alias('exit', 'quit')
                self.set_alias('gs', 'git status')
                self.set_alias('ga', 'git add .')
                self.set_alias('gc', 'git commit -m')
                self.set_alias('gp', 'git push')

                self._update_prompt()

            except Exception as exception:
                self._logger.error(exception)
                raise RuntimeError("prompt core module not initialized")

    def _remove_command(self, command_name: str):
        """
        Hide and disable a built-in cmd2 command by overriding its method.
        """
        # Step 1: Hide from help
        if command_name not in self.hidden_commands:
            self.hidden_commands.append(command_name)

        # Step 2: Disable functionality
        def disabled_command(_self, _):
            _self.perror(f"The '{command_name}' command is disabled in this shell.")

        setattr(self, f'do_{command_name}', disabled_command)

        # Optionally disable help and completer too
        setattr(self, f'help_{command_name}', lambda _self: None)
        setattr(self, f'complete_{command_name}', lambda *_: [])

    def _add_auto_commands(self):
        """
        Dynamically adds AutoForge command methods to the Prompt instance based on
        the registered commands from the loader. Each command is dispatched
        via the loader's `execute()` method using its registered name.
        """
        command_summaries = self._commands_loader.get_commands()

        for cmd_summary in command_summaries:
            cmd_name = cmd_summary.name
            description = cmd_summary.description

            # Define the function and attach a docstring BEFORE binding
            def make_cmd(name, doc):
                # noinspection PyShadowingNames
                def dynamic_cmd(self, arg):
                    try:
                        result = self._commands_loader.execute(name, arg)
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

    def _split_command_line(self, line: str) -> Optional[tuple[str, str]]:
        """
        Safely splits a shell-style command line into command and arguments.
        Args:
            line (str): The raw input command line.

        Returns:
            Optional[tuple[str, str]]: A tuple (command, args) if valid, otherwise None.
        """
        try:
            parts = shlex.split(line)

            if not parts:
                return None

            command = parts[0]
            args = ' '.join(parts[1:]) if len(parts) > 1 else ''
            return command, args

        except ValueError as value_error:
            self._logger.warning(f"Error while parsing command line arguments {value_error}")
            return None

    def _update_prompt(self, board_name: Optional[str] = None):
        """
        Dynamically update the cmd2 prompt to mimic a modern Zsh-style shell prompt.

        The prompt includes:
            - The active virtual environment or board name (if provided).
            - The current working directory, using `~` when within the home folder.
            - Git branch name (if in a Git repo).
            - A rightward Unicode arrow symbol as a prompt indicator.

        Args:
            board_name (Optional[str]): A board name to display in the prompt. If not provided,
                                        falls back to the VIRTUAL_ENV or '(unknown)'.
        """

        # Virtual environment / board name section
        venv = os.environ.get("VIRTUAL_ENV")
        venv_prompt = f"({board_name})" if board_name else ("(unknown)" if venv else "")

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

    @staticmethod
    def get_instance() -> "Prompt":
        """
        Returns the singleton instance of this class.
        Returns:
            Prompt: The global stored class instance.
        """
        return Prompt._instance

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

    def complete(self, text: str, state: int,
                 custom_settings: Optional[CustomCompletionSettings] = None) -> Optional[str]:
        """
        Global tab completion handler. Adds shell-style binary name completion at the beginning of the line.
        Also completes dynamically loaded commands from the loader.
        """
        buffer = readline.get_line_buffer()
        tokens = buffer.strip().split()

        if len(tokens) <= 1 and buffer.strip().startswith(text):
            # From your system executable db
            matches = [cmd for cmd in self._executable_db if cmd.startswith(text)]

            # Add dynamically registered commands
            dynamic_commands = [cmd.name for cmd in self._commands_loader.get_commands()]
            matches.extend([cmd for cmd in dynamic_commands if cmd.startswith(text)])

            # Deduplicate and sort
            matches = sorted(set(matches))
            try:
                return matches[state]
            except IndexError:
                return None

        # Delegate to cmd2's default behavior for args, flags, etc.
        return super().complete(text, state)

    def do_cd(self, path: str):
        """
        Change the current working directory and update the CLI prompt accordingly.
        This method mimics the behavior of the shell `cd` command:

        Args:
            path (str): The target directory path, relative or absolute. Shell-like expansions are supported.
        """
        # Expand
        path = self._toolbox.get_expanded_path(path)

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
                command=f"ls {args} --color=auto -F",
                shell=True,
                immediate_echo=True,
                auto_expand=False,
                use_pty=True,
                expected_return_code=None
            )
        except Exception as exception:
            self.perror(f"ls: {exception}")

    def do_help(self, arg) -> None:
        """
        AutoForge CLI Help. command.
        """
        if arg:
            # noinspection PyArgumentList
            return super().do_help(arg)

        console = Console()

        # Build a table of commands
        table = Table(box=box.ROUNDED, highlight=True, expand=False)
        table.add_column("Command", style="bold green", no_wrap=True)
        table.add_column("Description", style="dim", overflow="fold")

        commands = sorted(self.get_all_commands())
        for cmd in commands:
            if cmd in self.hidden_commands:
                continue
            method = getattr(self, f'do_{cmd}', None)
            doc = method.__doc__.strip().splitlines()[0] if method and method.__doc__ else ""
            table.add_row(cmd, doc)

        # Wrap in a nice panel
        panel = Panel.fit(table, title="🛠  Available Commands", border_style="cyan")

        # Show everything
        console.print("\n", panel, "\n")  # Adds newlines before and after
        return None

    def default(self, statement: Any) -> None:
        """
        Fallback handler for unrecognized commands — executes them via the system shell.
        Method is called when a user types a command that is not defined as a `do_*` method.

        Args:
            statement (Any): Either a raw string command or a `cmd2.Statement` object.
        """

        command = statement.command + " " + statement.args if hasattr(statement, 'args') else str(statement)
        parts = self._split_command_line(command.strip())

        if not parts:
            return  # Nothing to execute

        cmd, args = parts

        try:
            self._environment.execute_shell_command(
                command=cmd,
                arguments=args,
                shell=True,
                immediate_echo=True,
                auto_expand=False,
                expected_return_code=None
            )
        except Exception as execution_error:
            print(f"{self._prompt_base}: {execution_error}")
