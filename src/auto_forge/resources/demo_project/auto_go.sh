#!/bin/bash
# shellcheck disable=SC2296
# shellcheck disable=SC1090

# ------------------------------------------------------------------------------
#
# Script Name:    auto_go.sh
# Description:    AutoForge shell starter.
# Author:         AutoForge team.
#
# ------------------------------------------------------------------------------

#
# @brief AutoForge solution entry point.
# @return Returns 0 on overall success, else failure.
#

solution_name="sample"

main() {

	# Determine the script's directory (works in both bash and zsh)
	if  [[ -n "${ZSH_VERSION:-}" ]]; then
		script_path="${(%):-%x}"
	else
		script_path="${BASH_SOURCE[0]:-$0}"
	fi

	local  script_dir
	script_dir="$( cd "$(dirname "$script_path")" && pwd)"

	# Change to the workspace (script) directory
	cd  "$script_dir" || {
		echo     "Error: Failed to change to workspace directory '$script_dir'"
		return     1
	}

	local  venv_path=".venv/bin/activate"
	local  solution_path="$solution_name/scripts/solution"

	# Check if virtual environment exists
	if  [[ ! -f "$venv_path" ]]; then
		echo     "Error: Python virtual environment not found at $venv_path"
		return     1
	fi

	# Source the virtual environment
	source  "$venv_path"

	# Check if 'autoforge' is available
	if  ! command -v autoforge > /dev/null 2>&1; then
		echo     "Error: 'autoforge' command not found in PATH"
		return     2
	fi

	# Check if the solution path exists
	if  [[ ! -d "$solution_path" ]]; then
		echo     "Error: Solution path '$solution_path' does not exist"
		return     3
	fi

	# Execute the solution in interactive mode.
	autoforge  -w . -p "$solution_path" --no-create-workspac
	return  $?
}

#
# @brief Invoke the main function with command-line arguments.
# @return The exit status of the main function.
#
main "$@"
exit $?
