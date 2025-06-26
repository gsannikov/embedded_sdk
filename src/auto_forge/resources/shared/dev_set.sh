#!/bin/bash

# ------------------------------------------------------------------------------
#
# Script Name:    dev_set.sh
# Description:    AutoForge developer helper.
# Version:        1.1
#
# ------------------------------------------------------------------------------

#
# @brief  Checks if Auto Forge is installed in the current Python environment,
#         and verifies it is in editable mode (i.e., installed from a local path).
#
# @output Prints the installed version and path, or an error message.
# @return 0 if installed and editable
#         1 if not installed or not editable
#

show_auto_forge() {

    local pip_bin
    pip_bin=$(command -v pip) || {
        echo "Error: pip not found in PATH." >&2
        return 1
    }

    local awk_bin
    awk_bin=$(command -v awk) || {
        echo "Error: awk not found in PATH." >&2
        return 1
    }

    local info
    info="$("$pip_bin" list)" || {
        echo "Error: Failed to run pip list." >&2
        return 1
    }

    # shellcheck disable=SC2016
    echo "$info" | "$awk_bin" '
    /^auto_forge[[:space:]]/ {
      version = $2
      path = $NF
      if (version == path) {
        print "Error: Auto Forge is installed, but not in editable mode (no writable path)." > "/dev/stderr"
        exit 1
      } else {
        printf "Auto Forge version %s is installed to %s\n", version, path
        exit 0
      }
    }
    END {
      if (NR == 0) {
        print "Error: Auto Forge is not installed in this environment." > "/dev/stderr"
        exit 1
      }
    }'
}

#
# @brief Print tool usage.
#

print_help() {

    echo "Usage: source $(basename "$0") [OPTION]"
    echo ""
    echo "Options:"
    echo "  -i, --install       Install developer dependencies and the AutoForge package as needed."
    echo "  -p, --project_name  Project name to use."
    echo "  -?, --help          Show this help message."
    echo ""
    echo "When no option is provided, only the virtual environment is activated."
}

#
# @brief Installer entry point function.
# @return Returns 0 on overall success, else failure.
#

main() {

    local mode="activate"
    local project_name=""

    # Show help if no arguments were passed
    if [[ "$#" -eq 0 ]]; then
        print_help
        return 0
    fi

    # Parse command-line arguments
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
        -p | --project_name)
            if [[ -z "$2" ]]; then
                echo "Error: --project_name requires an argument."
                return 1
            fi
            project_name="$2"
            shift 2
            ;;
        -i | --install)
            mode="install"
            shift
            ;;
        -h | --help | -\?)
            print_help
            return 0
            ;;
        *)
            printf "\nError: Unknown option: %s\n\n" "$1"
            print_help
            return 1
            ;;
        esac
    done

    # We should have been executed from the test zone root path.
    local af_project_base="$PWD"

    # Construct paths based on arguments and current path
    local package_path="$af_project_base/auto_forge"
    local project_path="$af_project_base/$project_name"
    local venv_path="$project_path/ws/.venv"
    local activation_script="$venv_path/bin/activate"
    local requirements_file="$package_path/requirements-dev.txt" # Optional addition requirements

    # Verifying project paths
    if [ ! -d "$project_path" ]; then
        printf "Error: Project path '%s' not found.\n" "$project_path"
        return 1
    fi

    # Validate venv and activation script
    if [ ! -d "$venv_path" ]; then
        printf "Error: Virtual environment directory '%s' not found.\n" "$venv_path"
        return 1
    fi

    if [ ! -f "$activation_script" ]; then
        printf "Error: Activate script '%s' not found.\n", "$activation_script"
        return 1
    fi

    # Activate Python venv
    # shellcheck disable=SC1090
    source "$activation_script" || {
        printf "Error: non-zero value after sourcing '%s'\n" "$activation_script"
        return 1
    }

    # Verify we have pip
    if ! command -v pip >/dev/null 2>&1; then
        printf "Error: pip is not available in the virtual environment.\n"
        return 1
    fi

    # If not install mode, we're done
    if [ "$mode" != "install" ]; then
        return 0
    fi

    printf "\nRunning development steps for project '%s'..\n" "$project_name"

    # Clone AutoForge if package path is missing
    if [ ! -d "$package_path" ]; then
        printf "AutoForge package not found at '%s', cloning silently..." "$package_path"
        git clone --quiet https://github.com/emichael72/auto_forge.git "$package_path" || {
            printf "Error: Failed to clone AutoForge repository into '%s'." "$package_path"
            return 1
        }
    fi

    printf "Installing requirements and 'pydev' support.\n"

    # Attempting to install fresh 'pydev' at a specific revision to our venv.
    pip uninstall pydevd-pycharm -y &>/dev/null
    pip install pydevd-pycharm~=251.25410.122 &>/dev/null || {
        printf "Warning: 'pydev' was not installed successfully.\n"
    }

    # Installing other package requirements if we have requirements
    if [ -f "$requirements_file" ]; then
        pip install --force-reinstall -r "$requirements_file" &>/dev/null || return 1
    else
        printf "Requirements file '%s' not found, skipping step\n" "$requirements_file"
    fi

    printf "Switching venv to writable auto-forge '%s'\n" "$package_path"

    cd "$package_path" || {
        print "Error: Could not switch to auto-forge local clone in: '%s'\n" "$package_path"
        return 1
    }

    pip install -e . --force-reinstall &>/dev/null || {
        print "Error: 'pip install' did not complete successfully\n"
        return 1
    }

    # Last validation that auto-forge is installed locally and it is readable
    show_auto_forge || return 1

    printf "\nAll done, development environment installed.\n\n"
    return 0
}

#
# @brief Invoke the main function with command-line arguments.
# @return The exit status of the main function.
#

main "$@"
