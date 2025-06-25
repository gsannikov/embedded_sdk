"""
Script:         signatures.py
Author:         AutoForge Team

Description:
    Core module that simplifies handling binary signatures, which are used to tag binaries—
    typically compiled firmware—with a user-defined structure marked by specific identifiers.

    This API allows you to verify, enumerate, insert, and check the integrity of signatures,
    as well as attach extended information (e.g., Git metadata). The signature structure is
    defined using a concise and human-readable schema.
"""

import logging
import mmap
import os
import re
import struct
import zlib
from re import Match
from typing import Any, Optional, cast

# AutoForge imports
from auto_forge import (
    AutoForgeModuleType, AutoLogger, CoreJSONCProcessor, CoreModuleInterface, CoreTelemetry,
    CoreVariables, CoreRegistry, SignatureFieldType, SignatureSchemaType)

AUTO_FORGE_MODULE_NAME = "Signatures"
AUTO_FORGE_MODULE_DESCRIPTION = "Signatures operations support"


class CoreSignatures(CoreModuleInterface):
    """
    Signatures is the root class which ties all the Auxiliary classes to provide a functional
    interface around signatures.
    """

    def _initialize(self, signatures_config_file_name: str) -> None:
        """
        Initializes the SignaturesLib class by loading a signature schema from a JSON descriptor file.
        This initialization searches for the specific signature with the given ID and constructs
        a Python format string based on the signature's schema. This format string is crucial
        for creating, reading, and modifying signatures in binary files.

        Args:
            signatures_config_file_name (str): The path to the JSON file containing the signature descriptors.
        """

        self._config_file_name: Optional[str] = None
        self._signature_id: Optional[int] = 42
        self._raw_dictionary: Optional[dict[str, Any]] = {}
        self._schemas: list[SignatureSchemaType] = []
        self._processor: CoreJSONCProcessor = CoreJSONCProcessor.get_instance()
        self._variables: Optional[CoreVariables] = CoreVariables.get_instance()
        self._telemetry: CoreTelemetry = CoreTelemetry.get_instance()

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

        if not signatures_config_file_name:
            raise RuntimeError("signatures configuration file not specified")

        # Preform expansion as needed
        expanded_file = os.path.expanduser(os.path.expandvars(signatures_config_file_name))
        self._config_file_name = os.path.abspath(expanded_file)  # Resolve relative paths to absolute paths

        signatures = self._processor.render(file_name=signatures_config_file_name).get("signatures", None)
        if signatures is None or not isinstance(signatures, (list, dict)):
            raise RuntimeError(f"no signatures found in '{signatures_config_file_name}'")

        # Locate the signature matching the id
        for signature in signatures:
            if signature.get("id", None) == self._signature_id:
                # Populate the class members once we've located the correct signature dictionary
                self._raw_dictionary = signature
                self._signature_id = self._to_decimal(self._signature_id)
                self._json_descriptor = signatures_config_file_name
                break

        if self._raw_dictionary is None:
            raise RuntimeError(f"no signatures with id {self._signature_id} found in {signatures_config_file_name}")

        # Load all schemas and create
        schemas = self._raw_dictionary.get("schemas")
        if schemas is None:
            raise RuntimeError(f"schemas not found in {signatures_config_file_name}")

        self._logger.debug(f"Initialized using '{os.path.basename(self._config_file_name)}'")
        for raw_schema in schemas:
            schema: SignatureSchemaType = SignatureSchemaType()  # Create new instance
            schema.dictionary = raw_schema
            schema.name = raw_schema.get('name', 'anonymous')
            schema.description = raw_schema.get('description', 'no description')
            schema.header = self._to_decimal(raw_schema.get('header'))
            schema.footer = self._to_decimal(raw_schema.get('footer'))
            schema.size = self._to_decimal(raw_schema.get('size'))
            schema.is_default = raw_schema.get('default', False)

            # Peek into the schema dictionary and fetch those three essential field sizes
            header_field_size: int = self._get_field_size_from_dictionary(dictionary=schema.dictionary,
                                                                          field_name='header')
            footer_field_size: int = self._get_field_size_from_dictionary(dictionary=schema.dictionary,
                                                                          field_name='footer')
            # Make sure we got meaningful values
            if any(value in (None, 0) for value in
                   {schema.name, schema.header, schema.footer, schema.size, header_field_size, footer_field_size}):
                raise RuntimeError(f"essential schema fields (header, footer, size) are missing or "
                                   f"incorrectly set from {signatures_config_file_name}")

            # Convert the header,and footer and header to their binary format and create binary regex patterns
            # First get the expected bytes count between the known markers
            arbitrary_data_length = schema.size - (header_field_size + footer_field_size)
            start_pattern = (struct.pack("<I", schema.header))
            end_pattern = struct.pack("<I", schema.footer)

            # Construct and compile regex string
            regex_pattern = rb"%s.{%d}%s" % (start_pattern, arbitrary_data_length, end_pattern)
            schema.search_pattern = re.compile(regex_pattern, re.DOTALL)

            # Validates that each field name is unique within the same structural level of the schema.
            # This approach mirrors the scoping rules of C structs, facilitating the direct conversion of this schema
            # into C header files without naming conflicts.
            self._validate_schema_structure_members(schema.dictionary)

            # Construct a Python format string based on the schema, which can be used to
            # serialize and deserialize signature data to and from binary format.
            schema.format_string = self._build_format_string_from_dictionary(schema.dictionary)

            calculated_schema_size = struct.calcsize(schema.format_string)
            if calculated_schema_size is None or calculated_schema_size == 0:
                raise RuntimeError("could not calculate schema expected size")

            # Verify that the size calculated matches the 'size' attribute specified in the schema.
            if calculated_schema_size != schema.size:
                raise RuntimeError(
                    f"calculated schema size {calculated_schema_size}, but schema reported size is {schema.size}")

            self._schemas.append(schema)

        # Register this module with the package registry
        registry = CoreRegistry.get_instance()
        registry.register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                 auto_forge_module_type=AutoForgeModuleType.CORE)

        # Inform telemetry that the module is up & running
        self._telemetry.mark_module_boot(module_name=AUTO_FORGE_MODULE_NAME)

    def deserialize(  # noqa: C901 # Acceptable complexity
            self, file_name: str) -> Optional["SignatureFileHandler"]:
        """
        Loads and maps a binary file into memory, searches for signatures and extracts their fields.
        The returned `FileHandler` instance allows iteration over signatures and their respective fields.

        Args:
            file_name (str): The name of the file to load.
        Returns:
            Optional[SignatureFileHandler]: A `FileHandler` instance if the file is successfully loaded, otherwise `None`.
        """
        try:
            def product_lookup(product_id: int, product_sub_id: int) -> Optional[dict[str, Any]]:
                """ Search for product """
                raw_products = self._raw_dictionary.get("products")
                if raw_products is None:
                    return None

                for raw_product in raw_products:
                    raw_id = self._to_decimal(raw_product.get("id", 0))
                    raw_sub_id = self._to_decimal(raw_product.get("subId", 0))
                    if product_id == raw_id and product_sub_id == raw_sub_id:
                        return raw_product

                return None  # Did not find any product matching the search clues

            def _manufacture_lookup(manufacturer_id: int) -> Optional[dict[str, Any]]:
                """ Search for signature """
                raw_manufactures = self._raw_dictionary.get("manufacturers")
                if raw_manufactures is None:
                    return None

                for raw_manufacture in raw_manufactures:
                    raw_manufacture_id = self._to_decimal(raw_manufacture.get("id", 0))
                    if raw_manufacture_id == manufacturer_id:
                        return raw_manufacture

                return None  # Did not find any manufacture matching the search clues

            def _family_lookup(family_id: int) -> Optional[dict[str, Any]]:
                """ Search for family """
                raw_families = self._raw_dictionary.get("families")
                if raw_families is None:
                    return None

                for raw_family in raw_families:
                    raw_family_id = self._to_decimal(raw_family.get("id", 0))
                    if raw_family_id == family_id:
                        return raw_family

                return None  # Did not find any family  matching the search clues

            # Preform expansion as needed
            expanded_file = os.path.expanduser(os.path.expandvars(file_name))
            file_name = os.path.abspath(expanded_file)  # Resolve relative paths to absolute paths

            # Load the default schema and look for signatures
            file_handler: SignatureFileHandler = SignatureFileHandler(file_name=file_name, signatures_lib=self)
            for signature in file_handler.signatures:
                searched_id = signature.get_field_data(signature.find_first_field('product_id'))
                searched_sub_id = signature.get_field_data(signature.find_first_field('sub_product_id'))
                searched_manufacturer_id = signature.get_field_data(signature.find_first_field('manufacturer'))
                searched_family_id = signature.get_field_data(signature.find_first_field('product_family'))

                if None not in {searched_id, searched_sub_id, searched_manufacturer_id, searched_family_id}:
                    product = product_lookup(product_id=searched_id, product_sub_id=searched_sub_id)
                    manufacture = _manufacture_lookup(manufacturer_id=searched_manufacturer_id)
                    family = _family_lookup(family_id=searched_family_id)

                    if product is not None:
                        signature.product_name = product.get("name")
                        signature.product_description = product.get("description")
                    if manufacture is not None:
                        signature.manufacturer_name = manufacture.get("name")
                    if family is not None:
                        signature.family_name = family.get("name")

                # Olf SigTool backwards compatability
                if signature.verified and signature.padding_bytes > 0:
                    stored_padding = signature.get_field_data(signature.find_first_field('padding_bytes'), 0)
                    if stored_padding != signature.padding_bytes:
                        signature.set_field_data(signature.find_first_field('padding_bytes'), signature.padding_bytes)
                        signature.save()

            return file_handler

        except Exception as exception:
            self._logger.error(f"Exception while deserializing {exception}")
            raise RuntimeError(exception) from exception

    def find_schemas(self, schema_name: Optional[str] = None) -> Optional[list["SignatureSchemaType"]]:
        """
        Retrieves a list of schemas based on the specified criteria.
        If a name is provided, it returns all schemas matching that name.
        If no name is provided, it returns the default schema(s).

        Args:
            schema_name (Optional[str]): The name of the schema to find, or None for default schemas.
        Returns:
            Optional[List[SignatureSchemaType]]: A list of matching schemas, or None if no matches are found or no schemas aew loaded
        """
        if self._schemas is None or len(self._schemas) == 0:
            return None

        if schema_name is None:
            # Return all default schemas
            schemas = [schema for schema in self._schemas if schema.is_default]
        else:
            # Return all schemas matching the specified name
            schemas = [schema for schema in self._schemas if schema.name == schema_name]

        return schemas

    def type_to_size(self, field_type: str) -> int:
        """
        Converts a field type description to a size in bytes.
        Maps high-level type descriptions such as 'uint32' or 'char[24]' to their size in bytes.
        Supports basic types and fixed-size arrays.

        Args:
            field_type (str): A string describing the type, which may include array notation.
        Returns:
            int: The size in bytes corresponding to the input type.
        """
        type_sizes = {'uint64': 8, 'uint64_t': 8, 'uint32': 4, 'uint32_t': 4, 'uint16': 2, 'uint16_t': 2, 'uint8': 1,
                      'uint8_t': 1, 'char': 1, 'uintptr_t': 8, 'intptr_t': 8}
        type_base, array_size = self._parse_type_and_array(field_type)
        if type_base in type_sizes:
            size = type_sizes[type_base]
            return size * array_size if array_size else size
        else:
            raise ValueError(f"Unsupported type '{type_base}'.")

    def type_to_format(self, field_type: str) -> Optional[str]:
        """
        Converts a type description from a schema to a format string usable with Python's struct module.
        Maps high-level type descriptions such as 'uint32' or 'char[24]' to struct module compatible format
        strings. It supports basic types and fixed-size arrays.

        Args:
            field_type (str): A string describing the type, which may include array notation.
        Returns:
            str: A format string corresponding to the input type.
        """
        try:
            # Basic type to struct format mappings
            type_mappings = {'uint64': 'Q', 'uint64_t': 'Q', 'uint32': 'I', 'uint32_t': 'I', 'uint16': 'H',
                             'uint16_t': 'H', 'uint8': 'B', 'uint8_t': 'B', 'char': 'c',
                             # 'c' is used for a single byte character
                             'uintptr_t': 'Q',  # Assuming 64-bit addressing
                             'intptr_t': 'q'  # Assuming 64-bit addressing for pointer types
                             }
            type_base, array_size = self._parse_type_and_array(field_type)
            if type_base in type_mappings:
                format_char = type_mappings[type_base]
                # If it's an array, prepend the size to the format string
                if array_size > 1:
                    if type_base == 'char':  # Special handling for 'char' to use 's' correctly
                        format_char = 's'  # Change to string type for arrays of chars
                    return f'{array_size}{format_char}'
                else:
                    return format_char
            else:
                raise ValueError(f"unsupported type '{type_base}'.")

        except Exception as exception:
            raise RuntimeError(f"error processing type '{field_type}': {exception}") from exception

    @staticmethod
    def _parse_type_and_array(field_type: str):
        """
        Parses the type description to extract the base type and any array size specification.
        Args:
            field_type (str): The string representation of the type, possibly including array notation.
        Returns:
            tuple: A tuple where the first element is the normalized base type (str) and the second
                   element is the array size (int). If the type is not an array, the array size is 1.
        """
        if '[' in field_type:
            type_base, array_part = field_type.split('[')
            array_size = int(array_part.rstrip(']'))
        else:
            type_base, array_size = field_type, 1

        # Normalize type name if it's an integer type without '_t' suffix
        if any(type_base.startswith(prefix) for prefix in ['uint', 'int']) and not type_base.endswith('_t'):
            type_base += '_t'

        return type_base.strip(), array_size

    @staticmethod
    def _validate_schema_mandatory_field(signature: dict[str, Any], field_name: str, field_value_type: type) -> bool:
        """
        Validates whether a specified field exists in the provided dictionary and checks if its type matches the expected type.

        Args:
            signature (Dict[str, Any]): The dictionary containing the field to be validated.
            field_name (str): The name of the field to check for existence and type correctness.
            field_value_type (type): The expected Python type of the field value (e.g., int, str, list).

        Returns:
            bool: True if the field exists, it is not None and is of the expected type; otherwise, False.
        """
        if field_name in signature and signature[field_name] is not None:
            field_value = signature[field_name]
            if isinstance(field_value, field_value_type):
                return True

        return False

    def _validate_schema_structure_members(self, schema: dict[str, Any]):
        """
        Validates that each field in a given schema has a unique name within the same structural level,
        including any nested structures. This method does not enforce global uniqueness across different
        nested structures, allowing the same field name in separate nested structs.
        This validation is essential if we would need to translate the schema structure into C header file consist
        of several structures.

        Args:
            schema (Dict[str, Any]): The schema dictionary containing a list of fields under the 'fields' key.
        """
        seen_names = set()  # A set to track names that have already been encountered at the current level

        for field in schema['fields']:
            field_name = field['name']
            if field_name in seen_names:
                raise ValueError(f"duplicate field name found within the same structure: {field_name}")
            seen_names.add(field_name)

            # Recurse into nested structures, but reset the seen_names for each new structure
            if field['type'] == 'struct' and 'fields' in field:
                self._validate_schema_structure_members(field)  # Recursive call without passing seen_names

    @staticmethod
    def _to_decimal(value: Any):
        """
        Converts a string representing a number in various formats (decimal, hex, octal)
        to a decimal integer.

        Args:
            value (Any): The input representing the number.
        Returns:
            int: The decimal integer representation of the input string.
        """
        try:
            if isinstance(value, int):
                return value
            elif isinstance(value, float):
                return int(value)
            elif isinstance(value, str):
                # Check if the value is in hexadecimal format
                if value.startswith("0x") or value.startswith("0X"):
                    return int(value, 16)
                # Check for octal (leading zero, but not hexadecimal)
                elif value.startswith("0") and len(value) > 1 and not any(c in value for c in '89abcdefABCDEF'):
                    return int(value, 8)
                # Attempt to detect hexadecimal without '0x' prefix (must contain A-F or a-f)
                elif any(c in value for c in 'abcdefABCDEF'):
                    return int(value, 16)
                else:
                    return int(value)
            else:
                raise TypeError(f"unsupported type {type(value)}")
        except ValueError as value_error:
            raise ValueError(f"value '{value}' is not a valid number") from value_error

    def _get_field_size_from_dictionary(self, dictionary: dict[str, Any], field_name: str) -> Optional[int]:
        """
        Retrieves the size of a specified field from a schema dictionary. This method is crucial during the
        initial schema handling steps when the fields are not yet organized into a structured list of objects.

        Args:
            dictionary (Dict[str, Any]): The dictionary containing the schema.
            field_name (str): The name of the field to find the size of.

        Returns:
            Optional[int]: The size of the field in bytes, or None if the field is not found.
        """

        def _get_filed_size(item: dict[str, Any]) -> Optional[int]:
            """Uses type_to_size() along with the sham specified type to get a filed size in bytes"""
            item_size: int = 0
            nonlocal field_name

            try:
                item_name: Optional[str] = item.get('name')
                if item_name is None:
                    raise RuntimeError("missing field property 'name' from schema")
                if field_name == item_name:
                    item_type: Optional[str] = item.get('type')
                    if item_type is None:
                        raise RuntimeError("missing field property 'type' from schema")

                    item_size = self.type_to_size(item_type)
                return self._to_decimal(item_size)

            except Exception as exception:
                raise RuntimeError(exception) from exception

        for field in dictionary['fields']:
            field_type: Optional[str] = field.get('type', None)
            if field_type is None:
                raise RuntimeError("missing field property 'type' from schema")
            if field_type == 'struct':
                # Handle nested structs
                for subfield in field['fields']:
                    field_size = _get_filed_size(subfield)
                    if field_size > 0:
                        return field_size
            else:
                field_size = _get_filed_size(field)
                if field_size > 0:
                    return field_size

        return -1  # $ Error

    def _build_format_string_from_dictionary(self, dictionary: dict[str, Any]) -> Optional[str]:
        """
        Constructs a format string for struct packing/unpacking based on a given schema.
        Iteratively processes a schema dictionary that defines types and potentially nested structures to create a
        single format string that can be used with Python's struct module to pack or unpack binary data.

        Args:
            dictionary (Dict[str, Any]): A dictionary representing the data structure's schema,
                which includes fields and their types possibly nested.
        Returns:
            Optional[str]: A string representing the format for struct packing/unpacking, or None on error.
        """
        try:
            fmt = '<'  # Little endian
            for field in dictionary['fields']:
                if field['type'] == 'struct':
                    # Handle nested structs
                    for subfield in field['fields']:
                        fmt += self.type_to_format(subfield['type'])
                else:
                    fmt += self.type_to_format(field['type'])
            return fmt
        except KeyError as key_error:
            raise RuntimeError(f"could not construct format string missing {key_error}") from key_error
        except Exception as exception:
            raise RuntimeError(f"could not construct format string {exception}") from exception


