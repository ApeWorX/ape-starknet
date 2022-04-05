import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Union

import click
from ape.api import AccountAPI, AccountContainerAPI, TransactionAPI
from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractContainer, ContractInstance
from ape.exceptions import AccountsError
from ape.logging import logger
from ape.types import AddressType, SignableMessage
from ape.utils import abstractmethod
from eth_keyfile import create_keyfile_json, decode_keyfile_json  # type: ignore
from eth_utils import text_if_str, to_bytes
from ethpm_types.abi import ConstructorABI
from hexbytes import HexBytes
from starknet_py.net import KeyPair  # type: ignore
from starknet_py.net.account.account_client import AccountClient  # type: ignore
from starknet_py.net.account.compiled_account_contract import (  # type: ignore
    COMPILED_ACCOUNT_CONTRACT,
)
from starknet_py.utils.crypto.cpp_bindings import ECSignature  # type: ignore
from starknet_py.utils.crypto.facade import sign_calldata  # type: ignore
from starkware.cairo.lang.vm.cairo_runner import verify_ecdsa_sig  # type: ignore
from starkware.crypto.signature.signature import get_random_private_key  # type: ignore
from starkware.starknet.services.api.contract_definition import ContractDefinition  # type: ignore
from starkware.starknet.services.api.feeder_gateway.response_objects import (  # type: ignore
    TransactionInfo,
)

from ape_starknet._utils import PLUGIN_NAME, get_chain_id, handle_client_errors
from ape_starknet.provider import StarknetProvider
from ape_starknet.transactions import InvokeFunctionTransaction, StarknetTransaction


class StarknetAccountContracts(AccountContainerAPI):

    ephemeral_accounts: Dict[str, Dict] = {}
    """Local-network accounts that do not persist."""

    cached_accounts: Dict[str, "StarknetKeyfileAccount"] = {}

    @property
    def _key_file_paths(self) -> Iterator[Path]:
        return self.data_folder.glob("*.json")

    @property
    def aliases(self) -> Iterator[str]:
        for key in self.ephemeral_accounts.keys():
            yield key

        for key_file in self._key_file_paths:
            yield key_file.stem

    @property
    def accounts(self) -> Iterator[AccountAPI]:
        for alias, account_data in self.ephemeral_accounts.items():
            yield StarknetEphemeralAccount(raw_account_data=account_data, account_key=alias)

        for key_file_path in self._key_file_paths:
            if key_file_path.stem in self.cached_accounts:
                yield self.cached_accounts[key_file_path.stem]
            else:
                account = StarknetKeyfileAccount(key_file_path=key_file_path)
                self.cached_accounts[key_file_path.stem] = account
                yield account

    def __len__(self) -> int:
        return len([*self._key_file_paths])

    def __setitem__(self, address: AddressType, account: AccountAPI):
        pass

    def __delitem__(self, address: AddressType):
        pass

    def __getitem__(self, item: Union[AddressType, int]) -> AccountAPI:
        address: AddressType = (
            self.network_manager.starknet.decode_address(item) if isinstance(item, int) else item
        )
        return super().__getitem__(address)

    def load(self, alias: str) -> "BaseStarknetAccount":
        if alias in self.ephemeral_accounts:
            account = StarknetEphemeralAccount(
                raw_account_data=self.ephemeral_accounts[alias], account_key=alias
            )
            return account

        return self.load_key_file_account(alias)

    def load_key_file_account(self, alias: str) -> "StarknetKeyfileAccount":
        if alias in self.cached_accounts:
            return self.cached_accounts[alias]

        for key_file_path in self._key_file_paths:
            if key_file_path.stem == alias:
                account = StarknetKeyfileAccount(key_file_path=key_file_path)
                self.cached_accounts[alias] = account
                return account

        raise AccountsError(f"Starknet account '{alias}' not found.")

    def import_account(
        self,
        alias: str,
        network_name: str,
        contract_address: str,
        private_key: Union[int, str],
    ):
        if isinstance(private_key, str):
            private_key = private_key.strip("'\"")
            private_key = int(private_key, 16)

        network_name = _clean_network_name(network_name)
        key_pair = KeyPair.from_private_key(private_key)
        deployment_data = {
            "deployments": [
                {"network_name": network_name, "contract_address": contract_address},
            ],
        }

        if network_name == LOCAL_NETWORK_NAME:
            account_data = {
                "public_key": key_pair.public_key,
                "private_key": key_pair.private_key,
                **deployment_data,
            }
            self.ephemeral_accounts[alias] = account_data
        else:
            # Only write keyfile if not in a local network
            path = self.data_folder.joinpath(f"{alias}.json")
            new_account = StarknetKeyfileAccount(key_file_path=path)
            new_account.write(passphrase=None, private_key=private_key, **deployment_data)

    def deploy_account(self, alias: str, private_key: Optional[int] = None) -> str:
        """
        Deploys an account contract for the given alias.

        Args:
            alias (str): The alias to use to reference the account in ``ape``.
            private_key (Optional[int]): Optionally provide your own private key.`

        Returns:
            str: The contract address of the account.
        """

        if alias in self.aliases:
            raise AccountsError(f"Account with alias '{alias}' already exists.")

        network_name = self.provider.network.name
        logger.info(f"Deploying an account to '{network_name}' network ...")

        private_key = private_key or get_random_private_key()
        key_pair = KeyPair.from_private_key(private_key)

        account_contract = ContractDefinition.loads(COMPILED_ACCOUNT_CONTRACT)
        constructor_abi_data: Dict = next(
            (member for member in account_contract.abi if member["type"] == "constructor"),
            {},
        )

        constructor_abi = ConstructorABI(**constructor_abi_data)
        transaction = self.provider.network.ecosystem.encode_deployment(
            HexBytes(account_contract.serialize()), constructor_abi, key_pair.public_key
        )
        receipt = self.provider.send_transaction(transaction)

        if not receipt.contract_address:
            raise AccountsError("Failed to deploy account contract.")

        self.import_account(alias, network_name, receipt.contract_address, key_pair.private_key)
        return receipt.contract_address

    def delete_account(self, alias: str, network: Optional[str] = None):
        network = network or self.provider.network.name
        if alias in self.ephemeral_accounts:
            del self.ephemeral_accounts[alias]
        else:
            account = self.load_key_file_account(alias)
            account.delete(network)


