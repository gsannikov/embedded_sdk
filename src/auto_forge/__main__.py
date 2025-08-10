#!/usr/bin/env python3
"""
Script:         __main__.py
Author:         AutoForge Team

Description:
    This script serves as the entry point for the AutoForge package.
"""
import argparse
import sys
from argparse import ArgumentParser
from contextlib import suppress
from typing import Optional

# Globally initialize colorama library
from colorama import (init, deinit)

# AutoForge imports
from auto_forge import PackageGlobals
from auto_forge.auto_forge import auto_forge_start as _start


def arguments_process() -> Optional[argparse.Namespace]:
    """
    Command line arguments processing.
    Returns:
        Optional[argparse.Namespace]: Command line arguments namespace when successfully parsed, None otherwise.
    """

    def _validate_workspace_or_bare(_args, _parser: ArgumentParser):
        """
        Ensure that either --bare is used alone, or both --workspace-path and --solution-name are provided.
        --solution-package is optional, but only allowed in workspace mode.
        """
        has_workspace = bool(_args.workspace_path)
        has_solution = bool(_args.solution_name)
        has_package = bool(_args.solution_package)
        has_mcp_service = bool(_args.mcp_service)

        is_bare = _args.bare

        if is_bare:
            if has_workspace or has_solution or has_package or has_mcp_service:
                _parser.error(
                    "--bare cannot be used together with --workspace-path, --solution-name, --mcp-service or --solution-package.")
            return  # Valid bare mode with no extras

        # Not in bare mode: workspace and solution name are mandatory
        if not has_workspace or not has_solution:
            missing = []
            if not has_workspace:
                missing.append("--workspace-path")
            if not has_solution:
                missing.append("--solution-name")
            _parser.error(
                f"Missing required arguments for workspace mode: {', '.join(missing)}.\n"
                "You must provide both --workspace-path and --solution-name unless using --bare.")

    with suppress(Exception):
        version_string = f"{PackageGlobals.NAME} Ver {PackageGlobals.VERSION}"

        # Check early for the version flag before constructing the parser
        if len(sys.argv) == 2 and sys.argv[1] in ("-v", "--version"):
            print(f"\n{version_string}\n")
            sys.exit(0)

        # Normal arguments handling
        parser = argparse.ArgumentParser(prog="autoforge", description=f"{version_string} arguments:")

        # Required argument specifying the workspace path. This can point to an existing workspace
        # or a new one to be created by AutoForge, depending on the solution definition.
        parser.add_argument("-w", "--workspace-path",
                            help="Path to an existing or new workspace to be used by AutoForge.")

        parser.add_argument("-n", "--solution-name",
                            help="Name of the solution to use. It must exist in the solution file.")

        # AutoForge requires a solution to operate. This can be provided either as a pre-existing local ZIP archive,
        # or as a Git URL pointing to a directory containing the necessary solution JSON files.
        parser.add_argument("-p", "--solution-package",
                            help=("Path to an AutoForge solution package. This can be either:\n"
                                  "- Path to an existing .zip archive file.\n"
                                  "- Path to an existing directory containing solution files.\n"
                                  "- Github URL pointing to git path which contains the solution files.\n"
                                  "The package path will be validated at runtime, if not specified, the solution will "
                                  "be searched for in the local solution workspace path under 'scripts/solution'"))

        # Bare mode enables a limited AutoForge operation and is mutually exclusive with (-w, -n, -p, -m) arguments.
        parser.add_argument("-b", "--bare", action="store_true", help="Use bare solution")

        # Other optional configuration arguments
        parser.add_argument("-d", "--remote-debugging", type=str, required=False,
                            help="Remote debugging endpoint in the format <ip-address>:<port> (e.g., 127.0.0.1:5678)")

        parser.add_argument("--proxy-server", type=str, required=False,
                            help="Optional proxy server endpoint in the format <ip-address>:<port> (e.g., 192.168.1.1:8080).")

        parser.add_argument("--git-token", type=str, required=False,
                            help="Optional GitHub token to use for authenticating HTTP requests.")

        parser.add_argument("--log-file", type=str, required=False,
                            help="Optional Specify log fie name.")

        # AutoForge supports two mutually exclusive non-interactive modes:
        # (1) Running step recipe data (typically used to set up a fresh workspace),
        # (2) Running a single command from an existing workspace.
        # (3) Running in MCP service compatability mode.
        # Only one of these modes may be used at a time.

        op_mode_group = parser.add_mutually_exclusive_group()
        op_mode_group.add_argument(
            "-s", "--run-sequence", type=str, required=False,
            help="Solution properties name which points to a sequence of operations")
        op_mode_group.add_argument(
            "-r", "--run-command",
            nargs=argparse.REMAINDER, help="One or more commands separated by ','")
        op_mode_group.add_argument(
            "-m", "--mcp-service", action="store_true", help="MCP service mode")

        args = parser.parse_args()

        # Manual mutual-exclusiveness validation between 'bare solution' and 'normal solution'
        _validate_workspace_or_bare(_args=args, _parser=parser)

        if args.run_command is not None:
            # Remove '--' if was injected by shell
            cleaned = [arg for arg in args.run_command if arg != "--"]
            if not cleaned or not any(a.strip() for a in cleaned):
                parser.error("You must provide at least one command after --run-command.")

            args.run_command = " ".join(cleaned).strip()

        return args

    # Arguments parser exception
    print()
    return None


def main() -> int:
    """
    The main entry point for the AutoForge package.
    The following will provide you with all you need to know about this method:
    https://en.wikipedia.org/wiki/Entry_point
    Returns:
        Shell status, 0 success, else failure.
    """
    # Package command line starter.
    return_code = 1
    init(autoreset=True, strip=False)  # Required by colorama

    arguments = arguments_process()
    if arguments is not None:
        return_code = _start(arguments)

    deinit()
    return return_code  # Arguments processing error


if __name__ == "__main__":
    sys.exit(main())
