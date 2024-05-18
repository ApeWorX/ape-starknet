from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, List, Optional, Union

import requests  # type: ignore
from ape.api.providers import ProviderAPI
from ape.api.transactions import ReceiptAPI, TransactionAPI
from ape.exceptions import (
    ProviderNotConnectedError,
)
from ape.logging import logger
from ape.types.address import AddressType
from ape.types.events import LogFilter
from ape.types.vm import BlockID
from ape.utils.misc import cached_property, raises_not_implemented
from eth_pydantic_types import HexBytes
from ethpm_types import ContractType
from starknet_py.abi.v1.shape import AbiDictList as AbiDictListV1
from starknet_py.abi.v2.shape import AbiDictList as AbiDictListV2
from starknet_py.contract import Contract
from starknet_py.net.client_errors import ClientError
from starknet_py.net.client_models import (
    DeprecatedContractClass,
    EstimatedFee,
    ResourcePrice,
    SierraContractClass,
)
from starknet_py.net.full_node_client import FullNodeClient

from ape_starknet.basemodel import StarknetBase
from ape_starknet.config import StarknetConfig
from ape_starknet.constants import PLUGIN_NAME
from ape_starknet.contracts import (
    StarknetFeeTokenContractClass,
    StarknetUDCContractClass,
)
from ape_starknet.exceptions import StarknetProviderError
from ape_starknet.transactions import (
    StarknetBlock,
    convert_to_base_tx,
    convert_to_starknet_tx,
    is_declare_tx,
    is_deploy_account_tx,
    is_invoke_tx,
)
from ape_starknet.utils import (
    FeeTokenType,
    pad_hex_str,
    parse_block_id,
    to_hex,
    to_int,
)

if TYPE_CHECKING:
    from ape_starknet.accounts import BaseStarknetAccount

import atexit
import os
import signal

import psutil


