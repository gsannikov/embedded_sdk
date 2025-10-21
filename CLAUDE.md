# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AutoForge** is an extensible Python-based framework for automating complex build and validation workflows across embedded and software systems. It uses JSON-based declarative definitions to orchestrate build pipelines with dynamic variable resolution, dependency injection, and robust logging.

## Development Setup

### Environment Requirements
- Python 3.9 to 3.12 (inclusive)
- Linux platform (Fedora, Ubuntu, or WSL)
- Virtual environment recommended

### Installation Commands
```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install from local source for development
pip install -e .

# Or install from Git
pip install git+https://github.com/emichael72/auto_forge.git
```

### Running AutoForge
```bash
# Bare solution mode (no build capability, exploration only)
autoforge --bare

# With workspace and solution
autoforge -w <workspace-path> -n <solution-name>

# With solution package
autoforge -w <workspace-path> -n <solution-name> -p <solution-package-path>

# Non-interactive: run a sequence
autoforge -w <workspace-path> -n <solution-name> -s <sequence-name>

# Non-interactive: run specific commands
autoforge -w <workspace-path> -n <solution-name> -r <command>

# MCP service mode
autoforge -m -w <workspace-path> -n <solution-name>
```

### Testing
Testing is configured via VSCode settings using unittest (not pytest):
```bash
# Run tests using Python's unittest module
python3 -m unittest discover -v -s ./src -p "*test.py"
```

## Architecture

### Core Design Principles

1. **Singleton Pattern**: All core classes derive from `CoreModuleInterface` to enforce single instance per session
2. **Modular CLI Framework**: Self-registering command modules via `CLICommandInterface`
3. **Dynamic Discovery**: Plugins, commands, and builders are auto-discovered at runtime
4. **Declarative Configuration**: JSONC-based solution files define build flows

### Directory Structure

```
src/auto_forge/
├── auto_forge.py          # Main entry point, orchestrates lifecycle
├── __main__.py            # CLI argument processing
├── settings.py            # Package-wide settings
├── core/                  # Singleton core modules (logging, context, registry, etc.)
│   ├── interfaces/        # Core interfaces (CoreModuleInterface, BuilderRunnerInterface, etc.)
│   └── protocols/         # Type protocols
├── commands/              # CLI command implementations
│   ├── ai_command.py
│   ├── deploy_command.py
│   ├── edit_command.py
│   ├── lsd_command.py
│   ├── mini_west_command.py
│   ├── refactor_command.py
│   ├── sig_tool_command.py
│   ├── sln_command.py
│   ├── start_command.py
│   ├── xray_command.py
│   └── zephyr_tools_command.py
├── builders/              # Build system runners (cmake, make, etc.)
│   └── analyzers/         # Build analysis tools
├── common/                # Shared utilities
├── config/                # Configuration files and schemas
└── resources/             # Static resources, help files, clip art
```

### Key Subsystems

- **CoreLogger**: Structured, colorized logging with telemetry
- **CoreContext**: Manages workspace and solution context
- **CoreRegistry**: Central service registry for core modules
- **CoreDynamicLoader**: Discovers and loads plugins/commands/builders
- **CoreBuildShell**: Interactive shell with tab-completion and history
- **CoreSolution**: Parses and manages solution configurations
- **CoreVariables**: Handles dynamic variable resolution with references
- **CoreJSONCProcessor**: Processes JSONC with variable substitution
- **CorePlatform**: Platform-specific system detection and SDK probing
- **CoreToolBox**: Common build and file operations
- **CoreMCPService**: Model Context Protocol service mode

### Solution Configuration

Solutions use a hierarchical structure:
- **Solutions** → **Projects** → **Configurations**

Each level supports:
- **Variable References**: `<$ref_key>` (local), `<$ref_solutions[Sol].projects[Proj].configurations[Cfg].key>` (explicit)
- **Derivation**: `"data": "<$derived_from_solutions[Sol].projects[Proj].configurations[Cfg]>"`
- **JSONC Format**: Supports inline comments (`//`, `/* */`)

Example reference patterns:
```jsonc
{
  "board": "some_board",
  "path": "/home/dev/<$ref_board>",  // Local reference
  "other": "<$ref_solutions[OurSolution].projects[Zephyr].configurations[debug].board>"  // Explicit
}
```

### Builder System

Builders implement `BuilderRunnerInterface` and are dynamically loaded:
- Each builder registers under a unique name (e.g., "cmake", "make")
- Solutions specify which builder to use via the registered name
- Custom builders can be added by placing Python scripts in paths tagged as `BUILDERS` in solution variables
- Built-in builders are in `src/auto_forge/builders/`

### Command System

Commands implement `CLICommandInterface` and are auto-discovered from:
1. Built-in commands in `src/auto_forge/commands/`
2. Solution-specific commands from paths defined in solution config

Commands provide:
- Interactive help via `--help` flag
- Tab-completion support
- Argument parsing and validation
- Integration with core services via CoreRegistry

## Important Implementation Notes

1. **Singleton Access**: Always use the singleton pattern for core modules - avoid direct instantiation
2. **Interface Compliance**: New commands must implement `CLICommandInterface`, builders must implement `BuilderRunnerInterface`
3. **Variable Resolution**: Use `CoreVariables` for resolving references in configuration data
4. **Logging**: Use `CoreLogger` singleton for all logging - supports structured logs and telemetry
5. **Path Handling**: Store initial working directory early; AutoForge changes directories during execution
6. **Thread Safety**: Core modules include synchronization primitives; be mindful of thread switches (configured to 1ms)
7. **Error Handling**: Use `ExceptionGuru` for consistent error reporting
8. **GUI Integration**: `CoreGUI` provides terminal UI components (tables, progress bars, markdown rendering via Textual 4.0.0)

## Dependencies

Key pinned dependencies (see pyproject.toml):
- Python: 3.9-3.12.10
- Textual: 4.0.0 (do NOT upgrade - v5+ breaks Markdown rendering)
- prompt_toolkit: 3.0.51 (interactive shell)
- rich: 13.9.4 (terminal formatting)
- OpenTelemetry: 1.36.0 (telemetry)
- tree-sitter: 0.25.0 (code analysis)
- GitPython: 3.1.45 (Git operations)
- jsonschema: 4.25.0 (validation)
- openai: 1.30.5 (AI integration)

Note: `tkinter` must be installed via system package manager (not pip)

## Working with Solution Files

Solution files are typically located at:
- `<workspace>/scripts/solution/` (default)
- Or specified via `-p/--solution-package` argument

When modifying solution configurations:
1. Validate JSONC syntax (comments allowed)
2. Ensure variable references resolve correctly
3. Test derivation chains for circular dependencies
4. Verify builder names match registered builders
5. Check that referenced paths exist or will be created

## AI Integration

AutoForge includes AI-friendly features:
- **Predictable Structure**: Consistent directory layout and naming
- **Rich Metadata**: Detailed configuration and telemetry
- **Standardized Errors**: Uniform error handling via ExceptionGuru
- **Structured Logs**: JSON-compatible logging format
- AI commands available via `ai_command.py` module
