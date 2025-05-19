"""
Script:         make_builder.py
Author:         AutoForge Team

Description:
    ToDo
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from typing import Optional

from packaging.version import parse as vparse

# AutoForge imports
from auto_forge import (
    BuilderInterface,
    BuildProfileType,
    BuilderToolchainValidationError,
    BuilderConfigurationBuildError,
    CoreEnvironment,
)
from auto_forge.core.prompt import CorePrompt

AUTO_FORGE_MODULE_NAME = "make"
AUTO_FORGE_MODULE_DESCRIPTION = "make files builder"
AUTO_FORGE_MODULE_VERSION = "1.0"


class MakeBuilder(BuilderInterface):
    """
    A simple 'hello world' command example for AutoForge CLI.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the MakeBuilder class.
        Args:
            **_kwargs (Any): Optional keyword arguments.
        """
        self._environment: Optional[CoreEnvironment] = None
        self._prompt: Optional[CorePrompt] = None

        super().__init__(build_system=AUTO_FORGE_MODULE_NAME)

    @staticmethod
    def _validate_tool_chain(toolchain: dict[str, Any]) -> None:
        """
        Validates that the given toolchain dictionary describes tools and versions
        that are available on the current system.
        Args:
            toolchain (dict): A dictionary describing the toolchain.
        """

        def check_version(path: str, version: str) -> None:
            try:
                output = subprocess.check_output([path, '--version'], text=True, timeout=3)
            except Exception as e:
                raise BuilderToolchainValidationError(f"Failed to invoke '{path} --version': {e}")

            match = re.search(r"(\d+\.\d+(\.\d+)?)", output)
            if not match:
                raise BuilderToolchainValidationError(f"Unable to parse version for tool: {path}")

            current = vparse(match.group(1))
            expected = vparse(version.lstrip(">="))
            if current < expected:
                raise BuilderToolchainValidationError(
                    f"{path} version {current} does not meet required version {version}"
                )

        for tool in toolchain.get("required_tools", []):
            # If tool is a path or binary name
            tool_path = shutil.which(tool) if not os.path.isabs(tool) else tool
            if not tool_path or not Path(tool_path).exists():
                raise BuilderToolchainValidationError(
                    f"Required tool '{tool}' not found in system path or given location.")

            # Lookup version requirement (e.g., 'make_version', 'gcc_version')
            key = f"{Path(tool).name}_version"
            required_version = toolchain.get(key)
            if required_version:
                check_version(tool_path, required_version)

    def _make_configuration(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        - Compiles a configuration using the provided build profile and validated toolchain.
        - Validates all required paths (build path, execute_from, etc.)
        - Executes the compiler command with options
        - Verifies the compiler's return code matches the expected result
        - Confirms that all expected artifacts were created

        Args:
            build_profile (BuildProfileType): The build profile containing config and toolchain data.
        Returns:
            Optional[int]: The compiler's return code if successful.

        """
        config = build_profile.config_data
        self._environment = CoreEnvironment.get_instance()

        # Those are essential properties we must get
        mandatory_required_fields = ["build_path", "compiler_options", "artifacts"]

        # Gets the build system
        compiler_command = build_profile.tool_chain_data.get("build_system")
        if not compiler_command:
            raise BuilderConfigurationBuildError(
                "toolchain does not specify a 'build_system' (e.g., 'make')"
            )

        # Validate required fields
        for field in mandatory_required_fields:
            if field not in config:
                raise BuilderConfigurationBuildError(f"missing mandatory field in configuration: '{field}'")

        # Get optional 'execute_from' property and validate it
        execute_from = config.get("execute_from", None)
        if execute_from is not None:
            execute_from = Path(self._tool_box.get_expanded_path(execute_from))
            # Validate it's a path since we have it.
            if not execute_from.is_dir():
                raise BuilderConfigurationBuildError(f"Invalid source directory: '{execute_from}'")

        # Process pre-build steps if specified
        steps_data: Optional[dict[str, str]] = config.get("pre_build_steps", {})
        if steps_data:
            self._process_build_steps(steps=steps_data)

        # Validate or create build_path
        build_path = Path(config["build_path"]).expanduser().resolve()
        if not build_path.exists():
            try:
                build_path.mkdir(parents=True)
            except Exception as make_dir_error:
                raise BuilderConfigurationBuildError(f"failed to create build path: '{build_path}': {make_dir_error}")

        if not build_path.is_dir():
            raise BuilderConfigurationBuildError(f"build path is not a directory: '{build_path}'")

        compiler_options = config["compiler_options"]
        artifacts = config["artifacts"]

        # Ensure compiler tool exists
        if shutil.which(compiler_command) is None:
            raise BuilderConfigurationBuildError(f"Compiler '{compiler_command}' not found in PATH.")

        # Prepare the 'make' command line
        command_line = [compiler_command] + compiler_options

        # Execute
        try:
            self._logger.debug(f"Executing build in '{execute_from}'")
            results = self._environment.execute_shell_command(
                command_and_args=command_line,
                cwd=str(execute_from),
                expand_command=True)

        except Exception as execution_error:
            raise BuilderConfigurationBuildError(f"build process failed to start: {execution_error}")

        # Validate expected return code
        if results.return_code != 0:
            self._logger.error(f"Build failed with error: {results.return_code}")
            if results.response:
                self._logger.error(f"Build response: {results.response}")

            raise BuilderConfigurationBuildError(f"Build returned unexpected result code")

        # Process post build steps if specified
        steps_data: Optional[dict[str, str]] = config.get("post_build_steps", {})
        if steps_data:
            self._process_build_steps(steps=steps_data)

        # Check for all expected artifacts
        missing_artifacts = []
        for artifact_path in artifacts:
            artifact_file = Path(artifact_path).expanduser().resolve()
            if not artifact_file.exists():
                missing_artifacts.append(str(artifact_file))

        if missing_artifacts:
            raise BuilderConfigurationBuildError(f"Missing expected build artifacts:\n" + "\n".join(missing_artifacts))

        self._logger.info(f"Build succeeded")
        return 0

    def _process_build_steps(self, steps: dict[str, str]) -> None:
        """
        Execute a dictionary of build steps where values prefixed with '!' are run as cmd2 shell commands.
        Args:
            steps (dict[str, str]): A dictionary of named build steps to execute.
        """

        self._prompt = CorePrompt().get_instance()
        if self._prompt is None:
            raise BuilderConfigurationBuildError("could attach to the prompt class instance")

        for step_name, command in steps.items():
            self._logger.debug(f"Running build step: '{step_name}'")

            command = command.strip()

            if command.startswith("!"):
                cmd_text = command[1:].lstrip()
                try:
                    self._prompt.onecmd_plus_hooks(cmd_text)
                except Exception as execution_error:
                    self._prompt.perror(f"Failed to execute '{step_name}': {execution_error}")
            else:
                self._prompt.pwarning(f"Step '{step_name}' ignored: no '!' prefix")

    def build(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        Validates the provided build configuration and executes the corresponding build flow.
        Args:
            build_profile (BuildProfileType): The build profile containing solution, project,
            configuration, and toolchain information required for the build process.

        Returns:
            Optional[int]: The return code from the build process, or None if not applicable.
        """

        self._validate_tool_chain(build_profile.tool_chain_data)

        return self._make_configuration(build_profile=build_profile)
