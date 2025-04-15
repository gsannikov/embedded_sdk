"""
Script:         west_world.py
Author:         Intel AutoForge team

Description
------------

This module processes a typical west.yml file and clones projects in parallel,
unlike Zephyr's 'west' which clones projects sequentially.
Parallel cloning reduces clone times significantly.

"""

import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, List

import git
import yaml
from colorama import Fore, Style

AUTO_FORGE_MODULE_NAME = "MiniWest"
AUTO_FORGE_MODULE_DESCRIPTION = "Zephyr 'west' helper library"


class WestProject:
    """
    Auxiliary class for managing repository related information
    """

    def __init__(self):
        self.name: Optional[str] = None  # west.yml mandatory property
        self.description: Optional[str] = None  # west.yml mandatory property
        self.url: Optional[str] = None  # west.yml mandatory property
        self.revision: Optional[str] = None  # west.yml mandatory property
        self.path: Optional[str] = None  # west.yml mandatory property

        # Extended properties used by 'MiniWest' class
        self.formated_name: Optional[str] = None
        self.formated_revision: Optional[str] = None
        self.clone_state: bool = False  # Adds 'clone_state' key and sets it to False
        self.clone_dir: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self.retry_count: Optional[int] = 0
        self.is_top_level: bool = False
        self.is_retried: bool = False
        self.is_running: bool = False
        self.attempts: int = 0
        self.operation_stderr: Optional[str] = None
        self.formated_message: Optional[str] = None


