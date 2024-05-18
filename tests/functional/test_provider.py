import pytest
from ape.api.providers import BlockAPI, TransactionAPI, ReceiptAPI

from ape.exceptions import ContractLogicError
from ape.types.address import AddressType
import logging
from ape_starknet.provider import StarknetProvider

from ape_starknet.utils import to_int, to_hex, to_checksum_address
from ape_starknet.constants import MAX_FEE

import json
import pytest
from ape.exceptions import ContractLogicError
from ape_starknet.provider import StarknetProvider
from ape_starknet.utils import to_int, to_hex, to_checksum_address
from ape_starknet.constants import MAX_FEE
from starknet_py.net.client_models import Call
import logging

logging.getLogger(__name__)

from starkware.starknet.public.abi import get_selector_from_name
from ape_starknet.transactions import (
    StarknetAccountTransaction,
    StarknetInvokeV1,
    StarknetInvokeV3,
    ResourceBoundsMapping,
)
from starknet_py.net.client_models import (
    ResourceBounds,
    TransactionType,
    TransactionExecutionStatus,
)
from starknet_py.net.models.transaction import InvokeV3


def test_block_num(provider):
    block_num = provider.get_block("latest")
    assert block_num is not None


def test_chain_id(provider):
    assert provider.chain_id is not None


def test_uri(provider):
    assert isinstance(provider.uri, str)
    assert provider.uri != ""


def test_get_l1_gas_price(provider: StarknetProvider):
    gas_price = provider.l1_gas_price
    assert gas_price is not None


def test_create_transaction(provider, account, second_account):
    value = 10
    invoke_tx = provider.get_fee_token_contract(account).transfer(
        to_int(account), value, auto_estimate=True
    )
    receipt = provider.get_receipt(invoke_tx.hash)
    assert receipt is not None


def test_get_transactions_by_block(provider):
    transactions = provider.get_transactions_by_block(76691)
    assert transactions is not None


def test_get_block_by_number(provider):
    block = provider.get_block("latest")
    assert isinstance(block, BlockAPI)


def test_get_block_by_hash(provider):
    latest_block = provider.get_block("latest")
    block = provider.get_block(latest_block.hash)
    assert isinstance(block, BlockAPI)
    assert block.hash == latest_block.hash


def test_get_latest_block(provider):
    block = provider.get_block("latest")
    assert isinstance(block, BlockAPI)
    assert block.number is not None


def test_get_pending_block(provider):
    block = provider.get_block("pending")
    assert isinstance(block, BlockAPI)
    assert block.number is None or block.number > provider.get_block("latest").number


def test_get_nonce_increases_after_transaction(provider, account, second_account):
    initial_nonce = provider.get_nonce(account.address)

    # Perform a transaction (this is a simplified example, adjust based on your setup)
    receipt = account.transfer(second_account, 1000)
    assert receipt is not None

    new_nonce = provider.get_nonce(account.address)
    assert new_nonce == initial_nonce + 1


def test_l1_gas_price_positive(provider):
    gas_price = provider.l1_gas_price.price_in_wei
    assert gas_price > 0


def test_estimate_gas_cost(provider, account, second_account):
    # Use the second account as the recipient for the transfer
    recipient = second_account.address

    # Prepare a transfer transaction
    value = 1000  # Transfer amount (adjust as needed)
    transfer_tx = account.transfer(account=recipient, value=value)

    # Extract relevant information from the transfer_tx
    calldata = transfer_tx.transaction.calldata

    # Create a StarknetInvokeV3 transaction
    starknet_tx = StarknetInvokeV3.prepare(
        sender_address=to_int(account.address),
        calldata=calldata,
        nonce=account.nonce,
    )

    prepared_tx = account.prepare_transaction(starknet_tx)
    estimated_fee = provider.estimate_gas_cost(prepared_tx)
    assert estimated_fee is not None

    assert (
        transfer_tx.execution_status == TransactionExecutionStatus.SUCCEEDED
    ), "Transaction execution did not succeed"
    assert len(transfer_tx.events) > 0, "No events were emitted"


def test_get_balance(provider, account):
    balance = provider.get_balance(account.address)
    assert balance >= 0
