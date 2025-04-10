#!/usr/bin/env python3
"""
Script:     imcv2_sig_tool.py
Author:     Intel AutoForge team


TBD: <Placeholder, mockup only>"
"""
import argparse
import logging
import os
from typing import Optional

from auto_forge import Signatures, logger_setup
from git import Commit, Repo


class SigUtils:
    def __init__(self, descriptor_file: str, signature_id: int, git_repo_path: str, logger: Optional[logging.Logger]):

        self._service_name: str = self.__class__.__name__
        self._sig_tool: Optional[Signatures] = None
        self._descriptor_file: str = self._expand_path(descriptor_file)
        self._signature_id: int = signature_id
        self._git_repo_path: Optional[str] = self._expand_path(git_repo_path)
        self._git_repo: Optional[Repo] = None
        self._git_commit: Optional[Commit] = None
        self.logger: Optional[logging.Logger] = logger

        self.logger = logging.getLogger(self._service_name)
        self.logger.setLevel(level=logging.DEBUG)

        # Ensure we got valid paths
        if not os.path.exists(self._descriptor_file):
            raise RuntimeError(f"Schema descriptor file '{self._descriptor_file}' not found")

        if not os.path.exists(self._git_repo_path):
            raise RuntimeError(f"Git path '{self._git_repo_path}' not found")

        # Retrieve git properties which we use later to update an image
        self._git_repo = Repo(self._git_repo_path)
        self._git_commit = self._git_repo.head.commit
        self._git_commit_hash = self._git_commit.hexsha

        # Create SignaturesLib instance using the provided schema a\nd the signature id.
        self._sig_tool = Signatures(descriptor_file=self._descriptor_file,
                                    signature_id=self._signature_id)

    def update_crc(self, source_binary_file: str, validate_only: Optional[bool] = True,
                   destination_path: Optional[str] = None, pad_to_size: Optional[int] = None) -> Optional[bool]:
        """
        Update or validate the CRC of a binary file with a signature.

        Args:
            source_binary_file (str): Path to a binary file that contains a single signature.
            validate_only (bool, optional): If True, only validate the CRC without modifying the file. Defaults to True.
            destination_path (str, optional): Path where the modified file will be saved. If not specified, the
                source file will be updated in place.
            pad_to_size(Optional[int], optional): Resize the file to the specified size. Defaults to None.

        Returns:
            bool: True if the CRC check or update succeeded, False otherwise.
        """
        try:
            # Expand and validate the source file path
            source_binary_file = self._expand_path(source_binary_file)
            source_binary_file_base_name = os.path.basename(source_binary_file)
            padding_bytes: int = 0

            if not os.path.exists(source_binary_file):
                raise RuntimeError(f"Source binary file '{source_binary_file}' not found")

            # Ensure the file is not empty
            source_file_size: int = os.path.getsize(source_binary_file)
            if source_file_size == 0:
                raise RuntimeError(f"Source file '{source_binary_file_base_name}' is empty")

            # Do we have to resize the file?
            if pad_to_size is not None:
                if source_file_size >= pad_to_size:
                    self.logger.warning(
                        f"Source size '{source_file_size}' >= padded size '{pad_to_size}', padding ignored")
                else:
                    pad_results = self._pad_file(source_binary_file=source_binary_file, required_size=pad_to_size)
                    if pad_results:
                        # Update the new file size and the bytes that ware added
                        padding_bytes = pad_to_size - source_file_size
                    else:
                        return False  # Error padding the file

            # Load the file
            file_handler = self._sig_tool.deserialize(source_binary_file)
            if not file_handler:
                raise RuntimeError(f"Error deserializing source file '{source_binary_file_base_name}'")

            # Check the number of signatures
            if len(file_handler.signatures) != 1:
                raise RuntimeError("CRC validation is not supported on files with multiple signatures")

            # Handle the single signature scenario
            signature = file_handler.signatures[0]
            if not signature.verified:
                self.logger.warning(f"Binary CRC is invalid")

            # Only validate the CRC without updating
            if validate_only:
                if not signature.verified:
                    raise RuntimeError(f"CRC verification for '{source_binary_file_base_name}' failed")
                return True

            # Update the git hash and image size in the signature
            git_field = signature.find_first_field('git_commit')
            image_size_field = signature.find_first_field('image_size')

            if git_field is None or image_size_field is None:
                raise RuntimeError("Required fields are missing")

            signature.set_field_data(git_field, self._git_commit_hash)
            signature.set_field_data(image_size_field, source_file_size)

            # Update the 'padding_bytes' field in the signature
            if padding_bytes > 0:
                padding_bytes_field = signature.find_first_field('padding_bytes')
                if padding_bytes_field is not None:
                    signature.set_field_data(padding_bytes_field, padding_bytes)

            # Recalculate the CRC, update the signature, and save the file
            return signature.save(ignore_bad_integrity=True, file_name=destination_path)

        except Exception as exception:
            raise RuntimeError(f"Cannot validate/update CRC on '{source_binary_file}': {exception}")

    def _pad_file(self, source_binary_file: str, required_size: int, pad_byte: Optional[int] = 0xFF) -> bool:
        """
        Resize a file by appending bytes to its end until it reaches the required size.

        Args:
            source_binary_file (str): Path to the file.
            required_size (int): The desired final size in bytes.
            pad_byte (int, optional): Byte to use for padding, defaults to 0xFF.

        Returns:
            bool: True on success, or False if the target size is less than the current file size.
        """
        try:
            # Expand as needed
            source_binary_file = self._expand_path(source_binary_file)

            if not os.path.exists(source_binary_file):
                raise RuntimeError(f"Source binary file '{source_binary_file}' not found")

            # Get the current size of the file
            source_file_size = os.path.getsize(source_binary_file)

            # Check if resizing is necessary or possible
            if source_file_size >= required_size:
                self.logger.error(
                    f"Cannot pad file '{source_binary_file}' to {required_size} bytes as it is already {source_file_size} bytes or larger.")
                return False

            self.logger.debug(
                f"Padding '{os.path.basename(source_binary_file)}' to {required_size} bytes, current size {source_file_size} bytes.")

            # Calculate the number of bytes to add
            bytes_to_add = required_size - source_file_size

            # Open the file in append-binary mode and pad it
            with open(source_binary_file, 'ab') as file:
                file.write(bytes([pad_byte] * bytes_to_add))

            self.logger.debug(f"File '{os.path.basename(source_binary_file)}' resized to {required_size} bytes.")
            return True

        except Exception as exception:
            raise RuntimeError(
                f"Cannot resize '{os.path.basename(source_binary_file)}' to {required_size} bytes: {exception}")

    @staticmethod
    def _expand_path(path: str) -> str:
        # Preform expansion as needed
        expanded_file = os.path.expanduser(os.path.expandvars(path))
        path = os.path.abspath(expanded_file)  # Resolve relative paths to absolute paths

        return path


