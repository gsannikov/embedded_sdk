# AutoForge Core Classes

This directory contains the **core modules** of the AutoForge build system.  
These classes implement the foundational logic that powers AutoForge’s solution processing, command execution, and
integration capabilities.

## Design Principles

- **Single Instance Guarantee** –  
  All core classes **must** derive from [`CoreModuleInterface`](./interfaces/core_module_interface.py) to enforce the *
  *singleton** pattern.  
  This ensures that there is exactly one active instance of each core module throughout the lifecycle of an AutoForge
  session.

- **Modularity & Separation of Concerns** –  
  Each core module is self-contained, responsible for a specific area of functionality (e.g., logging, context
  management, command dispatch, system introspection).

- **High Internal Documentation** –  
  Every class in this directory contains **extensive docstrings** describing its purpose, architecture, and usage
  patterns.  
  The README intentionally avoids repeating that detail.

- **Integration-Ready** –  
  Core modules are designed to be **solution-agnostic** and can be reused across different AutoForge configurations and
  modes (bare solution mode, standard builds, or custom workflows).

## Usage Notes

When creating a new core class:

1. Inherit from `CoreModuleInterface`.
2. Follow the singleton access pattern to avoid unintended multiple instantiations.
3. Keep the module’s scope focused — avoid mixing unrelated responsibilities.
4. Document thoroughly in the class-level docstring.

## Directory Scope

This directory **only** contains the **core AutoForge modules**.  
Other high-level modules, plugins, or user-defined extensions should reside in their respective paths.

---

**Tip:** If you are adding or modifying a core class, review the existing docstrings and keep your changes aligned with
AutoForge’s architecture and coding standards.
