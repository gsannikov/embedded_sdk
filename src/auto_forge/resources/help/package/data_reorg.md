# What is "Data Reorganization"?

To unlock the full potential of AI integration and to ensure seamless, unified workflows across all projects, we must
first **reorganize** our existing project structures. This may sound daunting — but let’s demystify it.

This reorganization **isn't about bureaucracy**. It's about establishing **clear, predictable, and machine-friendly
conventions** so engineers, architects, DevOps, and AI agents can all collaborate efficiently.

---

## 1. Common Locations for Project Resources 🧭

Each project should adhere to a shared directory structure. These well-known locations help humans and AI alike reason
about the project.

- **📁 Sources**

  Active development files — *clean*. Only `.c` and `.h` files, no floating scripts, binaries, or documentation.

- **📁 Scripts**

  All local project scripts (Python, shell, etc.). Helps isolate automation logic from source code.

- **📁 Build**

- Temporary compilation outputs (object files, intermediate binaries). Not versioned.

- **📁 Logs**

  Logs from the build system or tools. All logs must follow a standard, cross-project structure.

- **📁 Help**

  Markdown-based help documentation linked to commands and topics. Discoverable and centrally managed.

- **📁 Commands**

  Build-system-native commands. Unlike general scripts, these conform to a known interface, support dynamic loading,
  self-reporting, and help binding.

- **📁 Documents**

  Documentation (in any format) to be indexed by an AI. The goal is machine-readability, not just human-readability.

- **📁 Persistent**

  Long-lived data not tied to the workspace lifecycle: SQLite indexes, custom toolchains, encrypted keys, installables
  for e.g Simics artifacts, etc.

- **📁 Images**

  Stores successfully compiled products and bundles. Used by tools generating things like NVM files.

- **📁 Externals**

  Third-party or unmodified source trees (Linux kernels, Zephyr, libraries). We depend on them but do not maintain them.

---

## 2. Build System Reporting 📝: Machine-Facing Interfaces

A modern build system must provide interfaces that allow **external agents (AI, tools, humans)** to query and discover
the state and structure of the project.

- 📂 `iterate_paths(path_type)`

  Iterate through all known paths of a specific type (e.g. sources, logs, compiled outputs). Abstracts physical layout
  for external tools.

- 📄 `get_context(context_type)`

  Returns a context dictionary:

    - `system`: platform info
    - `error`: last build failure (file, line, message, relevant code snippet, toolchain used, etc.)

- 🛠️ `get_commands(command_class)`

  Returns a structured list (e.g. JSON) of commands in a given class, such as `build`, with descriptions, flags, usage,
  etc.

- 🔭 `get_telemetry()`

  Real-time build performance or usage metrics.

---

## 3. Unified Workspace Creation 🎯

A centralized component should manage how a project is instantiated:

- Set up the environment
- Fetch required sources and resources
- Validate the platform
- Create a usable workspace

This enables **consistent onboarding**, reproducible environments, and AI-friendly automation — from DevOps pipelines to
local developer machines.

---

## 4. The Glue 🧠: Unified Solution Description

At the heart of AutoForge is a **standardized, structured project description** — sometimes referred to as *"the
solution"*.

This defines:

- Workspace layout
- Required environments and tools
- Project-specific behavior (on errors, help, logging)
- All dynamic commands, aliases, configurations
- Integration metadata (used for reporting, telemetry, and AI assistance)

This unified description ensures **predictability**, **discoverability**, and **extensibility**, allowing intelligent
tools and agents to work across any project *without hardcoded knowledge*.

---

By following these foundational steps, we are not just making our lives easier — we are building the bridge that allows
**AI to truly assist** us, understand our code, automate our build/test flows, and ensure consistency and insight across
the entire development lifecycle.
