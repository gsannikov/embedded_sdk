# AutoForge Demo: 'Userspace' Project Walkthrough

Welcome to the **AutoForge 'Userspace' Demo Walkthrough**. This guide will help you evaluate AutoForge in a real
development environment by running a fully functional build/test loop on a refactored version of Intel's internal
`userspace` project.

---

## ✨ Introduction

This demo provides a **non-intrusive** way to evaluate the `userspace` project safely. All operations are performed
within an isolated workspace, without modifying your existing project clone — allowing you to test AutoForge without
replacing your current `userspace` build flow.

* AutoForge will **never**:
    * Delete files
    * Rename paths,
    * Modify search paths
    * Performa operations using  `sudo`,
    * Install system packages, or interfere with your system settings in any way.
* AutoForge **may** add an alias to your `~/.bashrc` (or equivalent shell file) based on the
  workspace [sequence recipe JSON.](https://github.com/intel-innersource/firmware.ethernet.devops.auto_forge/blob/main/src/auto_forge/resources/samples/userspace/workspace_sequence.jsonc)
* The generated workspace is approximately **200 MB**, while compiled artifacts (both release and debug) may take up to
  **4 GB** of disk space.
* This setup flow has been tested on **Fedora versions 40 through 42**, as well as on **Microsoft WSL (Ubuntu)**.

---

## 🚀Getting Started

### Prerequisites

- Since the demo is designed to **overlay** an existing repository, you’ll need to **have a working production clone**
  of the `userspace` project available beforehand — typically located at:
  ```
  ~/projects/mmg_new_repo
  ```

- **Intel `dt` Developer Tool**: An internal, proprietary command-line utility that links your private GitHub account
  with your Intel SSO, enabling access to Intel’s private repositories.

  **Essential System Packages**: These are typically preinstalled. If not, you can install them manually using your
  package manager.

  > 📦 Install via package manager:  
  `sudo dnf install cmake ninja-build glib2-devel`

### Setup Instructions

Copy and paste the following into your terminal:

```bash

# The following command does quite a bit. Here's a breakdown:
#
# 1. Uses the 'dt' tool to retrieve a GitHub token for accessing Intel private repositories.
# 2. Downloads the 'bootstrap' script from the package Git repo using 'curl' and executes it.
#
# The 'bootstrap' script then:
#
# 3. Installs the latest AutoForge package into the user scope (via pip).
# 4. Loads the built-in 'userspace' sample.
# 5. Uses a solution also named 'userspace' from this sample.
# 6. Creates a new workspace in a local folder named 'ws'.
# 7. Runs the 'workspace_sequence ' defined by the solution, which:
#    - Verifies required tools are installed,
#    - Creates a dedicated Python virtual environment,
#    - Installs required Python packages,
#    - Performs any additional setup defined by the solution.
#
# ⚠ No 'sudo' is required, and no files are deleted without consent.

GITHUB_REPO="intel-innersource/firmware.ethernet.devops.auto_forge"
GITHUB_TOKEN=$(dt github print-token https://github.com/${GITHUB_REPO})

curl -sSL \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Cache-Control: no-store" \
  "https://raw.githubusercontent.com/${GITHUB_REPO}/main/src/auto_forge/resources/shared/bootstrap.sh" \
  | bash -s -- \
      -n userspace \
      -w ws \
      -s workspace_sequence \
      -p "<SAMPLES_PATH>/userspace"
```

### What Happens Next:

- A new workspace will be created under a directory named `ws`, relative to the location where you ran the install
  command.
- Your shell startup file (e.g., `.bashrc` for Bash) will be patched with an alias for quick access to `UserSpace`.
- You’ll be prompted to reopen your terminal for the new alias to take effect.

---

## ⚖️ Evaluation First Steps

* Launch the `Userspace` solution in an interactive shell by typing:  `us` .
* Inside the shell, run the `usgen` command. This will:
    1. Populate the otherwise bare workspace with source files based on the restructuring rules
       in [refactor.jsonc](https://github.com/intel-innersource/firmware.ethernet.devops.auto_forge/blob/main/src/auto_forge/resources/samples/userspace/refactor.jsonc).
    2. Inject missing CMake files by executing the deploy steps defined
       in [deploy.jsonc](https://github.com/intel-innersource/firmware.ethernet.devops.auto_forge/blob/main/src/auto_forge/resources/samples/userspace/deploy.jsonc).
* 🧪 Interesting Commands to Try
    * ? or help: Show available commands via the integrated Markdown viewer.
    * `showsln`: View the preprocessed Userspace solution file using the JSON viewer.
    * `shoenv`: Display the augmented environment variables table.
    * `busb`, `busr`: Build the Userspace libraries in release or debug mode.
    * `showtelem`: Show runtime telemetry data from recent operations.

---

## 📁 Files in the Userspace Demo

| File Name                  | Description                                                                                                     |
|----------------------------|-----------------------------------------------------------------------------------------------------------------|
| `commands`                 | Sample “hello world” command demonstrating how to construct and register new commands                           |
| `help/commands`            | Markdown help files describing the dynamically loaded `hello_world.py` command, invoked via `hello --tutorials` |
| `aliases.jsonc`            | Extra AutoForge commands added to the `userspace` solution (not related to shell aliases)                       |
| `deploy.jsonc`             | Steps for generating missing CMake files and placing them correctly                                             |
| `deploy.zip`               | Archived deploy artifacts (optional override of deploy.jsonc logic)                                             |
| `refactor.jsonc`           | Source mapping and transformation logic for importing from `mmg_new_repo`                                       |
| `solution.jsonc`           | Main solution descriptor — defines workspace structure and targets                                              |
| `variables.jsonc`          | User-defined and system-injected variables used throughout the flow                                             |
| `workspace_sequence.jsonc` | Defines the initialization and command sequence for setting up the workspace                                    |
| `storage`                  | Temporary path for AI related resources                                                                         |

---

Enjoy your AutoForge journey.

Contributions, ideas, and suggestions from any developer are always welcome 🙏 , feel free to open a pull request or
start a discussion!
