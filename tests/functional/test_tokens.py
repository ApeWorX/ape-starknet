from pathlib import Path

import pytest
from ape.api.networks import LOCAL_NETWORK_NAME

from ape_starknet import tokens as _tokens


@pytest.fixture(scope="module")
def token_contract(config, account, token_initial_supply, project):
    project_path = Path(__file__).parent.parent / "projects" / "token"

    with config.using_project(project_path):
        yield project.TestToken.deploy(123123, 321321, token_initial_supply, account.address)


@pytest.fixture(scope="module")
def proxy_token_contract(config, account, token_initial_supply, token_contract, project):
    project_path = Path(__file__).parent.parent / "projects" / "proxy"

    with config.using_project(project_path):
        return project.Proxy.deploy(token_contract.address)


@pytest.fixture(scope="module")
def tokens(token_contract, proxy_token_contract, provider, account):
    _tokens.add_token("test_token", LOCAL_NETWORK_NAME, token_contract.address)
    _tokens.add_token("proxy_token", LOCAL_NETWORK_NAME, proxy_token_contract.address)
    return _tokens


@pytest.mark.parametrize(
    "token",
    (
        "test_token",
        "proxy_token",
    ),
)
def test_get_balance(tokens, account, token_initial_supply, token):
    assert tokens.get_balance(account, token=token) == token_initial_supply


def test_get_fee_balance(tokens, account):
    # Separate from test above because likely fees have been spent already
    assert tokens.get_balance(account)


@pytest.mark.parametrize("token", ("eth",))
def test_transfer(tokens, account, second_account, token):
    initial_balance = tokens.get_balance(second_account.address, token=token)
    tokens.transfer(account.address, second_account.address, 10, token=token)
    assert tokens.get_balance(second_account.address, token=token) == initial_balance + 10
