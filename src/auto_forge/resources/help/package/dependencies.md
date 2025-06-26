# AutoForge Dependencies Reference

This document summarizes all runtime dependencies defined in `pyproject.toml`, including their purpose and licensing
terms.

## 🧩 Runtime Dependencies

| Package             | Description                                                                                  | License (Model)    |
|---------------------|----------------------------------------------------------------------------------------------|--------------------|
| `packaging`         | Parse and compare package versions and requirements.                                         | Apache License 2.0 |
| `wheel`             | Build standard Python wheel distribution packages.                                           | MIT                |
| `opentelemetry-api` | Core telemetry API for tracing and metrics instrumentation.                                  | Apache License 2.0 |
| `opentelemetry-sdk` | SDK implementation for OpenTelemetry API (exporters, processors).                            | Apache License 2.0 |
| `colorama`          | Cross-platform colored terminal text (Windows ANSI compatibility).                           | BSD 3-Clause       |
| `rich`              | Modern terminal formatting library for text, tables, trees, progress bars, etc.              | MIT                |
| `tabulate`          | Pretty-print tabular data in various formats (plain, HTML, grid, etc.).                      | MIT                |
| `pyfiglet`          | Render text as ASCII art using FIGlet fonts.                                                 | MIT                |
| `psutil`            | Access system and process info (CPU, memory, disks, network).                                | BSD 3-Clause       |
| `toml`              | Parser for TOML configuration files.                                                         | MIT                |
| `gitpython`         | Git repository access and automation via Python interface.                                   | BSD 3-Clause       |
| `jsonpath-ng`       | Extract values from nested JSON structures using JSONPath expressions.                       | Apache License 2.0 |
| `json5`             | Parser for relaxed JSON (comments, trailing commas, etc.).                                   | MIT                |
| `jsonschema`        | Validate JSON data structures against defined schemas (Draft 4+).                            | MIT                |
| `pyaml`             | Thin wrapper around PyYAML for cleaner YAML serialization.                                   | MIT                |
| `ruamel.yaml`       | YAML parser/emitter that preserves comments and formatting.                                  | MIT                |
| `prompt_toolkit`    | Powerful interactive command-line interface (CLI) toolkit.                                   | BSD 3-Clause       |
| `jmespath`          | Query language for filtering JSON data (used by AWS tools).                                  | MIT                |
| `textual`           | Terminal UI framework for building rich text user interfaces with layout, mouse, async, etc. | MIT                |
| `whoosh`            | Pure-Python search engine library for indexing and querying text.                            | BSD                |
| `watchdog`          | Monitor filesystem changes across platforms (used in hot reload, file triggers).             | Apache License 2.0 |
| `pynput`            | Control and monitor keyboard/mouse input, used for productivity tracking.                    | GPLv3 (copyleft)   |
| `cmd2`              | Enhances standard cmd module with features like auto-completion, history, and scripting.     | MIT                |