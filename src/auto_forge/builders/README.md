# AutoForge Builders

This directory contains **dynamically loaded build runner modules** for AutoForge.  
Each builder implements the [`BuilderRunnerInterface`](../core/interfaces/builder_interfcae.py) and encapsulates
the logic for executing a specific build flow.

## Design Principles

- **Interface-Driven Architecture** –  
  All builders **must** inherit from `BuilderRunnerInterface`.  
  This ensures a consistent lifecycle for initialization, configuration, execution, and artifact handling.

- **Specialized Build Flows** –  
  Each builder focuses on a **single build system or process**.  
  For example:
    - [`cmake_builder.py`](./cmake_builder.py) registers itself as `"cmake"` and handles CMake-based builds.
    - Other builders may target Make, Ninja, Bazel, or custom build systems.

- **Dynamic Discovery** –  
  AutoForge scans for Python modules in the `builders` path at runtime and dynamically loads any that implement
  `BuilderRunnerInterface`.  
  Builders register themselves under a unique **builder name**, which is used in solutions to select the desired build
  flow.

- **Extensibility** –  
  Solutions can provide custom builders by:
    1. Defining a path tagged as `BUILDERS` in the solution variables.
    2. Placing Python scripts in that path that implement `BuilderRunnerInterface` and register themselves under a
       unique name.

- **Modular & Maintainable** –  
  Builders should remain focused on their build system, avoid unrelated logic, and depend only on necessary core
  modules.

## Usage Notes

When creating a new builder:

1. Inherit from `BuilderRunnerInterface`.
2. Implement all required abstract methods (`run`, `configure`, `clean`, etc.).
3. Register the builder with a unique name so it can be referenced in solutions.
4. Keep build system logic isolated from unrelated code.

## Directory Scope

This directory contains:

- Built-in AutoForge builders for common build systems.
- Dynamically loaded solution-specific builders via the `BUILDERS` path.
