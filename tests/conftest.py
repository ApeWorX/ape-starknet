import json
import logging
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkdtemp
from typing import cast
import ape
import pytest
from ape.api.networks import LOCAL_NETWORK_NAME
from click.testing import CliRunner
from ape_starknet.accounts import StarknetAccountContainer, StarknetKeyfileAccount
from ape_starknet.constants import PLUGIN_NAME
from ape_starknet.constants import OPENZEPPELIN_ACCOUNTS
from ape_starknet.accounts import DEVNET_CONTRACT_SALT, StarknetAccountDeployment

logger = logging.getLogger(__name__)


# NOTE: Ensure that we don't use local paths for these
ape.config.DATA_FOLDER = Path(mkdtemp()).resolve()
print(f"Using data folder: {ape.config.DATA_FOLDER}")


@pytest.fixture
def give_input():
    def fn(msg):
        return CliRunner().isolation(input=msg)

    return fn


@pytest.fixture
def txn(devnet_keyfile_account):
    return devnet_keyfile_account.get_deploy_account_txn()


@pytest.fixture(scope="session")
def config():
    return ape.config


@pytest.fixture(scope="session")
def accounts():
    return ape.accounts


@pytest.fixture(scope="session")
def networks():
    return ape.networks


@pytest.fixture(scope="session")
def chain():
    return ape.chain


@pytest.fixture
def provider(chain):
    return chain.provider


@pytest.fixture(scope="session")
def account(account_container, use_local_starknet):
    with use_local_starknet:
        return account_container.test_accounts[0]


@pytest.fixture(scope="session")
def second_account(account_container, use_local_starknet):
    with use_local_starknet:
        return account_container.test_accounts[1]


@pytest.fixture(scope="session")
def use_local_starknet():
    choice = f"{PLUGIN_NAME}:{LOCAL_NETWORK_NAME}:{PLUGIN_NAME}"
    return ape.networks.parse_network_choice(choice)


@pytest.fixture(scope="session", autouse=True)
def connected(use_local_starknet):
    with use_local_starknet:
        yield


@pytest.fixture(scope="session")
def starknet():
    return ape.networks.starknet


@pytest.fixture(scope="session")
def testnet(starknet):
    return starknet.testnet


@pytest.fixture
def in_starknet_testnet(testnet):
    with testnet.use_provider("starknet"):
        yield


projects_directory = Path(__file__).parent / "projects"


@pytest.fixture(scope="session")
def project_hello_starknet():
    return projects_directory / "hello_starknet"


@pytest.fixture(scope="session")
def project_erc20_starknet():
    return projects_directory / "erc20_starknet"


ALIAS = "__TEST_ALIAS__"
SECOND_ALIAS = "__TEST_ALIAS_2__"
EXISTING_KEY_FILE_ALIAS = f"{ALIAS}existing_key_file"
EXISTING_EPHEMERAL_ALIAS = f"{ALIAS}existing_ephemeral"
PASSWORD = "123"

PUBLIC_KEY = 367323783092256132793135877206902311054243182689252847590820585364953599456

# Pre-checksummed
CONTRACT_ADDRESS = "0x06b7243aA4eDBe5bD629c6712B3ac9639B160480a7730a31483F7b387463A183"

START_BALANCE = "1000000 ETH"

# Purposely pick a number larest enough to test Uint256 logic
TOKEN_INITIAL_SUPPLY = 2 * 2**128


@pytest.fixture(scope="session")
def existing_key_file_alias():
    return EXISTING_KEY_FILE_ALIAS


@pytest.fixture(scope="session")
def contract_address():
    return CONTRACT_ADDRESS


@pytest.fixture(scope="session")
def password():
    return PASSWORD


@pytest.fixture(scope="session")
def public_key():
    return PUBLIC_KEY


@pytest.fixture(scope="session")
def token_initial_supply():
    return TOKEN_INITIAL_SUPPLY


@pytest.fixture(scope="session")
def convert():
    return ape.convert


@pytest.fixture(scope="session")
def account_container(accounts):
    return cast(StarknetAccountContainer, accounts.containers[PLUGIN_NAME])


@pytest.fixture(scope="session")
def eth_account(accounts):
    return accounts.test_accounts[0]


@pytest.fixture(scope="session")
def ephemeral_account(account_container):
    return create_account(account_container)


def create_account(container):
    return container.create_account(ALIAS)


OPEN_ZEPPELIN_ACCOUNT_CLASS_HASH = OPENZEPPELIN_ACCOUNTS[0]["class_hash"]


@pytest.fixture(scope="session")
def key_file_account_data():
    return {
        "ape-starknet": {
            "public_key": 367323783092256132793135877206902311054243182689252847590820585364953599456,  # noqa: E501
            "class_hash": 3146761231686369291210245479075933162526514193311043598334639064078158562617,  # noqa: E501
            "salt": 2383401134194309956567840095871463496630094772532838902518655252027359569593,
            "deployments": [
                {"network_name": "testnet", "contract_address": CONTRACT_ADDRESS},
            ],
        },
        "address": "93920847a9ab3c731562461bb7d0fb95a39df9f9",
        "crypto": {
            "cipher": "aes-128-ctr",
            "cipherparams": {"iv": "b279075a04c21ed621e466974c64e6e3"},
            "ciphertext": "e3fb18fbd034df62963ac776c04fec9a58a71273cbbd236fa6f22f98a7563e5a",
            "kdf": "scrypt",
            "kdfparams": {
                "dklen": 32,
                "n": 262144,
                "r": 1,
                "p": 8,
                "salt": "810c59cb704d6012ce2002012cbdf735",
            },
            "mac": "f903f6d42efd883e3b402f68c7681857080a889a363e78e7694e143b5a9a9a65",
        },
        "id": "2b50ad6b-d136-4981-b09c-08c006e2ae56",
        "version": 3,
    }


@pytest.fixture(scope="session")
def ephemeral_account_data():
    return {
        "private_key": 509219664670742235607272813021130138373595301613956902800973975925797957544,
        "public_key": 2068822281043178075870469557539081791152169138879468456959920393634230618024,
        "class_hash": OPEN_ZEPPELIN_ACCOUNT_CLASS_HASH,
    }


@pytest.fixture
def key_file_account(config, key_file_account_data):
    temp_accounts_dir = Path(config.DATA_FOLDER) / "starknet"
    temp_accounts_dir.mkdir(exist_ok=True, parents=True)
    test_key_file_path = temp_accounts_dir / f"{EXISTING_KEY_FILE_ALIAS}.json"

    if test_key_file_path.is_file():
        test_key_file_path.unlink()

    test_key_file_path.write_text(json.dumps(key_file_account_data))

    account = StarknetKeyfileAccount(key_file_path=test_key_file_path)
    account.unlock(passphrase="123")

    yield account

    if test_key_file_path.is_file():
        test_key_file_path.unlink()


@pytest.fixture
def devnet_keyfile_account(account_container, account, password, give_input):
    deployments = [
        StarknetAccountDeployment(
            contract_address=account.address, network_name="local", salt=DEVNET_CONTRACT_SALT
        )
    ]
    with give_input(f"{password}\n{password}\n"):
        return account_container.import_account(
            "__DEV_AS_KEYFILE_ACCOUNT__",
            OPEN_ZEPPELIN_ACCOUNT_CLASS_HASH,
            account.private_key,
            deployments=deployments,
            salt=DEVNET_CONTRACT_SALT,
            allow_local_file_store=True,
        )
