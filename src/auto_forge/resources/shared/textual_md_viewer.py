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
from pathlib import Path

# Safely check for textual availability
with suppress(ImportError):
    from textual.app import App, ComposeResult
    from textual.widgets import MarkdownViewer
    from textual import events
    from textual.geometry import Size


    class MarkdownApp(App):
        """Textual application to render a Markdown file in the terminal."""

        BINDINGS = [("q", "quit", "Quit")]

        def __init__(self, markdown_path: str):
            super().__init__()
            self.markdown_path = markdown_path

        async def on_mount(self) -> None:
            self.screen._current_size = Size(0, 0)
            self.screen.refresh(layout=True)

        def compose(self) -> ComposeResult:
            content = Path(self.markdown_path).read_text(encoding="utf-8")
            viewer = MarkdownViewer(content)
            viewer.styles.height = "100%"
            viewer.styles.width = "100%"
            yield viewer

        async def on_key(self, event: events.Key) -> None:
            if event.key.lower() == "q":
                await self.action_quit()


    if __name__ == "__main__":
        path = sys.argv[1] if len(sys.argv) > 1 else None
        if not path:
            print("Usage: viewer.py <path-to-md>")
            sys.exit(1)

        try:
            MarkdownApp(path).run()
            sys.argv = [sys.argv[0]]
            sys.exit(0)
        except Exception as e:
            print(f"Error running viewer: {e}")
            sys.exit(1)

# If we reach here, textual was not installed — exit silently
sys.exit(1)
