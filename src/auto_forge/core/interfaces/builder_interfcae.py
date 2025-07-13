"""
Script:         builder_interface.py
Author:         AutoForge Team

Description:
    Core abstract base class that defines a standardized interface for implementing a builder instance.
    Each builder implementation is registered at startup with a unique name, and can be invoked as needed based on the
    solution branch configuration, which specifies the registered name of the builder.
"""
import glob
import inspect
import json
import logging
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Optional, Tuple, Union, Any, TYPE_CHECKING

# Third-party
from colorama import Fore, Style

# AutoForge imports
from auto_forge import (AutoForgeModuleType, ModuleInfoType, BuildProfileType, CoreContext,
                        CommandResultType, SDKType, VersionCompare)

# Lazy import SDK class instance
if TYPE_CHECKING:
    from auto_forge import SDKType

# Module identification
AUTO_FORGE_MODULE_NAME = "BuilderInterface"
AUTO_FORGE_MODULE_DESCRIPTION = "Dynamic loadable builder interface"


class BuilderArtifactsValidator:
    """
    Handles validation and resolution of build artifact descriptors.

    Each artifact descriptor must include:
        - 'name': Arbitrary identifier for the artifact group.
        - 'path': Absolute or relative path (can include wildcards).
        - Optional 'recursive': bool (default True for wildcards) — controls glob recursion.

    After validation, exposes a mapping of 'name' to a list of resolved file paths.
    """

    def __init__(self, artifact_list: list[dict]):
        self._artifact_list = artifact_list
        self._resolved: dict[str, list[Path]] = {}
        self._validate_and_resolve()

    def _validate_and_resolve(self):
        for i, artifact in enumerate(self._artifact_list):
            if not isinstance(artifact, dict):
                raise TypeError(f"Artifact entry at index {i} must be a dictionary.")

            name = artifact.get("name")
            path_str = artifact.get("path")
            recursive = artifact.get("recursive", True)
            copy_to_path = artifact.get("copy_to")

            if not name or not path_str:
                raise ValueError(f"Artifact entry {artifact} must include 'name' and 'path'.")

            path_obj = Path(path_str)

            if "*" in path_str or "?" in path_str or "[" in path_str:
                matched_files = [
                    Path(p).resolve() for p in glob.glob(path_str, recursive=recursive)
                ]
                if not matched_files:
                    raise FileNotFoundError(f"No files matched wildcard path: {path_str}")
                self._resolved[name] = matched_files
            else:
                resolved_file = path_obj.resolve()
                if not resolved_file.exists():
                    raise FileNotFoundError(f"Expected file not found: {resolved_file}")
                self._resolved[name] = [resolved_file]

            # If copy_to is specified, perform immediate copy
            if copy_to_path:
                self._copy_to(group_name=name, destination=copy_to_path, preserve_structure=False)

    def _copy_to(self, group_name: str, destination: str, preserve_structure: bool = True):
        """
        Copy all files from the specified group to the destination directory.
        Args:
            group_name (str): The artifact group name.
            destination (str): Destination directory path.
            preserve_structure (bool): If True, recreate folder structure from the
                                       common root down. If False, flatten all files.
        """
        if group_name not in self._resolved:
            raise KeyError(f"Group '{group_name}' not found in resolved artifacts.")

        files = self._resolved[group_name]
        if not files:
            raise ValueError(f"No files found in group '{group_name}'.")

        destination_path = Path(destination).resolve()
        destination_path.mkdir(parents=True, exist_ok=True)
        common_base_path: Optional[Path] = None
        files_copied: int = 0

        if preserve_structure:
            try:
                # Extract common parent of all file *directories*
                common_base = os.path.commonpath([str(p.parent) for p in files])
                common_base_path = Path(common_base).resolve()
            except Exception as e:
                raise RuntimeError(f"Failed to compute common base path: {e}")
        for src in files:
            if preserve_structure:
                try:
                    relative_subpath = src.parent.relative_to(common_base_path)
                except ValueError:
                    raise ValueError(f"File {src} is not under common base path {common_base_path}")
                dest_dir = destination_path / relative_subpath
            else:
                dest_dir = destination_path

            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_dir / src.name)
            files_copied += 1

    def get_resolved_artifacts(self) -> dict[str, list[Path]]:
        """
        Returns:
            dict[str, list[Path]]: Mapping of artifact name to resolved file paths.
        """
        return self._resolved


