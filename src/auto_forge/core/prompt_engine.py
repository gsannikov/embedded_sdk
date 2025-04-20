"""
Script:         prompt_engine.py
Author:         AutoForge Team

Description:
    This module ...
"""

import cmd2
import subprocess

from auto_forge import (PROJECT_COMMANDS_PATH, CLICommandInterface, CLICommandInfo, AutoLogger)

AUTO_FORGE_MODULE_NAME = "PromptEngine"
AUTO_FORGE_MODULE_DESCRIPTION = "Terminal Prompt Manager"

class PromptEngine(cmd2.Cmd):
    prompt = "autoforge> "

    def default(self, line: str) -> None:
        """Fallback for unrecognized commands — redirect to shell."""
        try:
            result = subprocess.run(line, shell=True, check=False, text=True)
        except Exception as e:
            self.perror(f"Shell command failed: {e}")