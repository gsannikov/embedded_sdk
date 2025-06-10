#!/usr/bin/env python3
"""
Script:         __main__.py
Author:         AutoForge Team

Description:
    This script serves as the entry point for the AutoForge package, enabling it to be run as a console application.
"""
import argparse
import sys
from contextlib import suppress
from typing import Optional

# Globally initialize colorama library
from colorama import init

# AutoForge imports
from auto_forge import PROJECT_NAME, PROJECT_VERSION, start


def handle_arguments() -> Optional[argparse.Namespace]:
    """
    Command line arguments handler function.
    Returns:
        Optional[argparse.Namespace]: Command line arguments namespace when successfully parsed, None otherwise.
    """

    version_string = f"{PROJECT_NAME} Ver {PROJECT_VERSION}"

    with suppress(Exception):
        # Check early for the version flag before constructing the parser
        if len(sys.argv) == 2 and sys.argv[1] in ("-v", "--version"):
            print(f"\n{version_string}\n")
            sys.exit(0)

        # Normal arguments handling
        parser = argparse.ArgumentParser(prog="autoforge",
                                         description=f"{version_string} arguments:")

        # Required argument specifying the workspace path. This can point to an existing workspace
        # or a new one to be created by AutoForge, depending on the solution definition.
        parser.add_argument("-w", "--workspace-path", required=True,
                            help="Path to an existing or new workspace to be used by AutoForge.")

        parser.add_argument("-n", "--solution-name", required=True,
                            help="Name of the solution to use. It must exist in the solution file.")

        # AutoForge requires a solution to operate. This can be provided either as a pre-existing local ZIP archive,
        # or as a Git URL pointing to a directory containing the necessary solution JSON files.

        parser.add_argument("-p", "--solution-package", required=False,
                            help=("Path to an AutoForge solution package. This can be either:\n"
                                  "- Path to an existing .zip archive file.\n"
                                  "- Path to an existing directory containing solution files.\n"
                                  "- Github URL pointing to git path which contains the solution files.\n"
                                  "The package path will be validated at runtime, if not specified, the solution will "
                                  "be searched for in the local solution workspace path under 'scripts/solution'"))

        # AutoForge supports two mutually exclusive non-interactive modes:
        # (1) Running step recipe data (typically used to set up a fresh workspace),
        # (2) Running a single command from an existing workspace.
        # Only one of these modes may be used at a time.

        group = parser.add_mutually_exclusive_group()
        group.add_argument("-s", "--run_sequence", type=str, required=False,
                           help="Solution properties name which points to a sequence of operations")
        group.add_argument("-r", "--run-command", type=str, required=False,
                           help="Name of known command which will be executed")

        # Other optional configuration arguments
        parser.add_argument("-d", "--remote-debugging", type=str, required=False,
                            help="Remote debugging endpoint in the format <ip-address>:<port> (e.g., 127.0.0.1:5678)")

        parser.add_argument("--proxy-server", type=str, required=False,
                            help="Optional proxy server endpoint in the format <ip-address>:<port> (e.g., 192.168.1.1:8080).")

        parser.add_argument("--git-token", type=str, required=False,
                            help="Optional GitHub token to use for authenticating HTTP requests.")

        return parser.parse_args()

    # Terminate on error
    sys.exit(1)


if __name__ == "__main__":
    # Package command line starter.
    init(autoreset=True, strip=False)  # Required by colorama

    args = handle_arguments()
    sys.exit(start(args=args))
