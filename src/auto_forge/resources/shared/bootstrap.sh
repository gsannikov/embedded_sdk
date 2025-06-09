#!/bin/bash

# ------------------------------------------------------------------------------
#
# Script Name:    bootstrap.sh
# Description:    AutoForge bootstrap installer.
# Version:        1.0
#
# ------------------------------------------------------------------------------

AUTO_FORGE_URL="https://github.com/emichael72/auto_forge.git"

#
# @brief Installs AutoForge package
# @return Returns 0 on overall success, else failure.
#

install_autoforge_package() {

	local package_url="$1"

	# Validate inputs
	if [[ -z "$package_url" ]]; then
		printf "Usage: install_autoforge_package <package_url>\n"
		return 1
	fi

	# Simple in place text loging helper.
	_log_line() {
		echo -ne "\r$(tput el)$*"
	}

	# Check for Python 3.9 or higher
	if ! python3 --version | grep -qE 'Python 3\.(9|[1-9][0-9])'; then
		_log_line "Error: Python 3.9 or higher are required."
		return 1
	fi

	# Upgrade pip
	python3 -m pip install --upgrade pip >/dev/null 2>&1 || {
		_log_line "Error: Python 'pip' could not be upgraded."
		return 1
	}

	# Quietly uninstall auto_forge if it exists
	pip3 uninstall -y auto_forge &>/dev/null

	# Install auto_forge from the provided URL, without any output
	if pip3 install git+"$package_url" -q --force-reinstall >/dev/null 2>&1; then
		# Check if installation was successful
		if pip3 list 2>/dev/null | grep -q 'auto_forge'; then
			return 0
		else
			_log_line "Error: package was not found post installation."
			return 1
		fi
	else
		_log_line "Error: 'pip install' did not complete successfully."
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
	local solution_name=""
	local sequence_name=""
	local auto_forge_url=""
	local package=""
	local token=""

	# Help message function
	display_help() {
		echo
		echo "Usage: $0 [options]"
		echo
		echo "  -w, --workspace 	[path]      Destination workspace path."
		echo "  -n, --name      	[name]      Solution name to use."
		echo "  -p, --package   	[path/url]  Solution package to use (local path or URL)."
		echo "  -s, --sequence  	[name]      Reference sequence name in specified solution"
		echo "  -t, --token     	[token]     Optional Git token for remote solution."
		echo "  -a, --auto_forge    [url]    	Optional override AutoForge package URL."
		echo "  -h, --help                  	Display this help and exit."
		echo
	}

	# Parse command-line arguments.
	while [[ "$#" -gt 0 ]]; do
		case "$1" in
		-w | --workspace)
			workspace_path="$2"
			shift 2
			;;
		-n | --name)
			solution_name="$2"
			shift 2
			;;
		-p | --package)
			package="$2"
			shift 2
			;;
		-s | --sequence)
			sequence_name="$2"
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

	# Declare and validate mandatory arguments.
	declare -A required_args=(
		[workspace_path]="Workspace path (-w, --workspace)"
		[solution_name]="Solution name (-n, --name)"
		[package]="Solution package (-p, --package)"
		[sequence_name]="Sequence name (-s, --sequence)"
	)

	for var in "${!required_args[@]}"; do
		if [[ -z "${!var}" ]]; then
			printf "\nError: %s not provided.\n\n" "${required_args[$var]}"
			display_help
			return 1
		fi
	done

	# Use the default internal AutoForge package URL if none was specified
	if [[ -z "$auto_forge_url" ]]; then
		auto_forge_url="$AUTO_FORGE_URL"
	fi

	echo -ne '\e[2J\e[H\e[?25l' # Clear screen and hide cursor
	printf "\n\nPlease wait while 🛠️  AutoForge is being downloaded and installed...\r"

	# Install AutoForge using 'pip'
	install_autoforge_package "$auto_forge_url" || return 1

	# Construct the Package arguments
	autoforge_cmd=(
		python3 -m auto_forge
		-w "$workspace_path"
		-n "$solution_name"
		-p "$package"
		-s "$sequence_name"
	)

	# Pass the environment proxy definition to AutoFore.
	if [[ -n "$HTTP_PROXY_SERVER" ]]; then
		autoforge_cmd+=(--proxy-server "$HTTP_PROXY_SERVER")
	fi

	if [[ -n "$token" ]]; then
		autoforge_cmd+=(--git-token "$token")
	fi

	# Run AutoForge in sequence execution mode
	"${autoforge_cmd[@]}"
	ret_val=$?

	# Quietly uninstall auto_forge from the global scope to restrict it as possible  only to virtual environments.
	pip3 uninstall -y auto_forge &>/dev/null
	echo -ne '\e[?25h' # Restore cursor.
	return $ret_val
}

# Entry point
main "$@"
exit $?