class WestWorld:

    def __init__(self, automated_mode: bool = False):
        """
        Initializes 'WestWorld' main class.
        """

        self._exceptions: int = 0  # Errors counter
        self._threads_lock = threading.Lock()  # Global lock for synchronized output
        self._stop_event = threading.Event()  # Global event to signal threads to stop on failure
        self._projects_queue = queue.Queue()  # Message queue for printing status message safely.
        self._ignored_projects_list = {"zephyr"}  # Manifest projects to exclude
        self._automated_mode: bool = automated_mode  # Global to indicate if we're allowed to use colors
        self._projects: List[WestProject] = []  # List of 'WestProject' class instances

    def _is_top_level_repo(self, clone_dir, all_paths):
        """
        Recursively check if the repository is a top-level one.
        Args:
            clone_dir (str): The path of the repository.
            all_paths (set): Set of all repository paths.

        Returns:
            bool: True if the repository is top-level, False otherwise.
        """
        parent_dir = os.path.dirname(clone_dir)

        # If we've reached the root or base path, this is a top-level directory
        if parent_dir == '' or parent_dir == os.path.sep:
            return True

        # If the parent directory exists in the all_paths, it's not top-level
        if parent_dir in all_paths:
            return False

        # Recursively check the parent directory
        return self._is_top_level_repo(parent_dir, all_paths)

    def _update_top_levels(self):
        """
        Update the is_top_level status for each BranchInfo object in the list
        and sort the projects by top-level status, with top-level projects first.
        Returns:
            list: The sorted list of projects, with top-level projects first.
        """
        all_paths = {os.path.normpath(project.clone_dir) for project in self._projects}

        # Update each project with the correct top-level status
        for project in self._projects:
            clone_dir = os.path.normpath(project.clone_dir)
            project.is_top_level = self._is_top_level_repo(clone_dir, all_paths)

        # Sort projects by is_top_level (top levels first)
        sorted_projects = sorted(self._projects, key=lambda project: not project.is_top_level)
        return sorted_projects

    @staticmethod
    def _check_clone_dir(clone_dir):
        """
        Check if the provided clone directory is valid.
        Args:
            clone_dir (str): The path to the directory where the repository will be cloned.
        Returns:
            bool: True if the directory is valid for cloning (non-existent or empty), False otherwise.
        """
        # Check if the path does not exist; if so, create the directory
        if not os.path.exists(clone_dir):
            os.makedirs(clone_dir)  # Create the directory if it doesn't exist
            return True  # Return True since the directory is now valid for cloning

        # If the path exists but is not a directory, return False (invalid path)
        if not os.path.isdir(clone_dir):
            return False

        # If the directory is not empty, return False (invalid for cloning)
        if os.listdir(clone_dir):
            return False

        # If the directory exists and is empty, return True (valid for cloning)
        return True

    @staticmethod
    def _clean_clone_dir(clone_dir):
        """
        Ensure the provided clone directory is valid and clean.
        If the directory does not exist, it will be created.
        If the directory exists but is not empty, its contents will be removed.

        Args:
            clone_dir (str): The path to the directory where the repository will be cloned.
        Returns:
            bool: True if the directory is ready for cloning, False otherwise.
        """
        try:
            # If the directory does not exist, create it
            if not os.path.exists(clone_dir):
                os.makedirs(clone_dir)
                return True

            # If the path exists but is not a directory, return False
            if not os.path.isdir(clone_dir):
                return False

            # If the directory exists and is not empty, clear its contents
            for item in os.listdir(clone_dir):
                item_path = os.path.join(clone_dir, item)
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)  # Remove files or symbolic links
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)  # Remove directories

            return True  # The Directory is now clean and ready for cloning

        except Exception as general_error:
            raise RuntimeError(f"could not clean or prepare path '{clone_dir}': {general_error}")

    @staticmethod
    def _adjust_git_names(input_string):
        """
        Truncate a git hash or segmented string for display purposes.
        Args:
            input_string (str): The string to adjust (could be a git hash or a segmented string).
        Returns:
            str: Adjusted string.
        """
        if len(input_string) == 40 and all(c in '0123456789abcdef' for c in input_string.lower()):
            return input_string[-6:]
        segments = input_string.split('_')
        return '_'.join(segments[:3])

    @staticmethod
    def _strip_ansi_codes(text):
        """
        Remove ANSI escape sequences from the input text.
        Args:
            text (str): The text with ANSI escape sequences.
        Returns:
            str: Text without ANSI escape sequences.
        """
        ansi_escape = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
        return ansi_escape.sub('', text)

    def remove_matching_true_state(self, name, revision):
        """
        Removes the first item in the message queue with the given branch name and revision
        where clone_state is True. Any other items are re-added to the queue.
        Args:
            name (str): The name of the project to match.
            revision (str): The revision of the project to match.
        """
        filtered_queue = []  # Temporary list to hold non-matching messages

        # Process all items in the queue
        while not self._projects_queue.empty():
            project = self._projects_queue.get()

            branch_name = project.name
            branch_revision = project.revision
            clone_state = project.clone_state

            # Check if the current project matches the name and revision with clone_state=True
            if not (branch_name == name and branch_revision == revision and clone_state):
                filtered_queue.append(project)  # Save this item to add it back to the queue

            # Mark the task as done (important when using queues with multiple threads)
            self._projects_queue.task_done()

        # Put back the filtered items that didn't match the removal criteria
        with self._threads_lock:
            for item in filtered_queue:
                self._projects_queue.put(item)

    def _update_status_message(self, project: WestProject, clone_state: bool = True):
        """
        Update the global status message with the current repository cloning action.
        Args:
            project (WestProject): Project info datatype
            clone_state (bool): Whether the cloning is starting or ending.
        """
        with self._threads_lock:
            # When in CI mode, we use a less colorful method to inform the terminal:
            if self._automated_mode:
                timestamp = f"{datetime.now().strftime('%H:%M:%S')}:"
                message = 'started cloning' if clone_state else 'cloned!'
                sys.stdout.write(
                    f"[{timestamp}] Project '{project.name}, revision {project.revision} : {message}.\n")
                sys.stdout.flush()

        if not clone_state:
            # If the state is False, remove the corresponding item from the queue
            self.remove_matching_true_state(project.name, project.revision)
            with self._threads_lock:
                project.is_retried = False
                project.clone_state = False
                project.start_time = None

        else:
            # Insert to the queue
            with self._threads_lock:
                project.is_retried = False
                project.clone_state = clone_state
                project.start_time = datetime.now()
                self._projects_queue.put(project)

    def _format_terminal_message(self, project: WestProject, line_length: int):
        """
        Format a text line to be printed while the branch is being cloned.

        Args:
            project (WestProject): Project info datatype
            line_length (int): Desired length of the printed line.
        """
        # Format the message anb add it the message to the Q
        # Break down each part for readability
        project_name_colored = f"{Fore.LIGHTCYAN_EX}{project.formated_name}{Style.RESET_ALL}"
        project_revision_colored = f"{Fore.YELLOW}{project.formated_revision}{Style.RESET_ALL}"

        # Combine the parts into the final-colored message
        colored_message = f"{project_name_colored} revision {project_revision_colored}"

        # Strip ANSI codes to calculate the visual length
        visual_message_len = len(self._strip_ansi_codes(colored_message))

        if visual_message_len > line_length:
            colored_message = colored_message[:line_length]

        dots = max(0, line_length - visual_message_len)  # Ensure dots don't become negative
        project.formated_message = colored_message + '.' * dots

    def _print_error(self, error_message: str, error_details=None):
        """
        Handle errors by printing a message and stopping all threads.
        Args:
            error_message (str): Error message to print.
            error_details (str, optional): Additional details to print, such as from Git commands.
        """
        with self._threads_lock:
            sys.stdout.write('\033[2K')  # Clear the entire line
            sys.stdout.write(f'\n{Fore.RED}Error: {Style.RESET_ALL}{error_message}\n')
            if error_details:
                sys.stdout.write(f'{Fore.RED}Details: {Style.RESET_ALL}{error_details}\n')

    def clone_and_checkout(self, project: WestProject) -> int:
        operation_status: int = 0
        try:
            # Clone the repository
            repo = git.Repo.clone_from(project.url, project.clone_dir, progress=None)
            if self._stop_event.is_set():
                return -1
            # Checkout a specific commit or branch
            repo.git.checkout(project.revision)
            return 0
        except git.exc.NoSuchPathError:
            project.operation_stderr = f"the specified path {project.clone_dir} does not exist."
        except git.exc.InvalidGitRepositoryError:
            project.operation_stderr = f"The specified directory is not a git repository."
        except git.exc.GitCommandError as git_error:
            project.operation_stderr = git_error.stderr.strip()
            operation_status = git_error.status
        except Exception as exception:
            project.operation_stderr = f"exception: {exception}"
            operation_status = 1

        return operation_status

    def _clone_repository_job(self, project: WestProject):
        """
        Clone a single repository based on project specifications.
        Args:
            project (WestProject): Project details including name, URL, revision, etc.
        Returns:
            bool: True if the operation was successful, False otherwise.
        """
        if self._stop_event.is_set():
            return False

        try:

            self._clean_clone_dir(project.clone_dir)
            if not self._check_clone_dir(project.clone_dir):
                raise RuntimeError(f"Git path not empty {project.clone_dir}.")

            os.makedirs(project.clone_dir, exist_ok=True)
            if not project.is_retried:
                self._update_status_message(project=project, clone_state=True)

            return_code = self.clone_and_checkout(project)
            if return_code == -1:
                return False

            # Get terminated with error
            if return_code != 0:
                # Increment attempts count and adjust project properties for a re-run
                with self._threads_lock:
                    project.attempts = project.attempts + 1
                    project.clone_state = False
                    project.start_time = None

                    # Mark this project as being retried so will not attempt to remove or re-insert it back to the queue
                    project.is_retried = True

                if project.attempts < project.retry_count:
                    # Clear residuals from the path, allow minimal delay and try again.
                    time.sleep(1)
                    if self._clean_clone_dir(project.clone_dir):
                        time.sleep(5)
                        project.operation_stderr = ""
                        return self._clone_repository_job(project)  # Recursion

                # Could not clear the path, too many attempts in any case - exit and terminate.
                raise RuntimeError(f"Git operation failed for '{project.name}', "
                                   f"attempt {project.attempts}\n{project.operation_stderr}")

            self._update_status_message(project, clone_state=False)
            return True

        except KeyboardInterrupt:
            sys.stdout.write("\nProcess interrupted by user.\n")
            self._stop_event.set()
        except Exception as job_exception:
            self._print_error(f"Exception in worker thread: {str(job_exception)}")
            self._stop_event.set()
            self._exceptions += 1
            self._close(force_terminate=False)
            return False

    def _build_projects_list(self, west_yaml_path: str, clone_path: Optional[str] = None, retry_count: int = 1,
                             status_line_length: int = 80):
        """
        Builds a list of WestProject instances from a YAML configuration file.

        This method processes a given YAML file to extract project data, expanding
        environment variables and resolving paths where necessary. It handles missing
        data and filtering through an ignore list.

        Parameters:
        west_yaml_path (str): The path to the YAML file containing west project configurations.
        clone_path (Optional[str]): The base path for cloning the projects, which can include
                                    environment variables and may be a relative path.
        retry_count (int): Default retry count assigned to each project.
        """
        try:
            # Resolve the full path for clone directory
            expanded_path = os.path.expandvars(clone_path)
            absolute_clone_path = os.path.abspath(expanded_path)

            # Regular expression pattern to match URLs that start with 'https://' and end with '.git'
            pattern = r'^https:\/\/.*\.git$'

            with open(west_yaml_path, 'r') as file:
                data = yaml.safe_load(file)
                projects = data.get('manifest', {}).get('projects', [])
                if not projects:
                    raise RuntimeError(f"did not find any project in {west_yaml_path}")

                for project in projects:
                    if self._ignored_projects_list is None or project['name'] not in self._ignored_projects_list:
                        new_project = WestProject()
                        new_project.name = project.get('name', None)
                        new_project.description = project.get('description', None)
                        new_project.url = project.get('url', None)
                        new_project.revision = project.get('revision', None)
                        new_project.path = project.get('path', None)

                        # Make sure the YAML is valid
                        if None in (new_project.name, new_project.description, new_project.url, new_project.revision,
                                    new_project.path):
                            raise RuntimeError(f"a west project is missing a required field(s)")

                        # Use re.match to check if the URL matches the pattern
                        if not re.match(pattern, new_project.url):
                            raise RuntimeError(f"{new_project.url} is not a valid Git URL.")

                        new_project.formated_name = self._adjust_git_names(new_project.name.strip())
                        new_project.formated_revision = self._adjust_git_names(new_project.revision.strip())
                        new_project.clone_dir = os.path.join(absolute_clone_path, new_project.path.strip())

                        self._format_terminal_message(new_project, status_line_length)
                        new_project.retry_count = retry_count
                        # send the class list
                        self._projects.append(new_project)

                if len(self._projects) == 0:
                    raise RuntimeError(f"no projects found in {west_yaml_path}")

        except Exception as parse_exception:
            raise Exception(f"Failed to read and process {west_yaml_path}: {str(parse_exception)}")

    def process_yml(self, west_yml_path: str, clone_path: Optional[str], status_line_length: int = 80,
                    max_workers=20, retry_count=1,
                    delay_between=2):
        """
        Process the `west.yml` file and clone all specified repositories concurrently.

        Args:
            west_yml_path (str): Path to the `west.yml` file.
            clone_path (str): Base directory where repositories should be cloned.
            status_line_length (int): Desired length of the printed line.
            max_workers (int): Maximum number of concurrent threads.
            retry_count  (int): Total attempt to retry cloning on failure.
            delay_between  (int): Delay before firing threads.

        Returns:
            int: 0 if all operations were successful, 1 otherwise.
        """
        try:
            # Use the current path if we did net get a destination path to work on
            if clone_path is None:
                clone_path = os.getcwd()

            # Limit concurrent clones
            if max_workers >= 20:
                max_workers = 20

            # Normalize retry counting to a minimum of 1
            retry_count = max(retry_count, 1)

            # Build the list of projects
            self._build_projects_list(west_yaml_path=west_yml_path, clone_path=clone_path, retry_count=retry_count,
                                      status_line_length=status_line_length)

            self._projects = self._update_top_levels()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for project in self._projects:
                    # If any thread has failed, stop launching new tasks
                    if self._stop_event.is_set():
                        raise RuntimeError(f"stop event was set, can't spawn more threads")

                    futures[executor.submit(self._clone_repository_job, project)] = project
                    time.sleep(delay_between)

                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if not result:
                            raise RuntimeError(f"a worker thread returned an error ({result})")

                    except Exception as general_exception:
                        executor.shutdown(cancel_futures=True)
                        raise RuntimeError(str(general_exception))

            # Mark end operation
            with self._threads_lock:
                sys.stdout.write(f'{Fore.GREEN} OK\r{Style.RESET_ALL}')
                sys.stdout.flush()
            return 0

        except Exception as runtime_exception:
            self._stop_event.set()
            sys.stdout.write(f"{Fore.RED} Error{Style.RESET_ALL}: {str(runtime_exception)}\r")
            sys.stdout.flush()
            return 1

    def _close(self, force_terminate: bool = False):
        """
        Close the application and terminate all related subprocesses, specifically targeting 'git' processes.
        Args:
            force_terminate (bool): If True, forcibly terminate the application after killing subprocesses.
                                    If False, perform regular shutdown and return any exceptions.
        Returns:
            int: Returns 1 if forcibly terminated, otherwise returns the count of exceptions encountered.
        """
        # Kill all 'git' processes to clean up before closing
        subprocess.run(['pkill', '-f', 'git'])
        # Optional: Use an event to signal other parts of the application to stop
        # self.stop_event.set()
        time.sleep(0.1)  # Brief pause to allow signal processing

        if force_terminate:
            # Redirect stderr to null to suppress any error messages during forced termination
            sys.stderr = open(os.devnull, 'w')
            # Forcibly terminate the current process
            os.kill(os.getpid(), signal.SIGTERM)
            # Since os.kill will terminate the process, the following return is more for documentation
            return 1

        # Return the number of exceptions encountered during regular operation, if not forcibly terminated
        return self._exceptions
