#!/bin/bash
#shellcheck disable=SC2034  # Variable appears unused.

# ------------------------------------------------------------------------------
#
# Script Name:    auto_boot.sh
# Description:    AutoForge bootstrap - onliner script.
# Author:         AutoForge team.
#
# ------------------------------------------------------------------------------

# Define HTTP and HTTPS proxy servers. These are optional and, if specified,
# will override any proxy settings exported in the shell environment.
HTTP_PROXY_SERVER="http://proxy-dmz.intel.com:911"
HTTPS_PROXY_SERVER="http://proxy-dmz.intel.com:911"

DEMO_PROJECT_URL="https://github.com/emichael72/auto_forge/tree/main/src/auto_forge/resources/demo_project
"
#
# @brief Update the environment with proxy settings if we have them defined in this scriprt.
# @return Returns 0 on overall success, else failure.
#

setup_proxy_environment() {

	# Check if HTTP_PROXY_SERVER is set and not empty
	if [ -n "$HTTP_PROXY_SERVER" ]; then
		export http_proxy=$HTTP_PROXY_SERVER
		export HTTP_PROXY=$HTTP_PROXY_SERVER
	else
		echo "HTTP proxy not set."
	fi

	# Check if HTTPS_PROXY_SERVER is set and not empty
	if [ -n "$HTTPS_PROXY_SERVER" ]; then
		export https_proxy=$HTTPS_PROXY_SERVER
		export HTTPS_PROXY=$HTTPS_PROXY_SERVER
	else
		echo "HTTPS proxy not set."
	fi
}

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
	if pip3 install git+$AUTO_FORGE_URL -q > /dev/null 2>&1; then
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
	local verbose=0
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
		echo "  -v, --verbose                Enable verbose output."
		echo "  -h, --help                   Display this help and exit."
		echo
	}

	# Parse command-line arguments
	while [[ "$#" -gt 0 ]]; do
		case "$1" in
			-w|--workspace)
				workspace_path="$2"
				shift 2
				;;
			-s|--solution)
				solution="$2"
				shift 2
				;;
			-t|--token)
				token="$2"
				shift 2
				;;
			-v|--verbose)
				verbose=1
				shift
				;;
			-h|--help)
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

	# Optional verbose output
	if [[ "$verbose" -eq 1 ]]; then
		echo "Workspace path: $workspace_path"
		echo "Solution:       $solution"
		[[ -n "$token" ]] && echo "Git token:      (provided)"
	fi

	# Set proxy if needed
	setup_proxy_environment

	# Install AutoForge
	install_autoforge || return 1

	# Run AutoForge
	autoforge_cmd=(
		python -m autoforge
		-w "$workspace_path"
		-p "$solution"
		--create-workspace
		--proxy-server "$HTTP_PROXY_SERVER"
	)

	# Append --git-token only if token is provided
	if [[ -n "$token" ]]; then
		autoforge_cmd+=(--git-token "$token")
	fi

	"${autoforge_cmd[@]}"
	ret_val=$?
}

# Entry point
main "$@"
exit $?