class StarknetProvider(ProviderAPI, StarknetBase):
    name: str = PLUGIN_NAME
    _client: FullNodeClient = None
    _fee_token_contract_class: Optional[StarknetFeeTokenContractClass] = None
    _udc_contract_class: Optional[StarknetUDCContractClass] = None

    @property
    def fee_token_contract_class(self) -> StarknetFeeTokenContractClass:
        if not self._fee_token_contract_class:
            self._fee_token_contract_class = StarknetFeeTokenContractClass()
        return self._fee_token_contract_class

    @property
    def udc_contract_class(self) -> StarknetUDCContractClass:
        if not self._udc_contract_class:
            self._udc_contract_class = StarknetUDCContractClass()
        return self._udc_contract_class

    @property
    def connected_client(self):
        """
        Connect to client or raises a :class:`~ape_starknet.exceptions.ProviderNotConnectedError`
        otherwise.
        """
        if not self._client:
            raise ProviderNotConnectedError()

        return self._client

    @property
    def starknet_client(self) -> FullNodeClient:
        if not self.is_connected:
            # raise StarknetProviderError("Not connected to Starknet provider.")
            self.connect()

        return self._client

    def connect(self) -> None:
        """
        Connect a to a provider, such as start-up a process or create an HTTP connection.
        """
        self._client = self._create_client()

    def _create_client(self) -> FullNodeClient:
        if not self._client:
            self._client = FullNodeClient(self.uri)
        try:
            self._client.get_block_sync("latest")
        except Exception as e:
            raise StarknetProviderError(f"Failed to connect to Starknet provider: {str(e)}")
        return self._client

    def disconnect(self) -> None:
        """
        Disconnect from a provider, such as tear-down a process or quit an HTTP session.
        """
        self._client = None

    @property
    def max_gas(self) -> int:
        return 0

    @property
    def is_connected(self) -> bool:
        if not self._client:
            return False
        try:
            self._client.get_block_sync("latest")
        except ClientError:
            return False
        return True

    @cached_property
    def plugin_config(self) -> StarknetConfig:
        """Returns network configurations"""
        return self.config_manager.get_config(PLUGIN_NAME) or StarknetConfig()  # type: ignore

    @cached_property
    def uri(self) -> Optional[str]:
        network_config: dict = self.plugin_config.provider.get(self.network.name)
        if not network_config:
            raise StarknetProviderError(f"Unknown network '{self.network.name}'.")

        return network_config.get("uri")

    @cached_property
    def headers(self) -> Optional[dict]:
        network_config: dict = self.plugin_config.provider.get(self.network.name)
        if not network_config:
            raise StarknetProviderError(f"Unknown network '{self.network.name}'.")

        return network_config.get("headers", {})

    def update_settings(self, new_settings: dict) -> None:
        """
        Change a provider's setting, such as configure a new port to run on.
        May require a reconnect.

        Args:
            new_settings (dict): The new provider settings.
        """
        pass

    @property
    def chain_id(self) -> int:
        """
        The blockchain ID.
        See `ChainList <https://chainlist.org/>`__ for a comprehensive list of IDs.
        """
        return to_int(self.starknet_client.get_chain_id_sync())

    def get_fee_token_contract(
        self,
        account: Optional[BaseStarknetAccount] = None,
        token_type: FeeTokenType = FeeTokenType.STRK,
    ) -> Contract:
        return self.fee_token_contract_class.feetoken_contract(
            account=account, token_type=token_type
        )

    def get_udc_contract(self, account: Optional[BaseStarknetAccount] = None) -> Contract:
        return self.udc_contract_class.udc_contract(account=account)

    def get_balance(self, address: str, block_id: Optional[BlockID] = None) -> int:
        address_int = to_int(address)
        call_tx = self.get_fee_token_contract().functions["balanceOf"].call_sync(address_int)
        return call_tx[0]

    def get_contract_class(
        self, address: str, block_id: Optional[BlockID] = None
    ) -> Union[SierraContractClass, DeprecatedContractClass]:
        """
        Get the contract class at the given address.

        Args:
            address (int): The address of the contract.
            block_id (Optional[int | str]): The block number or hash, or 'latest' or 'pending'.
                                            If None, defaults to 'pending'.

        Returns:
            SierraContractClass | ContractClass: The contract class.
        """
        address_int = to_int(address)
        block_hash, block_number = parse_block_id(block_id)

        contract_class = self.starknet_client.get_class_at_sync(
            contract_address=address_int, block_number=block_number, block_hash=block_hash
        )
        return contract_class

    def get_code(self, address: str, block_id: Optional[BlockID] = None) -> bytes:
        """
        Get the bytes of a contract.

        Args:
            address (AddressType): The address of the contract.
            block_id (Optional[BlockID]): The block ID for checking a previous contract state.

        Returns:
            bytes: The contract bytecode.

        Raises:
            ContractNotFoundError: If the contract is not found at the given address.
        """
        try:
            address_int = to_int(address)
            # Parse the block identifier
            block_hash, block_number = parse_block_id(block_id)

            # Get the contract class
            contract_class = self.starknet_client.get_class_at_sync(
                contract_address=address_int, block_number=block_number, block_hash=block_hash
            )

            # For Cairo 0 contracts
            if hasattr(contract_class, "program"):
                code = contract_class.program
            # For Cairo 1 contracts (Sierra)
            elif hasattr(contract_class, "sierra_program"):
                code = contract_class.sierra_program
            # If neither program nor sierra_program is found
            else:
                raise Exception(f"No code found for contract at address {address}")

            # Convert the code to bytes
            return b"".join([HexBytes(x) for x in code])

        except Exception as e:
            raise Exception(f"Failed to get code for contract at address {address}: {str(e)}")

    def get_abi(self, address: AddressType, block_id: Optional[BlockID] = None) -> Optional[str]:
        """
        Get the ABI of a contract.

        Args:
            address (int): The address of the contract.
            block_id (Optional[Union[int, str]]): The block number, hash, or 'latest' or 'pending'.
                                                  If None, defaults to 'pending'.

        Returns:
            dict: The contract's ABI.
        """
        contract_class = self.get_contract_class(address, block_id)
        if not contract_class:
            return None

        return contract_class.abi

    def get_parsed_abi(
        self, address: AddressType, block_id: Optional[BlockID] = None
    ) -> Union[AbiDictListV2, AbiDictListV1]:
        """
        Get the ABI of a contract.

        Args:
            address (int): The address of the contract.
            block_id (Optional[Union[int, str]]): The block number, hash, or 'latest' or 'pending'.
                                                  If None, defaults to 'pending'.

        Returns:
            dict: The contract's ABI.
        """
        contract_class = self.get_contract_class(address, block_id)
        if not contract_class:
            return None

        return contract_class.parsed_abi  # return parsed abi

    def get_contract_type(self, address: str) -> Optional[ContractType]:
        """
        Get the contract type at the given address.

        Args:
            address (int): The address of the contract.

        Returns:
            Contract: The contract type.
        """

        contract_class = self.get_contract_class(address)
        # For Cairo 0 contracts
        if hasattr(contract_class, "program"):
            code = contract_class.program
        # For Cairo 1 contracts (Sierra)
        elif hasattr(contract_class, "sierra_program"):
            code = contract_class.sierra_program
        # If neither program nor sierra_program is found
        else:
            raise Exception(f"No code found for contract at address {address}")

        bytecode_str_list = [HexBytes(x).hex() for x in code]
        if not bytecode_str_list:
            return None

        longest_str = min(len(max(bytecode_str_list, key=len)) - 2, 0)
        code_parts = [
            pad_hex_str(x, to_length=longest_str).replace("0x", "") for x in bytecode_str_list
        ]

        contract_type_dict = {
            "abi": contract_class.parsed_abi,
            "deploymentBytecode": {"bytecode": f"0x{''.join(code_parts)}"},
        }
        return ContractType(**contract_type_dict)

    def get_class_hash(self, address: str, block_id: Optional[BlockID] = None) -> int:
        try:
            address_int = to_int(address)
            block_hash, block_number = parse_block_id(block_id)

            class_hash = self.starknet_client.get_class_hash_at_sync(
                contract_address=address_int, block_number=block_number, block_hash=block_hash
            )

            return class_hash

        except Exception as e:
            raise Exception(f"Failed to get class hash for contract at address {address}: {str(e)}")

    @raises_not_implemented
    def make_request(self, rpc: str, parameters: Optional[Iterable] = None):
        """
        Make a raw RPC request to the provider.
        Advanced featues such as tracing may utilize this to by-pass unnecessary
        class-serializations.
        """
        params = list(parameters) if parameters is not None else []
        return self.starknet_client._client.call(method_name=rpc, params=params)

    def get_nonce(self, address: str, block_id: Optional[BlockID] = None) -> int:
        """
        Get the number of times an account has transacted.

        Args:
            address (int): The address of the account.
            block_id (Optional[Union[int, str]]): The block number, hash, or 'latest' or 'pending'.
                                                  If None, defaults to 'pending'.

        Returns:
            int: The nonce of the account.
        """

        address_int = to_int(address)
        block_hash, block_number = parse_block_id(block_id)

        nonce = self.starknet_client.get_contract_nonce_sync(
            contract_address=address_int, block_number=block_number, block_hash=block_hash
        )
        return nonce

    def estimate_gas_cost(
        self, txn: TransactionAPI, block_id: Optional[BlockID] = None
    ) -> EstimatedFee:
        """
        Estimate the cost of gas for a transaction.

        Args:
            txn (:class:`~ape.api.transactions.TransactionAPI`):
              The transaction to estimate the gas for.
            block_id (Optional[:class:`~ape.types.BlockID`]): The block ID
              to use when estimating the transaction. Useful for checking a
              past estimation cost of a transaction.

        Returns:
            int: The estimated cost of gas to execute the transaction
            reported in the fee-currency's smallest unit, e.g. Wei. If the
            provider's network has been configured with a gas limit override, it
            will be returned. If the gas limit configuration is "max" this will
            return the block maximum gas limit.
        """
        block_hash, block_number = parse_block_id(block_id)
        base_tx = convert_to_base_tx(txn)

        estimated_fee = self.starknet_client.estimate_fee_sync(
            tx=base_tx, block_number=block_number, block_hash=block_hash
        )
        return estimated_fee

    @property
    def gas_price(self) -> ResourcePrice:
        block = self.get_block("latest")
        return block.l1_gas_price

    @property
    def l1_gas_price(self) -> ResourcePrice:
        return self.gas_price

    def get_block(self, block_id: BlockID) -> StarknetBlock:
        """
                Get a block.

                Args:
                    block_id (:class:`~ape.types.BlockID`): The ID of the block to get.
                        Can be ``"latest"``, ``"earliest"``, ``"pending"``, a block hash or a block number.
        "
                Raises:
                    :class:`~ape.exceptions.BlockNotFoundError`: Likely the exception raised when a block
                      is not found (depends on implementation).

                Returns:
                    :class:`~ape.types.BlockID`: The block for the given ID.
        """
        block_hash, block_number = parse_block_id(block_id)
        block = self.starknet_client.get_block_sync(
            block_number=block_number, block_hash=block_hash
        )
        decoded_block = self.starknet.decode_block(block)
        return decoded_block

    def get_block_with_txs(self, block_id: BlockID) -> StarknetBlock:
        """
        Get a block with its transactions

        Args:
            block_id (:class:`~ape.types.BlockID`): The ID of the block to get.
                Can be ``"latest"``, ``"earliest"``, ``"pending"``, a block hash or a block number.

        Raises:
            :class:`~ape.exceptions.BlockNotFoundError`: Likely the exception raised when a block
              is not found (depends on implementation).

        Returns:
            :class:`~ape.types.BlockID`: The block for the given ID.
        """
        block_hash, block_number = parse_block_id(block_id)
        block = self.starknet_client.get_block_with_txs_sync(
            block_number=block_number, block_hash=block_hash
        )
        block = self.starknet.decode_block(block)
        decoded_block = self.starknet.decode_block(block)
        return decoded_block

    def get_block_with_receipts(self, block_id: BlockID) -> StarknetBlock:
        """
        Get a block with its receipts.

        Args:
            block_id (:class:`~ape.types
            .BlockID`): The ID of the block to get.
                Can be ``"latest"``, ``"earliest"``, ``"pending"``, a block hash or a block number.

        Raises:
            :class:`~ape.exceptions.BlockNotFoundError`: Likely the exception raised when a block
              is not found (depends on implementation).

        Returns:
            :class:`~ape.types.BlockID`: The block for the given ID.
        """
        block_hash, block_number = parse_block_id(block_id)

        block = self.starknet_client.get_block_with_receipts_sync(
            block_number=block_number, block_hash=block_hash
        )
        decoded_block = self.starknet.decode_block(block)
        return decoded_block

    @raises_not_implemented
    def send_call(
        self,
        txn: TransactionAPI,
        block_id: Optional[BlockID] = None,
        state: Optional[dict] = None,
        **kwargs,
    ) -> HexBytes:  # Return value of function
        """
        Execute a new transaction call immediately without creating a
        transaction on the block chain.

        Args:
            txn: :class:`~ape.api.transactions.TransactionAPI`
            block_id (Optional[:class:`~ape.types.BlockID`]): The block ID
                to use to send a call at a historical point of a contract.
                Useful for checking a past estimation cost of a transaction.
            state (Optional[dict]): Modify the state of the blockchain
                prior to sending the call, for testing purposes.
            **kwargs: Provider-specific extra kwargs.

        Returns:
            str: The result of the transaction call.
        """

        block_hash, block_number = parse_block_id(block_id)  # try:
        result = self.starknet_client.call_contract(
            call=txn, block_number=block_number, block_hash=block_hash
        )
        return result

    def get_receipt(self, txn_hash: Union[int, str, bytes], **kwargs) -> ReceiptAPI:
        """
        Get the information about a transaction from a transaction hash.

        Args:
            txn_hash (str): The hash of the transaction to retrieve.
            kwargs: Any other kwargs that other providers might allow when fetching a receipt.

        Returns:
            :class:`~api.providers.ReceiptAPI`:
            The receipt of the transaction with the given hash.
        """
        txn_hash_int = to_int(txn_hash)
        self.starknet_client.wait_for_tx_sync(tx_hash=txn_hash_int)
        receipt = self.starknet_client.get_transaction_receipt_sync(tx_hash=txn_hash_int)
        decoded_receipt = self.starknet.decode_receipt(receipt)
        return decoded_receipt

    def get_transaction(self, txn_hash: Union[int, str, bytes]) -> TransactionAPI:
        txn_hash_int = to_int(txn_hash)
        txn = self.starknet_client.get_transaction_sync(tx_hash=txn_hash_int)
        starknet_txn = convert_to_starknet_tx(txn)
        return starknet_txn

    def get_transactions_by_block(self, block_id: BlockID) -> Iterator[TransactionAPI]:
        """
        Get the information about a set of transactions from a block.

        Args:
            block_id (:class:`~ape.types.BlockID`): The ID of the block.

        Returns:
            Iterator[:class: `~ape.api.transactions.TransactionAPI`]
        """

        block_hash, block_number = parse_block_id(block_id)
        block = self.starknet_client.get_block_sync(
            block_number=block_number, block_hash=block_hash
        )
        yield from [convert_to_starknet_tx(x) for x in block.transactions]

    @raises_not_implemented
    def get_transactions_by_account_nonce(  # type: ignore[empty-body]
        self,
        account: AddressType,
        start_nonce: int = 0,
        stop_nonce: int = -1,
    ):
        pass

    def prepare_transaction(self, txn) -> TransactionAPI:
        """
        Set default values on the transaction.

        Raises:
            :class:`~ape.exceptions.TransactionError`: When given negative required confirmations.

        Args:
            txn (:class:`~ape.api.transactions.TransactionAPI`): The transaction to prepare.

        Returns:
            :class:`~ape.api.transactions.TransactionAPI`
        """
        if hasattr(txn, "sender_address") and txn.is_prepared is False:
            sender_address = txn.sender_address  # convert from int
            if isinstance(sender_address, int):
                sender_address = to_hex(sender_address)

            account = self.account_manager[sender_address]
            account.prepare_transaction(txn)
        return txn

    def get_transaction_status(self, txn_hash: Union[int, str, bytes]) -> str:
        """
        Get the status of a transaction.

        Args:
            txn_hash (str): The hash of the transaction to check.

        Returns:
            str: The status of the transaction.
        """
        txn_hash_int = to_int(txn_hash)
        status = self.starknet_client.get_transaction_status_sync(tx_hash=txn_hash_int)
        return status

    def send_transaction(self, txn: TransactionAPI) -> ReceiptAPI:
        """
        Send a transaction to the network.

        Args:
            txn (:class:`~ape.api.transactions.TransactionAPI`): The transaction to send.

        Returns:
            :class:`~ape.api.transactions.ReceiptAPI`
        """

        txn = convert_to_starknet_tx(txn)
        if is_declare_tx(txn):
            response = self.starknet_client.declare_sync(txn)

        elif is_deploy_account_tx(txn):
            response = self.starknet_client.deploy_account_sync(txn)

        elif is_invoke_tx(txn):
            response = self.starknet_client.send_transaction_sync(txn)

        receipt = self.get_receipt(response.transaction_hash)
        return receipt

    @raises_not_implemented
    def get_contract_logs(
        self,
        log_filter: LogFilter,
    ):
        pass


