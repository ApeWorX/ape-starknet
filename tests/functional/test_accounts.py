from ape_starknet.accounts import StarknetDevnetAccount
from ape_starknet.provider import StarknetProvider
import pytest
from ape_starknet.constants import PLUGIN_NAME
from ape_starknet.utils import to_int
from ape_starknet.utils import is_hex_address
from ape.api.networks import LOCAL_NETWORK_NAME
from hexbytes import HexBytes
from ape_starknet.accounts import DEVNET_CONTRACT_SALT, StarknetAccountDeployment
from ape_starknet.utils import PREDEPLOYED_ACCOUNT_CLASS_HASH


def test_address(account: StarknetDevnetAccount):
    assert is_hex_address(account.address)


def test_get_balance(account: StarknetDevnetAccount):
    assert account.balance is not None


def test_account_container(account_container):
    assert account_container.name == PLUGIN_NAME


def test_test_accounts(account_container):
    assert len(account_container.test_accounts) > 0
    assert all(isinstance(acc, StarknetDevnetAccount) for acc in account_container.test_accounts)


def test_account_balance(account, provider):
    balance = provider.get_balance(account.address)
    assert balance >= 0 and account.balance is not None


def test_account_nonce(account, provider):
    nonce = provider.get_nonce(account.address)
    assert nonce >= 0


def test_provider_get_balance(account, provider: StarknetProvider):
    first_balance = provider.get_balance(account.address)

    second_balance = provider.get_balance(account)

    third_balance = provider.get_balance(to_int(account.address))  # type: ignore

    assert first_balance == second_balance == third_balance


def test_import_with_passphrase(account_container, account, give_input):

    alias = "__TEST_IMPORT_WITH_PASSPHRASE__"
    deployment = StarknetAccountDeployment(
        contract_address=account.address, network_name=LOCAL_NETWORK_NAME, salt=DEVNET_CONTRACT_SALT
    )

    with give_input("p@55W0rd\np@55W0rd\n"):
        account_container.import_account(
            alias,
            PREDEPLOYED_ACCOUNT_CLASS_HASH,
            account.private_key,
            deployments=[deployment],
        )
    new_account = account_container.load(alias)
    assert new_account.address == account.address


def test_public_key(key_file_account, public_key):
    actual = key_file_account.public_key
    assert int(actual, 16) == public_key


def test_account_container_contains(account, second_account, key_file_account, account_container):
    assert account.address in account_container
    assert second_account.address in account_container
    assert key_file_account.address in account_container


CASE_NAME_TO_LAMBDA = {
    "address_str": lambda a, _: a.address,
    "address_int": lambda a, e: e.encode_address(a.address),
    "public_key_str": lambda a, _: a.public_key,
    "public_key_int": lambda a, e: e.encode_address(a.public_key),
}


@pytest.mark.parametrize("case", [x for x in CASE_NAME_TO_LAMBDA.keys()])
def test_access_account_by_str_address(account, account_container, starknet, case):
    get_address = CASE_NAME_TO_LAMBDA[case]
    address = get_address(account, starknet)
    assert account_container[address] == account
    assert address in account_container


def test_can_access_devnet_accounts(account, second_account, chain):
    assert chain.contracts[account.address] == account.contract_type
    assert chain.contracts[second_account.address] == second_account.contract_type


@pytest.mark.parametrize("msg", (5, [5, 6], b"5", HexBytes(122)))
def test_sign_message(msg, key_file_account, password):
    key_file_account.set_autosign(True, passphrase=password)
    assert key_file_account.sign_message(msg)
    key_file_account.set_autosign(False)


def test_unlock_with_passphrase_and_sign_message(
    in_starknet_testnet, give_input, devnet_keyfile_account, password
):
    devnet_keyfile_account.unlock(passphrase=password)
    with give_input("y\n"):
        devnet_keyfile_account.sign_message([1, 2, 3])


def test_unlock_from_prompt_and_sign_message(
    in_starknet_testnet, give_input, devnet_keyfile_account, password
):
    with give_input(f"{password}\n"):
        devnet_keyfile_account.unlock()

    with give_input("y\n"):
        devnet_keyfile_account.sign_message([1, 2, 3])


def test_unlock_with_passphrase_and_sign_transaction(
    in_starknet_testnet, give_input, devnet_keyfile_account, password, txn
):
    devnet_keyfile_account.unlock(passphrase=password)
    with give_input("y\n"):
        devnet_keyfile_account.sign_transaction(txn)


def test_unlock_from_prompt_and_sign_transaction(
    in_starknet_testnet, give_input, devnet_keyfile_account, password, txn
):
    with give_input(f"{password}\n"):
        devnet_keyfile_account.unlock()

    with give_input("y\n"):
        devnet_keyfile_account.sign_transaction(txn)


def test_set_autosign(in_starknet_testnet, give_input, devnet_keyfile_account, password, txn):
    with give_input(f"{password}\n"):
        devnet_keyfile_account.set_autosign(True)

    devnet_keyfile_account.sign_transaction(txn)

    # Disable and verify we have to sign again
    devnet_keyfile_account.set_autosign(False)
    with give_input(f"y\n{password}\n"):
        devnet_keyfile_account.sign_transaction(txn)


def test_set_autosign_and_provide_passphrase(
    in_starknet_testnet, give_input, devnet_keyfile_account, password, txn
):
    devnet_keyfile_account.set_autosign(True, passphrase=password)
    devnet_keyfile_account.sign_transaction(txn)

    # Disable and verify we have to sign again
    devnet_keyfile_account.set_autosign(False)
    with give_input(f"y\n{password}\n"):
        devnet_keyfile_account.sign_transaction(txn)