@dataclass
class StarknetAccountDeployment:
    network_name: str
    contract_address: AddressType


class BaseStarknetAccount(AccountAPI):
    @abstractmethod
    def _get_key(self) -> int:
        ...

    @abstractmethod
    def get_account_data(self) -> Dict:
        ...

    @property
    def contract_address(self) -> AddressType:
        network = self.provider.network
        for deployment in self.deployments:
            if deployment.network_name == network.name:
                address = deployment.contract_address
                return network.ecosystem.decode_address(address)

        raise AccountsError(f"Account '{self.alias}' is not deployed on network '{network.name}'.")

    @property
    def address(self) -> AddressType:
        public_key = self.get_account_data()["public_key"]
        return self.network_manager.starknet.decode_address(public_key)

    def sign_transaction(self, txn: TransactionAPI) -> Optional[ECSignature]:
        if not isinstance(txn, InvokeFunctionTransaction):
            raise AccountsError("This account can only sign Starknet transactions.")

        starknet_object = txn.as_starknet_object()
        return self.sign_message(starknet_object.calldata)

    def deploy(self, contract: ContractContainer, *args, **kwargs) -> ContractInstance:
        return contract.deploy(sender=self)

    @handle_client_errors
    def send_transaction(self, txn: TransactionAPI) -> TransactionInfo:
        provider = self.provider
        if not isinstance(txn, StarknetTransaction) or not isinstance(provider, StarknetProvider):
            # Mostly for mypy
            raise AccountsError("Can only send Starknet transactions.")

        network = provider.network
        key_pair = KeyPair(
            public_key=network.ecosystem.encode_address(self.address),
            private_key=self._get_key(),
        )
        chain_id = get_chain_id(network.name)
        account_client = AccountClient(
            self.contract_address,
            key_pair,
            provider.uri,
            chain=chain_id,
        )
        return account_client.add_transaction_sync(txn.as_starknet_object())

    @property
    def deployments(self) -> List[StarknetAccountDeployment]:
        return [StarknetAccountDeployment(**d) for d in self.get_account_data()["deployments"]]

    def get_deployment(self, network_name: str) -> Optional[StarknetAccountDeployment]:
        for deployment in self.deployments:
            if deployment.network_name in network_name:
                return deployment

        return None

    def check_signature(  # type: ignore
        self,
        data: int,
        signature: Optional[ECSignature] = None,  # TransactionAPI doesn't need it
    ) -> bool:
        int_address = self.network_manager.get_ecosystem(PLUGIN_NAME).encode_address(self.address)
        return verify_ecdsa_sig(int_address, data, signature)