class Signature:
    """
    A class that represents a single signature within a binary file. It includes a list of fields parsed
    according to the signature's schema.
    """

    def __init__(self, file_name: str, unpacked_data: tuple, data: bytes, file_signature_offset: int,
                 file_handler: Optional["SignatureFileHandler"]):
        """
        Args:
            file_name (str): The name the file which this signature was fund in.
            unpacked_data (tuple): The structured data extracted from the raw binary signature.
            data (bytes): The original raw bytes of the matched signature.
            file_signature_offset (int): Offset within the containing file where the signature was found.
            file_handler (SignatureFileHandler): Parent class instance
        """

        # Get a logger instance
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

        self.unpacked_data: tuple = unpacked_data
        self.data: bytes = data
        self.file_name: str = file_name
        self.file_signature_offset: int = file_signature_offset
        self.file_handler: Optional[SignatureFileHandler] = file_handler
        self.verified: bool = False  # Signature integrity check was successful
        self.index: int = 0
        # The size of the image which this signature beings to
        self.file_image_size: Optional[int] = 0
        # Offset within the containing file where the image (firmware) which is this signature belongs to starts
        self.file_image_offset: Optional[int] = 0

        # The signature offset relative to the start of its firmware
        self.offset_from_image_start: Optional[int] = 0

        # Enhanced signatures with start/end addresses being added at compiled time.
        self.has_boundaries: bool = False

        # Optional padding bytes length
        self.padding_bytes: Optional[int] = 0
        self.product_name: Optional[str] = None
        self.product_description: Optional[str] = None
        self.manufacturer_name: Optional[str] = None
        self.family_name: Optional[str] = None

        # The List of empty fields belongs this signature
        self.fields: list[SignatureFieldType] = []

    def _read_image(self) -> Optional[bytearray]:
        """
        Reads a segment of data from a binary file based on the offset and size specified in a signature object.
        Returns:
            Optional[bytearray]: The binary data read from the file as specified, or None if an error occurs.
        """
        try:

            bytes_to_read: int = self.file_image_size
            if self.file_image_size == 0:
                raise RuntimeError("image size is unknown")

            # When the image has extra padding bytes, read them as well
            if self.padding_bytes > 0 and not self.has_boundaries:
                bytes_to_read = self.file_image_size + self.padding_bytes

            # Open the file in binary mode and read the specified segment of data
            with open(self.file_name, 'rb') as file:
                file.seek(self.file_image_offset)  # Move to the start offset
                image_data = bytearray(file.read(bytes_to_read))  # Read the data of specified size
                return image_data

        except Exception as exception:
            raise RuntimeError(f"failed to read the image data: {exception}") from exception

    def _update_integrity(self, image_bytes: bytearray, verify_only: Optional[bool] = False) -> Optional[bool]:
        """
        Checks the image integrity by using signature offsets and the CRC field to determine the starting point of
        the image within the larger NVM. It then loads the image, recalculates its CRC, and compares this
        calculated CRC with the CRC value found in the signature.

        Note: If the image was placed in a file system (e.g., LittleFS), this verification will most likely
        fail since the inner structure of a file within a file system is not guaranteed to remain as it was
        when it was compiled.

        Args:
            image_bytes (bytearray): The raw image data read from the file as specified.
            verify_only (Optional[bool]): If true, verifies that the image data matches the CRC without
                modifying anything.

        Returns:
            Optional[bool]: The operation status
        """
        integrity_field: Optional[SignatureFieldType] = None
        for field in self.fields:
            if field.is_integrity:
                integrity_field = field
                break

        if integrity_field is None:
            raise RuntimeError("integrity field is missing from schema")

        # Update image_bytes with our stored signature data
        start_offset = self.offset_from_image_start
        end_offset = start_offset + len(self.data)
        image_bytes[start_offset:end_offset] = self.data

        # Calculate offsets for CRC and signature
        crc_file_offset = self.offset_from_image_start + integrity_field.offset
        crc_size = integrity_field.size
        crc_stored_value = int(integrity_field.data)

        # Compute CRC32 excluding the CRC field
        suffix = Any(image_bytes[crc_file_offset + crc_size:])
        data_excluding_crc = bytes(image_bytes[:crc_file_offset]) + suffix
        computed_crc = zlib.crc32(cast(Any, data_excluding_crc)) & 0xFFFFFFFF
        if computed_crc == 0 and verify_only:
            raise RuntimeError(f"computed CRC value {computed_crc} is invalid")

        # if computed_crc != crc_stored_value:
        #   self.logger.warning(f"CRC mismatch at offset {hex(self.file_signature_offset)}, "
        #                       f"computed: {hex(computed_crc)}, stored: {hex(crc_stored_value)}")

        # Update the CRC field with the new computed data
        if not verify_only:
            integrity_field.data = computed_crc
            return True
        else:
            return crc_stored_value == computed_crc

    def _refresh(self) -> Optional["Signature"]:

        """
        Reconstructs the binary representation of the signature based on its field list and stores it in the
        signature class instance.
        This method should be executed whenever any of the fields are changed and before saving the signature.
        """
        try:
            # Initialize a bytearray with the original data to modify it
            new_data = bytearray(self.data)

            # Iterate over the selected fields and update the data in the bytearray
            for field in self.fields:
                if field.data is not None and field.offset is not None and field.size is not None:
                    # Use the helper function to get the correct struct format string for the field type
                    format_str = self.file_handler.signatures_lib.type_to_format(field.type)
                    if format_str is None:
                        raise RuntimeError(f"no valid format is found for field {field.name}")

                    # Ensure the data is in the correct format for packing
                    if isinstance(field.data, str):
                        field.data = field.data.encode()  # Encode string data to bytes

                    # Pack the data into bytes
                    packed_data = struct.pack(f'<{format_str}', field.data)
                    new_data[field.offset:field.offset + len(packed_data)] = packed_data

            # Convert the modified bytearray back to bytes and store it in self.data
            self.data = bytes(new_data)
            return self

        except Exception as exception:
            raise RuntimeError(f"failed to refresh signature: {exception}") from exception

    def verify(self) -> Optional[bool]:
        """
        Checks the signature CRC (integrity) by reading in its host image and comparing the calculated CRC with
        the value stored in the image signature
        """
        image_bytes = self._read_image()
        integrity_status = self._update_integrity(image_bytes=image_bytes, verify_only=True)
        return integrity_status

    def save(self, file_name: Optional[str] = None, ignore_bad_integrity: Optional[bool] = False) -> Optional[bool]:
        """
        Saves the modified image along with its modified signature to a specified file.

        Args:
            file_name (Optional[str]): The name of the file where the image is to be saved. If not provided,
                the image will be saved to the file specified by the object's `file_name` attribute.
            ignore_bad_integrity (bool, optional): If True, ignores the current CRC value if it is incorrect,
                computes a new CRC value, and saves the file. Be cautious: forcing a new checksum on an existing
                signature can compromise the integrity check. This is particularly risky if the file is part of
                a file system, where a 'wrong' CRC might appear correct but is actually a false positive due to
                non-continuous raw binary data.

        Returns:
            Optional[bool]: Returns True if the image is successfully saved, or None if an error occurs.
        """
        try:

            # Expand environment variables and user home in the path, then convert to an absolute path.
            if file_name is not None:
                expanded_file = os.path.expanduser(os.path.expandvars(file_name))
                file_name = os.path.abspath(expanded_file)

            # Choose the destination file path. Use provided `file_name` or fallback to the object's `file_name` attribute.
            destination_file = file_name if file_name is not None else self.file_name

            # Set the file offset for writing. Use default offset from the image object unless a new file name is provided.
            destination_offset = 0 if file_name is not None else self.file_image_offset

            self._logger.debug(f"Saving signature '{self.product_name}' to '{os.path.basename(destination_file)}',"
                               f"at offset {hex(destination_offset)}")

            if not self.verified and not ignore_bad_integrity:
                raise RuntimeError("can't save a signature which did not pass integrity check")

            # Read the current image data from storage.
            image_bytes = self._read_image()

            # Ensure data integrity and refresh object state.
            self._update_integrity(image_bytes=image_bytes)
            self._refresh()

            # Calculate the exact positions in the image byte array to update.
            start_offset = self.offset_from_image_start
            end_offset = start_offset + len(self.data)

            # Modify the image data at the specified range with the new data.
            image_bytes[start_offset:end_offset] = self.data

            # Dynamic mode based on if we're modifying ir writing a new file
            file_mode = 'r+b' if os.path.exists(destination_file) else 'wb'
            with open(destination_file, file_mode) as file:
                file.seek(destination_offset)
                file.write(image_bytes)

            # The image was saved with new calculated CRC so:
            self.verified = True
            return True

        except Exception as exception:
            self._logger.error(f"failed to save image: {exception}")
            raise RuntimeError(exception) from exception

    @staticmethod
    def get_field_data(field: Optional[SignatureFieldType] = None, default: Optional[Any] = None) -> Optional[Any]:
        """
        Returns the field content or a default value if none is specified.

        Args:
            field (Optional["FileHandler._SignatureField"]): The field to get the content from.
            default (Optional[Any]): The default value to return if none is specified.
        Returns:
            Any: The field content or a default value if none is specified.
        """
        if field is None or field.data is None:
            return default

        return field.data

    def set_field_data(self, field: Optional[SignatureFieldType], data: Optional[Any] = None) -> Optional[
        SignatureFieldType]:
        """
        Sets a field with new data, encoding and adjusting it according to the field's type and size.

        Args:
            field (FileHandler.SignatureField): The field object to update.
            data (Any): The new data to encode into the field.

        Returns:
            FileHandler.SignatureField: The updated field object with the new data encoded.
        """
        try:

            if field is None or data is None or field.data is None:
                raise RuntimeError("can't set an invalid field")

            if field.read_only:
                raise RuntimeError(f"field {field.name} is read-only")

            # Determine the format string for the field's type
            format_str = self.file_handler.signatures_lib.type_to_format(field.type)
            if format_str is None:
                raise ValueError(f"Unsupported field type {field.type}")

            # Convert and validate the input data based on a field type
            if 'char' in field.type:
                if isinstance(data, str):
                    data = data.encode()  # Convert string to bytes
                max_length = int(field.type[field.type.index('[') + 1:-1])
                data = data[:max_length]  # Truncate if necessary
                data += bytes(max_length - len(data))  # Pad with null bytes if shorter
            else:
                # For numeric types, ensure the data is an integer and within range
                data = int(data)  # This will raise ValueError if conversion fails or is inappropriate

            # Directly assigning integers
            if 'uint' in field.type or 'int' in field.type:
                field.data = data
            else:
                # Continue using struct.pack for other types as necessary
                packed_data = struct.pack(f'<{format_str}', data)
                field.data = packed_data[:field.size]

            self._refresh()  # Update the signature which has this field
            return field

        except Exception as exception:
            raise RuntimeError(exception) from exception

    def find_fields(self, name: str) -> Optional[list[SignatureFieldType]]:
        """
        Returns a list of all fields with the given name.

        Args:
            name (str): The name of the field.
        Returns:
            FileHandler.SignatureField: The list of fields matches the given name or None if none found.
        """
        if self.fields is None or len(self.fields) == 0:
            return None

        return [field for field in self.fields if field.name == name]

    def find_first_field(self, name: str) -> Optional[SignatureFieldType]:
        """
        Returns the first field with the given name, or None if not found.

        Args:
            name (str): The name of the field.
        Returns:
            FileHandler.SignatureField: The first field matches the given name or None if none found.
        """
        if self.fields is None or len(self.fields) == 0:
            return None

        return next((field for field in self.fields if field.name == name), None)


