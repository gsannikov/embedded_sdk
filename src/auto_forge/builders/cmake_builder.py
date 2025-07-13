"""
Script:         cmake_builder.py
Author:         AutoForge Team

Description:
    A builder implementation tailored for handling CMake-based builds, with or without Ninja.
    It provides the `_MakeToolChain` class, a concrete implementation of the `BuilderToolChainInterface`,
    which validates and resolves tools required for Make-driven workflows.

Classes:
    - _CMakeToolChain: Validates and resolves required tools for Make-based tool-chains.
"""

import logging
from enum import Enum, auto
from pathlib import Path
from typing import Any
from typing import Optional

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (BuilderRunnerInterface, BuilderToolChain, BuildProfileType, CommandFailedException,
                        BuilderArtifactsValidator, TerminalEchoType, GCCLogAnalyzer)

AUTO_FORGE_MODULE_NAME = "cmake"
AUTO_FORGE_MODULE_DESCRIPTION = "CMake builder"
AUTO_FORGE_MODULE_VERSION = "1.0"


class _CMakeBuildStep(Enum):
    PRE_CONFIGURE = auto()
    CONFIGURE = auto()
    PRE_BUILD = auto()
    BUILD = auto()
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

        self._toolchain: Optional[BuilderToolChain] = None
        self._last_rendered_ai_response: Optional[str] = None
        self._state: _CMakeBuildStep = _CMakeBuildStep.PRE_CONFIGURE
        self._gcc_analyzer = GCCLogAnalyzer()

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

        config: dict = build_profile.config_data
        if not isinstance(config, dict):
            raise ValueError("build profile contain invalid configuration")

        # Those are essential properties we must get
        mandatory_required_fields: list = ["build_path", "compiler_options", "artifacts"]

        # Erase intermediate generated files
        self._build_context_file.unlink(missing_ok=True)
        self._build_duplicate_symbols_file.unlink(missing_ok=True)
        self._build_ai_response_file.unlink(missing_ok=True)

        # Check if we should auto send build errors to the registered AI
        ai_auto_advise_config: Optional[bool] = self.sdk.solution.get_arbitrary_item(key="ai_auto_advise")
        ai_auto_advise = ai_auto_advise_config if isinstance(ai_auto_advise_config, bool) else False

        # Validate required fields
        for field in mandatory_required_fields:
            if field not in config:
                raise ValueError(f"missing mandatory field in configuration: '{field}'")

        # Update step and optionally handle extra arguments based on the current state
        self._set_state(build_state=_CMakeBuildStep.PRE_CONFIGURE, extra_args=build_profile.extra_args, config=config)

        # Get optional 'execute_from' property and validate it
        execute_from = config.get("execute_from", None)
        if isinstance(execute_from, str):
            execute_from = Path(self._tool_box.get_expanded_path(execute_from))
            # Validate it's a path since we have it.
            if not execute_from.is_dir():
                raise ValueError(f"invalid source directory: '{execute_from}'")

        # Get the target architecture from the tool chin object
        architecture = self._toolchain.get_value("architecture")
        build_target_message = (f"{Fore.LIGHTBLUE_EX}{build_profile.project_name}"
                                f"{Style.RESET_ALL}/{build_profile.config_name}")

        # Gets the exact compiler path from the toolchain class
        ninja_build_command = self._toolchain.get_tool('ninja')
        build_command = self._toolchain.get_tool('cmake')
        self.print_message(f"Build of '{build_target_message}' for {architecture} starting...")

        # Process pre-build steps if specified
        steps_data: Optional[list[dict[str, Any]]] = config.get("pre_build_steps", [])
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
        artifacts: Optional[list] = config.get("artifacts", None)

        # Merge cmake specific options from the tool chain with the build configuration options into  single list
        if cmake_options and compiler_options:
            merged_options = cmake_options + compiler_options
        else:
            merged_options = cmake_options or compiler_options or []

        # Prepare the 'cmake' command line
        command_line = [build_command, *merged_options]
        is_config_step = self._is_cmake_configuration_command(cmd=command_line)

        # Execute CMake, note that pending on the compilation options this could be a single
        # run or the first out of 2 when building with Ninja.
        try:
            # Update step and optionally handle extra arguments based on the current state
            self._set_state(build_state=_CMakeBuildStep.PRE_CONFIGURE, extra_args=build_profile.extra_args,
                            config=config)
            self.print_message(message=f"Configuring in '{execute_from}'")
            results = self.sdk.platform.execute_shell_command(command_and_args=command_line,
                                                              echo_type=TerminalEchoType.LINE,
                                                              cwd=str(execute_from),
                                                              leading_text=build_profile.terminal_leading_text)
        except CommandFailedException as execution_error:
            results = execution_error.results
            raise RuntimeError(
                f"CMake execution error {results.message if results else 'unknown'}") from execution_error

        # Validate CMake results
        self.print_build_results(results=results, raise_exception=True)

        # Update step and optionally handle extra arguments based on the current state
        self._set_state(build_state=_CMakeBuildStep.PRE_BUILD, extra_args=build_profile.extra_args, config=config)

        # Check if the previous step was configuration and if so verify that we have Ninja
        if is_config_step and ninja_build_command is not None:
            tool_error = False
            try:
                # Update step and optionally handle extra arguments based on the current state
                self._set_state(build_state=_CMakeBuildStep.BUILD, extra_args=build_profile.extra_args, config=config)

                ninja_verbose = self.sdk.build_shell.get_settable_param(name="ninja_verbose", default=False)
                ninja_max_cores = self.sdk.build_shell.get_settable_param(name="ninja_max_cores", default=16)

                # Construct Ninja command using optional settable parameters
                ninja_cmd = f"{ninja_build_command} -j{ninja_max_cores} -C {build_path}"
                if ninja_verbose:
                    ninja_cmd += " -v"

                results = self.sdk.platform.execute_shell_command(command_and_args=ninja_cmd,
                                                                  echo_type=TerminalEchoType.CLEAR_LINE,
                                                                  cwd=str(execute_from),
                                                                  leading_text=build_profile.terminal_leading_text)
            except CommandFailedException as execution_error:
                results = execution_error.results
                tool_error = True

            finally:
                if None not in (results, results.response):
                    if tool_error or "warning" in results.response:
                        # Ninja build error - start GCC log analyzer
                        if ai_auto_advise:
                            self.print_message(
                                message="🤖 AI request submitted in the background. You'll be notified once the response is ready.")
                            self._gcc_analyzer.analyze(log_source=results.response,
                                                       context_file_name=str(self._build_context_file),
                                                       ai_response_file_name=str(self._build_ai_response_file),
                                                       ai_auto_advise=ai_auto_advise,
                                                       toolchain=self._toolchain.tools)
                        else:
                            self.print_message(message="🤖 AI Advise disabled.")

                if tool_error:
                    raise RuntimeError(
                        f"Ninja execution error {results.message if results else 'unknown'}")

                    # Validate CMaKE results
            self.print_build_results(results=results, raise_exception=True)

            # Update step and optionally handle extra arguments based on the current state
            self._set_state(build_state=_CMakeBuildStep.POST_BUILD, extra_args=build_profile.extra_args, config=config)

        # Process post build steps if specified
        steps_data: Optional[list[dict[str, Any]]] = config.get("post_build_steps", [])
        if steps_data:
            self._process_build_steps(steps=steps_data, is_pre=False)

        # Validate / process artifacts
        BuilderArtifactsValidator(artifact_list=artifacts)

        self.print_message(message=f"Building of '{build_target_message}' was successful", log_level=logging.INFO)

        # Update step and optionally handle extra arguments based on the current state
        self._set_state(build_state=_CMakeBuildStep.DONE_BUILD, extra_args=build_profile.extra_args, config=config)

        # Libraries analysis
        nm_command = self._toolchain.get_tool('nm')
        self.analyze_library_exports(path=str(build_path), nm_tool_name=nm_command, max_libs=100,
                                     json_report_path=str(self._build_duplicate_symbols_file))

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
            results = self.sdk.platform.execute_shell_command(command_and_args=command,
                                                              echo_type=TerminalEchoType.SINGLE_LINE)
            return results.return_code

        except Exception as execution_error:
            self.print_message(message=f"Failed to execute '{name}': {execution_error}", log_level=logging.ERROR)
            return 1

    def _process_build_steps(self, steps: list[dict[str, Any]], is_pre: bool = True) -> None:
        """
        Execute a list of build steps where values prefixed with '!' are run as cmd2 shell commands.
        Args:
            steps (dict[str, str]): A dictionary of named build steps to execute.
            is_pre (bool): Specifies if those are pre- or post-build steps.
        """
        for step in steps:
            step_name = step.get("name", "Unknown")
            step_command = step.get("command", "").lstrip()
            prefix = "pre" if is_pre else "post"

            self.print_message(message=f"Running {prefix}-build step: '{step_name}'")
            if step_command.startswith("!"):
                self._execute_single_step(command=step_command, name=step_name)
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

            # Add shell optional settable parameters specifically for CMake and Ninja
            self.sdk.build_shell.add_settable_param(
                name="ninja_verbose", default=False, doc="Enable verbose output when running Ninja builds")
            self.sdk.build_shell.add_settable_param(
                name="ninja_max_cores", default=self.sdk.system_info.cpu_count,
                doc="Maximum number of CPU cores Ninja is allowed to use")

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