class StarknetEphemeralAccount(BaseStarknetAccount):
    raw_account_data: Dict
    account_key: str

    def get_account_data(self) -> Dict:
        return self.raw_account_data

    @property
    def alias(self) -> Optional[str]:
        return self.account_key

    def _get_key(self) -> int:
        return self.raw_account_data["private_key"]

    def sign_message(self, msg: SignableMessage) -> Optional[ECSignature]:
        if not isinstance(msg, (list, tuple)):
            msg = [msg]

        return sign_calldata(msg, self._get_key())


class StarknetKeyfileAccount(BaseStarknetAccount):
    key_file_path: Path
    locked: bool = True
    __cached_key: Optional[int] = None

    def write(self, passphrase: Optional[str] = None, private_key: Optional[int] = None, **kwargs):
        passphrase = (
            click.prompt("Enter a new passphrase", hide_input=True, confirmation_prompt=True)
            if passphrase is None
            else passphrase
        )

        key_file_data = self.__encrypt_key_file(passphrase, private_key=private_key)
        key_file_data["public_key"] = key_file_data["address"]
        del key_file_data["address"]
        account_data = {**key_file_data, **self.get_account_data(), **kwargs}
        self.key_file_path.write_text(json.dumps(account_data))

    @property
    def alias(self) -> Optional[str]:
        return self.key_file_path.stem

    def get_account_data(self) -> Dict:
        if self.key_file_path.is_file():
            return json.loads(self.key_file_path.read_text())

        return {}

    def delete(self, network: str):
        passphrase = click.prompt(
            f"Enter passphrase to delete '{self.alias}'",
            hide_input=True,
        )
        self.__decrypt_key_file(passphrase)

        remaining_deployments = [vars(d) for d in self.deployments if d.network_name not in network]
        if not remaining_deployments:
            # Delete entire account JSON if no more deployments.
            self.key_file_path.unlink()
        else:
            self.write(passphrase=passphrase, deployments=remaining_deployments)

    def sign_message(
        self, msg: SignableMessage, passphrase: Optional[str] = None
    ) -> Optional[ECSignature]:
        if not isinstance(msg, (list, tuple)):
            msg = [msg]

        private_key = self._get_key(passphrase=passphrase)
        return sign_calldata(msg, private_key)

    def change_password(self):
        self.locked = True  # force entering passphrase to get key
        original_passphrase = self._get_passphrase_from_prompt()
        private_key = self._get_key(passphrase=original_passphrase)
        self.write(private_key=private_key)

    def add_deployment(self, network_name: str, contract_address: AddressType):
        passphrase = self._get_passphrase_from_prompt()
        network_name = _clean_network_name(network_name)
        deployments = [vars(d) for d in self.deployments if d.network_name not in network_name]
        new_deployment = StarknetAccountDeployment(
            network_name=network_name, contract_address=contract_address
        )
        deployments.append(vars(new_deployment))
        self.write(
            passphrase=passphrase,
            private_key=self._get_key(passphrase=passphrase),
            **{"deployments": deployments},
        )

    def _get_key(self, passphrase: Optional[str] = None) -> int:
        if self.__cached_key is not None:
            if not self.locked:
                click.echo(f"Using cached key for '{self.alias}'")
                return self.__cached_key
            else:
                self.__cached_key = None

        if passphrase is None:
            passphrase = self._get_passphrase_from_prompt()

        key_hex_str = self.__decrypt_key_file(passphrase).hex()
        key = int(key_hex_str, 16)
        if self.locked and (
            passphrase is not None or click.confirm(f"Leave '{self.alias}' unlocked?")
        ):
            self.locked = False
            self.__cached_key = key

        return key

    def _get_passphrase_from_prompt(self) -> str:
        return click.prompt(
            f"Enter passphrase to unlock '{self.alias}'",
            hide_input=True,
            default="",  # Just in case there's no passphrase
        )

    def __encrypt_key_file(self, passphrase: str, private_key: Optional[int] = None) -> Dict:
        private_key = self._get_key(passphrase=passphrase) if private_key is None else private_key
        key_bytes = HexBytes(private_key)
        passphrase_bytes = text_if_str(to_bytes, passphrase)
        return create_keyfile_json(key_bytes, passphrase_bytes, kdf="scrypt")

    def __decrypt_key_file(self, passphrase: str) -> HexBytes:
        key_file_dict = json.loads(self.key_file_path.read_text())
        key_file_dict["address"] = key_file_dict["public_key"]
        del key_file_dict["public_key"]
        password_bytes = text_if_str(to_bytes, passphrase)
        decoded_json = decode_keyfile_json(key_file_dict, password_bytes)
        return HexBytes(decoded_json)


def _clean_network_name(network: str) -> str:
    for net in ("local", "mainnet", "testnet"):
        if net in network:
            return net

    return network