"""
Module: ai_command.py
Author: AutoForge Team

Description:
    Interact with an AI.
"""

import argparse
import asyncio
from typing import Any, Optional

# AutoForge imports
from auto_forge import (CoreAI, CommandInterface)

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

        # Base class initialization
        super().__init__(command_name=AUTO_FORGE_MODULE_NAME, hidden=True)

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("-m", "--message", type=str, required=True, help="Message text")

    async def async_run(self, _prompt: str):
        """
        Asynchronously sends the provided prompt to the AI service and prints the response.
        Args:
            _prompt (str): The user's input prompt to be analyzed.
        """
        # prompt = "Build failed with error: undefined reference to `foo_init`"
        # response = await self._ai_bridge.query(prompt=_prompt)
        response = await self._ai_bridge.query(_prompt.strip())

        if response is not None:
            print("AI response:", response)
        else:
            print("No response received.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the command based on parsed arguments.
        Args:q
            args (argparse.Namespace): The parsed arguments.
        Returns:
            int: Exit status (0 for success, non-zero for failure).
        """

        if args.message:
            asyncio.run(self.async_run(args.message))
            return 0

        return CommandInterface.COMMAND_ERROR_NO_ARGUMENTS
