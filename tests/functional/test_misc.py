import pytest

from ape_starknet.accounts import StarknetDevnetAccount
from ape_starknet.constants import (
    OPENZEPPELIN_ACCOUNTS,
)
from ape_starknet.provider import StarknetProvider


def test_account_balance(account: StarknetDevnetAccount, provider: StarknetProvider):
    """Test account balance retrieval"""
    balance = provider.get_balance(account.address)
    assert balance >= 0
    assert isinstance(balance, int)
    assert balance == account.balance  # Check both ways of getting balance match


def test_nonce_handling(account: StarknetDevnetAccount, second_account: StarknetDevnetAccount):
    """Test account nonce functionality"""
    initial_nonce = account.nonce

    # Make a transaction
    receipt = account.transfer(second_account, 1000)

    assert account.nonce == initial_nonce + 1
    assert not receipt.failed


def test_account_signing(account: StarknetDevnetAccount):
    """Test message and transaction signing"""
    msg = [1234]  # Simple test message
    signature = account.sign_message(msg)

    assert signature is not None
    assert len(signature) == 2  # StarkNet signatures have r,s components
    assert account.check_signature(msg, signature)


def test_account_key_validation(account: StarknetDevnetAccount):
    """Test keypair functionality"""
    assert hasattr(account, "key_pair")
    assert account.key_pair.public_key == int(account.public_key, 16)


def test_transfer_insufficient_balance(
    account: StarknetDevnetAccount, second_account: StarknetDevnetAccount
):
    """Test transferring more than the account balance"""
    initial_balance = account.balance
    transfer_amount = initial_balance + 1000  # Amount greater than balance

    with pytest.raises(Exception):
        account.transfer(second_account, transfer_amount)


def test_import_account(account_container):
    """Test importing an existing Starknet account"""
    private_key = "0x123456789"  # Example private key
    account = account_container.import_account(
        alias="imported_account",
        class_hash=OPENZEPPELIN_ACCOUNTS[0]["class_hash"],
        private_key=private_key,
    )
    assert account.private_key == private_key
    assert account.address is not None


def test_account_nonce_management(account):
    """Test account nonce handling"""
    initial_nonce = account.nonce

    # Perform a transaction
    receipt = account.transfer(
        account="0x123",  # Example address
        value=100,
    )

    new_nonce = account.nonce
    assert new_nonce == initial_nonce + 1


# Test Provider Functionality
def test_provider_connection(provider):
    """Test provider connection and basic functionality"""
    assert provider.is_connected
    assert provider.chain_id is not None
    assert provider.uri is not None


def test_provider_block_queries(provider):
    """Test block-related provider functions"""
    latest_block = provider.get_block("latest")
    assert latest_block.number is not None

    specific_block = provider.get_block(latest_block.number)
    assert specific_block.hash == latest_block.hash

    pending_block = provider.get_block("pending")
    assert pending_block is not None
