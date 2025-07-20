# 📁 Files in the `shared` Path

This directory contains shared utility scripts, primarily written in shell (compatible with both `bash` and `zsh`),
designed to assist with deploying and running the AutoForge solution.

| File Name      | Description                                                                                                                   |
|----------------|-------------------------------------------------------------------------------------------------------------------------------|
| `viewers`      | Terminal-based Markdown and JSON viewers built with Textual.                                                                  |
| `bootstrap.sh` | AutoForge installer entrypoint, intended to be run via `curl` as a one-liner for quick copy-paste execution.                  |
| `dev_set.sh`   | Developer helper script for installing AutoForge in editable mode, enabling local development and debugging.                  |
| `env.sh` *     | Minimal solution entrypoint script that sources the Python virtual environment and launches AutoForge in the local workspace. |
| `installer.sh` | Secondary installation script used by `bootstrap.sh`. Can be either sourced or executed directly.                             |

> * This script is automatically copied to the root of any newly created workspace.
