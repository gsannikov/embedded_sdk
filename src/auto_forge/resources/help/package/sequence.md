# Workspace Setup Sequence Guide

This guide describes how to create and customize a `sequence.jsonc` file to automate the setup of a functional
development workspace. The file uses **JSONC (JSON + Comments)**, which allows inline comments for readability while
preserving JSON compatibility.

---

## Overview

A sequence file consists of:

- Global configuration (e.g., title formatting, pre/post messages)
- A list of **steps**, each with a `description`, a `method` to invoke, and `arguments`

This file is interpreted by the setup engine to perform validations, install dependencies, configure environments, and
apply final customizations.

---

## Top-Level Fields

```jsonc
{
  "status_title_length": 60,                    // Maximum width for titles in the status output
  "status_add_time_prefix": true,               // Adds timestamps to each status line
  "status_new_line": true,                      // New line between status entries
  "status_pre_message": "\nWelcome message...", // Message before steps begin
  "status_post_message": "\nSetup done...",     // Message after completion
  "steps": [ ... ]                              // The actual step sequence
}
```

---

## Step Definition

Each step is an object with the following fields:

### Required Fields

- `description`: Human-readable summary of the step
- `method`: The name of the method to execute
- `arguments`: Dictionary of arguments passed to the method

### Optional Fields

- `action_on_error`: Behavior on failure (`"resume"`, `"break"`, or omit for default)
- `status_on_error`: Custom error message (can be distro-specific)
- `response_store_key`: Store output of step (if applicable)

---

## Supported Methods

All method entries in the sequence file map to Python methods implemented in the `"CorePlatform"` class located in the
`"platform_tools.py"` module of the AutoForge codebase.
To extend the sequence language with new functionality, simply add a new method to this class—its name will be callable
directly from the JSONC sequence.

### 1. `validate_prerequisite`

Checks if a tool or command exists and matches a version.

```jsonc
{
  "method": "validate_prerequisite",
  "arguments": {
    "command": "python3",
    "cli_args": "--version",
    "version": ">=3.9.0"
  }
}
```

You can provide distro-specific `arguments` or `status_on_error` blocks for maximum flexibility.

### 2. `execute_shell_command`

Runs a shell command and optionally stores its output.

```jsonc
{
  "method": "execute_shell_command",
  "arguments": {
    "command_and_args": "dt github print-token",
    "cwd": "$HOME/bin"
  },
  "response_store_key": "dt_token"
}
```

### 3. `initialize_workspace`

Creates and optionally cleans a working directory.

```jsonc
{
  "method": "initialize_workspace",
  "arguments": {
    "delete_existing": false,
    "must_be_empty": true,
    "create_as_needed": true,
    "change_dir": true
  }
}
```

### 4. `python_virtualenv_create` and `python_update_pip`

Set up and prepare a virtual environment.

### 5. `python_package_add`

Install packages into a virtual environment. Accepts raw package strings or a requirements file.

### 6. `url_get`

Downloads files.

```jsonc
{
  "method": "url_get",
  "arguments": {
    "url": "...",
    "destination": "...",
    "timeout": 240.0,
    "delete_if_exist": true
  }
}
```

### 7. `decompress`

Extracts `.tar`, `.zip`, etc. into a destination directory.

### 8. `create_alias`

Adds shell aliases to the environment.

### 9. `conditional`

Run steps only if a condition fails or passes.

```jsonc
{
  "method": "conditional",
  "arguments": {
    "condition": {
      "method": "validate_prerequisite",
      "arguments": {
        "command": "...",
        "version": ">=1.0"
      }
    },
    "if_false": [ { ... steps ... } ]
  }
}
```

> ⚠️ Currently, `if_true` is unsupported and will raise an error if defined.

---

## Tips

- Prefer `action_on_error: "resume"` only in non-critical steps (e.g., alias creation)
- Use `$VARS` for paths and tokens — all strings are expanded
- Organize steps by purpose: prerequisites, environment, tools, final customizations

---

## Example: Minimal Setup

```jsonc
{
  "steps": [
    {
      "description": "Check Python version",
      "method": "validate_prerequisite",
      "arguments": {
        "command": "python3",
        "cli_args": "--version",
        "version": ">=3.9.0"
      }
    },
    {
      "description": "Initialize workspace",
      "method": "initialize_workspace",
      "arguments": {
        "create_as_needed": true,
        "change_dir": true
      }
    }
  ]
}
```

---

## Conclusion

Use this sequence format to fully automate workspace provisioning for developers. Extend, comment, and reuse blocks for
consistent setups across environments.
