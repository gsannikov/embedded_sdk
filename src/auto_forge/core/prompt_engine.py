"""
Script:         prompt_engine.py
Author:         AutoForge Team

Description:
    Defines the PromptEngine class, based on the cmd2 interactive
    shell for the AutoForge build system.
"""

import os
import readline
import shlex
import subprocess
import sys
from contextlib import suppress
from typing import Any, Optional, Dict

import cmd2
from cmd2 import ansi, CustomCompletionSettings
from colorama import Fore, Style

# AutoForge imports
import auto_forge
from auto_forge import (SetupTools, AutoLogger, PROJECT_NAME)
from auto_forge.core.setup_tools import CommandType

AUTO_FORGE_MODULE_NAME = "PromptEngine"
AUTO_FORGE_MODULE_DESCRIPTION = "Terminal Prompt Manager"


class PromptEngine(cmd2.Cmd):
    """
    Interactive CLI shell for AutoForge with shell-like behavior.

    Provides dynamic prompt updates, path-aware tab completion,
    and passthrough execution of unknown commands via the system shell.
    """

    def __init__(self, prompt: Optional[str] = None):
        """
        Initialize the PromptEngine and its underlying cmd2 components.

        Args:
            prompt (Optional[str]): Optional custom base prompt string.
                If not specified, the lowercase project name ('autoforge') will be used
                as the base prefix for the dynamic prompt.
        """

        self._auto_forge = auto_forge.auto_forge.AutoForge.get_instance()
        self._setup_tool: SetupTools = self._auto_forge.tools
        self._prompt_base = prompt if prompt else PROJECT_NAME.lower()
        self._executable_db: Optional[Dict[str, str]] = None

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

        # Clear command line buffer
        sys.argv = [sys.argv[0]]
        ansi.allow_ansi = True

        if self._setup_tool.execute_with_spinner(message=f"Initializing {PROJECT_NAME}... ",
                                                 command=self._build_executable_index,
                                                 command_type=CommandType.PYTHON_METHOD,
                                                 new_lines=1) != 0:
            raise RuntimeError("could not finish initializing")

        # Modify readline behaviour to allow for single TAB when auto completing binary name
        readline.parse_and_bind("set show-all-if-ambiguous on")
        readline.parse_and_bind("TAB: complete")

        # Initialize cmd2 core
        super().__init__()

        # Assign path_complete to the complete_cd and complete_ls methods
        self.complete_cd = self.path_complete
        self.complete_ls = self.path_complete

        self._add_aliases()
        self._update_prompt()

    def complete(self, text: str, state: int, custom_settings: Optional[CustomCompletionSettings] = None) -> Optional[
        str]:
        """
        Global tab completion handler. Adds shell-style binary name completion at the beginning of the line.
        Complete unknown commands using preloaded system executable index.
        """

        buffer = readline.get_line_buffer()
        tokens = buffer.strip().split()

        # Only complete command name at the beginning of a line
        if len(tokens) <= 1 and buffer.strip().startswith(text):
            matches = [cmd for cmd in self._executable_db if cmd.startswith(text)]
            matches.sort()
            try:
                return matches[state]
            except IndexError:
                return None

        return super().complete(text, state)

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

    def _add_aliases(self):
        """Define shell-style command aliases."""
        self.aliases['ll'] = 'ls -la'
        self.aliases['..'] = 'cd ..'

    def do_cd(self, args):
        """
        Change the current working directory and update the CLI prompt accordingly.
        This method mimics the behavior of the shell `cd` command:

        Args:
            args (str): The target directory path, relative or absolute. Shell-like expansions are supported.
        """
        path = os.path.expanduser(args)

        try:
            if not os.path.exists(path):
                raise RuntimeError(f"no such file or directory: {path}")

            os.chdir(path)
            self._update_prompt()

        except Exception as change_path_errors:
            print(f"cd: {change_path_errors}")

    def do_ls(self, args):
        """
        List directory contents with enhanced shell behavior.
        This command mimics the traditional Unix `ls` command by:

        Args:
            args (str): Any additional arguments to pass to `ls`, such as `-l`, paths, or wildcards.
        """
        try:
            self._setup_tool.execute_shell_command(
                command=f"ls {args} --color=auto -F",
                shell=True,
                immediate_echo=True,
                auto_expand=False,
                use_pty=True,
                expected_return_code=None
            )
        except Exception as e:
            self.perror(f"ls: {e}")

    def default(self, statement: Any) -> None:
        """
        Fallback handler for unrecognized commands — executes them via the system shell.
        Method is called when a user types a command that is not defined as a `do_*` method.

        Args:
            statement (Any): Either a raw string command or a `cmd2.Statement` object.

        """
        command = statement.raw if hasattr(statement, 'raw') else statement
        parts = self._split_command_line(command.strip())

        if not parts:
            return  # Nothing to execute

        cmd, args = parts
        try:
            self._setup_tool.execute_shell_command(
                command=cmd,
                arguments=args,
                shell=True,
                immediate_echo=True,
                auto_expand=False,
                expected_return_code=None
            )
        except Exception as execution_error:
            print(f"{self._prompt_base}: {execution_error}")
