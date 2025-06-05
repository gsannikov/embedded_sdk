"""
Script:         help_viewer.py
Author:         AutoForge Team

Description:
    A simple terminal-based Markdown viewer built using the Textual framework.
    Launches a full-screen TUI (Textual User Interface) that renders and displays a Markdown (.md) file using
    a scrollable panel. If Textual is not installed, the script exits quietly without error.
"""
import argparse
import sys
from contextlib import suppress
from pathlib import Path
from typing import Union

# Safely check for textual availability
with suppress(ImportError):
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import Footer, MarkdownViewer
    from textual import events


    class MarkdownApp(App):
        """A simple Markdown viewer application."""

        BINDINGS = [Binding("t", "toggle_table_of_contents", "TOC", tooltip="Toggle the Table of Contents Panel", ),
                    Binding("x", "exit", "Exit", tooltip="Exit Help Viewer"), ]

        def __init__(self, path: str):
            super().__init__()
            self.path = path

        @property
        def markdown_viewer(self) -> MarkdownViewer:
            """Get the Markdown widget."""
            return self.query_one(MarkdownViewer)

        def compose(self) -> ComposeResult:
            yield Footer()
            yield MarkdownViewer()

        async def on_mount(self) -> None:
            """Go to the first path when the app starts."""
            try:
                await self.markdown_viewer.go(self.path)
            except FileNotFoundError:
                self.exit(message=f"Unable to load {self.path!r}")

        def on_markdown_viewer_navigator_updated(self) -> None:
            """Refresh bindings for forward / back when the document changes."""
            self.refresh_bindings()

        def action_toggle_table_of_contents(self) -> None:
            """Toggles display of the table of contents."""
            self.markdown_viewer.show_table_of_contents = (not self.markdown_viewer.show_table_of_contents)

        async def on_key(self, event: events.Key) -> None:
            """Exit Viewer."""
            if event.key.lower() == "q":
                await self.action_quit()

        async def action_exit(self) -> None:
            """Exit Viewer."""
            await self.action_quit()

        async def action_back(self) -> None:
            """Navigate backwards."""
            await self.markdown_viewer.back()

        async def action_forward(self) -> None:
            """Navigate forwards."""
            await self.markdown_viewer.forward()

        def check_action(self, action: str, _) -> Union[bool, None]:
            """Check if certain actions can be performed."""
            if action == "forward" and self.markdown_viewer.navigator.end:
                # Disable footer link if we can't go forward
                return None
            if action == "back" and self.markdown_viewer.navigator.start:
                # Disable footer link if we can't go backward
                return None
            # All other keys display as normal
            return True


def main() -> int:
    """ Markdown viewer entry point. """

    parser = argparse.ArgumentParser(description="AutoForge Markdown Viewer")
    parser.add_argument("-m", "--markdown", type=str, required=True,
                        help="Path to Markdown file to view using Textual UI")
    parser.add_argument("-p", "--print", action="store_true", help="Print file content before starting the UI")

    args = parser.parse_args()
    file_path = Path(args.markdown)

    if not file_path.is_file():
        print(f"Error: File '{file_path}' does not exist or is not a regular file.")
        return 1

    try:
        if args.print:
            print(f"\nShowing '{file_path}'\n")
            with file_path.open("r", encoding="utf-8") as f:
                print(f.read())

        return MarkdownApp(str(file_path)).run()

    except Exception as e:
        print(f"Error running viewer: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
