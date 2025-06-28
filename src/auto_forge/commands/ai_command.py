"""
Module: ai_command.py
Author: AutoForge Team

Description:
    Interact with an AI.
"""

import argparse
import asyncio
from typing import Any, Optional

# Third-party
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# AutoForge imports
from auto_forge import (CoreAI, CoreSystemInfo, CommandInterface, TerminalSpinner)

AUTO_FORGE_MODULE_NAME = "ai_chat"
AUTO_FORGE_MODULE_DESCRIPTION = "Chat with an AI"
AUTO_FORGE_MODULE_VERSION = "1.0"


class AICommand(CommandInterface):
    """
    Implements a command to allow interacting with an AI.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the EditCommand class.
        Args:
            **_kwargs (Any): Optional keyword arguments, such as:
        """

        self._ai_bridge = CoreAI.get_instance()
        self._system_info_data = CoreSystemInfo.get_instance().get_data
        self._console = Console(force_terminal=True)

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=True)

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("message", nargs=argparse.REMAINDER, help="Message text")

    async def async_run(self, prompt: str, response_width: int = 100):
        """
        Asynchronously sends the provided prompt to the AI service and prints the response.
        Args:
            prompt (str): The user's input prompt to be analyzed.
            response_width (int, optional): The width of the response line length.
        """
        print()
        response: Optional[str] = None
        spin_task = asyncio.create_task(TerminalSpinner.run("Thinking..."))

        try:
            response = await self._ai_bridge.query(
                prompt=prompt,
                context="My thoughts",
            )
        finally:
            spin_task.cancel()
            try:
                await spin_task
            except asyncio.CancelledError:
                pass
            print("\r", end='', flush=True)

        if response:
            text = Text(response, style="white")
            wrapped_lines = text.wrap(self._console, width=response_width)
            wrapped_text = Text()
            for line in wrapped_lines:
                wrapped_text.append(line)
                wrapped_text.append("\n")
            wrapped_text = f"\n{wrapped_text}"
            self._console.print(Panel(wrapped_text, title="AI Response", border_style="cyan", expand=False))
        else:
            self._console.print("[bold red]No response received.[/bold red]")
        print()

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:q
            args (argparse.Namespace): The parsed arguments.
        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """

        if args.message:
            full_message = " ".join(args.message).strip()
            asyncio.run(self.async_run(prompt=full_message))
            return 0

        return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
