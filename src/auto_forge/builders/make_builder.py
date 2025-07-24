"""
Script:         make_builder.py
Author:         AutoForge Team

Description:
    A builder implementation tailored for handling Make-based build systems.
    It provides the `_MakeToolChain` class, a concrete implementation of the `BuilderToolChainInterface`,
    which validates and resolves tools required for Make-driven workflows.
Classes:
    - _MakeToolChain: Validates and resolves required tools for Make-based tool-chains.
"""

import logging
import os
from pathlib import Path
from typing import Any
from typing import Optional

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (BuilderRunnerInterface, BuilderToolChain, BuildProfileType, CoreVariables, TerminalEchoType)

AUTO_FORGE_MODULE_NAME = "make"
AUTO_FORGE_MODULE_DESCRIPTION = "make files builder"
AUTO_FORGE_MODULE_VERSION = "1.0"


# noinspection DuplicatedCode
class MakeBuilder(BuilderRunnerInterface):
    """
    Implementation of the BuilderRunnerInterface for Make-based builds.
    This builder executes the 'make' command using a validated toolchain and configuration
    provided by the surrounding build context. It is intended for use with projects that
    define their build process via Makefiles.
    """

    def __init__(self, **_kwargs: Any):
        """
        Initializes the MakeBuilder instance.
        Args:
            **_kwargs (Any): Optional keyword arguments for future extensibility.
                             Currently unused but accepted for interface compatibility.
        """
        self._toolchain: Optional[BuilderToolChain] = None
        self._variables: CoreVariables = CoreVariables.get_instance()

        super().__init__(build_system=AUTO_FORGE_MODULE_NAME)

    def _execute_build(  # noqa: C901
            self, build_profile: BuildProfileType) -> Optional[int]:
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
        NOTE:
            This function exceeds typical complexity limits (C901) by design.
            It encapsulates a critical, tightly-coupled sequence of logic that benefits from being kept together
            for clarity, atomicity, and maintainability. Refactoring would obscure the execution flow.
        """
        config = build_profile.config_data

        # Those are essential properties we must get
        mandatory_required_fields = ["build_path", "compiler_options", "artifacts"]

        # Validate required fields
        for field in mandatory_required_fields:
            if field not in config:
                raise ValueError(f"missing mandatory field in configuration: '{field}'")

        # Get optional 'execute_from' property and validate it
        execute_from = config.get("execute_from", None)
        if isinstance(execute_from, str):
            execute_from = Path(self._tool_box.get_expanded_path(execute_from))
            # Validate it's a path since we have it.
            if not execute_from.is_dir():
                raise ValueError(f"invalid source directory: '{execute_from}'")

        # Get the target architecture from the tool chin object
        architecture = self._toolchain.get_value("architecture")
        build_target_string = (f"{Fore.LIGHTBLUE_EX}{build_profile.project_name}"
                               f"{Style.RESET_ALL}/{build_profile.config_name}")

        # Reset 'last build' variables'
        self._variables.remove(key="LAST_BUILD_CONFIGURATION")
        self._variables.remove(key="LAST_BUILD_PROJECT")
        self._variables.remove(key="LAST_BUILD_PATH")

        # Gets the exact compiler path from the toolchain class
        build_command = self._toolchain.get_tool('make')
        self.print_message(f"Build of '{build_target_string}' for {architecture} starting...")

        # Process pre-build steps if specified
        steps_data: Optional[dict[str, str]] = config.get("pre_build_steps", {})
        if steps_data:
            self._process_build_steps(steps=steps_data, is_pre=True)

        # Validate or create build_path
        build_path = Path(config["build_path"]).expanduser().resolve()
        if not build_path.exists():
            try:
                build_path.mkdir(parents=True)
            except Exception as make_dir_error:
                raise RuntimeError(f"failed to create build path: "
                                   f"'{build_path}': {make_dir_error}") from make_dir_error

        if not build_path.is_dir():
            raise ValueError(f"build path is not a directory: '{build_path}'")

        # Optional, additional environment keys
        environment_data: Optional[dict[str, str]] = config.get("environment", None)

        compiler_options = config["compiler_options"]
        artifacts = config["artifacts"]

        # Prepare the 'make' command line
        command_line = [build_command, *compiler_options]

        # Execute
        try:
            self.print_message(message=f"Executing build in '{execute_from}'")
            results = self.sdk.platform.execute_shell_command(command_and_args=command_line,
                                                              echo_type=TerminalEchoType.SINGLE_LINE,
                                                              cwd=str(execute_from),
                                                              env=environment_data,
                                                              apply_colorization=True,
                                                              leading_text=build_profile.terminal_leading_text)

        except Exception as execution_error:
            raise RuntimeError(f"build process failed to start: {execution_error}") from execution_error

        # Validate expected return code
        if results.return_code != 0:
            self.print_message(message=f"Build failed with error: {results.return_code}", log_level=logging.ERROR)
            if results.response:
                self.print_message(message=f"Build response: {results.response}", log_level=logging.ERROR)

            raise RuntimeError(f"build returned unexpected result code: {results.return_code}")

        # Process post build steps if specified
        steps_data: Optional[dict[str, str]] = config.get("post_build_steps", {})
        if steps_data:
            self._process_build_steps(steps=steps_data, is_pre=False)

        # Check for all expected artifacts
        missing_artifacts = []
        for artifact_path in artifacts:
            artifact_file = Path(artifact_path).expanduser().resolve()
            if not artifact_file.exists():
                missing_artifacts.append(str(artifact_file))
            else:
                base_artifact_file_name: str = os.path.basename(artifact_file)
                formated_size: str = self._tool_box.get_formatted_size(artifact_file.stat().st_size)
                self.print_message(message=f"Artifact '{base_artifact_file_name}' created, size: "
                                           f"{Fore.LIGHTYELLOW_EX}{formated_size}{Style.RESET_ALL}")

        if missing_artifacts:
            raise ValueError("missing expected build artifacts:" + "\n".join(missing_artifacts))

        # Update variables 'last build''
        self._variables.add(key="LAST_BUILD_CONFIGURATION", value=build_profile.config_name, update_if_exist=True)
        self._variables.add(key="LAST_BUILD_PROJECT", value=build_profile.project_name, update_if_exist=True)
        self._variables.add(key="LAST_BUILD_PATH", value=build_path, update_if_exist=True)

        self.print_message(message=f"Building of '{build_target_string}' was successful", log_level=logging.INFO)
        return results.return_code

    def _process_build_steps(self, steps: dict[str, str], is_pre: bool = True) -> None:
        """
        Execute a dictionary of build steps where values prefixed with '!' are run as cmd2 shell commands.
        Args:
            steps (dict[str, str]): A dictionary of named build steps to execute.
            is_pre (bool): Specifies if those are pre- or post-build steps.
        """
        for step_name, command in steps.items():
            prefix = "pre" if is_pre else "post"

            self.print_message(message=f"Running {prefix}-build step: '{step_name}'")

            command = command.strip()

            if command.startswith("!"):
                command_line = command[1:].lstrip()
                try:
                    self.sdk.platform.execute_shell_command(command_and_args=command_line,
                                                            echo_type=TerminalEchoType.SINGLE_LINE)
                except Exception as execution_error:
                    self.print_message(message=f"Failed to execute '{step_name}': {execution_error}",
                                       log_level=logging.ERROR)
            else:
                self.print_message(message=f"Step '{step_name}' ignored: no '!' prefix", log_level=logging.WARNING)

    def build(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        Validates the provided build configuration and executes the corresponding build flow.
        Args:
            build_profile (BuildProfileType): The build profile containing solution, project, configuration,
                and toolchain information required for the build process.
        Returns:
            Optional[int]: The return code from the build process, or None if not applicable.
        """
        try:

            self._tool_box.set_cursor(visible=False)
            self._toolchain = BuilderToolChain(toolchain=build_profile.tool_chain_data, builder_instance=self)

            if not self._toolchain.validate():
                self.print_message(message="Toolchain validation failed", log_level=logging.ERROR)
                return 1

            return self._execute_build(build_profile=build_profile)

        except Exception as build_error:
            self.print_message(message=f"{build_error}", log_level=logging.ERROR)
            return 1

        finally:
            self._tool_box.set_cursor(visible=True)
