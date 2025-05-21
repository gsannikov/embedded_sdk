"""
Script:         textual_md_viewer.py
Author:         AutoForge Team

Description:
    A simple terminal-based Markdown viewer built using the Textual framework.
    Launches a full-screen TUI (Textual User Interface) that
    renders and displays a Markdown (.md) file using a scrollable panel.

    If Textual is not installed, the script exits quietly without error.


Requirements:
    - Python 3.8+
    - textual (install via `pip install textual`)
"""

import sys
from contextlib import suppress

# Safely check for textual availability
with suppress(ImportError):
    from textual.app import App, ComposeResult
    from textual.widgets import MarkdownViewer


    class MarkdownApp(App):
        """Textual application to render a Markdown file in the terminal."""

        def __init__(self, markdown_path: str):
            """
            Initialize the MarkdownApp with the given file path.
            Args:
                markdown_path (str): Path to the Markdown file to display.
            """
            super().__init__()
            self.markdown_path = markdown_path

        def compose(self) -> ComposeResult:
            """
            Read the Markdown content from file and return a MarkdownViewer widget.
            Returns:
                ComposeResult: A generator yielding the MarkdownViewer widget.
            """
            with open(self.markdown_path, "r", encoding="utf-8") as f:
                content = f.read()
            yield MarkdownViewer(content)


    if __name__ == "__main__":
        path = sys.argv[1] if len(sys.argv) > 1 else None
        if not path:
            print("Usage: viewer.py <path-to-md>")
            sys.exit(1)

        try:
            MarkdownApp(path).run()
            sys.exit(0)
        except Exception as e:
            print(f"Error running viewer: {e}")
            sys.exit(1)

# If we reach here, textual was not installed — exit silently
sys.exit(1)
