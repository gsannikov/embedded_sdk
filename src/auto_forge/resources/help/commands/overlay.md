# Overlay - JSON Recipe Guide

## 📄 Description

This tool allows you to define a conversion table, where each entry consists of two fields: `'archive'` and
`'destination'`.
These fields indicate the path and name of a file within an archive (typically a ZIP file) and its corresponding path
and name in the regular file system.

### Example mapping:

    Archive path                        Destination path
    ---------------------               ----------------------------
    path/to/source/hello.c              new/repo/sources/new_hello.c

### What does it do?

The tool supports two main operations:

1. Deploying the contents of an archive to a destination directory based on the mapping.
2. Creating an archive from destination files, using the specified archive paths.

### When is this useful?

When you need to apply modifications to a set of source files without committing them to a Git repository.
This tool allows you to spread modified files across a target path, resulting in a patched version of the repository.
Later, as files are changed or new ones are added to the list, you can regenerate an archive that’s ready to be
deployed.

---

## 🧾 Recipe Format

The tool uses a JSON or JSONC file (called a "recipe") to describe the file mapping and behavioral defaults.

### ✅ Required Fields

```json
{
  "defaults": {
    "full_debug": true,
    "break_on_errors": false,
    "create_destination_path": true,
    "max_depth": 10,
    "overwrite": "always"
  },
  "files": [
    {
      "archive": "apps/test.cmake_dummy.txt",
      "destination": "test/b/CMakList.txt"
    },
    {
      "archive": "file_in_root.txt",
      "destination": "test/a/test.txt"
    }
  ]
}