def sig_tool_commands() -> Optional[int]:
    """
    Main function to handle command-line arguments and invoke the respective functions.
    Supports:
    - Verifying the CRC32 of the binary.
    - Updating the CRC32 in the binary.
    - Padding a file prior to signing
    - Patching destination with source signed regions.
    - Printing the signature from the binary.
    """

    try:

        # Logger instance for this module
        logger = logger_setup(name="AutoForge", level=logging.INFO)

        # Set up the argument parser
        parser = argparse.ArgumentParser(
            description="IMCV2 Signature Tool - Find, print, verify, and update CRC32 in a binary file."
        )
        parser.add_argument("-p", "--path", type=str, required=True, help="Path to the binary file.")
        parser.add_argument("--verify-crc", action="store_true", help="Verify the CRC32 of the binary.")
        parser.add_argument("--update-crc", action="store_true", help="Update the CRC32 in the binary file.")
        parser.add_argument("-g", "--grow", type=lambda x: int(x, 0), nargs="?", const=0, default=0,
                            help="Resize file prior to signing.")
        parser.add_argument("-git", "--git_path", type=str, required=True,
                            help="Path where we can retrieve git related info.")
        parser.add_argument("-r", "--replace", type=str, required=False,
                            help="Search and replace a signed section with the one created.")
        parser.add_argument("-m", "--mini_loader", action="store_true",
                            help="Updates NVM mini-loader CRC value.")
        parser.add_argument("-s", "--show", action="store_true", help="Show signature content.")
        parser.add_argument("-i", "--signature_id", type=lambda x: int(x) if int(x) > 0
        else argparse.ArgumentTypeError(f"{x} is not a positive integer"), default=42,
                            help="Signature Id to use")
        parser.add_argument('-d', '--descriptor_file', required=True,
                            help="The path to the signature descriptor file")
        parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output.")
        parser.add_argument("-ver", "--version", action="store_true", help="Only show binary version")

        # Parse the command-line arguments
        args = parser.parse_args()

        sig_utils: SigUtils = SigUtils(logger=logger, descriptor_file=args.descriptor_file,
                                       signature_id=args.signature_id, git_repo_path=args.git_path)

        sig_utils.update_crc(source_binary_file=args.path, validate_only=False, pad_to_size=0x40000)


    except KeyboardInterrupt:
        print("Interrupted\n")
    except Exception as exception:
        print(f"Exception: {exception}")
