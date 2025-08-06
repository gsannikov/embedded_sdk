#!/bin/bash

# ------------------------------------------------------------------------------
#
# Script Name:    bootstrap.sh
# Description:    AutoForge bootstrap installer.
# Version:        1.5
#
# ------------------------------------------------------------------------------

AUTO_FORGE_URL="https://github.com/emichael72/auto_forge.git"

#
# @brief Installs the AutoForge package
# @return 0 on success, non-zero on failure.
#

install_autoforge_package() {
	local package_url="$1"
	local ret_val=0

	# Validate input
	if [[ -z "$package_url" ]]; then
		printf "Usage: install_autoforge_package <package_url>\n"
		return 1
	fi

	# In-place status message helper
	_log_line() {
		echo -ne "\r$(tput el)$*"
	}

	# Print all lines starting from the first "ERROR:" and onward
	# Avoids awk/sed for maximum compatibility
	_print_errors_to_end() {
		local error_found=0
		while IFS= read -r line || [[ -n "$line" ]]; do
			if [[ $error_found -eq 0 && "$line" == ERROR:* ]]; then
				error_found=1
			fi
			if [[ $error_found -eq 1 ]]; then
				echo "$line"
			fi
		done <"$1"
	}

	# Require Python 3.9 or higher
	if ! python3 --version | grep -qE 'Python 3\.(9|[1-9][0-9])'; then
		_log_line "Error: Python 3.9 or higher is required."
		return 1
	fi

	# Upgrade pip quietly
	python3 -m pip install --upgrade pip --break-system-packages --no-warn-script-location >/dev/null 2>&1 || {
		_log_line "Error: Failed to upgrade pip."
		return 1
	}

	# Attempt to uninstall any existing auto_forge package
	pip3 uninstall -y auto_forge &>/dev/null

	# Determine a writable temp directory
	tmp_dir="${TMPDIR:-$(getconf DARWIN_USER_TEMP_DIR 2>/dev/null || echo /tmp)}"
	[[ -w "$tmp_dir" ]] || tmp_dir="/tmp"

	# Define log file path using the package defined prefix
	log_file="${tmp_dir}/__AUTO_FORGE_bootstrap.log"

	# Attempt installation and capture stderr to log
	if pip3 install "git+$package_url" -q --force-reinstall --break-system-packages --no-warn-script-location 2>"$log_file"; then
		if ! pip3 list 2>/dev/null | grep -q 'auto_forge'; then
			_log_line "Error: Package appears to be missing after installation."
			ret_val=1
		fi
	else
		_log_line "Error: 'pip install' did not complete successfully. Showing the final lines of output:"
		echo
		_print_errors_to_end "$log_file"
		echo
		ret_val=1
	fi

	# Clean up
	rm -f "$log_file" 2>/dev/null
	return $ret_val
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
	local original_dir="$PWD"

	# Help message function
	display_help() {
		echo
		echo "Usage: $0 [options]"
		echo
		echo "  -w, --workspace 	  [path]          Destination workspace path."
		echo "  -n, --name      	  [name]          Solution name to use."
		echo "  -p, --package   	  [path/url]      Solution package to use (local path or URL)."
		echo "  -s, --sequence  	  [json/prop]     Solution sequence name required for preparing new workspace."
		echo "  -u, --url		  	  [url]           Optional override AutoForge package URL."
		echo "  -h, --help				  			  Display this help and exit."
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
			-u | --url)
				auto_forge_url="$2"
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

	# Attempt to get Git token using 'dt'
	if output="$(dt github print-token 2>/dev/null)"; then
		token="$output"
		autoforge_cmd+=(--git-token "$token")
	fi

	# Run AutoForge in sequence execution mode
	"${autoforge_cmd[@]}"
	ret_val=$?

	# Quietly uninstall auto_forge from the global scope to restrict it as possible
	# only to virtual environments.
	pip3 uninstall -y auto_forge &>/dev/null
	echo -ne '\e[?25h' # Restore cursor.

	# Best-effort: silently move residual sequence logs to workspace log path
	local destination_path="$PWD/$workspace_path/build/logs"
	local source_path
	source_path="$(dirname "$PWD/$workspace_path")"

	if [[ -d "$destination_path" ]]; then
		shopt -s nullglob
		for file in "$source_path"/*sequence.log; do
			[[ -e "$file"   ]] && mv -f -- "$file" "$destination_path/" 2>/dev/null
		done
		shopt -u nullglob
	fi

	# Restore original directory
	if ! cd "$original_dir"; then
		echo "Error: Failed to return to original directory: $original_dir"
		exit 1
	fi

	return $ret_val
}

# Entry point
main "$@"
exit $?
