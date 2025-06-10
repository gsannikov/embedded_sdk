"""
Script:         cmake_builder.py
Author:         AutoForge Team

Description:
    A builder implementation tailored for handling CMake-based builds, with or without Ninja.
    It provides the `_MakeToolChain` class, a concrete implementation of the `BuilderToolChainInterface`,
    which validates and resolves tools required for Make-driven workflows.

Classes:
    - _CMakeToolChain: Validates and resolves required tools for Make-based toolchains.
"""

import logging
import os
from enum import Enum, auto
from pathlib import Path
from typing import Any
from typing import Optional

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (BuilderRunnerInterface, BuilderToolChain, BuildProfileType, TerminalEchoType,
                        CoreEnvironment, CorePrompt, )

AUTO_FORGE_MODULE_NAME = "cmake"
AUTO_FORGE_MODULE_DESCRIPTION = "CMake builder"
AUTO_FORGE_MODULE_VERSION = "1.0"


class _CMakeBuildStep(Enum):
    PRE_CONFIGURE = auto()
    CONFIGURE = auto()  # cmake -S . -B build/ ...
    PRE_BUILD = auto()
    BUILD = auto()  # ninja (or cmake --build)
    POST_BUILD = auto()
    DONE_BUILD = auto()


class ExitBuildEarly(Exception):
    def __init__(self, message: str = "", exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


# noinspection DuplicatedCode
class CMakeBuilder(BuilderRunnerInterface):
    """
    Implementation of the BuilderInterface for CMake-based builds.
    This builder executes the 'cmake'  or 'ninja' command using a validated toolchain and configuration
    provided by the surrounding build context. It is intended for use with projects that
    define their build process via CMakeFiles.txt files.
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
        self._toolchain: Optional[BuilderToolChain] = None
        self._state: _CMakeBuildStep = _CMakeBuildStep.PRE_CONFIGURE

        super().__init__(build_system=AUTO_FORGE_MODULE_NAME)

    @staticmethod
    def _is_cmake_configuration_command(cmd: list[str]) -> bool:
        """
        Determine if the given command is a CMake configuration command (phase 1 of a two-phase build).

        This identifies commands that:
        - Use '-G', '-S', or '-B'
        - Include any '-D' definitions (e.g., -DCMAKE_C_COMPILER)

        These indicate the command is setting up a build system (typically generating build.ninja or Makefiles),
        not executing the actual build (which is done by Ninja or `cmake --build`).
        Returns:
            True if this is a CMake configuration command, False otherwise.
        """
        return (
                "cmake" in cmd[0]
                and any(flag in cmd for flag in ("-G", "-S", "-B"))
                or any(arg.startswith("-D") for arg in cmd)
        )

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
        self._environment = CoreEnvironment.get_instance()

        if not isinstance(config, dict):
            raise ValueError("build profile contain invalid configuration")

        # Those are essential properties we must get
        mandatory_required_fields = ["build_path", "compiler_options", "artifacts"]

        # Validate required fields
        for field in mandatory_required_fields:
            if field not in config:
                raise ValueError(f"missing mandatory field in configuration: '{field}'")

        # Update step and optionally handle extra argumnets based on the current state
        self._set_state(build_state=_CMakeBuildStep.PRE_CONFIGURE, extra_args=build_profile.extra_args, config=config)

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
        ninja_build_command = self._toolchain.get_tool('ninja')
        build_command = self._toolchain.get_tool('cmake')
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
        cmake_options = (
            build_profile.tool_chain_data
            .get("required_tools", {})
            .get("cmake", {})
            .get("options", [])
        )

        compiler_options = config.get("compiler_options")
        artifacts: Optional[list[str]] = config.get("artifacts", None)

        # Merge cmake specific options from the tool chain with the build configuration options into  single list
        if cmake_options and compiler_options:
            merged_options = cmake_options + compiler_options
        else:
            merged_options = cmake_options or compiler_options or []

        # Prepare the 'cmake' command line
        command_line = [build_command, *merged_options]
        is_config_step = self._is_cmake_configuration_command(cmd=command_line)
        # Execute CMake, note that pending on the compilation options this could a single run or the first run
        # out of 2 when building with Ninja.
        try:

            # Update step and optionally handle extra argumnets based on the current state
            self._set_state(build_state=_CMakeBuildStep.PRE_CONFIGURE, extra_args=build_profile.extra_args,
                            config=config)

            self.print_message(message=f"Configuring in '{execute_from}'")
            results = self._environment.execute_shell_command(command_and_args=command_line,
                                                              echo_type=TerminalEchoType.LINE,
                                                              cwd=str(execute_from),
                                                              leading_text=build_profile.terminal_leading_text)

        except Exception as execution_error:
            raise RuntimeError(f"build process failed to start: {execution_error}") from execution_error

        # Validate CMake results
        self.print_build_results(results=results, raise_exception=True)

        # Update step and optionally handle extra argumnets based on the current state
        self._set_state(build_state=_CMakeBuildStep.PRE_BUILD, extra_args=build_profile.extra_args, config=config)

        # Check if the previous step was configuration and if so verify that we have Ninja
        if is_config_step and ninja_build_command is not None:
            try:

                # Update step and optionally handle extra argumnets based on the current state
                self._set_state(build_state=_CMakeBuildStep.BUILD, extra_args=build_profile.extra_args, config=config)

                ninja_command_line = f"{ninja_build_command} -C {str(build_path)}"
                results = self._environment.execute_shell_command(command_and_args=ninja_command_line,
                                                                  echo_type=TerminalEchoType.LINE,
                                                                  cwd=str(execute_from),
                                                                  leading_text=build_profile.terminal_leading_text)
            except Exception as execution_error:
                raise RuntimeError(f"build process failed to start: {execution_error}") from execution_error

            # Validate CMaKE results
            self.print_build_results(results=results, raise_exception=True)

            # Update step and optionally handle extra argumnets based on the current state
            self._set_state(build_state=_CMakeBuildStep.POST_BUILD, extra_args=build_profile.extra_args, config=config)

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

        self.print_message(message=f"Building of '{build_target_string}' was successful", log_level=logging.INFO)

        # Update step and optionally handle extra argumnets based on the current state
        self._set_state(build_state=_CMakeBuildStep.DONE_BUILD, extra_args=build_profile.extra_args, config=config)
        return results.return_code

    def _set_state(self, build_state: _CMakeBuildStep,
                   extra_args: Optional[list[str]] = None,
                   config: Optional[dict[str, Any]] = None) -> int:
        """
        Setting the build state and handling optionally extra arguments which may have to be addressed
        at a specific state during the build.
        Args:
            build_state: The build state to set.
            extra_args: The extra arguments to pass to the build command.
            config: The configuration options to pass to the build command.
        Returns:
            int: exit code of the execute command.
        """
        self._state = build_state

        for arg in extra_args:
            if arg in ("--clean", "--clean_build"):
                clean_command: Optional[str] = config.get("clean", None)
                if isinstance(clean_command, str) and build_state == _CMakeBuildStep.PRE_BUILD:
                    exit_code = self._execute_single_step(command=clean_command, name=arg)
                    extra_args.remove(arg)
                    if arg == "--clean" and exit_code == 0:
                        raise ExitBuildEarly("Build stopped after clean", exit_code=exit_code)
                    elif exit_code != 0:
                        raise RuntimeError(f"Command failed with exit code: {exit_code}")
                    else:
                        return exit_code
        return 0

    def _execute_single_step(self, command: str, name: str) -> int:
        """
        Execute a single step
        """
        if command.startswith("!"):
            command = command[1:].lstrip()  # Remove a trailing '!'
        try:
            results = self._environment.execute_shell_command(command_and_args=command,
                                                              echo_type=TerminalEchoType.SINGLE_LINE)
            return results.return_code

        except Exception as execution_error:
            self.print_message(message=f"Failed to execute '{name}': {execution_error}", log_level=logging.ERROR)
            return 1

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
                self._execute_single_step(command=command, name=step_name)
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

        def _normalize_message(_s: str) -> str:
            """ Make sure the error message is trimmed, capitalized and has dit at the end """
            if not isinstance(_s, str):
                return _s
            _s = _s.strip().capitalize()
            return _s if _s.endswith('.') else _s + '.'

        try:
            print()
            self._tool_box.set_cursor(visible=False)
            self._toolchain = BuilderToolChain(toolchain=build_profile.tool_chain_data, builder_instance=self)

            if not self._toolchain.validate():
                self.print_message(message="Toolchain validation failed", log_level=logging.ERROR)
                return 1

            return self._execute_build(build_profile=build_profile)

        except ExitBuildEarly as exit_build:
            # Normal early build termination
            build_message = _normalize_message(str(exit_build))
            if exit_build.exit_code == 0:
                self.print_message(message=f"{build_message}", log_level=logging.INFO)
            else:
                self.print_message(message=f"{build_message} Exit code: {exit_build.exit_code}",
                                   log_level=logging.ERROR)
            return exit_build.exit_code

        except Exception as build_exception:
            build_message = _normalize_message(str(build_exception))
            self.print_message(message=build_message, log_level=logging.ERROR)
            return 1

        finally:
            self._tool_box.set_cursor(visible=True)
            print()
