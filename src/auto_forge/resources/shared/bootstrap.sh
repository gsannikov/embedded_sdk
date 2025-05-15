#!/bin/bash

# ------------------------------------------------------------------------------
#
# Script Name:    bootstrap.sh
# Description:    AutoForge bootstrap installer.
#
# ------------------------------------------------------------------------------

AUTO_FORGE_URL="https://github.com/emichael72/auto_forge.git"

#
# @brief Install AutoForge python object.
# @return Returns 0 on overall success, else failure.
#

install_autoforge() {

	# Check for Python 3.9 or higher
	if ! python3 --version | grep -qE 'Python 3\.(9|[1-9][0-9])'; then
		echo "Python 3.9 or higher is not installed."
		return 1
	fi

	# Check if pip is installed
	if ! command -v pip3 &> /dev/null; then
		echo "pip is not installed."
		return 1
	fi

	# Uninstall auto_forge if it exists, without any output
	pip3 uninstall -y auto_forge &> /dev/null

	# Install auto_forge from the provided URL, without any output
	if pip3 install git+$AUTO_FORGE_URL -q --force-reinstall > /dev/null 2>&1; then
		# Check if installation was successful
		if pip3 list 2> /dev/null | grep -q 'auto_forge'; then
			return 0
		else
			echo "Failed to install auto_forge."
			return 1
		fi
	else
		echo "Failed to install auto_forge."
		return
	fi
}

#
# @brief Installer entry point function.
# @return Returns 0 on overall success, else failure.
#

main() {

	local ret_val=0
	local workspace_path=""
	local solution=""
	local token=""

	# Help message function
	display_help() {
		echo
		echo "Usage: $0 [options]"
		echo
		echo "  -w, --workspace [path]       Destination workspace path."
		echo "  -s, --solution  [path/url]   Solution to use (local path or URL)."
		echo "  -t, --token     [token]      Optional Git token for remote solution."
		echo "  -h, --help                   Display this help and exit."
		echo
	}

	# Parse command-line arguments
	while [[ "$#" -gt 0 ]]; do
		case "$1" in
		-w | --workspace)
			workspace_path="$2"
			shift 2
			;;
		-s | --solution)
			solution="$2"
			shift 2
			;;
		-t | --token)
			token="$2"
			shift 2
			;;
		-h | --help)
			display_help
			return 0
			;;
		*)
			printf "\nError: Unknown option: %s\n\n" "$1"
			display_help
			return 1
			;;
		esac
	done

	# Validate required arguments
	if [[ -z "$workspace_path" ]]; then
		printf "\nError: Workspace not provided (-w).\n\n"
		display_help
		return 1
	fi
	if [[ -z "$solution" ]]; then
		printf "\nError: Solution not specified (-s).\n\n"
		display_help
		return 1
	fi

	# Install AutoForge using pip
	clear
	printf "\nPlease wait while AutoForge is being downloaded and installed...\r"
	install_autoforge || return 1

	# Execute AutoForge build system
	autoforge_cmd=(
		python3 -m auto_forge
		-w "$workspace_path"
		-p "$solution"
		--create-workspace
		--proxy-server "$HTTP_PROXY_SERVER"
	)

	# Append --git-token only if token is provided
	if [[ -n "$token" ]]; then
		autoforge_cmd+=(--git-token "$token")
	fi

	# Running AutoForge using the specified solution
	"${autoforge_cmd[@]}"
	ret_val=$?

	# Quietly uninstall auto_forge from the user environment, suppressing all output
	pip3 uninstall -y auto_forge &> /dev/null

	return $ret_val
}

# Entry point
main "$@"
exit $?