class BuilderToolChain:
    """
    Toolchain validation auxiliary class
    """

    def __init__(self, toolchain: dict[str, object], builder_instance: Optional["BuilderRunnerInterface"]) -> None:
        """
        Checks that the specified tool chin exists and that its different components,has the correct version.
        Args:
            toolchain (dict[str, object]): The toolchain to check.
            builder_instance (Optional[BuilderRunnerInterface]): The parent builder instance.

        """
        self._toolchain = toolchain
        self._resolved_tools: dict[str, str] = {}
        self._builder_instance = builder_instance
        self._registry = self._builder_instance.sdk.registry
        self._tool_box = self._builder_instance.sdk.tool_box

        if self._tool_box is None:
            raise RuntimeError("unable to instantiate dependent core module")

    def validate(self, show_help_on_error: bool = False) -> Optional[bool]:
        """
        Validates the toolchain structure and required tools specified by the solution.
        For each tool:
          - Attempts to resolve the binary using the defined path.
          - Confirms the version requirement is met.
          - Optionally shows help (Markdown-rendered) if validation fails.
        Args:
            show_help_on_error (bool): Show help message if validation fails.
        Return:
            bool: True if validation passes, otherwise an exception is raised.
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
                # If we have to auto show help
                if show_help_on_error:
                    if help_path:
                        if self._tool_box.show_markdown_file(help_path) != 0:
                            self._builder_instance.print_message(
                                message=f"Error displaying help file '{help_path}' see log for details",
                                log_level=logging.WARNING)
                # Break the build
                raise RuntimeError(f"missing toolchain component: {name}")

            self._resolved_tools[name] = resolved_t_tool_path

        return True

    def get_tool(self, tool_name: str) -> Optional[str]:
        """
        Returns the resolved absolute path of the specified tool name,
        or None if not found.
        """
        return self._resolved_tools.get(tool_name)

    def get_value(self, key_name: str) -> Optional[str]:
        """
        Returns the value of a top-level key in the toolchain dictionary,
        only if it is a string. Returns None otherwise.
        """
        value = self._toolchain.get(key_name)
        return value if isinstance(value, str) else None

    def _resolve_tool(self, candidates: list[str], version_expr: str) -> Optional[str]:
        """
        Attempts to locate a binary from the provided list of candidates that satisfies the required version expression.
        Args:
            candidates: A list of binary names or absolute paths to check.
            version_expr: A version requirement string (e.g., ">=3.2").
        Returns:
            The resolved binary path if found and version is valid, otherwise None.
        """
        for binary in candidates:
            path = binary if os.path.isabs(binary) else shutil.which(binary)

            if not path:
                self._builder_instance.print_message(message=f"Toolchain item '{binary}' not found.",
                                                     log_level=logging.ERROR)
                continue

            version_ok, detected_version = self._version_ok(path, version_expr)
            if not version_ok:
                base_name = os.path.basename(path)
                if detected_version:
                    msg = (f"Toolchain item '{base_name}' version {detected_version} "
                           f"does not satisfy required {version_expr}.")
                else:
                    msg = f"Toolchain item '{base_name}' version could not be determined."
                self._builder_instance.print_message(message=msg, log_level=logging.ERROR)
                continue
            return path
        return None

    @staticmethod
    def _version_ok(binary_path: str, version_expr: str) -> Optional[tuple[bool, Optional[str]]]:
        """
        Checks whether the binary at binary_path satisfies the version constraint (e.g., ">=10.0").
        Args:
            binary_path (str): Path to the binary.
            version_expr (str): Version constraint expression (e.g., ">=10.0", "==1.2.3").
        Returns:
            Tuple[bool, Optional[str]]: A tuple of (is_satisfied, detected_version_str).
        """
        try:
            # Run the binary with --version and capture output
            binary_output = subprocess.check_output(args=[binary_path, "--version"], stderr=subprocess.STDOUT,
                                                    text=True)

            compare_results = VersionCompare().compare(detected=binary_output, expected=version_expr)
            return compare_results

        except Exception as version_verify_error:
            raise version_verify_error from version_verify_error

    @property
    def tools(self) -> dict[str, str]:
        """ Gets the the resolved tools dictionary """
        return self._resolved_tools


class BuildLogAnalyzerInterface(ABC):
    """
    Abstract base class defining the interface for log analysis.
    Any specific log analyzer (e.g., GCC, Clang, Java) should
    inherit from this interface and implement the 'analyze' method.
    """

    def __init__(self):
        # Keep track of last analysis
        self._last_analysis: Optional[list[dict[str, Union[str, int, None, list[str]]]]] = None
        self._core_logger = self.sdk.logger
        self._logger = self.sdk.logger.get_logger(name="GCCAnalyzer")

    @abstractmethod
    def analyze(self, log_source: Union[Path, str],
                context_file_name: Optional[str] = None,
                ai_response_file_name: Optional[str] = None,
                ai_auto_advise: Optional[bool] = False,
                toolchain: Optional[dict[str, Any]] = None) -> Optional[list[dict]]:
        """
        Analyzes a compilation log and extracts structured diagnostic events.

        A parser that identifies and collects warnings, errors, and notes emitted by the build tool,
        grouping them into structured event entries that include source file, line, column,
        type, function context, and a cleaned message string. It handles multi-line diagnostics
        (including caret and source lines), removes duplicated prefixes like "warning:", and
        associates diagnostics with detected function names where available.
        Args:
            log_source: Path to a file or raw log string containing compiler output.
            context_file_name: Path to store structured diagnostics (JSON).
            ai_response_file_name: Path for AI response Markdown (optional).
            ai_auto_advise: Auto forward the error context to an AI
            toolchain: The tool-chain dictionary used to during this build.
