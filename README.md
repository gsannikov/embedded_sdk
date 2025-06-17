# Welcome to **AutoForge**

**AutoForge** is an extensible, Python-based framework built to streamline complex build and validation workflows across embedded and software systems. Designed with automation, clarity, and scale in mind, AutoForge empowers developers to define, run, and manage sophisticated build pipelines with minimal friction and maximum control.

## 🔧 What is AutoForge?

AutoForge turns JSON-based declarative definitions into fully automated build flows. It handles everything from environment preparation and configuration validation to build orchestration, logging, error recovery, and post-build analytics. Whether you're compiling a single RTOS image or coordinating cross-platform toolchains, AutoForge provides a unified interface to do it efficiently.

---

## 🌟 Key Features

- **Declarative Build Recipes**  
  Define and reuse build flows using structured, human-readable JSONC files with support for dynamic variable resolution and dependency injection.

- **Modular CLI Framework**  
  Add and extend functionality via self-registering Python modules that conform to a common `CLICommandInterface`.

- **Integrated Help System**  
  Discover commands, arguments, and usage examples via a built-in help interface—accessible from the terminal without leaving your workflow.

- **Robust Logging and Telemetry**  
  Structured, colorized logs and build-time telemetry for auditability and debugging across local and CI environments.

- **Environment Virtualization & Probing**  
  Automated setup and teardown of environment variables, toolchains, and paths—including native detection of SDKs, tool versions, and platform capabilities.

- **Plugin-Based Extensibility**  
  Dynamically discoverable plugins let teams introduce new command types, validators, and tool integrations without altering the core.

---

## 🧠 AI-Friendly by Design

AutoForge’s predictable structure, rich metadata, and standardized error handling make it ideal for AI-assisted development and debugging. Its JSON-based configuration, uniform logging, and consistent directory layout allow AI tools to:

- Understand project state quickly  
- Locate build artifacts and failures reliably  
- Offer actionable suggestions with minimal context  

This makes AutoForge particularly suitable for advanced workflows involving intelligent assistants and automated analysis tools.

---

## 🚀 Scales from Local to Enterprise

From embedded targets like Zephyr RTOS to large multi-stage Linux builds, AutoForge adapts to your development environment. Built-in safety checks, rollback handling, and modular architecture make it ideal for everything from rapid prototyping to enterprise-level CI/CD.

---

## 🧩 Why Use AutoForge?

If you're tired of scattered shell scripts, brittle CI jobs, and inconsistent build behaviors—AutoForge gives you a single, maintainable system for reproducible builds, insightful logs, and a consistent developer experience across the board.

---

## Setup Instructions.

The following link installs the 'userspace' demo solution.
To use it, copy and paste the command below into your terminal.

```bash
curl -sSL -H "Authorization: token $(dt github print-token https://github.com/intel-innersource/firmware.ethernet.devop)" \
  -H "Cache-Control: no-store" \
  "https://raw.githubusercontent.com/intel-innersource/firmware.ethernet.devops.auto-forge/refs/heads/main/src/auto_forge/resources/shared/bootstrap.sh" \
   | bash -s -- -n userspace -w ws -s create_environment_sequence -p "<samples>/userspace"
```

## Installing the package.

To install the latest AutoForge package use the following command:

```bash
pip install git+https://github.com/intel-innersource/firmware.ethernet.devops.auto-forge.git --force-reinstall
```

## License

This project is licensed under the MIT License—see the LICENSE file for details.

## Acknowledgments

Thanks to everyone who has contributed to the development of this exciting project!
