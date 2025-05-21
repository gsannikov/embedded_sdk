"""
Script:         make_builder.py
Author:         AutoForge Team

Description:
    A builder implementation tailored for handling Make-based build systems.
    It provides the `_MakeToolChain` class, a concrete implementation of the `BuilderToolChainInterface`,
    which validates and resolves tools required for Make-driven workflows.

Classes:
    - _MakeToolChain: Validates and resolves required tools for Make-based toolchains.
"""

import logging
import os
from pathlib import Path
from typing import Any
from typing import Optional

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (
    BuilderInterface,
    BuilderToolChainInterface,
    BuildProfileType,
    TerminalEchoType,
    CoreEnvironment,
    CorePrompt,
)

AUTO_FORGE_MODULE_NAME = "make"
AUTO_FORGE_MODULE_DESCRIPTION = "make files builder"
AUTO_FORGE_MODULE_VERSION = "1.0"


class _MakeToolChain(BuilderToolChainInterface):
    """
    Concrete implementation of BuilderToolChainInterface for Make-based build systems.
    The resolved tool paths are stored and can be queried using `get_tool()`.
    """

    def validate(self, show_help_on_error: bool = False) -> None:
        """
        Validates the toolchain structure and required tools specified by the solution.
        For each tool:
          - Attempts to resolve the binary using the defined path.
          - Confirms the version requirement is met.
          - Optionally shows help (Markdown-rendered) if validation fails.
        Args:
            show_help_on_error (bool): Show help message if validation fails.
        """

        required_keys = {"name", "platform", "architecture", "build_system", "required_tools"}
        missing = required_keys - self._toolchain.keys()
        if missing:
            raise ValueError(f"missing top-level toolchain keys: {missing}")

        tools = self._toolchain["required_tools"]
        if not isinstance(tools, dict) or not tools:
            raise ValueError("'required_tools' must be a non-empty dictionary")

        for name, definition in tools.items():
            if not isinstance(definition, dict):
                raise ValueError(f"Tool '{name}' definition must be a dictionary")

            tool_path = definition.get("path")
            version_expr = definition.get("version")
            help_path = definition.get("help")

            if not tool_path or not version_expr:
                raise ValueError(f"toolchain element '{tool_path}' must define 'path' and 'version' fields")

            resolved_t_tool_path = self._resolve_tool([tool_path], version_expr)
            if not resolved_t_tool_path:

                # Exit without attempting to locate and the troubleshooting md file.
                if not show_help_on_error:
                    return None

                self._builder_instance.print_message(
                    message=f"toolchain item '{tool_path}' not found or not satisfied", log_level=logging.ERROR)

                if help_path:
                    if self._tool_box.show_help_file(help_path) != 0:
                        self._builder_instance.print_message(
                            message=f"Error displaying help file '{help_path}' see log for details",
                            log_level=logging.WARNING)

                    raise RuntimeError(f"missing toolchain component: {name}")

            self._resolved_tools[name] = resolved_t_tool_path
        return None


class MakeBuilder(BuilderInterface):
    """
    Implementation of the BuilderInterface for Make-based build systems.
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
        self._environment: Optional[CoreEnvironment] = None
        self._prompt: Optional[CorePrompt] = None
        self._toolchain: Optional[BuilderToolChainInterface] = None

        super().__init__(build_system=AUTO_FORGE_MODULE_NAME)

    def _execute_build(  # noqa: C901
            self,
            build_profile: BuildProfileType) -> Optional[int]:
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
        self._environment = CoreEnvironment.get_instance()

        # Those are essential properties we must get
        mandatory_required_fields = ["build_path", "compiler_options", "artifacts"]

        # Validate required fields
        for field in mandatory_required_fields:
            if field not in config:
                raise ValueError(f"missing mandatory field in configuration: '{field}'")

        # Get optional 'execute_from' property and validate it
        execute_from = config.get("execute_from", None)
        if execute_from is not None:
            execute_from = Path(self._tool_box.get_expanded_path(execute_from))
            # Validate it's a path since we have it.
            if not execute_from.is_dir():
                raise ValueError(f"invalid source directory: '{execute_from}'")

        # Get the target architecture from the tool chin object
        architecture = self._toolchain.get_value("architecture")
        build_target_string = (f"{Fore.LIGHTBLUE_EX}{build_profile.project_name}"
                               f"{Style.RESET_ALL}/{build_profile.config_name}")

        # Gets the exact compiler path from the toolchain class
        build_command = self._toolchain.get_tool('make')
        self.print_message(f"Build of '{build_target_string}' for {architecture} starting...")

        # Process pre-build steps if specified
        steps_data: Optional[dict[str, str]] = config.get("pre_build_steps", {})
        if steps_data:
            self._process_build_steps(steps=steps_data, do_clean=build_profile.do_clean, is_pre=True)

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

        compiler_options = config["compiler_options"]
        artifacts = config["artifacts"]

        # Prepare the 'make' command line
        command_line = [build_command, *compiler_options]

        # Execute
        try:
            self.print_message(message=f"Executing build in '{execute_from}'")
            results = self._environment.execute_shell_command(
                command_and_args=command_line,
                echo_type=TerminalEchoType.SINGLE_LINE,
                cwd=str(execute_from),
                leading_text=build_profile.terminal_leading_text,
                expand_command=True)

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
            self._process_build_steps(steps=steps_data, do_clean=build_profile.do_clean, is_pre=False)

        # Check for all expected artifacts
        missing_artifacts = []
        for artifact_path in artifacts:
            artifact_file = Path(artifact_path).expanduser().resolve()
            if not artifact_file.exists():
                missing_artifacts.append(str(artifact_file))
            else:
                base_artifact_file_name: str = os.path.basename(artifact_file)
                formated_size: str = self._tool_box.get_formatted_size(artifact_file.stat().st_size)
                self.print_message(
                    message=f"Artifact '{base_artifact_file_name}' created, size: "
                            f"{Fore.LIGHTYELLOW_EX}{formated_size}{Style.RESET_ALL}")

        if missing_artifacts:
            raise ValueError("missing expected build artifacts:" + "\n".join(missing_artifacts))

        self.print_message(message=f"Building of '{build_target_string}' was successful", log_level=logging.INFO)
        return results.return_code

    def _process_build_steps(self, steps: dict[str, str], do_clean: bool = False, is_pre: bool = True) -> None:
        """
        Execute a dictionary of build steps where values prefixed with '!' are run as cmd2 shell commands.
        Args:
            steps (dict[str, str]): A dictionary of named build steps to execute.
            do_clean (bool): Process steps that carries 'clean' label.
            is_pre (bool): Specifies if those are pre- or post-build steps.
        """
        for step_name, command in steps.items():
            prefix = "pre" if is_pre else "post"

            # Skip cleaning steps
            if not do_clean and step_name == 'clean':
                continue

            self.print_message(message=f"Running {prefix}-build step: '{step_name}'")

            command = command.strip()

            if command.startswith("!"):
                command_line = command[1:].lstrip()
                try:
                    self._environment.execute_shell_command(
                        command_and_args=command_line,
                        echo_type=TerminalEchoType.SINGLE_LINE,
                        expand_command=True)
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
            print()
            self._tool_box.set_cursor(visible=False)
            self._toolchain = _MakeToolChain(toolchain=build_profile.tool_chain_data, builder_instance=self)
            build_status = self._execute_build(build_profile=build_profile)

        except Exception as build_error:
            self.print_message(message=f"{build_error}", log_level=logging.ERROR)
            build_status = 1

        finally:
            self._tool_box.set_cursor(visible=True)
            print()

        return build_status
