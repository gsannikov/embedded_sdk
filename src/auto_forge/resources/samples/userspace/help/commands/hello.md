# `hello` Command

A sample `hello` command demonstrating how to create, register, and implement a custom command within the **AutoForge
SDK**. This command prints a simple greeting and serves as a developer reference for building new commands.

## Usage

```bash
hello --text "World"
```

### Options

| Option             | Description                                      |
|--------------------|--------------------------------------------------|
| `-h`, `--help`     | Show help message and exit.                      |
| `-t`, `--text`     | Optional text to print in the console greeting.  |
| `-v`, `--version`  | Show version and exit.                           |
| `-vv`, `--verbose` | Show more information while running the command. |

---

## Developer Notes

This command serves as a template for implementing your own commands using the **AutoForge SDK**.

### 1. Base Interface: `CommandInterface`

All commands must inherit from the abstract base class `CommandInterface`:

```python
# AutoForge imports
from auto_forge import (CommandInterface)

class HelloCommand(CommandInterface):
```

This ensures a consistent API and lifecycle across all commands.

---

### 2. Required Constructor Initialization

Each command must call the base class constructor using `super()`, passing the command name and type:

```python
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
def create_parser(self, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-t", "--text", type=str,
                        help="Optional text to print in the console greeting.")
```

#### `run(self, args: argparse.Namespace) -> int`

Executes the command using parsed arguments:

```python
def run(self, args: argparse.Namespace) -> int:
    return_code: int = 0

    if args.text:
        print(f"Hello '{args.text}' 😎")
    else:
        # Error: no arguments
        return_code = CommandInterface.COMMAND_ERROR_NO_ARGUMENTS

    return return_code
```

---

By following this pattern, developers can implement consistent, discoverable, and extendable commands across the
AutoForge SDK.