class StarknetDevnetProvider(StarknetProvider):
    _process: Optional[subprocess.Popen] = None
    _devnet_config: Optional[dict] = None

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5050,
        accounts: int = 10,
        account_class: str = "cairo1",
        account_class_custom: Optional[str] = None,
        initial_balance: int = 1000000000000000000000,
        seed: Optional[int] = 0,  # Changed default to None as per help output
        start_time: Optional[int] = None,
        timeout: int = 120,
        gas_price: int = 100000000000,
        gas_price_fri: int = 100000000000,
        data_gas_price: int = 100000000000,
        data_gas_price_fri: int = 100000000000,
        chain_id: Optional[str] = "TESTNET",  # Made Optional to handle incompatibility
        dump_on: Optional[str] = None,
        lite_mode: bool = False,
        dump_path: Optional[str] = None,
        block_generation_on: str = "transaction",
        state_archive_capacity: str = "none",
        fork_network: Optional[str] = None,  # Changed default to None as per help output
        fork_block: Optional[int] = None,
        request_body_size_limit: int = 2000000,
        restrictive_mode: Optional[list] = None,  # Added new argument
        **kwargs,
    ):
        super().__init__(**kwargs)
        atexit.register(self.stop_devnet)

        self._devnet_config = {
            "host": host,
            "port": port,
            "accounts": accounts,
            "account_class": account_class,
            "account_class_custom": account_class_custom,
            "initial_balance": initial_balance,
            "seed": seed,
            "start_time": start_time,
            "timeout": timeout,
            "gas_price": gas_price,
            "gas_price_fri": gas_price_fri,
            "data_gas_price": data_gas_price,
            "data_gas_price_fri": data_gas_price_fri,
            "chain_id": chain_id,
            "dump_on": dump_on,
            "lite_mode": lite_mode,
            "dump_path": dump_path,
            "block_generation_on": block_generation_on,
            "state_archive_capacity": state_archive_capacity,
            "fork_network": fork_network,
            "fork_block": fork_block,
            "request_body_size_limit": request_body_size_limit,
            "restrictive_mode": restrictive_mode,
        }
        if self.provider_settings:
            for key, value in self.provider_settings.items():
                underscored_key = key.replace("-", "_")
                if underscored_key in self._devnet_config:
                    self._devnet_config[underscored_key] = value

            if "chain_id" in self._devnet_config and "fork_network" in self._devnet_config:
                if (
                    self._devnet_config["fork_network"]
                    and self._devnet_config["chain_id"] == chain_id
                ):
                    del self._devnet_config["chain_id"]

                elif (
                    self._devnet_config["chain_id"] != chain_id
                    and not self._devnet_config["fork_network"]
                ):
                    del self._devnet_config["fork_network"]

                elif (
                    self._devnet_config["chain_id"] != chain_id
                    and self._devnet_config["fork_network"]
                ):
                    raise Exception("Cannot set both chain_id and fork_network")

    @property
    def node_url(self) -> Optional[str]:
        if self._devnet_config is None:
            return None
        return f"http://{self._devnet_config['host']}:{self._devnet_config['port']}"

    def start_devnet(self, timeout=60):
        if self._process or self.is_devnet_running():
            raise Exception("Devnet is already running")

        command = ["starknet-devnet"]
        for key, value in self._devnet_config.items():
            if value is not None:
                if isinstance(value, bool):
                    if value:
                        command.append(f"--{key.replace('_', '-')}")
                else:
                    command.extend([f"--{key.replace('_', '-')}", str(value)])

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except FileNotFoundError:
            raise Exception("starknet-devnet not found. Make sure it's installed and in your PATH.")

        # Wait for the "listening" message
        start_time = time.time()
        while True:
            output = self._process.stdout.readline()

            if str(output).lower().strip() == "error":
                logger.error(output)
                self.stop_devnet()
                raise Exception("Devnet process terminated unexpectedly")

            if "Starknet Devnet listening on" in output:
                break
            if time.time() - start_time > timeout:
                self.stop_devnet()
                raise Exception("Timeout waiting for devnet to start")

            if self._process.poll() is not None:
                raise Exception("Devnet process terminated unexpectedly")

    def _create_client(self, max_retries=5, initial_delay=1, max_delay=16) -> FullNodeClient:
        if not self._client:

            self._client = FullNodeClient(self.uri)

        delay = initial_delay
        for attempt in range(max_retries):
            try:
                self._client.get_block_sync("latest")
                return self._client
            except Exception as e:
                if attempt == max_retries - 1:
                    self.stop_devnet()
                    raise StarknetProviderError(
                        f"Failed to connect to Starknet provider after {max_retries} attempts: {str(e)}"
                    )

                logger.info(
                    f"Connection attempt {attempt + 1} failed. Retrying in {delay} seconds..."
                )
                time.sleep(delay)
                delay = min(delay * 2, max_delay)

        raise StarknetProviderError("Failed to connect to Starknet provider: Max retries reached")

    def stop_devnet(self):
        # Try to stop the process we know about
        if self._process:
            try:
                os.kill(self._process.pid, signal.SIGTERM)
                self._process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                self._process = None

        # Check for any starknet-devnet processes and terminate them
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.name() == "starknet-devnet":
                    proc.terminate()
                    proc.wait(timeout=5)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                pass

        # Final check: forcefully kill any remaining starknet-devnet processes
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.name() == "starknet-devnet":
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def __del__(self):
        self.stop_devnet()

    def is_devnet_running(self) -> bool:
        """
        Check if the devnet is running by making a request to the node URL.

        Returns:
            bool: True if the devnet is running and responding, False otherwise.
        """
        if not self.node_url:
            return False

        try:
            is_alive_endpoint = f"{self.node_url}/is_alive"
            # Make a GET request to the /is_alive endpoint
            if self.node_url.endswith("/"):
                is_alive_endpoint = f"{self.node_url}is_alive"

            response = requests.get(url=is_alive_endpoint, timeout=5)

            # Check if the response status code is 200 (OK)
            return response.status_code == 200

        except Exception:
            # If there's any kind of request exception (connection error, timeout, etc.),
            # we assume the devnet is not running
            return False

    def connect(self):
        if not self.is_devnet_running():
            self.start_devnet()
        self._client = self._create_client()
        assert self.is_connected

    def disconnect(self):
        if self.is_devnet_running():
            self.stop_devnet()

        self._client = None

    @cached_property
    def uri(self) -> Optional[str]:
        return self.node_url

    def create_block(self) -> Dict[str, str]:
        """Create a new block."""
        return self._make_request("devnet_createBlock")

    def set_time(self, time: int, generate_block: bool = True) -> Dict[str, Any]:
        """Set the time of the devnet."""
        return self._make_request(
            "devnet_setTime", {"time": time, "generate_block": generate_block}
        )

    def increase_time(self, time: int) -> Dict[str, Any]:
        """Increase the current time of the devnet."""
        return self._make_request("devnet_increaseTime", {"time": time})

    def mint(self, address: str, amount: int, unit: str = "WEI") -> Dict[str, Any]:
        """Mint tokens to a specified address."""
        return self._make_request(
            "devnet_mint", {"address": address, "amount": amount, "unit": unit}
        )

    def set_gas_price(self, price: int) -> Dict[str, Any]:
        """Set the gas price for the devnet."""
        return self._make_request("devnet_setGasPrice", {"price": price})

    def dump_state(self, path: str) -> Dict[str, Any]:
        """Dump the current state of the devnet to a file."""
        return self._make_request("devnet_dump", {"path": path})

    def load_state(self, path: str) -> Dict[str, Any]:
        """Load a previously dumped state into the devnet."""
        return self._make_request("devnet_load", {"path": path})

    def impersonate_account(self, address: str) -> None:
        """Impersonate an account for testing purposes."""
        self._make_request("devnet_impersonateAccount", {"account_address": address})

    def stop_impersonating_account(self, address: str) -> None:
        """Stop impersonating an account."""
        self._make_request("devnet_stopImpersonateAccount", {"account_address": address})

    def auto_impersonate(self) -> None:
        """Enable automatic account impersonation."""
        self._make_request("devnet_autoImpersonate")

    def stop_auto_impersonate(self) -> None:
        """Stop automatic account impersonation."""
        self._make_request("devnet_stopAutoImpersonate")

    def get_predeployed_accounts(self, with_balance: bool = False) -> List[Dict[str, Any]]:
        """Get information about predeployed accounts."""
        x = self._make_request("devnet_getPredeployedAccounts", {"with_balance": with_balance})

        return self._make_request("devnet_getPredeployedAccounts", {"with_balance": with_balance})

    def restart_state(self) -> Dict[str, Any]:
        """Restart the devnet, resetting its state."""
        return self._make_request("devnet_restart")

    def abort_blocks(self, starting_block_hash: str) -> Dict[str, List[str]]:
        """Abort blocks starting from the specified block hash."""
        return self._make_request(
            "devnet_abortBlocks", {"starting_block_hash": starting_block_hash}
        )

    def postman_load_l1_messaging_contract(
        self, network_url: str, address: Optional[str] = None
    ) -> Dict[str, str]:
        """Load a MockStarknetMessaging contract for L1-L2 communication."""
        params = {"network_url": network_url}
        if address:
            params["address"] = address
        return self._make_request("devnet_postmanLoad", params)

    def postman_flush(self, dry_run: bool = False) -> Dict[str, Any]:
        """Flush the postman, sending messages between L1 and L2."""
        return self._make_request("devnet_postmanFlush", {"dry_run": dry_run})

    def postman_send_message_to_l2(
        self,
        l2_contract_address: str,
        entry_point_selector: str,
        l1_contract_address: str,
        payload: List[int],
        nonce: int,
        paid_fee_on_l1: int,
    ) -> Dict[str, str]:
        """Send a mock message from L1 to L2."""
        params = {
            "l2_contract_address": l2_contract_address,
            "entry_point_selector": entry_point_selector,
            "l1_contract_address": l1_contract_address,
            "payload": [hex(p) for p in payload],
            "nonce": hex(nonce),
            "paid_fee_on_l1": hex(paid_fee_on_l1),
        }
        return self._make_request("devnet_postmanSendMessageToL2", params)

    def postman_consume_message_from_l2(
        self, l2_contract_address: str, l1_contract_address: str, payload: List[int]
    ) -> Dict[str, str]:
        """Consume a mock message from L2 to L1."""
        params = {
            "l2_contract_address": l2_contract_address,
            "l1_contract_address": l1_contract_address,
            "payload": [hex(p) for p in payload],
        }
        return self._make_request("devnet_postmanConsumeMessageFromL2", params)

    def _make_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Make a synchronous JSON-RPC request to the devnet."""
        headers = {"Content-Type": "application/json"}
        payload = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params or {}}
        response = requests.post(self.node_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json().get("result")
