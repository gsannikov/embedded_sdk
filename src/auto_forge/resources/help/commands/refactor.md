# Refactor - JSON Recipe Guide

This document provides a guide to creating a correct `json` or `jsonc` configuration file for the **Refactoring command
**.
The tool is used to reconstruct / convert a large source tree into a new structure based on user-defined mappings and
options.

## Overview

The configuration file consists of two main sections:

1. **`defaults`** (optional): Defines global settings and behaviors applied across all folder mappings unless
   overridden.
2. **`folders`** (mandatory): A list of folder mappings specifying the source and destination directories, with optional
   overrides.

---

## Configuration File Structure

### Example

```jsonc
{
    "defaults": {
        "delete_destination_on_start": true,
        "full_debug": true,
        "file_types": [
            "c",
            "h"
        ],
        "create_grave_yard": true,
        "max_copy_depth": 3,
        "create_empty_cmake_file": true
    },
    "folders": [
        {
            "description": "Management related modules",
            "source": "libs/lib_mac",
            "destination": "mng/lib_mac",
            "file_types": [
                "c",
                "h",
                "txt"
            ]
        },
        {
            "description": "Management related modules",
            "source": "mev_i2c_app",
            "destination": "mng/i2c_app",
            "file_types": [
                "c"
            ]
        }
    ]
}
```

---

## Defaults Section

Global configuration options. These apply to all entries in the `folders` list unless overridden locally.

| Key                           | Type   | Description                                                                                 |
|-------------------------------|--------|---------------------------------------------------------------------------------------------|
| `delete_destination_on_start` | `bool` | If `true`, deletes the destination directory before copying. Default is `false`.            |
| `full_debug`                  | `bool` | If `true`, enables verbose debug output. Default is `false`.                                |
| `file_types`                  | `list` | A list of file extensions (e.g., `"c"`, `"h"`) to include. Default is `"*"` (all types).    |
| `create_grave_yard`           | `bool` | If `true`, creates a `grave_yard` folder for omitted files. Default is `false`.             |
| `max_copy_depth`              | `int`  | Maximum depth allowed for folder nesting in the destination. Use `-1` for unlimited.        |
| `create_empty_cmake_file`     | `bool` | If `true`, places an empty `CMakeLists.txt` in each destination folder. Default is `false`. |

---

## Folders Section

A list of folder mapping entries. Each entry must include `source` and `destination`. Optional settings can override
`defaults`.

| Key           | Type     | Required | Description                                                         |
|---------------|----------|----------|---------------------------------------------------------------------|
| `description` | `string` | No       | Description shown in logs.                                          |
| `source`      | `string` | Yes      | Relative source directory appended to `base_source_path`.           |
| `destination` | `string` | Yes      | Relative destination directory appended to `base_destination_path`. |
| `file_types`  | `list`   | No       | Overrides `defaults.file_types` for this folder.                    |

---

## Best Practices

- Use comments in `jsonc` files (`.jsonc` extension) for better readability.
- Validate the JSON before execution to ensure correctness using `l <json_fie>`.
- Maintain consistent naming for folder descriptions.
- Avoid deep nesting unless explicitly needed and supported via `max_copy_depth`.
- Keep a backup of the destination directory if `delete_destination_on_start` is enabled.

---

## Error Handling

If the configuration is invalid, the tool will output an error message specifying the problematic key or section. Common
issues include:

- Missing `folders` section.
- Missing `source` or `destination` in any folder mapping.
- Invalid type (e.g., using a string instead of a list for `file_types`).

---

## Running

Once your configuration is ready, pass it to the refactor tool:

```sh
refactor -r refcator_recipe.jsonc -s sourceh -d destination
```

---
