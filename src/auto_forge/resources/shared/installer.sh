#!/bin/bash
# shellcheck disable=SC2059 ## Don't use variables in the printf format string. Use printf '..%s..' "$foo".

# ------------------------------------------------------------------------------
#
# Script Name:    installer.sh
# Description:    Helper script for launching AutoForge bootstrap.
#                 This script could be sourced oe directly invoked.
# Version:        1.0
#
# ------------------------------------------------------------------------------

AUTO_FORGE_BOOTSTRAP_URL="https://raw.githubusercontent.com/emichael72/auto_forge/main/src/auto_forge/resources/shared/bootstrap.sh"

#
# @brief Checks if the script was sourced or executed directly.
# This function is compatible with both bash and zsh. It determines if the
# current script was sourced or executed directly.
# @return 0 if the script was sourced, 1 if executed directly.
#

is_sourced() {

	# Check for Bash / Zsh compatibility
	if [ -n "$ZSH_VERSION" ]; then
		case $ZSH_EVAL_CONTEXT in *:file) return 0 ;; esac
	elif [ -n "$BASH_VERSION" ]; then
		# shellcheck disable=SC2128 ## Expanding an array without an index only gives the first element.
		case $0 in "$BASH_SOURCE") return 1 ;; esac
	else
		# If not Bash or Zsh, fallback method
		case $(basename -- "$0") in "$(basename -- "${BASH_SOURCE[0]:-$0}")") return 1 ;; esac
	fi
	return 0
}

#
# @brief Install the AutoForge package which will then fetch the required solution package and take over the
#        installation process.
# @return 0 if the script was sourced, 1 if executed directly.
#

