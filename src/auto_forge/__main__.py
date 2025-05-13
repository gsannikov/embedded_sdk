#!/usr/bin/env python3
"""
Script:         __main__.py
Author:         AutoForge Team

Description:
    This script serves as the entry point for the AutoForge package, enabling it to be run as a console application.
"""

import sys

# Globally initialize colorama library
from colorama import init

# AutoForge imports
from auto_forge import main

# from auto_forge import readline_patch
# Replace the standard readline module globally
# sys.modules['readline'] = readline_patch._readline

if __name__ == "__main__":
    init(autoreset=True, strip=False)  # Required by 'colorama'
    result: int = main()
    sys.exit(result)
