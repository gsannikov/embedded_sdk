#!/bin/bash

# ------------------------------------------------------------------------------
#
# Script Name:    dev_set.sh
# Description:    Developer helper script for AutoForge.
#                 Installs AutoForge as an editable (writable) package,
#                 enabling local development and debugging.
# Version:        1.5
#
# ------------------------------------------------------------------------------

AUTO_FORGE_URL="https://github.com/emichael72/auto_forge.git"

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
	echo "  -i, --install           Install developer dependencies and the AutoForge package as needed."
	echo "  -v, --venv_path         Path to a Python virtual environment which was created durin install."
	echo "  -a, --auto_forge_path   Path to the auto forge local cloned project."
	echo "  -p  --pydev_ver         Optional 'pydev' specific version, default is '$pydev_ver'."
	echo "  -?, --help              Show this help message."
	echo ""
	echo "When no option is provided, only the virtual environment is activated."
}

#
# @brief Installer entry point function.
# @return Returns 0 on overall success, else failure.
#

main() {

	local mode="activate"
	local venv_path=""
	local auto_forge_path=""
	local pydev_ver="252.23892.439"
	local original_dir="$PWD"
	printf "\n"

	# Show help if no arguments were passed
	if [[ "$#" -eq 0 ]]; then
		print_help
		return 0
	fi

	# Parse command-line arguments
	while [[ "$#" -gt 0 ]]; do
		case "$1" in
			-v | --venv_path)
				if [[ -z "$2" ]]; then
					echo "Error: --venv_path requires an argument."
					return 1
				fi
				venv_path="$2"
				shift 2
				;;
			-a | --auto_forge_path)
				if [[ -z "$2" ]]; then
					echo "Error: --auto_forge_path requires an argument."
					return 1
				fi
				auto_forge_path="$2"
				shift 2
				;;
			-p | --pydev_ver)
				pydev_ver="$2"
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
				printf "Error: Unknown option: %s\n\n" "$1"
				print_help
				return 1
				;;
		esac
	done

	# Construct paths based on arguments
	local activation_script="$venv_path/bin/activate"
	local requirements_file="$auto_forge_path/requirements-dev.txt" # Optional addition requirements

	# Verifying project paths
	if [ ! -d "$auto_forge_path" ]; then
		printf "Error: auto forge project clone path '%s' not found.\n" "$auto_forge_path"
		return 1
	fi

	# Validate virtual environment, create if missing
	if [ ! -d "$venv_path" ]; then
		printf "Warning: Virtual environment directory '%s' not found. Creating...\n" "$venv_path"
		if ! python3 -m venv "$venv_path" &>/dev/null; then
			printf "Error: Could not create virtual environment in '%s'\n" "$venv_path"
			return 1
		fi
	fi

	# Validate activation script
	if [ ! -f "$activation_script" ]; then
		printf "Error: Activate script '%s' not found.\n", "$activation_script"
		return 1
	fi

	printf "Activating '%s'.\n" "$activation_script"

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

	printf "Running development steps in '%s'..\n" "$auto_forge_path"
	cd "$auto_forge_path" || return 1

	# Clone AutoForge if package path is missing
	if [ ! -d "$auto_forge_path" ]; then
		printf "AutoForge package not found at '%s', cloning silently..." "$auto_forge_path"
		git clone --quiet "$AUTO_FORGE_URL" "$auto_forge_path" || {
			printf "Error: Failed to clone AutoForge repository into '%s'." "$auto_forge_path"
			return 1
		}
	fi

	printf "Installing requirements and 'pydev' version %s support.\n" "$pydev_ver"

	# Attempting to install fresh 'pydev' at a specific revision to our venv.
	pip uninstall pydevd-pycharm -y &>/dev/null
	pip install pydevd-pycharm~="$pydev_ver" &>/dev/null || {
		printf "Warning: 'pydev' was not installed successfully.\n"
	}

	# Installing other package requirements if we have requirements
	if [ -f "$requirements_file" ]; then
		pip install --force-reinstall -r "$requirements_file" &>/dev/null || return 1
	else
		printf "Requirements file '%s' not found, skipping step\n" "$requirements_file"
	fi

	printf "Switching to writable package in '%s'\n" "$auto_forge_path"

	cd "$auto_forge_path" || {
		print "Error: Could not switch to auto-forge clone path in: '%s'\n" "$auto_forge_path"
		return 1
	}

	pip install -e . --force-reinstall &>/dev/null || {
		print "Error: 'pip install' did not complete successfully\n"
		return 1
	}

	# Last validation that auto-forge is installed locally and it is readable
	show_auto_forge || return 1

	# Restore original directory
	cd "$original_dir" || return 1

	printf "\nAll done, development environment installed.\n\n"
	return 0
}

#
# @brief Invoke the main function with command-line arguments.
# @return The exit status of the main function.
#

main "$@"
