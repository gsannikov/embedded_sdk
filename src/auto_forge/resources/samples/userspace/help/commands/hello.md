# `hello` Sample Command.

A sample `hello` command demonstrating how to create, register, and implement a custom command within the **SDK**.
This command echo a message back to the terminal and servers as a developer reference for building new commands.

## Usage

```bash
hello --m "Hello world!"
```

### Options

| Option             | Description                                      |
|--------------------|--------------------------------------------------|
| `-h`,  `--help`    | Show help message and exit.                      |
| `-m`,  `--message` | Message to print back.                           |
| `-v`,  `--version` | Show version and exit.                           |
| `-vv`, `--verbose` | Show more information while running the command. |

---

## Developer Notes

This command serves as a template for implementing your own commands using the **SDK**.

### 1. Interface: `CommandInterface`

All commands must inherit from the abstract base class `CommandInterface`:

```python
# AutoForge imports
from auto_forge import (CommandInterface)


class HelloCommand(CommandInterface):
    ...
```

This ensures a consistent API and lifecycle across all commands.

---

### 2. Constructor

Each command must call the base class constructor using `super()`, passing the command name and type:

```python
from typing import Any, Optional
from auto_forge import (AutoForgCommandType)


def __init__(self, **_kwargs: Any):
    """
    Initializes the HelloCommand class.
    """
    super().__init__(command_name="hello", command_type=AutoForgCommandType.MISCELLANEOUS)
```

---

### 3. Required Methods

Each command must implement **two** key methods:

#### `create_parser(parser: argparse.ArgumentParser)`

Defines expected arguments using `argparse`. For example:

```python
import argparse


def create_parser(self, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-m", "--message", type=str,
                        help="Message to print")
```

#### `run(self, args: argparse.Namespace) -> int`

Executes the command using parsed arguments:

```python
import argparse
from auto_forge import (CommandInterface)


def run(self, args: argparse.Namespace) -> int:
    return_code: int = 0

    if args.message:
        print(f"Hello '{args.text}' ?")
    else:
        # Error: no arguments
        return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

    return return_code
```

---

By following this pattern, developers can implement consistent, discoverable, and extendable commands across the
AutoForge SDK.
