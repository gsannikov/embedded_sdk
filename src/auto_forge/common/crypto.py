"""
Script:         crypto,py
Author:         AutoForge Team

Description:
    Basic and self-contained `Crypto` class designed for managing encrypted dictionaries using a pre-shared key.
    It allows for secure storage, retrieval, modification, and deletion of key-value pairs within encrypted files.
"""
import json
import os
from typing import Any

# Third-party
from cryptography.fernet import Fernet, InvalidToken

AUTO_FORGE_MODULE_NAME = "Crypto"
AUTO_FORGE_MODULE_DESCRIPTION = "Basic Preshared Key Cryptography Provider"


class Crypto:
    """
    A general-purpose class for managing an encrypted dictionary using a pre-shared key.
    It provides methods to encrypt/decrypt dictionaries and to read, create,
    modify, and delete entries within encrypted files.
    """

    def __init__(self, key_file: str, create_as_needed: bool = True, force_new_key: bool = False):
        """
        Initializes the Crypto class by loading or generating a Fernet key from a file.

        Args:
            key_file (str): The path to the file where the Fernet key is or will be stored.
            create_as_needed (bool): If True, and the key file does not exist, a new key
                                     will be generated and stored in `key_file`.
                                     If False and the file does not exist, a FileNotFoundError is raised.
            force_new_key (bool): If True, and the key file exists, the existing key file
                                  will be unlinked (deleted) and a new key will be generated
                                  and stored in `key_file`. This parameter implies `create_as_needed=True`
                                  if the file exists, but will still raise FileNotFoundError
                                  if the file does not exist and `create_as_needed` is False.
        """
        self.key_file = key_file
        key_bytes = None

        if os.path.exists(self.key_file):
            if force_new_key:
                try:
                    os.unlink(self.key_file)
                    # File removed, proceed to create new
                    key_bytes = self._generate_fernet_key(path=self.key_file)
                except OSError as e:
                    raise RuntimeError(f"Error unlinking existing key file '{self.key_file}': {e}")
            else:
                # Load existing key
                try:
                    with open(self.key_file, 'rb') as f:
                        key_bytes = f.read()
                except IOError as e:
                    raise RuntimeError(f"Error reading key file '{self.key_file}': {e}")
        else:  # Key file does not exist
            if create_as_needed:
                key_bytes = self._generate_fernet_key(path=self.key_file)
            else:
                raise FileNotFoundError(
                    f"Key file '{self.key_file}' not found and 'create_as_needed' is False."
                )

        if not key_bytes:
            raise ValueError("Failed to obtain a valid key from file.")

        try:
            self.fernet = Fernet(key_bytes)
        except Exception as e:
            raise ValueError(f"Invalid Fernet key loaded/generated from '{self.key_file}': {e}. "
                             "Ensure it's a base64 URL-safe encoded 32-byte key.")

    @staticmethod
    def _generate_fernet_key(path: str = None) -> bytes:
        """
        Generates a new URL-safe Fernet key. (Internal static method)
        If a path is provided, the key will be stored in the specified file.
        This method is now primarily called internally by the __init__ method.
        Args:
            path (str, optional): The file path where the key will be stored.
                                  If None, the key is returned but not saved.
        Returns:
            bytes: A new Fernet key.

        """
        key = Fernet.generate_key()
        if path:
            try:
                with open(path, 'wb') as f:
                    f.write(key)
            except IOError as e:
                raise RuntimeError(f"Error storing key to file '{path}': {e}")
        return key

    def _encrypt_dict(self, data: dict[str, Any]) -> bytes:
        """
        Encrypts a Python dictionary into bytes. (Internal method)
        Args:
            data (dict[str, Any]): The dictionary to be encrypted.
        Returns:
            bytes: The encrypted data.
        """
        if not isinstance(data, dict):
            raise TypeError("Input data must be a dictionary")
        try:
            json_data = json.dumps(data)
            encrypted_data = self.fernet.encrypt(json_data.encode('utf-8'))
            return encrypted_data
        except Exception as e:
            raise RuntimeError(f"Error encrypting dictionary: {e}")

    def _decrypt_dict(self, encrypted_data: bytes) -> dict[str, Any]:
        """
        Decrypts bytes into a Python dictionary. (Internal method)
        Args:
            encrypted_data (bytes): The encrypted data to be decrypted.
        Returns:
            dict[str, Any]: The decrypted dictionary.
        """
        if not isinstance(encrypted_data, bytes):
            raise TypeError("Input encrypted_data must be bytes.")
        try:
            decrypted_json_data = self.fernet.decrypt(encrypted_data).decode('utf-8')
            decrypted_data = json.loads(decrypted_json_data)
            return decrypted_data
        except InvalidToken:
            raise RuntimeError("Invalid key or corrupted data during decryption.")
        except json.JSONDecodeError:
            raise RuntimeError("Decrypted data is not valid JSON.")
        except Exception as e:
            raise RuntimeError(f"Error decrypting dictionary: {e}")

    def read_encrypted_file(self, filename: str) -> dict[str, Any]:
        """
        Reads encrypted data from a file, decrypts it, and returns the dictionary.
        Args:
            filename (str): The path to the encrypted file.
        Returns:
            dict[str, Any]: The decrypted dictionary.
        """
        if not os.path.exists(filename):
            raise FileNotFoundError(f"File not found: {filename}")
        try:
            with open(filename, 'rb') as f:
                encrypted_data = f.read()
            return self._decrypt_dict(encrypted_data)
        except Exception as e:
            raise RuntimeError(f"Error reading or decrypting file '{filename}': {e}")

    def write_encrypted_file(self, filename: str, data: dict[str, Any]):
        """
        Encrypts a dictionary and writes it to a file.
        Args:
            filename (str): The path where the encrypted data will be saved.
            data (dict[str, Any]): The dictionary to be encrypted and saved.
        """
        try:
            encrypted_data = self._encrypt_dict(data)
            with open(filename, 'wb') as f:
                f.write(encrypted_data)
        except Exception as e:
            raise RuntimeError(f"Error writing encrypted data to file '{filename}': {e}")

    def create_or_load_encrypted_dict(self, filename: str, default_data: dict[str, Any] = None) -> dict[str, Any]:
        """
        Loads an encrypted dictionary from a file. If the file does not exist,
        it creates a new one with optional default data.
        Args:
            filename (str): The path to the encrypted file.
            default_data (dict[str, Any], optional): Initial data for a new file.
                                                    Defaults to an empty dictionary.
        Returns:
            dict[str, Any]: The loaded or newly created dictionary.
        """

        try:
            # If default data is valid, encrypt and save it to file
            if isinstance(default_data, dict):
                self.write_encrypted_file(filename, default_data)
                return default_data
            else:
                if os.path.exists(filename):
                    return self.read_encrypted_file(filename)
                else:
                    raise RuntimeError(f"Could not load and decrypt existing file '{filename}'")
        except Exception as exception:
            raise exception from exception

    def modify_encrypted_dict(self, filename: str, key: str, value: Any):
        """
        Loads the encrypted dictionary, modifies a specific key-value pair,
        and saves it back encrypted.
        Args:
            filename (str): The path to the encrypted file.
            key (str): The key to modify or add.
            value (Any): The new value for the key.
        """
        try:
            current_data = self.read_encrypted_file(filename)
            current_data[key] = value
            self.write_encrypted_file(filename, current_data)
        except Exception as e:
            raise RuntimeError(f"Error modifying key '{key}' in '{filename}': {e}")

    def delete_from_encrypted_dict(self, filename: str, key: str):
        """
        Loads the encrypted dictionary, deletes a key-value pair,
        and saves it back encrypted.
        Args:
            filename (str): The path to the encrypted file.
            key (str): The key to delete.
        """
        try:
            current_data = self.read_encrypted_file(filename)
            if key in current_data:
                del current_data[key]
                self.write_encrypted_file(filename, current_data)
            else:
                raise KeyError(f"Key '{key}' not found in the encrypted dictionary.")
        except Exception as e:
            raise RuntimeError(f"Error deleting key '{key}' from '{filename}': {e}")