.
        Returns:
            List of structured diagnostic dictionaries, or None if no diagnostics found.
        """
        raise NotImplementedError("Subclasses must implement the 'analyze' method.")

    @property
    def sdk(self) -> SDKType:
        """
        Returns the global SDK singleton instance, which holds references
        to all registered core module instances.
        This property provides convenient access to the centralized SDKType
        container, after all core modules have registered themselves during
        initialization.
        """
        return SDKType.get_instance()


class BuilderRunnerInterface(ABC):
    """
    Abstract base class for builder instances that can be dynamically registered and executed by AutoForge.
    """

    def __init__(self, build_system: Optional[str] = None, build_label: Optional[str] = None):
        """
        Initializes the builder and registers it with the AutoForge registry.

        Args:
            build_system (str, optional): The unique name of the builder instance build system to use, for ex.
                make, cmake and so on. If not provided, the value of the
                class field 'AUTO_FORGE_MODULE_NAME' will be used.
            build_label (str, optional): The unique name of the builder instance build label to use.
        """

        self._registry = self.sdk.registry
        self._build_context_file: Optional[Path] = None
        self.build_logs_path: Optional[Path] = None

        # Probe caller globals for command description and name
        caller_frame = inspect.stack()[1].frame
        caller_globals = caller_frame.f_globals
        caller_module_name = caller_globals.get("AUTO_FORGE_MODULE_NAME", None)
        caller_module_description = caller_globals.get("AUTO_FORGE_MODULE_DESCRIPTION", "Description not provided")
        caller_module_version = caller_globals.get("AUTO_FORGE_MODULE_VERSION", "0.0.0")

        self._build_system: str = build_system if build_system is not None else caller_module_name
        if self._build_system is None:
            raise RuntimeError("build_system properties cannot be None")

        # Set optional build label
        self._build_label: str = build_label if build_label is not None else None

        # Register this builder instance in the global registry for centralized access
        self._module_info: ModuleInfoType = (
            self._registry.register_module(name=self._build_system, description=caller_module_description,
                                           version=caller_module_version,
                                           auto_forge_module_type=AutoForgeModuleType.BUILDER))

        # Get configuration from the root auto_forge class through context provider
        self._configuration = CoreContext.get_config_provider().configuration
        self._core_logger = self.sdk.logger
        self._logger = self.sdk.logger.get_logger(name=self._build_system.capitalize())
        self._tool_box = self.sdk.tool_box

        # Dependencies check
        if None in (self._logger, self._tool_box):
            raise RuntimeError("unable to instantiate dependent core")

        try:
            # Construct optional intermediate autogenerated file names we might generate
            self.build_logs_path = self._tool_box.get_valid_path(self.sdk.variables.get("BUILD_LOGS"),
                                                                 create_if_missing=False)
            context_file: str = self._configuration.get("build_error_context_file", "build_error_context.json")
            duplicate_symbols_file: str = self._configuration.get("build_duplicate_symbols_file",
                                                                  "build_duplicate_symbols.json")
            ai_response_file: str = self._configuration.get("build_ai_response_file", "build_ai_response.md")

            self._build_context_file = self.build_logs_path / context_file
            self._build_duplicate_symbols_file = self.build_logs_path / duplicate_symbols_file
            self._build_ai_response_file = self.build_logs_path / ai_response_file

            # Erase them
            self._build_context_file.unlink(missing_ok=True)
            self._build_duplicate_symbols_file.unlink(missing_ok=True)
            self._build_ai_response_file.unlink(missing_ok=True)


        except Exception as path_prep_error:
            raise RuntimeError(f"failed to prepare build paths {path_prep_error}")

    @abstractmethod
    def build(self, build_profile: BuildProfileType) -> Optional[int]:
        """
        Validates the provided build configuration and executes the corresponding build flow.
        Args:
            build_profile (BuildProfileType): The build profile containing solution, project, configuration,
                and toolchain information required for the build process.
        Returns:
            Optional[int]: The return code from the build process, or None if not applicable.
        """
        raise NotImplementedError("must implement 'build'")

    def get_info(self) -> ModuleInfoType:
        """
        Retrievers information about the implemented builder.
        Note: Implementation class must call _set_info().
        Returns:
            ModuleInfoType: a named tuple containing the implemented command id
        """
        if self._module_info is None:
            raise RuntimeError('command info not initialized, make sure call set_info() first')

        return self._module_info

    def print_build_results(self, results: Optional[CommandResultType], raise_exception: bool = True) -> Optional[int]:
        """
        Handle and report the result of a build command.
        Args:
            results: The command result object containing return code and optional response.
            raise_exception: Whether to raise an exception if the build failed.
        Returns:
            The return code if results are provided; otherwise, None.
        """
        if results is None:
            return 1  # Error

        if results.return_code != 0:
            self.print_message(message=f"Build failed with error code: {results.return_code}", log_level=logging.ERROR)
            if results.response:
                self.print_message(message=f"Build response: {results.response}", log_level=logging.ERROR)

            if raise_exception:
                raise RuntimeError(f"Build failed with return code: {results.return_code}")

        return results.return_code

    def analyze_library_exports(self,
                                path: str,
                                nm_tool_name: str = "nm",
                                max_libs: int = 50,
                                json_report_path: Optional[str] = None) -> dict[str, list[str]]:
        """
        Analyzes exported symbols from .so files in the given directory and optionally
        exports a JSON report of the analysis.

        Args:
            path (str): The root path to search for .so files.
            nm_tool_name (str): The tool to use for extracting symbols (e.g., 'nm', 'readelf').
                                Defaults to 'nm'.
            max_libs (int): The maximum number of .so files to process. Defaults to 50.
            json_report_path (Optional[str]): If provided, the path where the JSON report
                                             of the analysis results will be saved.
                                             Defaults to None (no JSON report).

        Returns:
            dict[str, list[str]]: A dictionary mapping .so file paths to a list of their exported
                                 function names.
        """
        if not os.path.isdir(path):
            self.print_message(message=f"Error: Provided path '{path}' is not a valid directory.",
                               log_level=logging.ERROR)
            return {}

        try:
            subprocess.run([nm_tool_name, '--version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.print_message(
                message=f"Error: The tool '{nm_tool_name}' was not found or is not executable. "
                        "Please ensure it's installed and in your system's PATH.",
                log_level=logging.ERROR
            )
            return {}

        so_exports: dict[str, list[str]] = {}
        seen_symbols: dict[str, list[str]] = defaultdict(list)
        processed_libs = 0

        for root, _, files in os.walk(path):
            for file_name in files:
                if file_name.endswith(".so"):
                    full_path = os.path.join(root, file_name)
                    if processed_libs >= max_libs:
                        self.print_message(
                            message=f"Reached maximum limit of {max_libs} libraries. Stopping scan.",
                            log_level=logging.INFO
                        )
                        break

                    try:
                        result = subprocess.run(
                            [nm_tool_name, '-D', '-g', '--defined-only', full_path],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            text=True,
                            check=True,
                            timeout=30
                        )
                        symbols: list[str] = []
                        for line in result.stdout.splitlines():
                            parts = line.strip().split()
                            if len(parts) >= 3 and parts[-2] in ('T', 't'):
                                symbol_name = parts[-1]
                                symbols.append(symbol_name)
                                seen_symbols[symbol_name].append(full_path)

                        so_exports[full_path] = symbols
                        processed_libs += 1

                    except subprocess.CalledProcessError as e:
                        self.print_message(
                            message=f"Warning: Failed to analyze '{full_path}'. Error: {e}",
                            log_level=logging.WARNING
                        )
                    except subprocess.TimeoutExpired:
                        self.print_message(
                            message=f"Warning: Analysis of '{full_path}' timed out.",
                            log_level=logging.WARNING
                        )
                    except Exception as e:
                        self.print_message(
                            message=f"An unexpected error occurred while processing '{full_path}': {e}",
                            log_level=logging.ERROR
                        )
            if processed_libs >= max_libs:
                break

        # Generate and save JSON report if json_report_path is provided
        if json_report_path:
            self._export_symbol_conflicts_report(so_exports, seen_symbols, processed_libs, json_report_path)

        self._report_symbol_conflicts(seen_symbols, processed_libs)
        return so_exports

    def _report_symbol_conflicts(self, seen_symbols: dict[str, list[str]], processed_libs: int):
        """
        Reports any duplicate symbols found across libraries.
        """
        conflicts = {sym: paths for sym, paths in seen_symbols.items() if len(paths) > 1}

        if conflicts:
            self.print_message(message="Duplicate Symbols Detected", log_level=logging.WARNING)
            for sym, libs in conflicts.items():
                self.print_message(message=f"Symbol '{sym}' found in multiple libraries", log_level=logging.WARNING)
                for lib_full_path in libs:
                    self.print_message(message=f"> {os.path.basename(lib_full_path)}", log_level=logging.WARNING)
        else:
            self.print_message(
                message=f"✅ No duplicate symbols found across {processed_libs} libraries.",
                log_level=logging.INFO
            )

    def _export_symbol_conflicts_report(self,
                                        so_exports: dict[str, list[str]],
                                        seen_symbols: dict[str, list[str]],
                                        processed_libs: int,
                                        report_path: str):
        """
        Generates and saves a JSON report of the library analysis.
        """
        conflicts = {sym: paths for sym, paths in seen_symbols.items() if len(paths) > 1}

        report_data = {
            "analysis_summary": {
                "total_libraries_processed": processed_libs,
                "duplicate_symbols_found": bool(conflicts)
            },
            "exported_symbols_by_library": so_exports,
            "symbol_conflicts": conflicts
        }

        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, indent=4, ensure_ascii=False)
            self.print_message(message=f"JSON report successfully exported to: {report_path}",
                               log_level=logging.INFO)
        except IOError as e:
            self.print_message(message=f"Error: Could not write JSON report to '{report_path}'. Error: {e}",
                               log_level=logging.ERROR)
        except Exception as e:
            self.print_message(message=f"An unexpected error occurred while generating JSON report: {e}",
                               log_level=logging.ERROR)

    def print_message(self, message: str, bare_text: bool = False, log_level: Optional[int] = logging.DEBUG) -> None:
        """
        Prints a build-time message prefixed with an AutoForge label.
        Args:
            message (str): The text to print.
            bare_text (bool, optional): If True, prints without ANSI color formatting.
            log_level (int, optional): Logging level to use (e.g., logging.INFO).
                                       If None, the message is not logged.
        """
        if not bare_text:
            # Map log levels to distinct label colors
            level_color_map = {logging.CRITICAL: Fore.LIGHTRED_EX, logging.ERROR: Fore.RED,
                               logging.WARNING: Fore.YELLOW, logging.INFO: Fore.CYAN,
                               logging.DEBUG: Fore.LIGHTGREEN_EX, }
            color = level_color_map.get(log_level, Fore.WHITE)
            leading_text = f"{color}-- {self._build_label}:{Style.RESET_ALL} " if self._build_label else "-- "

        else:
            leading_text = f"-- {self._build_label}: "
            message = self._tool_box.strip_ansi(text=message, bare_text=True)

        # Optionally log the message
        if log_level is not None:
            self._logger.log(log_level, message)

        sys.stdout.write("\r\033[K")  # Clear the current line
        print(leading_text + message)

    def update_info(self, command_info: ModuleInfoType):
        """
        Updates information about the implemented builder.
        """
        self._module_info = command_info

    @property
    def sdk(self) -> SDKType:
        """
        Returns the global SDK singleton instance, which holds references
        to all registered core module instances.
        This property provides convenient access to the centralized SDKType
        container, after all core modules have registered themselves during
        initialization.
        """
        return SDKType.get_instance()
