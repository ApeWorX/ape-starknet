from __future__ import annotations

from typing import Any, Iterator, Union

from ape.api.networks import EcosystemAPI
from ape.api.transactions import ReceiptAPI, TransactionAPI
from ape.contracts.base import ContractContainer
from ape.types.address import AddressType, RawAddress
from ape.types.events import ContractLog
from ape.utils.misc import raises_not_implemented
from ethpm_types import ContractType
from ethpm_types.abi import ConstructorABI, EventABI, MethodABI
from hexbytes import HexBytes
from starknet_py.cairo.felt import decode_shortstring
from starknet_py.common import (
    create_casm_class,
    create_sierra_compiled_contract,
)
from starknet_py.hash.casm_class_hash import compute_casm_class_hash
from starknet_py.net.client_models import (
    BlockStatus,
    CasmClass,
    Event,
    PendingStarknetBlock,
    SierraContractClass,
    TransactionReceipt,
)
from starknet_py.net.client_models import StarknetBlock as StarknetClientBlock
from starknet_py.net.models.address import parse_address
from starknet_py.serialization import serializer_for_event
from starkware.starknet.public.abi import get_selector_from_name

from ape_starknet.constants import STARKNET_FEE_TOKEN_SYMBOL
from ape_starknet.transactions import (
    StarknetBlock,
    StarknetDeclareV3,
    StarknetTransactionReceipt,
)
from ape_starknet.utils import to_checksum_address, to_hex


