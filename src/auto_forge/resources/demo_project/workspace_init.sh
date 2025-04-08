#!/bin/bash
#shellcheck disable=SC2034  # Variable appears unused.

# ------------------------------------------------------------------------------
#
# Script Name:    workspace_init.sh
# Description:    Generic project workspace initiation script.
# Copyright:      2025 Intel Corporation.
#
# ------------------------------------------------------------------------------

SCRIPT_VERSION="1.0"

# Define HTTP and HTTPS proxy servers. These are optional and, if specified,
# will override any proxy settings exported in the shell environment.
HTTP_PROXY_SERVER="http://proxy-dmz.intel.com:911"
HTTPS_PROXY_SERVER="http://proxy-dmz.intel.com:911"

# Variable to hold the path used for the project workspace.
WORKSPACE_PATH=""

# Variable to hold the path used for downloading and storing temporary files.
AUTO_FORGE_URL="https://github.com/emichael72/auto_forge.git"

# AutoForge URL

# An array of resources we have to download locally
downloadable_resources=(
	"https://raw.githubusercontent.com/emichael72/auto_forge/refs/heads/main/src/auto_forge/resources/demo_project/setup.jsonc"
)

# One liner installer link example that adds cache-buster to the URL to mitigate Proxy aggressive caching.
# curl -s -S -H "Cache-Control: no-store" --proxy http://proxy-dmz.intel.com:911 "https://raw.githubusercontent.com/emichael72/auto_forge/refs/heads/main/src/auto_forge/resources/demo_project/workspace_init.sh?$(date +%s)" | bash -s -- -w ws -f -a

#
# @brief SwissKnife wrapper around 'curl' which allows to downloads a file from a specified
#         URL to a destination path and handles command-line arguments.
# @param Use -h, --help to get a list of supported options.
# @return Returns 0 on successful download, else, a positive integer corresponding
#         to 'curl' exit status or HTTP error code.
#

