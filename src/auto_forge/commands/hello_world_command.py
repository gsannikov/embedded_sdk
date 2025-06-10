"""
Script:         hello_command.py
Author:         AutoForge Team

Description:
    😎 Sample 'hello world' command demonstrating how to construct and register a new command with AutoForge.

"""

import argparse
from typing import Any, Optional

# AutoForge imports
from auto_forge import (CommandInterface, CoreGUI, InputBoxButtonType, InputBoxLineType, InputBoxTextType,
                        MessageBoxType, AutoForgCommandType)


class HelloCommand(CommandInterface):
    """
    A simple 'hello world' command example for registering dynamically command.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the HelloCommand class.
        Args:
            **kwargs (Any): Optional keyword arguments.
        """

        super().__init__(command_name="hello", hidden=False, command_type=AutoForgCommandType.MISCELLANEOUS)

    def create_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        Adds command-line arguments for the hello command.
        Args:
            parser (argparse.ArgumentParser): The argument parser to extend.
        """
        parser.add_argument("-t", "--text", type=str, help="Optional text to print in the console greeting.")

        parser.add_argument("-m", "--message_box_text", type=str,
                            help="Optional text to display using the GUI message box.")

        parser.add_argument("-i", "--input_box_text", type=str,
                            help="Optional text to display using the GUI input box.")

    def run(self, args: argparse.Namespace) -> int:
        """
        Executes the hello command based on parsed arguments.
        Args:
            args (argparse.Namespace): Parsed command-line arguments.
        Returns:
            int: 0 on success, non-zero on failure.
        """
        return_code: int = 0
        response: Optional[Any] = None
        default_text: str = "Hello from AutoForge!"
        gui: CoreGUI = CoreGUI.get_instance()

        if args.text:
            print(f"Hello '{args.text}' 😎")

        elif args.message_box_text:
            message_box_text = args.message_box_text or args.text or default_text
            response = gui.message_box(text=message_box_text, caption="GUI Greeting", box_type=MessageBoxType.MB_OK)

        elif args.input_box_text:
            input_box_text = args.input_box_text or args.text or default_text
            # Prepare the input lines
            input_lines = [
                InputBoxLineType(label="Username", input_text="", text_type=InputBoxTextType.INPUT_TEXT, length=30),
                InputBoxLineType(label="Password", input_text="", text_type=InputBoxTextType.INPUT_PASSWORD, length=30)]

            response = gui.input_box(caption=input_box_text, lines=input_lines,
                                     button_type=InputBoxButtonType.INPUT_CANCEL)

        else:
            # Error: no arguments
            return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

        if response:
            print(f"Got {response}")

        return return_code