af_install() {

	local dest_workspace_path="" # Path for the new workspace
	local solution_name=""   # In this context: also the sample path name
	local solution_package=""
	local bootstrap_url=""
	local auto_start=false
	local allow_non_empty=false
	local verbose=false
	local workspace_name=""

	# Define ANSI style variables
	BOLD_GREEN="\033[1;32m"
	DIM="\033[2m"
	RESET="\033[0m"

	# Display help message
	_display_help() {
		echo
		echo "Usage: $0 [options]"
		echo
		echo "  -w, --workspace    [path]     Destination workspace path."
		echo "  -n, --name         [name]     Solution name to use."
		echo "  -p, --package      [path/url] Optional solution package URL or local path."
		echo "  -b, --bootstrap    [url]      Optional override for the bootstrap URL."
		echo "      --auto_start              Automatically start the package upon successful setup."
		echo "      --allow_non_empty         Allow using non-empty directories by clearing their contents first."
		echo "      --verbose                 Enable more detailed output."
		echo "  -h, --help                    Display this help message and exit."
		echo
		printf "Examples:\n\n"
		printf "To install the AutoForge 'demo' solution sample and automatically start it upon completion:\n"
		printf "  ${BOLD_GREEN}  af_install${RESET} -w ${DIM}/home/bill_g/projects/demo${RESET} -n ${DIM}demo${RESET} --allow_non_empty --auto_start\n"
		printf "To install the AutoForge 'iphone' solution from a specific package URL on GitHub:\n"
		printf "  ${BOLD_GREEN}  af_install${RESET} -w ${DIM}/home/tim_c/projects/iphone${RESET} -n ${DIM}iphone${RESET} -p ${DIM}https://raw.githubusercontent.com/tim_c/iphone/solution${RESET} --verbose\n" printf "\n"
		printf "\n"

	}

	# Show help if no arguments were passed
	if [[ "$#" -eq 0 ]]; then
		_display_help
		return 0
	fi

	# Parse command-line arguments.
	while [[ "$#" -gt 0 ]]; do
		case "$1" in
		-w | --workspace)
			dest_workspace_path="$2"
			shift 2
			;;
		-n | --name)
			solution_name="$2"
			shift 2
			;;
		-p | --packge)
			solution_package="$2"
			shift 2
			;;
		-b | --bootstrap)
			bootstrap_url="$2"
			shift 2
			;;
		--auto_start)
			auto_start=true
			shift
			;;
		--allow_non_empty)
			allow_non_empty=true
			shift
			;;
		--verbose)
			verbose=true
			shift
			;;
		-h | --help)
			_display_help
			return 0
			;;
		*)
			printf "\nError: Unknown option: %s\n\n" "$1"
			_display_help
			return 1
			;;
		esac
	done

	# Print wrapper that tales into account verbosity flag
	_verbose_print() {
		if [[ "$verbose" == true ]]; then
			printf "%s\n" "$@"
		fi
	}

	# Validate inputs
	# Check that destination path looks like a directory path (not empty and not ending in a slash)
	if [[ -z "$dest_workspace_path" || "$dest_workspace_path" =~ /$ ]]; then
		printf "Error: Invalid directory path format: '%s'.\n" "$dest_workspace_path"
		return 1
	fi

	# Checks that the workspace path is at least 3 levels deep from root (prevent excremental deletion if system resources)
	local depth
	depth=$(echo "$dest_workspace_path" | grep -o "/" | wc -l)
	if ((depth < 3)); then
		printf "Error: Workspace path : '%s' must be at least 3 levels deep from root.\n" "$dest_workspace_path"
		return 1
	fi

	# Extract top path component which will serve as the workspace name.
	workspace_name=$(basename "$dest_workspace_path")
	workspace_parent_path=$(dirname "$dest_workspace_path")

	# Warn or fail if destination path already exists.
	if [[ -e "$dest_workspace_path" ]]; then
		if [[ "$allow_non_empty" == true ]]; then
			if [[ "$verbose" == true ]]; then
				printf "Warning: Workspace path '%s' already exists and will be deleted.\n" "$dest_workspace_path"
				sleep 2
			fi
			# Forcefully remove existing workspace
			rm -rf "$dest_workspace_path" || {
				printf "Error: Failed to remove existing workspace at '%s'.\n" "$dest_workspace_path"
				return 1
			}
		else
			printf "Error: Destination path '%s' already exists. Use --allow_non_empty to override.\n" "$dest_workspace_path"
			return 1
		fi
	fi

	# Create the workspace directory.
	_verbose_print "Workspace: '$workspace_name', will be created in '$workspace_parent_path'."

	mkdir -p "$dest_workspace_path" || {
		printf "Error: Could not create the workspace path '%s'.\n" "$dest_workspace_path"
		return 1
	}

	# Go into the the workspace parent path.
	cd "$workspace_parent_path" || {
		printf "Error: Could not switch to the workspace parent path '%s'.\n" "$workspace_parent_path"
		return 1
	}

	# If no solution package was specified, use the <samples> placeholder.
	# AutoForge will resolve it to a built-in sample directory.
	if [[ -z "$solution_package" ]]; then
		_verbose_print "No solution package specified, attempting to load a built-in sample..."
		solution_package="<samples>/$solution_name"
	fi

	# Use the default internal bootstrap URL if none was specified
	if [[ -z "$bootstrap_url" ]]; then
		_verbose_print "Using internally defined bootstrap URL: $AUTO_FORGE_BOOTSTRAP_URL"
		bootstrap_url="$AUTO_FORGE_BOOTSTRAP_URL"
	fi

	# Execute AutoForge bootstrap and point it to the required solution and let it handle the rest.
	if ! curl -sSL -H "Cache-Control: no-store" "$bootstrap_url" |
		bash -s -- -p "$solution_package" -w "$workspace_name" -n "$solution_name" -s create_environment_sequence; then
		echo "Bootstrap failed"
		return 1
	fi

	# Basic verification that installation succeeded.
	if [[ ! -f "$dest_workspace_path/env.sh" ]]; then
		printf "Error: Workspace startup script 'env.sh' was not found after installation.\n"
		return 1
	fi

	# Auto start if specified
	if [[ "$auto_start" == true ]]; then
		cd "$dest_workspace_path" || return 1
		./env.sh
	fi
}

#
# @brief https://en.wikipedia.org/wiki/Entry_point
# @param "$@" Command-line arguments passed to the script.
# Notes should always be the last function in this file.
# @return 0 | 1
#

main() {

	local ret_val=0

	# We can only be sourced.
	if ! is_sourced; then
		af_install "$@"
		ret_val=$?
		exit $ret_val
	fi

	return $ret_val

}

#
# @brief Invoke the main function with command-line arguments.
# @return The exit status of the main function.
#

main "$@"
return $?