download_file() {

	local http_status=0
	local curl_exit_status=0
	local timeout=0
	local verbose=0
	local extra_verbose=0
	local remote_url=""
	local destination_path=""
	local destination_filename=""
	local proxy_configured=""

	# Help message function
	display_help() {
		echo "Usage: download_file [options]"
		echo "  -u,  --url [url]            URL to download the file from."
		echo "  -d,  --destination [path]   Destination path to save the file."
		echo "  -t,  --timeout [seconds]    Timeout for the download operation."
		echo "  -v,  --verbose              Enable verbose output."
		echo "  -vv, --extra_verbose        Enable extra 'curl' verbose output."
		echo "  -h,  --help                 Display this help and exit."
		echo
	}

	# Function to extract the filename from a URL
	extract_filename() {
		echo "${1##*/}"
	}

	# Print an error string if verbosity is enabled and a message is provided
	print_error() {
		if [[ -n $1 && $verbose -eq 1 ]]; then
			printf "Error: %s\n" "$1"
		fi
	}

	# Parse command-line arguments
	while [[ "$#" -gt 0 ]]; do
		case "$1" in
			-u | --url)
				remote_url="$2"
				shift 2
				;;
			-d | --destination)
				destination_path="$2"
				shift 2
				;;
			-t | --timeout)
				timeout="$2"
				shift 2
				;;
			-v | --verbose)
				verbose=1
				shift
				;;
			-vv | --extra_verbose)
				verbose=1
				extra_verbose=1
				shift
				;;
			-h | --help | -? | --?)
				display_help
				return 1
				;;
			*)
				printf "Error: Unknown option: %S\n" "$1"
				return 1
				;;
		esac

	done

	# Validate URL
	if [[ -z "$remote_url" ]]; then
		print_error "URL not provided"
		return 2
	fi

	# Check if 'curl' is available
	if ! command -v curl &> /dev/null; then
		print_error "'curl' not found"
		return 3
	fi

	if [[ $verbose -eq 1 ]]; then
		printf "Getting: %s\n" "$remote_url"
	fi

	# Use temporary path if not specified
	if [[ -z "$destination_path" ]]; then
		destination_path=$(mktemp -d -q)
	fi

	# Normalize
	destination_path=$(realpath -- "$destination_path")

	# Validate and set the destination path
	if [[ -z "$destination_path" ]] || [[ -d "$destination_path" ]]; then
		filename=$(extract_filename "$remote_url")
		if [[ -z "$filename" ]]; then
			print_error "Could not extract the file name from the URL"
			return 4
		fi
		# Append filename to the destination path if it's a directory, or set as filename if path is empty
		destination_path="${destination_path%/}/$filename"
		destination_filename=$(basename "$destination_path")
	fi

	# Check if the path points to a file
	if [[ -f "$destination_path" ]]; then
		print_error "File '$destination_path' already exists"
		return 5
	fi

	# Check if the path is writable or not
	directory_path=$(dirname "$destination_path")
	if [[ ! -d "$directory_path" ]] || [[ ! -w "$directory_path" ]]; then
		print_error "Path '$directory_path' does not exist or is not writable"
		return 6
	fi

	# Determine the directory of the destination path
	# If the directory path is '.', use the current working directory
	directory_path=$(dirname "$destination_path")
	if [[ "$directory_path" == "." ]]; then
		directory_path=$(pwd)
	fi

	# Store globally - use the resolved directory path
	RESOURCES_PATH="$directory_path"

	# Initialize the array for curl options
	local curl_opts=("--silent" "--fail" "-H Cache-Control: no-store")

	# Conditionally add verbose output option
	if [[ $extra_verbose -eq 1 ]]; then
		curl_opts=("-v") # This replaces the silent and fail options if extra verbose is needed
	fi

	# Add timeout if specified
	if [[ $timeout -gt 0 ]]; then
		curl_opts+=("--max-time" "$timeout")
	fi

	# Add HTTP proxy settings to curl command if defined
	if [[ -n "$HTTP_PROXY_SERVER" ]]; then
		curl_opts+=("--proxy" "$HTTP_PROXY_SERVER")
		proxy_configured="$HTTP_PROXY_SERVER"
	fi

	# Add HTTPS proxy settings to curl command if defined
	if [[ -n "$HTTPS_PROXY_SERVER" ]]; then
		curl_opts+=("--proxy" "$HTTPS_PROXY_SERVER")
		proxy_configured="$proxy_configured $HTTPS_PROXY_SERVER"
	fi

	# Perform the download
	http_status=$(curl "${curl_opts[@]}" -f -o "$destination_path" -w "%{http_code}" -s "$remote_url?$(date +%s)")
	curl_exit_status=$?

	if [[ $http_status -lt 200 || $http_status -ge 300 ]]; then
		# Handle HTTP errors
		print_error "Failed with HTTP status $http_status"
		return 7 # We can't return the HTTP status since the shell is limited to 8 bit return value.

	elif [[ $curl_exit_status -ne 0 ]]; then
		# Handle non-HTTP errors (e.g., network issues, DNS failures)
		print_error "'Curl' failed with exit status $curl_exit_status"
		return $curl_exit_status
	fi

	# If verbose is set, display operation status
	if [[ $verbose -eq 1 ]]; then
		printf "File name: %s\n" "$destination_filename"
		printf "Destination path: %s\n" "$destination_path"
		if [[ -n "$HTTP_PROXY_SERVER" ]] || [[ -n "$HTTPS_PROXY_SERVER" ]]; then
			printf "Proxy: %s\n" "$proxy_configured"
		fi
		printf "HTTP status: %d\n\n" "$http_status"
	fi

	return 0
}

#
# @brief Prepares a workspace directory.
# Validates and prepares a workspace directory.
#
# @param workspace_path The path to the workspace directory to be prepared.
# @param force_create A flag to indicate whether to force the creation of the workspace by deleting and recreating it if it already exists.
# @param verbose A flag to control the verbosity of the output.
# @return Returns 0 on success, or 1 if there was an error with detailed messages based on the verbosity level.
#

