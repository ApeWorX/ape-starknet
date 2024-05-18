from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional, Tuple, Union, cast, get_args

from ape.types.address import AddressType, RawAddress
from ape.types.vm import BlockID
from eth_pydantic_types import HexBytes
from eth_typing import HexAddress, HexStr
from eth_utils import add_0x_prefix, is_0x_prefixed, is_text, remove_0x_prefix
from eth_utils import to_int as eth_to_int
from ethpm_types import ContractType
from starknet_py.net.client_models import Hash, Tag
from starknet_py.net.models.address import parse_address
from starknet_py.net.signer.stark_curve_signer import KeyPair
from starkware.cairo.bootloaders.compute_fact import keccak_ints
from starkware.crypto.signature.signature import (
    get_random_private_key as get_random_pkey,
)
from starkware.starknet.services.api.contract_class.contract_class_utils import (
    ContractClass,
)

from ape_starknet.exceptions import StarknetProviderError
from ape_starknet.networks import NETWORKS

_HEX_ADDRESS_REG_EXP = re.compile("(0x)?[0-9a-f]*", re.IGNORECASE | re.ASCII)
"""Same as from eth-utils except not limited length."""


class NetworkType(Enum):
    MAINNET = "mainnet"
    TESTNET = "testnet"
    LOCAL = "local"


class FeeTokenType(Enum):
    ETH = "eth"
    STRK = "strk"


def get_random_private_key() -> str:
    private_key = HexBytes(get_random_pkey()).hex()
    return pad_hex_str(private_key)


def convert_contract_class_to_contract_type(
    name: str, source_id: str, contract_class: ContractClass
):
    return ContractType.parse_obj(
        {
            "contractName": name,
            "sourceId": source_id,
            "deploymentBytecode": {"bytecode": contract_class.serialize().hex()},
            "runtimeBytecode": {},
            "abi": contract_class.abi,
        }
    )


def create_keypair(private_key: Union[str, int]) -> KeyPair:
    # Validate private key.
    if isinstance(private_key, str) and is_0x_prefixed(private_key):
        private_key = to_int(private_key.strip("'\""))
    elif isinstance(private_key, str):
        private_key = to_int(private_key)

    return KeyPair.from_private_key(private_key)


def pad_hex_str(value: str, to_length: int = 64) -> str:
    if value.startswith("0x"):
        val = value.replace("0x", "")
    actual_len = len(val)
    padding = "0" * (to_length - actual_len)
    return f"0x{padding}{val}"


def clean_network_name(network: str) -> str:
    for net in NETWORKS.keys():
        if net in network:
            return net

    if "goerli" in network:
        raise ValueError("Goerli is not supported. Please use 'testnet' instead.")
    return network


def to_hex(value: Any) -> str:
    """
    Convert a value to a hexadecimal string.

    Args:
        value (Any): The value to convert.

    Returns:
        str: The hexadecimal string representation of the input value.
    """
    if isinstance(value, int):
        hex_string = hex(value)
        return hex_string if hex_string.startswith("0x") else "0x" + hex_string[2:]

    elif isinstance(value, str):
        if value.startswith("0x"):
            return value
        try:
            return "0x" + format(int(value), "x")

        except ValueError:
            return "0x" + value.encode().hex()

    elif isinstance(value, bytes):
        return "0x" + value.hex()
    else:
        return "0x" + str(value).encode().hex()


def is_hex_address(value: Any) -> bool:
    return _HEX_ADDRESS_REG_EXP.fullmatch(value) is not None if is_text(value) else False


def to_int(val: Any) -> int:
    """
    Convert various types to integer.

    Args:
        val (Any): The value to convert to an integer.

    Returns:
        int: The converted integer value.

    Raises:
        ValueError: If the input cannot be converted to an integer.
    """
    if isinstance(val, int):
        return val

    if isinstance(val, str):
        if is_0x_prefixed(val):
            return eth_to_int(hexstr=val)
        if val.isnumeric():
            return int(val)
        try:
            return eth_to_int(val.encode())
        except ValueError:
            pass  # If this fails, we'll raise a custom error at the end

    if isinstance(val, bytes):
        return eth_to_int(val)

    if hasattr(val, "address"):
        return to_int(val.address)

    try:
        return eth_to_int(val)
    except (TypeError, ValueError):
        raise ValueError(f"Cannot convert {val} of type {type(val)} to int")


def calldata_to_hex(self, calldata):
    if not calldata:
        return "0x"
    return "0x" + "".join([hex(i)[2:].zfill(64) for i in calldata])


def parse_block_id(block_id: Optional[BlockID]) -> Tuple[Optional[Hash], Optional[Union[int, Tag]]]:
    """
    Parse the block_id into block_hash and block_number.

    Args:
        block_id (Optional[BlockID]): The block identifier.

    Returns:
        Tuple[Optional[Hash], Optional[Union[int, Tag]]]:
        A tuple of (block_hash, block_number) for use in API calls.

    Raises:
        StarknetProviderError: If the block_id is invalid.
    """
    if block_id is None:
        return None, "pending"

    if isinstance(block_id, bytes):
        block_id = to_int(block_id)

    if isinstance(block_id, int):
        # Assume integers larger than 2^64 are block hashes
        if block_id > 2**64:
            return hex(block_id), None
        return None, block_id  # Return (None, block_number) for integer inputs

    if isinstance(block_id, str):
        if block_id.startswith("0x") and len(block_id) >= 66:
            return block_id, None  # Return (block_hash, None) for hash inputs

        if block_id in ("pending", "latest"):
            return None, block_id  # Return (None, "pending"/"latest") for special tags
        try:
            return None, int(block_id)  # Try to convert string to int for block numbers
        except ValueError:
            pass  # If conversion fails, continue to the next checks

    if isinstance(block_id, HexBytes) or isinstance(block_id, get_args(HexStr)):
        return str(block_id), None  # Convert HexBytes/HexStr to string hash

    raise StarknetProviderError(f"Invalid block_id: {block_id}")


def to_checksum_address(address: RawAddress) -> AddressType:
    if is_checksum_address(address):
        return cast(AddressType, address)

    return _to_checksum_address(address)


def _to_checksum_address(address: RawAddress) -> AddressType:
    if isinstance(address, bytes):
        address = HexBytes(address).hex()

    address_int = parse_address(address)
    address_str = pad_hex_str(HexBytes(address_int).hex().lower())
    chars = [c for c in remove_0x_prefix(HexStr(address_str))]
    hashed = [b for b in HexBytes(keccak_ints([address_int]))]

    for i in range(0, len(chars), 2):
        try:
            if hashed[i >> 1] >> 4 >= 8:
                chars[i] = chars[i].upper()
            if (hashed[i >> 1] & 0x0F) >= 8:
                chars[i + 1] = chars[i + 1].upper()
        except IndexError:
            continue

    rejoined_address_str = add_0x_prefix(HexStr("".join(chars)))
    return AddressType(HexAddress(HexStr(rejoined_address_str)))


def is_checksum_address(value: Any) -> bool:
    if not is_text(value):
        return False

    if not is_hex_address(value):
        return False

    return value == _to_checksum_address(value)


PREDEPLOYED_ACCOUNT_CLASS_HASH = to_int(
    "0x061dac032f228abef9c6626f995015233097ae253a7f72d68552db02f2971b8f"
)

DEVNET_CONTRACT_SALT = 20
