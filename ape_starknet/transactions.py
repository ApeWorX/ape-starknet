from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union, get_args

from ape import networks
from ape.api.providers import BlockAPI
from ape.api.transactions import ReceiptAPI, TransactionAPI
from ape.contracts.base import ContractEvent
from ape.types.events import ContractLog, ContractLogContainer
from ape.types.signatures import TransactionSignature
from eth_pydantic_types.hex import HexBytes as EthHexBytes
from eth_utils import to_bytes, to_hex
from ethpm_types.abi import EventABI
from hexbytes import HexBytes
from pydantic import BaseModel, Field, field_validator, model_validator
from starknet_py.abi.v2 import AbiParser
from starknet_py.common import create_compiled_contract, create_sierra_compiled_contract
from starknet_py.hash.address import compute_address
from starknet_py.hash.transaction import (
    CommonTransactionV3Fields,
    TransactionHashPrefix,
    compute_declare_transaction_hash,
    compute_declare_v2_transaction_hash,
    compute_declare_v3_transaction_hash,
    compute_deploy_account_transaction_hash,
    compute_deploy_account_v3_transaction_hash,
    compute_invoke_transaction_hash,
    compute_invoke_v3_transaction_hash,
)
from starknet_py.net.client_models import (
    BlockStatus,
    DAMode,
    DeclareTransactionV0,
    DeclareTransactionV1,
    DeclareTransactionV2,
    DeclareTransactionV3,
    DeployAccountTransactionV1,
    DeployAccountTransactionV3,
    DeployTransaction,
    DeprecatedContractClass,
    Event,
    ExecutionResources,
    FeePayment,
    InvokeTransactionV0,
    InvokeTransactionV1,
    InvokeTransactionV3,
    L1DAMode,
    L1HandlerTransaction,
    L2toL1Message,
    ResourceBoundsMapping,
    ResourcePrice,
    SierraCompiledContract,
    SierraContractClass,
    TransactionExecutionStatus,
    TransactionFinalityStatus,
    TransactionStatus,
    TransactionType,
)
from starknet_py.net.models.transaction import (
    DeclareV1,
    DeclareV2,
    DeclareV3,
    DeployAccountV1,
    DeployAccountV3,
    InvokeV1,
    InvokeV3,
)
from starkware.starknet.public.abi import get_selector_from_name

from ape_starknet.basemodel import StarknetBase

ContractEventABI = Union[List[Union[EventABI, ContractEvent]], Union[EventABI, ContractEvent]]


class StarknetBlock(BlockAPI):
    status: BlockStatus
    new_root: Optional[int]
    sequencer_address: int
    l1_gas_price: ResourcePrice
    l1_data_gas_price: ResourcePrice
    l1_da_mode: L1DAMode
    starknet_version: str

    @property
    def size(self) -> int:
        return 0  # Placeholder

    def __str__(self) -> str:
        return str(self.model_dump())


class StarknetTransaction(TransactionAPI, StarknetBase, ABC, BaseModel):
    version: int = Field(..., description="Transaction version")
    is_prepared: bool = Field(False, exclude=True)
    max_fee: int = Field(0, description="Maximum fee for the transaction")

    @abstractmethod
    def calculate_hash(self, chain_id: int) -> int:
        """Calculates the transaction hash in the Starknet network."""

    def serialize_transaction(self) -> dict:  # type: ignore
        return self.model_dump()


