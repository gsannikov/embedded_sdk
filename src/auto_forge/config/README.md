# AutoForge Configuration

This directory contains **internal configuration resources** used by AutoForge, including default settings, validation
schemas, and the bare solution definition.

## Contents

1. **`auto_forge.jsonc`** –  
   The package’s internal configuration file, defining global defaults, behavior flags, and package-level settings.  
   This file is read at runtime and should only be modified by maintainers with a clear understanding of its impact.

2. **`schemas/1.0/`** –  
   JSON Schema definitions for validating **critical AutoForge structures** such as:
    - Solution files
    - Variables files
    - Other core JSON/JSONC configuration inputs  
      These schemas ensure that configuration files follow the expected structure and data types.

3. **`bare_solution/solution.jsonc`** –  
   A minimal **bare solution** definition used when AutoForge is executed in `--bare` mode.  
   In bare mode, AutoForge operates with limited capabilities commands can be executed, but no full build is possible.

## Design Principles

- **Validation-First** –  
  All critical configuration inputs are validated against their respective schemas before being processed.

- **Isolation of Defaults** –  
  Internal defaults (in `auto_forge.jsonc`) are kept separate from solution-specific configurations to avoid unintended
  overrides.

- **Backward-Compatible Structure** –  
  Schema versioning (e.g., `schemas/1.0/`) ensures that changes to validation rules do not break older solutions.

## Usage Notes

- **Do not remove or rename** `auto_forge.jsonc`, as it is required for package initialization.
- When updating a schema, increment the version number and maintain older versions for compatibility.
- Bare mode is intended for quick exploration, testing, or running non-build commands.

## Directory Scope

This directory is **purely configuration-oriented**, it contains no executable Python code.  
Its role is to provide **stable, validated, and maintainable configuration data** for AutoForge.

---

**Tip:**  
If you add a new schema, ensure it’s referenced in the validation logic and clearly documented so other developers know
how to use it.
