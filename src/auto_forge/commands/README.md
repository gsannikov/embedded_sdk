# AutoForge Commands

This directory contains **dynamically loaded command modules** for AutoForge.  
Each command implements the [`CommandInterface`](../core/interfaces/command_interface.py) and provides a specific action
or capability that can be executed from AutoForge.

## Design Principles

- **Interface-Driven Implementation** –  
  All commands **must** inherit from `CommandInterface`.  
  This enforces a consistent API for command discovery, initialization, and execution.

- **Dynamic Discovery**  
  AutoForge automatically scans for Python modules in the `commands` path at runtime, dynamically loading any that
  implement `CommandInterface`.

- **Solution-Extensible**  
  A solution can provide **its own** commands by:
    1. Defining a path tagged as `COMMANDS` in the solution variables.
    2. Placing Python scripts in that path that implement `CommandInterface`.
    3. AutoForge will load them alongside the built-in commands.

- **Loose Coupling**
  Commands should remain focused and self-contained, depending only on the core interfaces they require.  
  Avoid tight coupling with unrelated modules.

- **Internal Documentation**
  Each command module should include a **class-level docstring** describing:
    - Purpose
    - Expected arguments/parameters
    - Example usage

## Usage Notes

When creating a new command:

1. Inherit from `CommandInterface`.
2. Implement all required abstract methods.
3. Keep logic cohesive — a single command should serve a clear, well-defined purpose.
4. Provide helpful docstrings and inline comments where appropriate.

## Directory Scope

This directory contains:

- Built-in AutoForge commands.
- Dynamically loaded commands defined by solutions via the `COMMANDS` path.
