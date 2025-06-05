"""
Script:         json_viewer.py
Author:         AutoForge Team

Description:
    Terminal-friendly JSON viewer built using the Textual framework.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.events import Key
from textual.widgets import Header, Footer, Tree
from textual.widgets.tree import TreeNode


class TreeApp(App):
    BINDINGS = [("q", "quit", "Quit"), ]

    def __init__(self, json_data: Any, application_title: str = "JSON Viewer", root_node_name: str = "JSON"):
        """ Initializes the JSON viewer"""
        super().__init__()
        self.json_data = json_data
        self.root_node_name = root_node_name
        self.title = application_title  # This sets the app/window title

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Tree("Root")

    @classmethod
    def add_json(cls, node: TreeNode, json_data: object, root_node_name: str) -> None:
        """Recursively add JSON data to a tree node, highlighting 'name' keys."""

        def add_node(name: str, _node: TreeNode, _data: object) -> None:
            if isinstance(_data, dict):
                _node.set_label(Text.from_markup(f"[bold blue]{{}}[/] [cyan]{name}[/]"))
                for key, value in _data.items():
                    new_node = _node.add("")
                    add_node(key, new_node, value)
            elif isinstance(_data, list):
                _node.set_label(Text.from_markup(f"[bold magenta][][/] [cyan]{name}[/]"))
                for index, value in enumerate(_data):
                    new_node = _node.add("")
                    add_node(str(index), new_node, value)
            else:
                _node.allow_expand = False
                if name:
                    if name.lower() == "name":
                        # Special color for 'name' keys
                        label = Text.from_markup(f"[bold cyan]{name}[/] = [bright_white]{repr(_data)}[/]")
                    else:
                        label = Text.from_markup(f"[green]{name}[/] = [yellow]{repr(_data)}[/]")
                else:
                    label = Text.from_markup(f"[yellow]{repr(_data)}[/]")
                _node.set_label(label)

        add_node(root_node_name, node, json_data)

    async def on_key(self, event: Key) -> None:
        tree = self.query_one(Tree)
        node = tree.cursor_node  # currently selected node

        if not node:
            return

        if event.key == "right":
            if node.allow_expand and not node.is_expanded:
                node.expand()
                event.stop()
        elif event.key == "left":
            if node.is_expanded:
                node.collapse()
                event.stop()
        elif event.key.lower() == "q":
            await self.action_quit()

    async def on_mount(self) -> None:
        bg_color = "#1a1c2c"  # Dark blue-gray

        # Apply background to major widgets
        self.styles.background = bg_color
        self.query_one(Header).styles.background = bg_color
        self.query_one(Footer).styles.background = bg_color
        self.query_one(Tree).styles.background = bg_color

        tree = self.query_one(Tree)
        tree.show_root = True
        tree.root.set_label(" ")
        self.add_json(node=tree.root, json_data=self.json_data, root_node_name=self.root_node_name)
        tree.root.expand()

        for child in tree.root.children:
            child.expand()


def load_and_validate_json(file_path: Path) -> Optional[Any]:
    """Loads and validates a JSON file. Returns the parsed data or None on failure."""
    if not file_path.is_file():
        print(f"Error: '{file_path}' does not exist or is not a file.")
        return None

    if file_path.suffix.lower() != '.json':
        print(f"Warning: '{file_path.name}' does not have a .json extension. Attempting to parse anyway.")

    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{file_path.name}': {e}")
    except Exception as e:
        print(f"Unexpected error reading '{file_path.name}': {e}")

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoForge JSON Viewer")
    parser.add_argument("-j", "--json", type=str, required=True, help="Path to the JSON file to view")
    parser.add_argument("-t", "--title", type=str, default="JSON Viewer",
                        help="Window title to show in the header and terminal")
    parser.add_argument("-r", "--root_name", type=str, default="JSON", help="Root node name")

    args = parser.parse_args()
    json_file_path = Path(args.json).resolve()

    json_data = load_and_validate_json(file_path=json_file_path)
    if json_data is None:
        return 1

    try:
        app = TreeApp(json_data=json_data, application_title=args.title, root_node_name=args.root_name)
        return app.run()
    except Exception as app_error:
        print(f"Error running viewer: {app_error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
