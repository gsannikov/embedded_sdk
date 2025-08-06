<!--suppress HtmlDeprecatedAttribute -->
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
tool-chains, this tool offers a unified interface to get the job done efficiently.

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
  Automated setup and tear-down of environment variables, tool-chains, and paths including native detection of SDKs,
  tool
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

## The Demo Project

For detailed instructions and structure, refer
to: [The Userspace Demo Project](https://github.com/intel-innersource/firmware.ethernet.devops.auto_forge/tree/main/src/auto_forge/resources/samples/userspace/README.md)

## Additional Resources

- **Package Details:**  
  [About AutoForge](https://github.com/intel-innersource/firmware.ethernet.devops.auto_forge/blob/main/src/auto_forge/resources/help/package/about.md)

- **SDK Integration Roadmap – Modular Breakdown:**  
  [Intel Wiki](https://wiki.ith.intel.com/x/1Jp98w)

<div style="text-align: center;">
  <img src="src/auto_forge/resources/package/clip_art/fork.png" alt="Get Involved">
</div>

Got ideas or improvements?<br>Jump in and help make **AutoForge** even better - contributions are always welcome!

## License

This project is licensed under the MIT License—see the LICENSE file for details.