class SignatureFileHandler:
    """
    An auxiliary class that loads a binary file, scans it for signatures,
    parses their fields based on a predefined schema, and stores them in nested lists.
    """

    def __init__(self, file_name: str, signatures_lib: CoreSignatures, schema_name: Optional[str] = None):
        """
        Initialize the FileHandler class with the file name.

        Args:
            file_name (str): The name of the file to handle.
            signatures_lib (CoreSignatures): The instance of the parent 'SignaturesLib' class that created this object.
            schema_name (str, optional): The name of the schema to handle, use default if not set.
        """
        # Preform expansion
        expanded_file = os.path.expanduser(os.path.expandvars(file_name))
        file_name = os.path.abspath(expanded_file)  # Resolve relative paths to absolute paths

        if not os.path.exists(file_name):
            raise FileNotFoundError(f"file '{file_name}' does not exist.")

        self._file_name: Optional[str] = file_name
        self._file: Optional[mmap.mmap] = None
        self._file_size: Optional[int] = None
        self._file_read_offset: Optional[int] = 0
        self._logger: logging.Logger = logging.getLogger(AUTO_FORGE_MODULE_NAME)
        self.schema_name: Optional[str] = schema_name
        self.signatures: list[Signature] = []  # Empty list to store found signatures
        self.signatures_lib: Optional[CoreSignatures] = signatures_lib

        # Load the binary file and scan for signatures
        if self._build_signatures_list() == 0:
            raise RuntimeError(f"error handling file '{file_name}' no signatures found")

    def find_signatures(self, criteria_list: list[dict[str, Any]]) -> Optional[list['Signature']]:
        """
        Searches for signatures that match all the provided criteria sets. Each criteria set
        in the list is considered a distinct set of conditions that a signature's fields must
        satisfy. A signature is only returned if it matches all criteria sets provided.

        Args:
            criteria_list (List[Dict[str, Any]]): A list of dictionaries, where each dictionary
            contains field attributes (such as 'name' or 'data') as keys and the criteria for
            those attributes as values.

        Returns:
            Optional[List[Signature]]: A list of Signature objects that match all-criteria sets,
            or None if no matches are found.

        Example:
            # Define multiple criteria
            criteria_list = [
                {'name': 'sig_id', 'data': 42},  # Search for signature ID 42
                {'name': 'product_family', 'data': 0xA5},  # Filter for Ethernet devices
                {'name': 'product_id', 'data': 0xA6},  # Specific product ID, e.g., IMCv2
                {'name': 'sub_product_id', 'data': 0x01}  # Sub product ID for Zephyr
            ]

            # Perform the search
            found_signatures = find_signatures(criteria_list)
        """

        if self.signatures is None or len(self.signatures) == 0:
            return None

        results = []
        for signature in self.signatures:
            if all(any(self._matches_criteria(field, criteria) for field in signature.fields) for criteria in
                   criteria_list):
                results.append(signature)
        return results if results else None

    @staticmethod
    def _matches_criteria(field: SignatureFieldType, criteria: dict[str, Any]) -> bool:
        """
        Checks if a signature field matches all specified criteria.
        Iterates through each criterion and compares it to the attribute of the field object.
        Special handling is included for byte-type attributes that are compared to integer values.

        Args:
            field (_SignatureField): The field object to be evaluated.
            criteria (Dict[str, Any]): A dictionary of attributes and values that the field must match.

        Returns:
            bool: True if the field matches all criteria, False otherwise.
        """
        for key, value in criteria.items():
            if hasattr(field, key):
                field_value = getattr(field, key)
                if isinstance(field_value, bytes) and isinstance(value, int):
                    if field_value.decode() != str(value):
                        return False
                elif field_value != value:
                    return False
            else:
                return False
        return True

    def _build_fields_list(self, signature: Signature, schema: SignatureSchemaType):
        """
        Maps unpacked_data from a MatchResult to a structured list of SignatureField objects,
        properly handling nested structs and array types like char[N].

        Args:
            signature (MatchResult): A MatchResult object containing unpacked_data.
            schema (SignaturesLib.Schema): The loaded schema to use.
        Returns:
            None, any error will raise an exception.
        """
        field_offset: int = 0
        field_index: int = 0

        def _append_field(schema_field_name: str, schema_field_type: str, schema_field_size: int,
                          schema_field_is_integrity: bool, schema_field_read_only: bool,
                          schema_type_info: Optional[list]):
            """
            Create a field instance, populate it with data, and append it to the class list of fields.
            Ensures that field_index and signature.unpacked_data stay in sync.
            """
            nonlocal field_index, field_offset

            if field_index >= len(signature.unpacked_data):
                raise RuntimeError(
                    f"Mismatch: Expected {len(signature.unpacked_data)} fields, but schema defines more ({field_index + 1})")

            field_data = signature.unpacked_data[field_index]

            # Handle `char[N]` arrays properly
            if schema_field_type.startswith("char["):
                field_data = field_data.rstrip(b"\x00").decode("utf-8", errors="ignore")  # Strip nulls & decode

            # Create and store the SignatureField instance
            signature_field = SignatureFieldType()
            signature_field.name = schema_field_name
            signature_field.type = schema_field_type
            signature_field.size = schema_field_size
            signature_field.data = field_data
            signature_field.is_integrity = schema_field_is_integrity
            signature_field.read_only = schema_field_read_only
            signature_field.offset = field_offset

            # Populate enum fields if we have it
            if len(schema_type_info) > 0:
                type_info = schema_type_info[0]
                if len(type_info) > 0:
                    signature_field.type_info = type_info

            # Append the field to the field list which will later be attached to the containing signature
            signature.fields.append(signature_field)

            # Update indexes and offsets
            field_index += 1
            field_offset += schema_field_size

        try:
            if not signature.unpacked_data or "fields" not in schema.dictionary:
                raise RuntimeError("No unpacked data or schema provided")

            def _process_fields(fields: list[dict]):
                """
                Recursively process fields, ensuring every field is present in the unpacked data.
                Raises an error if the schema and unpacked data do not match.
                """

                for field in fields:
                    field_name = field.get("name")
                    field_type = field.get("type")
                    field_is_integrity = field.get("integrity", False)
                    field_read_only = field.get("read_only", False)
                    type_info = field.get("type_info", [])

                    if field_name is None or field_type is None:
                        raise RuntimeError(f"schema field missing crucial data: {field}")

                    if field_type == "struct" and "fields" in field:
                        # Recursively process nested struct
                        _process_fields(field["fields"])
                    else:
                        field_size = self.signatures_lib.type_to_size(str(field_type))
                        # Regular field
                        _append_field(schema_field_name=field_name, schema_field_type=str(field_type),
                                      schema_field_size=field_size, schema_field_is_integrity=field_is_integrity,
                                      schema_field_read_only=field_read_only, schema_type_info=type_info)

            # Start processing schema fields
            _process_fields(schema.dictionary["fields"])

            # Ensure all unpacked_data was consumed correctly
            if field_index != len(signature.unpacked_data):
                raise RuntimeError(
                    f"Schema defines {field_index} fields, but unpacked data contains {len(signature.unpacked_data)} values")

        except Exception as exception:
            raise RuntimeError(f"error processing fields: {exception}") from exception

    @staticmethod
    def _calculate_signature_offsets(signature: Signature):
        """
        Calculates the offset of the firmware start within a larger binary file containing multiple firmwares,
        such as a Non-Volatile Memory (NVM) file. This function uses the addresses set by the linker to determine
        the actual position of the firmware and its signature within the context of the larger binary file.

        The addresses used are:
        - `start_addr`: The address where the firmware binary starts, as set by the linker.
        - `sig_start_addr`: The address where the signature starts within the firmware, as set by the linker.
        - `end_addr`: The address where the firmware binary ends, as set by the linker.

        These addresses reflect the positions to which the firmware was linked, not offsets within the larger binary.
        However, by comparing these addresses, we can derive the size of the firmware binary and the offset of the
        firmware start within the larger file.

        Args:
            signature (Signature): The signature object containing field data for start, signature start, and end addresses.

        Returns:
            None: Updates the `signature` object with calculated offsets and size.

        Procedure:
        1. Extract the binary start, signature start, and binary end addresses from the signature object.
        2. Validate that these addresses are in a logical order (start < signature start < end).
        3. Calculate the size of the host firmware image and determine if offsets are applicable.
        4. Use the difference between the binary start and signature start addresses to determine the firmware's
           start offset within the larger file.
        """
        # Extract addresses from the signature object
        binary_start_address = signature.get_field_data(signature.find_first_field('start_addr'), 0)
        signature_start_address = signature.get_field_data(signature.find_first_field('sig_start_addr'), 0)
        binary_end_address = signature.get_field_data(signature.find_first_field('end_addr'), 0)

        # The size reported in the signature, may be larger that the linker-based properties combated size due to padding
        image_size = signature.get_field_data(signature.find_first_field('image_size'), 0)

        # Check if the addresses are in the expected order
        if 0 < binary_start_address < signature_start_address < binary_end_address:
            # Calculate the host image size from start-to-end address
            signature.file_image_size = binary_end_address - binary_start_address
            signature.offset_from_image_start = signature_start_address - binary_start_address
            signature.file_image_offset = signature.file_signature_offset - signature.offset_from_image_start
            signature.has_boundaries = True

            signature.padding_bytes = signature.get_field_data(signature.find_first_field('padding_bytes'), 0)
            # Old SigTool backwards compatibility
            if image_size > signature.file_image_size:
                signature.padding_bytes = image_size - signature.file_image_size

    def _build_signatures_list(self) -> Optional[int]:
        """
        Opens the file, maps it into memory, and scans for signature patterns.
        This method:
        - Memory-maps the file for efficient access.
        - Searches for defined signature patterns.
        - Extracts matched signature data, unpacking it using the specified format.
        - Stores the results in `self.signatures` for further processing.
        - Ensures the file is properly closed after processing.

        Returns:
            int: The number of signatures found in the file, or None if an error occurs.
        """
        try:

            if self._file_name is None:
                raise RuntimeError("file is already loaded or not specified.")

            schemas: Optional[list[SignatureSchemaType]] = self.signatures_lib.find_schemas(self.schema_name)
            if schemas is None or len(schemas) != 1:
                raise RuntimeError("no schema or more than one schema found")

            schema = schemas[0]
            self._logger.debug(
                f"Scanning for signatures in '{os.path.basename(self._file_name)}', using schema '{schema.name}'")

            # Reset read offset and clear previous results
            self._file_read_offset = 0
            self.signatures.clear()  # Drop currently stored signatures
            self.schema_name = schema.name
            signature_index: int = 0

            # Open the file and memory-map it
            with open(self._file_name, "r+b") as f:
                fd = f.fileno()
                self._file_size = os.path.getsize(self._file_name)

                # Memory-map the file (size=0 means the whole file)
                with mmap.mmap(fd, 0, access=mmap.ACCESS_READ) as mem_mapped_file:
                    self._file = mem_mapped_file

                    while self._file_read_offset < self._file_size:
                        # Extract remaining unmapped data
                        sliced_data = self._file[self._file_read_offset:]

                        # Search for the pattern in sliced data
                        match: Optional[Match] = schema.search_pattern.search(sliced_data)
                        if not match:
                            break  # No more matches, exit loop

                        # Compute match positions relative to the full file
                        match_start: int = match.start() + self._file_read_offset
                        match_end: int = match.end() + self._file_read_offset

                        # Extract and process the matched data
                        raw_data = cast(Any, self._file[match_start:match_end][:schema.size])

                        try:
                            unpacked_data = struct.unpack(schema.format_string, raw_data)
                        except struct.error:
                            unpacked_data = None  # Handle cases where unpacking fails

                        # Create a signature instance
                        signature = Signature(file_name=self._file_name, unpacked_data=unpacked_data, data=raw_data,
                                              file_signature_offset=match_start, file_handler=self)

                        # Retrieve the fields associated with this signature based on the schema
                        self._build_fields_list(signature=signature, schema=schema)
                        self._calculate_signature_offsets(signature=signature)
                        signature.verified = signature.verify()  # CRC verification

                        # Store the found signature
                        signature.index = signature_index
                        self.signatures.append(signature)

                        # Move the offset past the match to continue searching
                        self._file_read_offset = match_end + 1
                        signature_index += 1

                    self._close_file()  # Close the memory-mapped file
                    self._logger.debug(f"Found {len(self.signatures)} signatures")
                    return len(self.signatures)

        except Exception as exception:
            self._close_file()  # Ensure proper cleanup on error
            raise RuntimeError(f"error opening file '{self._file_name}': {exception}") from exception

    def _close_file(self):
        """
        Close the memory-mapped file and invalidate class members
        """
        if self._file is not None:
            self._file.close()
            self._file = None
            self._file_read_offset = 0
