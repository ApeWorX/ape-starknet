import pytest
from ape.api.networks import LOCAL_NETWORK_NAME
from starkware.cairo.lang.vm.cairo_runner import pedersen_hash

from ape_starknet.utils import is_hex_address

from ..conftest import PASSWORD


def test_public_keys(existing_key_file_account, public_key):
    actual = existing_key_file_account.public_key
    assert actual == public_key


def test_sign_message_using_key_file_account(existing_key_file_account, password):
    assert existing_key_file_account.sign_message(5, passphrase=password)


def test_address(account):
    assert is_hex_address(account.address)


def test_sign_message_and_check_signature(account):
    data = 500
    signature = account.sign_message(data)
    data_hash = pedersen_hash(data, 0)
    result = account.check_signature(data_hash, signature)
    assert result, "Failed to validate signature"


def test_sign_message_and_check_signature_using_deployed_account(ephemeral_account):
    data = 500
    signature = ephemeral_account.sign_message(data)
    data_hash = pedersen_hash(data, 0)
    result = ephemeral_account.check_signature(data_hash, signature)
    assert result, "Failed to validate signature"


@pytest.mark.parametrize(
    "get_address",
    [
        lambda a, _: a.address,
        lambda a, e: e.encode_address(a.address),
        lambda a, _: a.public_key,
        lambda a, e: e.encode_address(a.public_key),
    ],
)
def test_access_account_by_str_address(account, account_container, ecosystem, get_address):
    address = get_address(account, ecosystem)
    assert account_container[address] == account
    assert address in account_container


def test_balance(account):
    balance = account.balance
    assert isinstance(balance, int)
    assert account.balance > 0


def test_import_with_passphrase(account_container, existing_key_file_account):
    alias = "__TEST_IMPORT_WITH_PASSPHRASE__"
    account_container.import_account(
        alias,
        LOCAL_NETWORK_NAME,
        existing_key_file_account.address,
        existing_key_file_account._get_key(PASSWORD),
        passphrase="p@55W0rd",
    )
    new_account = account_container.load(alias)
    assert new_account.address == existing_key_file_account.address


def test_transfer(account, second_account):
    initial_balance = second_account.balance
    account.transfer(second_account, 10)
    assert second_account.balance == initial_balance + 10
