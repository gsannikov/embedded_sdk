#!/usr/bin/env python3
"""
Script:         __main__.py
Author:         AutoForge Team

Description:
    This script serves as the entry point for the AutoForge package.
"""
import argparse
import sys
from contextlib import suppress
from typing import Optional

# Globally initialize colorama library
from colorama import init

# AutoForge imports
from auto_forge import PackageGlobals
from auto_forge.auto_forge import auto_forge_start as _start


def arguments_process() -> Optional[argparse.Namespace]:
    """
    Command line arguments processing.
    Returns:
        Optional[argparse.Namespace]: Command line arguments namespace when successfully parsed, None otherwise.
    """

    with suppress(Exception):
        version_string = f"{PackageGlobals.NAME} Ver {PackageGlobals.VERSION}"

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
        # Only one of these modes may be used at a time.

        group = parser.add_mutually_exclusive_group()
        group.add_argument("-s", "--run_sequence", type=str, required=False,
                           help="Solution properties name which points to a sequence of operations")
        group.add_argument("-r", "--run-command", type=str, required=False,
                           help="Name of known command which will be executed")
        # This captures everything that comes after -- (REMAINDER)
        parser.add_argument("run_command_args", nargs=argparse.REMAINDER,
                            help="Arguments to pass to the command specified by --run-command")

        args = parser.parse_args()

        # Enforce correct usage for 'run-command'
        if "--run-command" in sys.argv:
            if args.run_command is None or args.run_command.startswith("-"):
                parser.error("You must provide a valid command name after --run-command.")

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
    init(autoreset=True, strip=False)  # Required by colorama

    arguments = arguments_process()
    if arguments is not None:
        return _start(arguments)
    else:
        return 1  # Arguments processing error


if __name__ == "__main__":
    sys.exit(main())