class StarknetTransactionReceipt(ReceiptAPI, StarknetBase):
    transaction_hash: int
    status: TransactionStatus
    execution_status: TransactionExecutionStatus
    finality_status: TransactionFinalityStatus
    execution_resources: ExecutionResources
    actual_fee: FeePayment
    type: TransactionType
    events: List[Event] = Field(default_factory=list)  # type: ignore
    messages_sent: List[L2toL1Message] = Field(default_factory=list)
    block_number: int
    block_hash: Optional[int] = None
    contract_address: Optional[str] = None  # type: ignore
    message_hash: Optional[int] = None
    revert_reason: Optional[str] = None
    transaction: Union[TransactionAPI, BaseTransactionModelsT]
    txn_hash: Optional[str] = None  # type: ignore
    sender: Optional[str] = None
    gas_used: int = 0

    @model_validator(mode="before")
    @classmethod
    def compute_gas_used(cls, data: Any) -> Any:
        if isinstance(data, dict) and "actual_fee" in data and data["actual_fee"] is not None:
            data["gas_used"] = data["actual_fee"].amount
        return data

    @model_validator(mode="before")
    @classmethod
    def compute_txn_hash(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(data, dict) and data.get("txn_hash") is None and "transaction_hash" in data:
            data["txn_hash"] = to_hex(data["transaction_hash"])
        return data

    @model_validator(mode="before")
    @classmethod
    def compute_sender(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(data, dict) and data.get("sender") is None and "transaction" in data:
            data["sender"] = HexBytes(data["transaction"].sender_address).hex()
        return data

    @model_validator(mode="before")
    @classmethod
    def compute_contract_address(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(data, dict) and "contract_address" in data:
            if isinstance(data["contract_address"], int):
                data["contract_address"] = HexBytes(data["contract_address"]).hex()
        return data

    @property
    def total_fees_paid(self) -> int:
        return self.actual_fee.amount

    @property
    def ran_out_of_gas(self) -> bool:
        return False  # Placeholder implementation

    def decode_logs(
        self,
        abi: Optional[str] = None,
    ) -> ContractLogContainer:
        log_data_items: List[Dict] = []
        for log in self.events:
            log_data = {
                **log.__dict__,
                "block_hash": self.block_hash,
                "transaction_hash": self.transaction_hash,
                "block_number": self.block_number,
            }
            log_data_items.append(log_data)

        if abi is None and self.contract_address is not None:
            provider = networks.active_provider
            abi = provider.get_abi(self.contract_address)

        if abi is not None:
            try:
                abi_dict = json.loads(abi)
            except json.JSONDecodeError:
                raise ValueError("Invalid ABI string. Must be a valid JSON.")

            abi_parser = AbiParser(abi_dict)
            parsed_abi = abi_parser.parse()

            event_abis = [event for event in parsed_abi.events.values()]
            return ContractLogContainer(self.starknet.decode_logs(log_data_items, *event_abis))

        else:
            # If ABI is not provided, decode all events
            address_map = {
                x["from_address"]: self.starknet.decode_address(x["from_address"])
                for x in log_data_items
            }
            contract_types = self.chain_manager.contracts.get_multiple(address_map.values())
            # address → selector → abi
            selectors = {
                address: {get_selector_from_name(e.name): e for e in contract.events}
                for address, contract in contract_types.items()
            }

            decoded_logs: ContractLogContainer = ContractLogContainer()
            for log in log_data_items:
                contract_address = address_map[log["from_address"]]
                if contract_address not in selectors:
                    continue

                for event_key in log.get("keys", []):
                    event_abi = selectors[contract_address][event_key]
                    decoded_logs.extend(self.starknet.decode_logs([log], event_abi))
            return decoded_logs


class StarknetAccountTransaction(StarknetTransaction, ABC):
    signature: Optional[TransactionSignature] = Field(
        default=None, description="Transaction signature"
    )
    nonce: int = Field(..., description="Transaction nonce")

    @field_validator("signature", mode="before")
    @classmethod
    def set_signature(cls, value):
        if isinstance(value, list) and len(value) == 2:
            return TransactionSignature(v=0, r=to_bytes(value[1]), s=to_bytes(value[2]))
        elif isinstance(value, TransactionSignature):
            return value
        raise ValueError("Invalid signature format.")


class _StarknetDeprecatedAccountTransaction(StarknetAccountTransaction):
    pass


class _StarknetAccountTransactionV3(StarknetAccountTransaction, ABC):
    resource_bounds: ResourceBoundsMapping
    tip: int = Field(0, description="Transaction tip")
    nonce_data_availability_mode: DAMode = Field(
        DAMode.L1, description="Nonce data availability mode"
    )
    fee_data_availability_mode: DAMode = Field(DAMode.L1, description="Fee data availability mode")
    paymaster_data: List[int] = Field(default_factory=list, description="Paymaster data")

    def get_common_fields(
        self,
        tx_prefix: TransactionHashPrefix,
        address: int,
        chain_id: int,
    ) -> CommonTransactionV3Fields:
        return CommonTransactionV3Fields(
            tx_prefix=tx_prefix,
            address=address,
            chain_id=chain_id,
            version=self.version,
            nonce=self.nonce,
            resource_bounds=self.resource_bounds,
            tip=self.tip,
            paymaster_data=self.paymaster_data,
            nonce_data_availability_mode=self.nonce_data_availability_mode,
            fee_data_availability_mode=self.fee_data_availability_mode,
        )


class StarknetDeclareV3(_StarknetAccountTransactionV3):
    sender_address: int
    compiled_class_hash: int
    contract_class: SierraContractClass
    account_deployment_data: List[int] = Field(default_factory=list)
    type: TransactionType = TransactionType.DECLARE

    def calculate_hash(self, chain_id: int) -> int:
        return compute_declare_v3_transaction_hash(
            account_deployment_data=self.account_deployment_data,
            contract_class=self.contract_class,
            compiled_class_hash=self.compiled_class_hash,
            common_fields=self.get_common_fields(
                tx_prefix=TransactionHashPrefix.DECLARE,
                address=self.sender_address,
                chain_id=chain_id,
            ),
        )

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> DeclareV3:
        return DeclareV3(
            version=self.version,
            signature=self.signature,
            nonce=self.nonce,
            resource_bounds=self.resource_bounds,
            sender_address=self.sender_address,
            compiled_class_hash=self.compiled_class_hash,
            contract_class=self.contract_class,
            account_deployment_data=self.account_deployment_data,
            # type=self.type,
        )

    @classmethod
    def from_base_tx(cls, transaction: DeclareV3) -> StarknetDeclareV3:
        return cls(  # type: ignore
            version=transaction.version,
            sender_address=transaction.sender_address,
            compiled_class_hash=transaction.compiled_class_hash,
            contract_class=transaction.contract_class,
            nonce=transaction.nonce,
            signature=transaction.signature,
            resource_bounds=transaction.resource_bounds,
            tip=transaction.tip,
            nonce_data_availability_mode=transaction.nonce_data_availability_mode,
            fee_data_availability_mode=transaction.fee_data_availability_mode,
            paymaster_data=transaction.paymaster_data,
            account_deployment_data=transaction.account_deployment_data,
        )

    @classmethod
    def prepare(
        cls,
        sender_address,
        compiled_contract: Union[str, HexBytes, SierraContractClass],
        compiled_class_hash: int,
        *,
        nonce: Optional[int] = None,
    ) -> "StarknetDeclareV3":
        # Get the current active provider
        provider = networks.active_provider

        if nonce is None:
            nonce = provider.get_nonce(sender_address)  # type: ignore

        if isinstance(compiled_contract, HexBytes):
            contract_class = create_sierra_compiled_contract(compiled_contract=compiled_contract)
        elif isinstance(compiled_contract, str):
            contract_class = create_sierra_compiled_contract(
                compiled_contract=HexBytes(compiled_contract)
            )
        elif isinstance(compiled_contract, SierraContractClass):
            contract_class = compiled_contract

        elif isinstance(compiled_contract, SierraCompiledContract):
            contract_class = compiled_contract.convert_to_sierra_contract_class()

        else:
            raise ValueError("compiled_contract must be a HexBytes, str, or SierraContractClass.")

        declare_tx = cls(  # type: ignore
            sender_address=sender_address,
            compiled_class_hash=compiled_class_hash,
            contract_class=contract_class,
            nonce=nonce,
            resource_bounds=ResourceBoundsMapping.init_with_zeros(),
            version=3,
        )
        return declare_tx


class StarknetDeclareV2(_StarknetDeprecatedAccountTransaction):
    contract_class: SierraContractClass
    compiled_class_hash: int
    sender_address: int
    type: TransactionType = TransactionType.DECLARE

    def calculate_hash(self, chain_id: int) -> int:
        return compute_declare_v2_transaction_hash(
            contract_class=self.contract_class,
            compiled_class_hash=self.compiled_class_hash,
            chain_id=chain_id,
            sender_address=self.sender_address,
            max_fee=self.max_fee,
            version=self.version,
            nonce=self.nonce,
        )

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> DeclareV2:
        return DeclareV2(
            version=self.version,
            signature=self.signature,
            nonce=self.nonce,
            max_fee=self.max_fee,
            contract_class=self.contract_class,
            compiled_class_hash=self.compiled_class_hash,
            sender_address=self.sender_address,
            # type=self.type,
        )

    @classmethod
    def prepare(
        cls,
        sender_address,
        compiled_contract: Union[str, HexBytes, SierraContractClass],
        compiled_class_hash: int,
        *,
        nonce: Optional[int] = None,
    ) -> "StarknetDeclareV2":
        provider = networks.active_provider

        if nonce is None:
            nonce = provider.get_nonce(sender_address)  # type: ignore

        if isinstance(compiled_contract, HexBytes):
            contract_class = create_sierra_compiled_contract(compiled_contract=compiled_contract)
        elif isinstance(compiled_contract, str):
            contract_class = create_sierra_compiled_contract(
                compiled_contract=HexBytes(compiled_contract)
            )
        elif isinstance(compiled_contract, SierraContractClass):
            contract_class = compiled_contract
        elif isinstance(compiled_contract, SierraCompiledContract):
            contract_class = compiled_contract.convert_to_sierra_contract_class()

        else:
            raise ValueError("compiled_contract must be a HexBytes, str, or SierraContractClass.")

        declare_tx = cls(  # type: ignore
            sender_address=sender_address,
            compiled_class_hash=compiled_class_hash,
            contract_class=contract_class,
            nonce=nonce,
            max_fee=0,
            version=2,
        )

        return declare_tx

    @classmethod
    def from_base_tx(cls, transaction: DeclareV2) -> "StarknetDeclareV2":
        return cls(  # type: ignore
            version=transaction.version,
            contract_class=transaction.contract_class,
            compiled_class_hash=transaction.compiled_class_hash,
            sender_address=transaction.sender_address,
            nonce=transaction.nonce,
            max_fee=transaction.max_fee,
            signature=transaction.signature,
        )


class StarknetDeclareV1(_StarknetDeprecatedAccountTransaction):
    contract_class: DeprecatedContractClass
    sender_address: int
    type: TransactionType = TransactionType.DECLARE

    def calculate_hash(self, chain_id: int) -> int:
        return compute_declare_transaction_hash(
            contract_class=self.contract_class,
            chain_id=chain_id,
            sender_address=self.sender_address,
            max_fee=self.max_fee,
            version=self.version,
            nonce=self.nonce,
        )

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> DeclareV1:
        return DeclareV1(
            version=self.version,
            signature=self.signature,
            nonce=self.nonce,
            max_fee=self.max_fee,
            contract_class=self.contract_class,
            sender_address=self.sender_address,
            # type=self.type,
        )

    @classmethod
    def prepare(
        cls,
        sender_address,
        compiled_contract: Union[str, HexBytes, DeprecatedContractClass],
        *,
        nonce: Optional[int] = None,
    ) -> "StarknetDeclareV1":
        provider = networks.active_provider

        if nonce is None:
            nonce = provider.get_nonce(sender_address)  # type: ignore

        if isinstance(compiled_contract, HexBytes):
            contract_class = create_compiled_contract(compiled_contract=compiled_contract)
        elif isinstance(compiled_contract, str):
            contract_class = create_compiled_contract(compiled_contract=HexBytes(compiled_contract))
        elif isinstance(compiled_contract, DeprecatedContractClass):
            contract_class = compiled_contract
        elif isinstance(compiled_contract, SierraCompiledContract):
            contract_class = compiled_contract.convert_to_sierra_contract_class()

        else:
            raise ValueError("compiled_contract must be a HexBytes, str, or ContractClass.")

        declare_tx = cls(  # type: ignore
            sender_address=sender_address,
            contract_class=contract_class,
            nonce=nonce,
            max_fee=0,
            version=1,
        )
        return declare_tx

    @classmethod
    def from_base_tx(cls, transaction: DeclareV1) -> "StarknetDeclareV1":
        return cls(  # type: ignore
            version=transaction.version,
            contract_class=transaction.contract_class,
            sender_address=transaction.sender_address,
            nonce=transaction.nonce,
            max_fee=transaction.max_fee,
            signature=transaction.signature,
        )


class StarknetDeployAccountV3(_StarknetAccountTransactionV3):
    class_hash: int
    contract_address_salt: int
    constructor_calldata: List[int]
    type: TransactionType = TransactionType.DEPLOY_ACCOUNT

    def calculate_hash(self, chain_id: int) -> int:
        contract_address = compute_address(
            salt=self.contract_address_salt,
            class_hash=self.class_hash,
            constructor_calldata=self.constructor_calldata,
            deployer_address=0,
        )
        return compute_deploy_account_v3_transaction_hash(
            class_hash=self.class_hash,
            constructor_calldata=self.constructor_calldata,
            contract_address_salt=self.contract_address_salt,
            common_fields=self.get_common_fields(
                tx_prefix=TransactionHashPrefix.DEPLOY_ACCOUNT,
                address=contract_address,
                chain_id=chain_id,
            ),
        )

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> DeployAccountV3:
        return DeployAccountV3(
            version=self.version,
            signature=self.signature,
            nonce=self.nonce,
            resource_bounds=self.resource_bounds,
            class_hash=self.class_hash,
            contract_address_salt=self.contract_address_salt,
            constructor_calldata=self.constructor_calldata,
            # type=self.type,
        )

    @classmethod
    def prepare(
        cls,
        class_hash: int,
        contract_address_salt: int,
        constructor_calldata: List[int],
        *,
        nonce: int = 0,
    ) -> "StarknetDeployAccountV3":
        deploy_account_tx = cls(  # type: ignore
            class_hash=class_hash,
            contract_address_salt=contract_address_salt,
            constructor_calldata=constructor_calldata,
            nonce=nonce,
            resource_bounds=ResourceBoundsMapping.init_with_zeros(),
            version=3,
        )

        return deploy_account_tx

    @classmethod
    def from_base_tx(cls, transaction: DeployAccountV3) -> "StarknetDeployAccountV3":
        return cls(  # type: ignore
            version=transaction.version,
            class_hash=transaction.class_hash,
            contract_address_salt=transaction.contract_address_salt,
            constructor_calldata=transaction.constructor_calldata,
            nonce=transaction.nonce,
            signature=transaction.signature,
            resource_bounds=transaction.resource_bounds,
            tip=transaction.tip,
            nonce_data_availability_mode=transaction.nonce_data_availability_mode,
            fee_data_availability_mode=transaction.fee_data_availability_mode,
            paymaster_data=transaction.paymaster_data,
        )


class StarknetDeployAccountV1(_StarknetDeprecatedAccountTransaction):
    class_hash: int
    contract_address_salt: int
    constructor_calldata: List[int]
    type: TransactionType = TransactionType.DEPLOY_ACCOUNT

    def calculate_hash(self, chain_id: int) -> int:
        contract_address = compute_address(
            salt=self.contract_address_salt,
            class_hash=self.class_hash,
            constructor_calldata=self.constructor_calldata,
            deployer_address=0,
        )
        return compute_deploy_account_transaction_hash(
            version=self.version,
            contract_address=contract_address,
            class_hash=self.class_hash,
            constructor_calldata=self.constructor_calldata,
            max_fee=self.max_fee,
            nonce=self.nonce,
            salt=self.contract_address_salt,
            chain_id=chain_id,
        )

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> DeployAccountV1:
        return DeployAccountV1(
            version=self.version,
            signature=self.signature,
            nonce=self.nonce,
            max_fee=self.max_fee,
            class_hash=self.class_hash,
            contract_address_salt=self.contract_address_salt,
            constructor_calldata=self.constructor_calldata,
            # type=self.type,
        )

    @classmethod
    def prepare(
        cls,
        class_hash: int,
        contract_address_salt: int,
        constructor_calldata: List[int],
        *,
        nonce: int = 0,
    ) -> "StarknetDeployAccountV1":
        deploy_account_tx = cls(  # type: ignore
            class_hash=class_hash,
            contract_address_salt=contract_address_salt,
            constructor_calldata=constructor_calldata,
            nonce=nonce,
            max_fee=0,
            version=1,
        )
        return deploy_account_tx

    @classmethod
    def from_base_tx(cls, transaction: DeployAccountV1) -> "StarknetDeployAccountV1":
        return cls(  # type: ignore
            version=transaction.version,
            class_hash=transaction.class_hash,
            contract_address_salt=transaction.contract_address_salt,
            constructor_calldata=transaction.constructor_calldata,
            nonce=transaction.nonce,
            max_fee=transaction.max_fee,
            signature=transaction.signature,
        )


class StarknetInvokeV3(_StarknetAccountTransactionV3):
    calldata: List[int]
    sender_address: int
    account_deployment_data: List[int] = Field(default_factory=list)
    type: TransactionType = TransactionType.INVOKE

    def calculate_hash(self, chain_id: int) -> int:
        return compute_invoke_v3_transaction_hash(
            account_deployment_data=self.account_deployment_data,
            calldata=self.calldata,
            common_fields=self.get_common_fields(
                tx_prefix=TransactionHashPrefix.INVOKE,
                address=self.sender_address,
                chain_id=chain_id,
            ),
        )

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> InvokeV3:
        return InvokeV3(
            version=self.version,
            signature=self.signature,
            nonce=self.nonce,
            resource_bounds=self.resource_bounds,
            calldata=self.calldata,
            sender_address=self.sender_address,
            account_deployment_data=self.account_deployment_data,
            # type=self.type,
        )

    @classmethod
    def prepare(
        cls,
        sender_address: int,
        calldata: List[int],
        *,
        nonce: Optional[int] = None,
    ) -> "StarknetInvokeV3":
        provider = networks.active_provider

        if nonce is None:
            nonce = provider.get_nonce(sender_address)  # type: ignore

        invoke_tx = cls(  # type: ignore
            sender_address=sender_address,
            calldata=calldata,
            nonce=nonce,
            resource_bounds=ResourceBoundsMapping.init_with_zeros(),
            version=3,
        )
        return invoke_tx

    @classmethod
    def from_base_tx(cls, transaction: InvokeV3) -> "StarknetInvokeV3":
        return cls(  # type: ignore
            version=transaction.version,
            sender_address=transaction.sender_address,
            calldata=transaction.calldata,
            nonce=transaction.nonce,
            signature=transaction.signature,
            resource_bounds=transaction.resource_bounds,
            tip=transaction.tip,
            nonce_data_availability_mode=transaction.nonce_data_availability_mode,
            fee_data_availability_mode=transaction.fee_data_availability_mode,
            paymaster_data=transaction.paymaster_data,
            account_deployment_data=transaction.account_deployment_data,
        )


class StarknetInvokeV1(_StarknetDeprecatedAccountTransaction):
    sender_address: int
    calldata: List[int]
    type: TransactionType = TransactionType.INVOKE

    def calculate_hash(self, chain_id: int) -> int:
        return compute_invoke_transaction_hash(
            version=self.version,
            sender_address=self.sender_address,
            calldata=self.calldata,
            max_fee=self.max_fee,
            chain_id=chain_id,
            nonce=self.nonce,
        )

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> InvokeV1:
        return InvokeV1(
            version=self.version,
            signature=self.signature,
            nonce=self.nonce,
            max_fee=self.max_fee,
            sender_address=self.sender_address,
            calldata=self.calldata,
            # type=self.type,
        )

    @classmethod
    def prepare(
        cls, sender_address: int, calldata: List[int], *, nonce: Optional[int] = None
    ) -> "StarknetInvokeV1":
        provider = networks.active_provider

        if nonce is None:
            nonce = provider.get_nonce(sender_address)  # type: ignore

        invoke_tx = cls(  # type: ignore
            sender_address=sender_address,
            calldata=calldata,
            nonce=nonce,
            max_fee=0,
            version=1,
        )
        return invoke_tx

    @classmethod
    def from_base_tx(cls, transaction: InvokeV1) -> "StarknetInvokeV1":
        return cls(  # type: ignore
            version=transaction.version,
            sender_address=transaction.sender_address,
            calldata=transaction.calldata,
            nonce=transaction.nonce,
            max_fee=transaction.max_fee,
            signature=transaction.signature,
        )


class StarknetL1HandlerTransaction(StarknetTransaction):
    contract_address: int
    calldata: List[int]
    entry_point_selector: int
    nonce: int
    type: TransactionType = TransactionType.L1_HANDLER

    def calculate_hash(self, chain_id: int) -> int:
        raise NotImplementedError("Hash calculation for L1 handler transactions is not implemented")

    @property
    def txn_hash(self) -> EthHexBytes:
        return EthHexBytes(to_hex(self.calculate_hash(self.provider.chain_id)))

    def to_base_tx(self) -> L1HandlerTransaction:
        return L1HandlerTransaction(
            version=self.version,
            signature=[],  # L1 handler transactions don't have signatures
            nonce=self.nonce,
            contract_address=self.contract_address,
            entry_point_selector=self.entry_point_selector,
            calldata=self.calldata,
        )

    @classmethod
    def from_base_tx(cls, transaction: L1HandlerTransaction) -> "StarknetL1HandlerTransaction":
        return cls(
            version=transaction.version,
            contract_address=transaction.contract_address,
            calldata=transaction.calldata,
            entry_point_selector=transaction.entry_point_selector,
            nonce=transaction.nonce,
        )

    @classmethod
    def prepare(
        cls,
        contract_address: int,
        entry_point_selector: int,
        calldata: List[int],
        *,
        nonce: Optional[int] = None,
    ) -> "StarknetL1HandlerTransaction":
        provider = networks.active_provider

        if nonce is None:
            nonce = provider.get_nonce(contract_address)  # type: ignore

        l1_handler_tx = cls(  # type: ignore
            contract_address=contract_address,
            entry_point_selector=entry_point_selector,
            calldata=calldata,
            nonce=nonce,
            version=0,  # L1 handler transactions typically use version 0
        )
        return l1_handler_tx


StarknetDeclare = Union[StarknetDeclareV1, StarknetDeclareV2, StarknetDeclareV3]
StarknetDeployAccount = Union[StarknetDeployAccountV1, StarknetDeployAccountV3]
StarknetInvoke = Union[StarknetInvokeV1, StarknetInvokeV3]

BaseDeclare = Union[DeclareV1, DeclareV2, DeclareV3]
BaseDeployAccount = Union[DeployAccountV1, DeployAccountV3]
BaseInvoke = Union[InvokeV1, InvokeV3]

Declare = Union[StarknetDeclare, BaseDeclare]
DeployAccount = Union[StarknetDeployAccount, BaseDeployAccount]
Invoke = Union[StarknetInvoke, BaseInvoke]

DepreciatedTxs = Union[
    StarknetDeclareV1, StarknetDeclareV2, StarknetInvokeV1, StarknetDeployAccountV1
]
NewTxs = Union[
    StarknetDeclareV3, StarknetDeployAccountV3, StarknetInvokeV3, StarknetL1HandlerTransaction
]

StarknetTransactionT = Union[
    StarknetDeclareV1,
    StarknetDeclareV2,
    StarknetDeclareV3,
    StarknetDeployAccountV1,
    StarknetDeployAccountV3,
    StarknetInvokeV1,
    StarknetInvokeV3,
    StarknetL1HandlerTransaction,
]
BaseTransactionT = Union[
    DeclareV1,
    DeclareV2,
    DeclareV3,
    DeployAccountV1,
    DeployAccountV3,
    InvokeV1,
    InvokeV3,
    L1HandlerTransaction,
]

# Mapping of starknet_py types to our custom types
transaction_type_mapping = {
    DeclareV1: StarknetDeclareV1,
    DeclareV2: StarknetDeclareV2,
    DeclareV3: StarknetDeclareV3,
    DeployAccountV1: StarknetDeployAccountV1,
    DeployAccountV3: StarknetDeployAccountV3,
    InvokeV1: StarknetInvokeV1,
    InvokeV3: StarknetInvokeV3,
    L1HandlerTransaction: StarknetL1HandlerTransaction,
}


BaseTransactionModelsT = Union[
    InvokeTransactionV0,
    InvokeTransactionV1,
    InvokeTransactionV3,
    DeclareTransactionV0,
    DeclareTransactionV1,
    DeclareTransactionV2,
    DeclareTransactionV3,
    DeployTransaction,
    DeployAccountTransactionV1,
    DeployAccountTransactionV3,
]


def convert_to_base_tx(txn):
    if is_base_model_tx(txn):
        return txn

    if is_starknet_tx(txn):
        txn = txn.to_base_tx()
        return txn

    elif is_base_tx(txn):
        return txn

    raise ValueError(f"Invalid transaction type: {type(txn)}")


def convert_to_starknet_tx(txn):
    if is_base_model_tx(txn):
        return txn

    if is_starknet_tx(txn):
        return txn

    for starknet_py_type, custom_type in transaction_type_mapping.items():
        if isinstance(txn, starknet_py_type):
            txn = custom_type.from_base_tx(txn)

            return txn

    raise ValueError(f"Invalid transaction type: {type(txn)}")


def is_base_model_tx(txn):
    return isinstance(txn, get_args(BaseTransactionModelsT))


def is_starknet_tx(txn):
    return isinstance(txn, get_args(StarknetTransactionT))


def is_base_tx(txn):
    return isinstance(txn, get_args(BaseTransactionT))


def is_declare_tx(txn):
    return isinstance(txn, get_args(Declare))


def is_deploy_account_tx(txn):
    return isinstance(txn, get_args(DeployAccount))


def is_invoke_tx(txn):
    return isinstance(txn, get_args(Invoke))
