# AutoForge Solution Configuration Guide

## Overview

The AutoForge Solution Configuration File defines a structured, modular, and dynamic way to represent complex software
and hardware build environments. Inspired by systems like Microsoft Visual Studio, it allows multiple projects and
configurations to coexist under a single top-level solution.

This document explains how to author a solution file, reference other elements dynamically to create flexible, maintainable build definitions.

---

## Structure

### 1. Solutions

Top-level entities encapsulating one or more projects.

### 2. Projects

Represent logical components (software or hardware) within a solution.

### 3. Configurations

Define individual build targets such as `debug`, `release`, or custom variants, each specifying tools, flags, paths,
etc.

#### Example:

```jsonc
"solutions": {
  "OurSolution": {
    "projects": {
      "ZephyrRTOS": {
        "configurations": {
          "debug": { ... },
          "release": { ... }
        }
      },
      "Bootloader": {
        "configurations": { ... }
      }
    }
  }
}
```

---

## Referencing Logic

AutoForge supports dynamic variable referencing for values across scopes, reducing redundancy.

### Local Referencing

Refer to keys in the current scope.

```jsonc
"board": "some_board",
"cmake_top_level_path": "/home/dev/<$ref_board>"
```

### Alternate Local Referencing

Refers to a field using a collection path.

```jsonc
"cmake_top_level_path": "/home/dev/<$ref_configurations[].board>"
```

### Explicit Referencing

Access keys from other solutions, projects, or configurations.

```jsonc
"dummy": "<$ref_solutions[OurSolution].projects[Zephyr].configurations[debug].board>"
```

### Reference Syntax

All references are enclosed within `"<$ref_...>"`.

---

## Derivation Logic

Configurations can inherit from others using the `data` key.

### Syntax:

```jsonc
"data": "<$derived_from_solutions[Solution].projects[Project].configurations[Config]>"
```

### Example:

```jsonc
{
  "name": "debug_extended",
  "data": "<$derived_from_solutions[OurSolution].projects[Zephyr].configurations[release]>",
  // Additional overrides go here
}
```

The system merges the referenced configuration and applies any overrides.

---

## Comments

This format supports inline comments (`//`, `/* */`), useful for documentation during development. Parsers should ignore
them in production.

---

## Summary

The AutoForge solution structure is a powerful, modular, and flexible system designed for defining build environments
declaratively. It empowers teams to:

- Reuse settings via references
- Adapt configurations using conditional logic
- Extend setups through derivation

This promotes clarity, maintainability, and scalability across complex development workflows.
