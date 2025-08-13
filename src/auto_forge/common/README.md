# AutoForge Common Classes

This directory contains **basic, standalone classes and definitions** that are **independent** of all other AutoForge
modules. They form the foundational building blocks used across the package.

## Design Principles

- **Zero Internal Dependencies**  
  Classes in this directory **must not** import or depend on any other module within AutoForge.  
  This prevents circular import issues and ensures these definitions can be used anywhere in the codebase.

- **Cross-Package Availability**  
  These classes and type definitions (e.g., those in [`local_types.py`](./local_types.py)) are referenced by modules
  throughout AutoForge.  
  Keeping them dependency-free guarantees that they can be safely imported in any context.

- **Minimal Scope & High Reusability**  
  Modules here are designed to be **small, focused, and reusable**, providing core data structures, constants, and
  simple helper classes.

- **Internal Documentation**
  Each file and class includes docstrings describing its purpose and usage.  
  This README provides an overview without duplicating that detail.

## Usage Notes

When adding a new module here:

1. Ensure it has **no imports** from other AutoForge modules.
2. Keep the design simple and generic avoid adding solution-specific logic.
3. Favor readability and maintainability over complexity.
4. Provide clear docstrings so the purpose and usage are immediately clear.

## Directory Scope

The `common` directory is **the base layer** of AutoForge’s architecture.  
Breaking the independence rule here risks introducing **circular import errors** and can compromise maintainability.

