"""
Script:         json_viewer.py
Author:         AutoForge Team
Version         1.0

Description:
    Terminal-friendly JSON viewer built using the Textual framework.
"""

import argparse
import base64
import json
import sys
from contextlib import suppress
from pathlib import Path
from typing import Optional, Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.events import Key
from textual.widgets import Header, Footer, Static
from textual.widgets import Tree
from textual.widgets.tree import TreeNode


class TreeApp(App):
    BINDINGS = [("q", "quit", "Quit"), ]
    CSS = """
    Screen {
        layout: vertical;
    }

    Container#main {
        layout: horizontal;
        height: 1fr;
    }

    Tree#json_tree {
        background: #1a1c2c;
        width: 7fr;  /* 80% */
    }

    Tree#side_tree, Static#side_panel {
        background: #2a2f4a;
        padding: 1 2;
        width: 3fr;  /* 20% */
        height: 100%;
        content-align: left top;
    }

    Header, Footer {
        background: #1a1c2c;
    }
    """

    def __init__(self, json_data: Any, application_title: str = "JSON Viewer",
                 root_node_name: str = "JSON", panel_data: Optional[dict] = None):

        """ Initializes the JSON viewer"""
        super().__init__()
        self.json_data = json_data
        self.root_node_name = root_node_name
        self.title = application_title  # This sets the app/window title
        self.show_panel = True if panel_data is not None else False
        self.panel_data: Optional[dict] = panel_data

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            yield Tree("Root", id="json_tree")

            if self.show_panel:
                if isinstance(self.panel_data, dict):
                    yield Tree("Info", id="side_tree")
                else:
                    yield Static("📄 JSON Viewer\n\nUse ←/→ to expand/collapse\nPress Q to quit.", id="side_panel")

        yield Footer()

    def add_json_to_panel_tree(self, parent: TreeNode, data: Any, key: str = "", root_label: str = "Summary") -> None:
        if isinstance(data, dict):
            label = key if key else root_label
            node = parent.add(label)
            for k, v in data.items():
                self.add_json_to_panel_tree(node, v, key=str(k))
        elif isinstance(data, list):
            label = key if key else root_label
            node = parent.add(label)
            for idx, item in enumerate(data):
                self.add_json_to_panel_tree(node, item, key=f"[]")
        else:
            label = f"{key}: {repr(data)}" if key else repr(data)
            leaf = parent.add(label)
            leaf.allow_expand = False

    @classmethod
    def add_json(cls, node: TreeNode, json_data: object, root_node_name: str) -> None:
        """Recursively add JSON data to a tree node, highlighting 'name' keys."""

        def add_node(name: str, _node: TreeNode, _data: object) -> None:
            """
            Recursively adds JSON-compatible data to a TreeNode with styled labels.
            Behavior:
            - For dictionaries: renders key names as labels using `{}` prefix.
            - For lists: renders indices or embedded "name" values (if available) as labels using `[]` prefix.
            - For primitive values: renders as 'key = value' with syntax highlighting.
            - Special styling is applied to keys named "name" to make them stand out.

            Args:
                name (str): The name or index to display for the current node.
                _node (TreeNode): The tree node to populate.
                _data (object): The data associated with the node (dict, list, or primitive).
            """
            if isinstance(_data, dict):
                _node.set_label(Text.from_markup(f"[bold blue]{{}}[/] [cyan]{name}[/]"))
                for key, value in _data.items():
                    new_node = _node.add("")
                    add_node(key, new_node, value)

            elif isinstance(_data, list):
                _node.set_label(Text.from_markup(f"[bold magenta][][/] [cyan]{name}[/]"))
                for index, value in enumerate(_data):
                    if isinstance(value, dict):
                        name_field = value.get("name")
                        if isinstance(name_field, str) and name_field.strip():
                            display_name = name_field.strip()
                        else:
                            display_name = str(index)
                    else:
                        display_name = str(index)

                    new_node = _node.add("")
                    add_node(display_name, new_node, value)

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

    def on_mount(self) -> None:
        tree = self.query_one("#json_tree", Tree)
        tree.show_root = True
        tree.root.set_label(" ")
        self.add_json(tree.root, self.json_data, self.root_node_name)
        tree.root.expand()
        for child in tree.root.children:
            child.expand()

        if not self.show_panel:
            tree.styles.width = "100%"  # or "1fr"

        if self.show_panel and isinstance(self.panel_data, dict):
            panel_tree = self.query_one("#side_tree", Tree)
            panel_tree.show_root = False
            self.add_json_to_panel_tree(parent=panel_tree.root, data=self.panel_data, root_label="Summary")

            # Expand all first-level visible nodes
            for child in panel_tree.root.children:
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
    parser.add_argument("-p", "--panel_content", type=str, help="Left side panel content (base64 encoded JSON)",
                        default=None)
    parser.add_argument("-r", "--root_name", type=str, default="JSON", help="Root node name")

    args = parser.parse_args()

    # Load the JSON file
    json_file_path = Path(args.json).resolve()
    json_data = load_and_validate_json(file_path=json_file_path)
    if json_data is None:
        return 1

    # Decode the panel data back to JSON
    panel_data: Optional[dict] = None

    if args.panel_content is not None:
        with suppress(Exception):
            decoded_text = base64.b64decode(args.panel_content).decode("utf-8")
            panel_data = json.loads(decoded_text)
    try:
        # Fire Textual to render the data
        app = TreeApp(json_data=json_data, application_title=args.title, root_node_name=args.root_name,
                      panel_data=panel_data)
        return app.run()
    except Exception as app_error:
        print(f"Error running viewer: {app_error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