prepare_workspace() {

	local workspace_path="$1"
	local force_create="${2:-0}"
	local verbose="${3:-0}"

	# Function to count the depth of a directory path, excluding the root
	get_depth() {

		local path="$1"
		local depth=""
		# Normalize the path to remove any trailing slashes for consistent counting
		path="${path%/}"
		# Count the slashes in the path, which represent directory boundaries
		depth=$(awk -F"/" '{print NF-1}' <<< "$path")
		echo "$depth"
	}

	# Check if a directory can be deleted based on its depth
	can_delete_directory() {

		local directory_path="$1"
		local min_depth=3 # Set minimum depth to allow deletion
		local normalized_path=""
		local depth=0
		local ret_val

		# Normalize path and remove trailing slashes for consistent depth calculation
		normalized_path=$(realpath -- "$directory_path")
		normalized_path="${normalized_path%/}"

		# Calculate depth
		depth=$(get_depth "$normalized_path")

		# Check if depth is less than the minimum required depth
		if [[ $depth -lt $min_depth ]]; then
			printf "Error: Cannot delete '%s' (Depth: %d). Minimum allowed depth is %d." "$directory_path" "$depth" "$min_depth"
			return 1
		else
			[[ $verbose -eq 1 ]] && printf "Allowed to delete '%s' (Depth: %d).\n" "$directory_path" "$depth"
			return 0
		fi
	}

	# Validate workspace argument
	if [[ -z "$workspace_path" ]]; then
		[[ $verbose -eq 1 ]] && printf "Error: Workspace argument not provided.\n"
		return 1
	fi

	# Convert to absolute path
	if [[ "$workspace_path" != /* ]]; then
		workspace_path="$PWD/$workspace_path"
	fi

	# Check if the resolved path exists
	if [[ -e "$workspace_path" ]]; then
		if [[ $force_create -eq 1 ]]; then
			# Safety check to avoid deleting critical system directories
			if can_delete_directory "$workspace_path"; then
				# Delete the directory and its contents silently
				rm -rf "$workspace_path" > /dev/null 2>&1
				ret_val=$?
				if [[ $ret_val -ne 0 ]]; then
					[[ $verbose -eq 1 ]] && printf "Error: Failed to delete existing workspace path: %s\n" "$workspace_path"
					return 1
				fi
			else
				[[ $verbose -eq 1 ]] && printf "Error: Refusing to delete critical directory: %s\n" "$workspace_path"
				return 1
			fi
		else
			[[ $verbose -eq 1 ]] && printf "Error: Workspace path exists: %s\n" "$workspace_path"
			return 1
		fi
	fi

	# Create the path
	mkdir -p "$workspace_path" || {
		[[ $verbose -eq 1 ]] && printf "Error: Failed to create workspace directory at %s\n" "$workspace_path"
		return 1
	}

	# Change current directory to the newly created path
	cd "$workspace_path" || {
		[[ $verbose -eq 1 ]] && printf "Error: Failed to change directory to %s\n" "$workspace_path"
		return 1
	}

	[[ $verbose -eq 1 ]] && printf "Workspace directory prepared at %s\n" "$workspace_path"

	# Store globally
	WORKSPACE_PATH=$workspace_path
	return 0

}

#
# @brief Update the environment with proxy settings if we have them defined in this scriprt.
# @return Returns 0 on overall success, else failure.
#

setup_proxy_environment() {

	# Check if HTTP_PROXY_SERVER is set and not empty
	if [ -n "$HTTP_PROXY_SERVER" ]; then
		export   http_proxy=$HTTP_PROXY_SERVER
		export   HTTP_PROXY=$HTTP_PROXY_SERVER
	else
		echo   "HTTP proxy not set."
	fi

	# Check if HTTPS_PROXY_SERVER is set and not empty
	if [ -n "$HTTPS_PROXY_SERVER" ]; then
		export   https_proxy=$HTTPS_PROXY_SERVER
		export   HTTPS_PROXY=$HTTPS_PROXY_SERVER
	else
		echo   "HTTPS proxy not set."
	fi
}

#
# @brief Install AutoForge python object.
# @return Returns 0 on overall success, else failure.
#

install_autoforge() {

	# Check for Python 3.9 or higher
	if ! python3 --version | grep -qE 'Python 3\.(9|[1-9][0-9])'; then
		echo   "Python 3.9 or higher is not installed."
		return   1
	fi

	# Check if pip is installed
	if ! command -v pip3 &> /dev/null; then
		echo   "pip is not installed."
		return   1
	fi

	# Uninstall auto_forge if it exists, without any output
	pip3 uninstall -y auto_forge &> /dev/null

	# Install auto_forge from the provided URL, without any output
    if pip3 install git+$AUTO_FORGE_URL -q >/dev/null 2>&1; then 
        # Check if installation was successful
        echo "Listing ... "
        if pip3 list 2>/dev/null | grep -q 'auto_forge'; then
            echo "AutoForge installed successfully."
            return 0
        else
            echo "Failed to install auto_forge."
            return 1
        fi
    else
        echo "Failed to install auto_forge."
        return 1
    fi
}

#
# @brief Installer entry point function.
# @return Returns 0 on overall success, else failure.
#

main() {

	local ret_val=0
	local force_create=0
	local verbose=0
	local use_autoforge=0
	local url=""
	local resources_path=""
	local workspace_path=""

	# Help message function
	display_help() {
		echo
		echo "Usage: $(basename "$0") [options]"
		echo "  -w,  --workspace [path  ]   Destination workspace path."
		echo "  -f,  --force_create         Erase and recreate the workspace path if already exists."
		echo "  -v,  --verbose              Enable verbose output."
		echo "  -a,  --autoforge            Install AutoForge."
		echo "  -h,  --help                 Display this help and exit."
		echo
	}

	# Parse command-line arguments
	while [[ "$#" -gt 0 ]]; do
		case "$1" in

			-w | --workspace)
				workspace_path="$2"
				shift 2
				;;
			-f | --force_create)
				force_create=1
				shift
				;;
			-a | --autoforge)
				use_autoforge=1
				shift
				;;
			-v | --verbose)
				verbose=1
				shift
				;;
			-h | --help | -? | --?)
				display_help
				return 1
				;;
			*)
				printf "Error: Unknown option: %s\n" "$1"
				return 1
				;;
		esac
	done

	# Set proxy as needed
	setup_proxy_environment

	# Validate workspace argument and create the path.
	prepare_workspace "$workspace_path" "$force_create" "$verbose" || return 1

	if [[ $use_autoforge -eq 1 ]]; then
	 	printf "Getting AutoForge ...\n"

		install_autoforge || return 1

		# Execute AutoForge
		python3.9 -m auto_forge
		ret_val=$?
		return $ret_val
	fi

	# Set the destination path where resources will be downloaded into.
	resources_path="$WORKSPACE_PATH/.init"
	mkdir -p "$resources_path" > /dev/null 2>&1

	# Loop and process each downloadable resource.
	for url in "${downloadable_resources[@]}"; do

		# Create an array of download options
		download_options=(-d "$resources_path")

		# Add the URL to the options
		download_options+=(-u "$url")

		# Add verbose option if verbose flag is set
		if [[ $verbose -eq 1 ]]; then
			download_options+=(-v)
		fi

		# Execute the download
		download_file "${download_options[@]}"
		ret_val=$?

		# Check the return value from the download function
		if [[ $ret_val -ne 0 ]]; then
			break
		fi

	done

	# Fire setup tools along with a project specific steps JSON
	python3 "$resources_path/setup_tools.py" -s "$resources_path/setup.jsonc" -w "$WORKSPACE_PATH"
	ret_val=$?

	return $ret_val

}

# @brief Invoke the main function with command-line arguments.
# @return The exit status of the main function.
main "$@"
exit $?