class Starknet(EcosystemAPI):
    fee_token_symbol: str = STARKNET_FEE_TOKEN_SYMBOL
    """The token symbol for the currency that pays for fees, such as ETH."""

    fee_token_decimals: int = 18
    """The number of the decimals the fee token has."""

    @classmethod
    def decode_address(cls, raw_address: RawAddress) -> AddressType:
        """
        Convert a raw address to the ecosystem's native address type.

        Args:
            raw_address (:class:`~ape.types.address.RawAddress`): The address to
              convert.

        Returns:
            :class:`~ape.types.address.AddressType`
        """
        return to_checksum_address(raw_address)

    @classmethod
    def encode_address(cls, address: AddressType) -> RawAddress:
        """
        Convert the ecosystem's native address type to a raw integer or str address.

        Args:
            address (:class:`~ape.types.address.AddressType`): The address to convert.

        Returns:
            :class:`~ape.types.address.RawAddress`
        """
        return parse_address(address)

    def create_sierra_class(self, sierra_code) -> SierraContractClass:
        """
        Create a SierraContractClass instance from the given Sierra code.

        Args:
            sierra_code (str): The Sierra code to compile.

        Returns:
            SierraContractClass
        """
        if isinstance(sierra_code, str):
            sierra_code = HexBytes(sierra_code)

        elif isinstance(sierra_code, HexBytes):
            sierra_code = sierra_code

        else:
            raise ValueError(f"Invalid sierra code: {sierra_code}")

        sierra_contract_class = create_sierra_compiled_contract(compiled_contract=sierra_code)
        return sierra_contract_class

    def create_casm_class(self, casm_code) -> CasmClass:
        if isinstance(casm_code, str):
            casm_code = HexBytes(casm_code)

        elif isinstance(casm_code, HexBytes):
            casm_code = casm_code

        else:
            raise ValueError(f"Invalid sierra code: {casm_code}")

        casm_class = create_casm_class(casm_code)
        return casm_class

    def encode_contract_blueprint(  # type: ignore[empty-body]
        self, contract: Union[ContractContainer, ContractType], sender_address, *args, **kwargs
    ) -> "TransactionAPI":
        """
        Encode a unique type of transaction that allows contracts to be created
        from other contracts, such as
        `EIP-5202 <https://eips.ethereum.org/EIPS/eip-5202>`__
        or Starknet's ``Declare`` transaction type.

        Args:
            contract_type (``ContractType``): The type of contract to create a blueprint for.
              This is the type of contract that will get created by factory contracts.
            *args (Any): Calldata, if applicable.
            **kwargs (Any): Transaction specifications, such as ``value``.

        Returns:
            :class:`~ape.ape.transactions.TransactionAPI`
        """
        contract_type = (
            contract.contract_type if isinstance(contract, ContractContainer) else contract
        )
        sierra_code = (
            (contract_type.deployment_bytecode.bytecode or 0)
            if contract_type.deployment_bytecode
            else 0
        )
        casm_code = (
            (contract_type.runtime_bytecode.bytecode or 0) if contract_type.runtime_bytecode else 0
        )

        sierra_contract_class = self.create_sierra_class(sierra_code)
        casm_class = self.create_casm_class(casm_code)
        casm_class_hash = compute_casm_class_hash(casm_class)

        declare_transaction = StarknetDeclareV3.prepare(
            sender_address=sender_address,
            compiled_contract=sierra_contract_class,
            compiled_class_hash=casm_class_hash,
        )

        return declare_transaction

    def decode_receipt(self, receipt: dict) -> ReceiptAPI:
        """
        Convert data to :class:`~ape.api.transactions.ReceiptAPI`.

        Args:
            data (Dict): A dictionary of Receipt properties.

        Returns:
            :class:`~ape.api.transactions.ReceiptAPI`
        """
        base_dict = receipt.__dict__
        if isinstance(receipt, TransactionReceipt):
            txn = self.provider.get_transaction(base_dict["transaction_hash"])
            base_dict["transaction"] = txn

        status = self.provider.get_transaction_status(base_dict["transaction_hash"])
        base_dict["status"] = status.finality_status

        if base_dict.get("contract_address", None) is not None:
            base_dict["contract_address"] = to_hex(base_dict["contract_address"])

        else:
            if hasattr(base_dict["transaction"], "contract_address"):
                base_dict["contract_address"] = to_hex(base_dict["transaction"].contract_address)

        base_dict["sender"] = HexBytes(base_dict["transaction"].sender_address).hex()[
            :42
        ]  # just to pass as addresstype for now. fails if not 42
        base_dict["sender_hex"] = HexBytes(base_dict["transaction"].sender_address).hex()

        transaction_receipt = StarknetTransactionReceipt(**base_dict)

        return transaction_receipt

    def decode_block(self, block: StarknetClientBlock) -> StarknetBlock:
        """
        Decode data to a BlockAPI.

        Args:
            block (StarknetClientBlock): A StarknetClientBlock to decode.

        Returns:
            BlockAPI
        """

        if isinstance(block, PendingStarknetBlock):
            block.status = BlockStatus.PENDING
            block.new_root = None

        starknet_block = StarknetBlock(
            num_transactions=len(block.transactions),
            hash=block.block_hash if hasattr(block, "block_hash") else None,
            number=block.block_number if hasattr(block, "block_number") else None,
            parentHash=block.parent_hash,
            timestamp=block.timestamp,
            status=block.status if hasattr(block, "status") else None,
            new_root=block.new_root if hasattr(block, "new_root") else None,
            sequencer_address=block.sequencer_address,
            l1_gas_price=block.l1_gas_price,
            l1_data_gas_price=block.l1_data_gas_price,
            l1_da_mode=block.l1_da_mode,
            starknet_version=block.starknet_version,
        )
        return starknet_block

    @raises_not_implemented
    def encode_deployment(
        self, deployment_bytecode: HexBytes, abi: ConstructorABI, *args, **kwargs
    ):
        """
        Create a deployment transaction in the given ecosystem.
        This may require connecting to other networks.

        Args:
            deployment_bytecode (HexBytes): The bytecode to deploy.
            abi (ConstructorABI): The constructor interface of the contract.
            *args (Any): Constructor arguments.
            **kwargs (Any): Transaction arguments.

        Returns:
            class:`~ape.api.transactions.TransactionAPI`
        """

    @raises_not_implemented
    def encode_transaction(self, address: AddressType, abi: MethodABI, *args, **kwargs):
        """
        Encode a transaction object from a contract function's ABI and call arguments.
        Additionally, update the transaction arguments with the overrides in ``kwargs``.

        Args:
            address (:class:`~ape.types.address.AddressType`): The address of the contract.
            abi (``MethodABI``): The function to call on the contract.
            *args (Any): Function arguments.
            **kwargs (Any): Transaction arguments.

        Returns:
            class:`~ape.api.transactions.TransactionAPI`
        """

    def decode_logs(self, logs, *events: EventABI) -> Iterator["ContractLog"]:

        events_by_selector = {get_selector_from_name(e.name): e for e in events}
        log_map = {s: [log for log in logs if s in log["keys"]] for s in events_by_selector}

        def from_uint(low: int, high: int) -> int:
            return low + (high << 128)

        for index, (selector, event_logs) in enumerate(log_map.items()):
            event = events_by_selector[selector]
            if not event_logs:
                continue

            event_serializer = serializer_for_event(event)

            for log in event_logs:
                starknet_event = Event(
                    from_address=int(log["from_address"]), keys=log["keys"], data=log["data"]
                )

                decoded_data = event_serializer.deserialize(starknet_event)

                # Handle Uint256 and attempt to decode shortstrings
                for key, value in decoded_data.items():
                    if isinstance(value, tuple) and len(value) == 2:
                        # This is likely a Uint256
                        decoded_data[key] = from_uint(value[0], value[1])
                    elif isinstance(value, int):
                        try:
                            decoded_data[key] = decode_shortstring(value)
                        except ValueError:
                            # If it's not a valid shortstring, keep the original value
                            pass

                yield ContractLog(
                    block_hash=log["block_hash"],
                    block_number=log["block_number"],
                    contract_address=self.decode_address(log["from_address"]),
                    event_arguments=decoded_data,
                    event_name=event.name,
                    log_index=index,
                    transaction_hash=log["transaction_hash"],
                    transaction_index=log.get("transaction_index", 0),
                )

    @raises_not_implemented
    def decode_primitive_value(
        self, value: Any, output_type: Union[str, tuple, list]
    ) -> Union[str, HexBytes, tuple]:
        """
        Decode a primitive value-type given its ABI type as a ``str``
        and the value itself. This method is a hook for converting
        addresses, HexBytes, or other primitive data-types into
        friendlier Python equivalents.

        Args:
            value (Any): The value to decode.
            output_type (Union[str, tuple, list]): The value type.

        Returns:
            Union[str, HexBytes, tuple]
        """
        pass

    @raises_not_implemented
    def create_transaction(self, **kwargs):
        """
        Create a transaction using key-value arguments.

        Args:
            **kwargs: Everything the transaction needs initialize.

        Returns:
            class:`~ape.api.transactions.TransactionAPI`
        """
        pass

    @raises_not_implemented
    def decode_calldata(self, abi: Union[ConstructorABI, MethodABI], calldata: bytes):
        """
        Decode method calldata.

        Args:
            abi (Union[ConstructorABI, MethodABI]): The method called.
            calldata (bytes): The raw calldata bytes.

        Returns:
            Dict: A mapping of input names to decoded values.
            If an input is anonymous, it should use the stringified
            index of the input as the key.
        """
        pass

    def encode_calldata(self, abi: Union[ConstructorABI, MethodABI], *args: Any):
        """
        Encode method calldata.

        Args:
            abi (Union[ConstructorABI, MethodABI]): The ABI of the method called.
            *args (Any): The arguments given to the method.

        Returns:
            HexBytes: The encoded calldata of the arguments to the given method.
        """
        pass

    @raises_not_implemented
    def decode_returndata(self, abi: MethodABI, raw_data: bytes) -> Any:
        """
        Get the result of a contract call.

        Arg:
            abi (MethodABI): The method called.
            raw_data (bytes): Raw returned data.

        Returns:
            Any: All of the values returned from the contract function.
        """
        pass
