<br>
<div align="center">
  <img src="src/auto_forge/resources/package/clip_art/logo.png" alt="Logo" style="width: 300px;">
</div>
<br>

**AutoForge** is an extensible, Python-based framework designed to streamline complex build and validation workflows
across embedded and software systems. Built with automation, clarity, and scalability in mind,
it enables developers to define, run, and manage sophisticated build pipelines with minimal
friction and maximum control.

### In A Nutshell

<div align="center">
  <img src="src/auto_forge/resources/package/clip_art/flow.png" alt="Build Flow" style="width: 300px;">
</div>

At its core, **AutoForge** transforms JSON-based declarative definitions into fully automated build flows.
It handles everything from environment setup and configuration validation to build orchestration,
structured logging, error recovery, and post-build analytics.
Whether you're compiling a single RTOS image or coordinating cross-platform
toolchains, this tool offers a unified interface to get the job done efficiently.

### Key Features

- **Declarative Build Recipes**  
  Define and reuse build flows using structured, human-readable JSONC files with support for dynamic variable
  resolution and dependency injection.

- **Modular CLI Framework**  
  Add and extend functionality via self-registering Python modules that conform to a common `CLICommandInterface`. The
  system includes a user-friendly interactive shell with support for tab-completion, history-based suggestions, and
  contextual
  help. <br><br><img src="src/auto_forge/resources/package/clip_art/auto_complete.png" alt="Auto Complete" style="width: 350px;"><br>

- **Integrated Help System**  
  Discover commands, arguments, and usage examples via a built-in help interface accessible from the terminal without
  leaving your workflow.

- **Robust Logging and Telemetry**  
  Structured, colorized logs and build-time telemetry for auditability and debugging across local and CI environments.

- **Environment Virtualization & Probing**  
  Automated setup and teardown of environment variables, toolchains, and paths including native detection of SDKs, tool
  versions, and platform capabilities.

- **Plugin-Based Extensibility**  
  Dynamically discoverable plugins let teams introduce new command types, validators, and tool integrations without
  altering the core.

### AI-Friendly by Design

<br>
<div align="center">
  <img src="src/auto_forge/resources/package/clip_art/ai.png" alt="AI Ready" style="width: 200px;">
</div>
<br>

**AutoForge's** predictable structure, rich metadata, and standardized error handling make it ideal for AI-assisted
development **and debugging. Its JSON-based configuration, uniform logging,
and consistent directory layout allow AI tools to:

- Understand project state quickly
- Locate build artifacts and failures reliably
- Offer actionable suggestions with minimal context

This makes this tool particularly suitable for advanced workflows involving intelligent
assistants and automated analysis tools.

---

### Awesome! What's In It For Me?

If you're tired of scattered shell scripts, fragile CI jobs, and inconsistent build behaviors **AutoForge** gives you a
single, maintainable system for reproducible builds, insightful logs, and a consistent developer experience across the
board.

### The Demo Project

The following link installs the `userspace` demo solution.
Rather than replacing your existing `userspace` build flow, this demo is designed to overlay an
already cloned repository. This approach lets you explore the tool in a realistic environment
without disrupting your current working setup.

#### Setup Instructions

Copy the following — `not-so-one-liner` — into your terminal and behold the magic.

```bash

# The following not-so-one-liner below does quite a bit. Here’s a breakdown:
#
# 1. Executes the 'bootstrap' script 'bootstrap.sh' directly from the Package Git repository.
# 2. Tell 'bootstrap' to use the solution 'userspace', which is part of the built-in sample set.
# 3. Next, 'bootstrap' will:
#    - Install the latest AutoForge package into the user scope (via pip).
#    - Load the 'userspace' sample from the package.
#    - Create a new workspace in a local folder named 'ws'.
#    - Execute the sequence 'create_environment_sequence' defined by the solution.
#
# This sequence typically:
#    - Validates that required tools are installed,
#    - Creates a dedicated Python virtual environment inside the workspace,
#    - Installs required Python packages into the venv,
#    - Performs any additional environment setup steps defined by the solution.
#
# ⚠ No actions require 'sudo', and nothing is deleted without consent.

GITHUB_REPO="https://github.com/intel-innersource/firmware.ethernet.devops.auto_forge"
TOKEN=$(dt github print-token https://github.com/intel-innersource/firmware.ethernet.devop)

curl -sSL \
  -H "Authorization: token ${TOKEN}" \
  -H "Cache-Control: no-store" \
  "${GITHUB_REPO}/raw/refs/heads/main/src/auto_forge/resources/shared/bootstrap.sh" \
  | bash -s -- \
      -n userspace \
      -w ws \
      -s create_environment_sequence \
      -p "<samples>/userspace"
```

<div style="text-align: center;">
  <img src="src/auto_forge/resources/package/clip_art/fork.png" alt="Get Involved">
</div>

Got ideas or improvements?<br>Jump in and help make **AutoForge** even better - contributions are always welcome!

## License

This project is licensed under the MIT License—see the LICENSE file for details.