from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union, get_args

import click
from ape.api.accounts import AccountAPI, AccountContainerAPI
from ape.api.address import BaseAddress
from ape.api.transactions import ReceiptAPI, TransactionAPI
from ape.contracts.base import ContractContainer, ContractInstance
from ape.logging import logger
from ape.utils.basemodel import BaseModel
from ape.utils.misc import cached_property, raises_not_implemented
from eth_keyfile import create_keyfile_json, decode_keyfile_json
from eth_utils import (
    text_if_str,
    to_bytes,
)
from ethpm_types import ContractType
from hexbytes import HexBytes
from pydantic import Field, field_validator, model_validator
from starknet_py.common import create_sierra_compiled_contract
from starknet_py.contract import Contract
from starknet_py.hash.sierra_class_hash import compute_sierra_class_hash
from starknet_py.hash.utils import (
    message_signature,
)
from starknet_py.net.account.account import Account
from starknet_py.net.client_models import (
    ResourceBounds,
    ResourceBoundsMapping,
)
from starknet_py.net.signer.stark_curve_signer import KeyPair, StarkCurveSigner
from starkware.cairo.lang.vm.cairo_runner import verify_ecdsa_sig
from starkware.crypto.signature.signature import ECSignature
from starkware.starknet.core.os.contract_address.contract_address import (
    calculate_contract_address_from_hash,
)
from starkware.starknet.definitions.fields import ContractAddressSalt
from typing_extensions import Annotated

from ape_starknet.basemodel import StarknetBase
from ape_starknet.config import LOCAL_NETWORK_NAME, ProviderConfig
from ape_starknet.constants import (
    APP_KEY_FILE_KEY,
    ARGENT_ACCOUNTS,
    BRAAVOS_ACCOUNTS,
    MAX_FEE,
    OPENZEPPELIN_ACCOUNTS,
)
from ape_starknet.provider import StarknetDevnetProvider
from ape_starknet.transactions import (
    BaseTransactionT,
    DepreciatedTxs,
    NewTxs,
    StarknetDeployAccountV3,
    is_base_tx,
    is_starknet_tx,
)
from ape_starknet.types import StarknetSignableMessage
from ape_starknet.utils import (
    DEVNET_CONTRACT_SALT,
    FeeTokenType,
    clean_network_name,
    create_keypair,
    get_random_private_key,
    pad_hex_str,
    to_hex,
    to_int,
)


class StarknetAccountContainer(AccountContainerAPI, StarknetBase):
    """Starknet Account Container"""

    temp_accounts: Dict[str, Dict] = {}
    """Local-network accounts that do not persist."""

    cached_accounts: Dict[str, StarknetKeyfileAccount] = {}
    """Accounts created in a live network that persist in key-files."""

    @property
    def aliases(self) -> Iterator[str]:
        """
        All available aliases.

        Returns:
            Iterator[str]
        """
        yield from self.temp_accounts.keys()
        for key_file in self._key_file_paths:
            yield key_file.stem

    @property
    def provider_config(self) -> ProviderConfig:
        """
        Provider network configuration

        Returns:
            :class:`~ape_starknet.config.ProviderConfig`
        """
        return self.starknet_config["provider"]

    @property
    def _key_file_paths(self) -> Iterator[Path]:
        for path in self.data_folder.glob("*.json"):
            if path.stem not in ("deployments_map",):
                yield path

    @property
    def test_accounts(self) -> List:
        """
        Makes a list of test accounts.

        Returns:
            List[:class:`~ape_starknet.accounts.StarknetDevelopmentAccount`]
        """
        if (
            self.network_manager.active_provider is None
            or self.provider.network.name != LOCAL_NETWORK_NAME
            or not isinstance(self.provider, StarknetDevnetProvider)
        ):

            return []

        return self._genesis_test_accounts

    @cached_property
    def _genesis_test_accounts(self) -> List:
        provider = self.provider
        if self.provider.network.name == LOCAL_NETWORK_NAME:
            predeployed_accounts = provider.get_predeployed_accounts(with_balance=True)

        else:
            return []
        devnet_accounts = [StarknetDevnetAccount(**account) for account in predeployed_accounts]

        # Caching.
        for account in devnet_accounts:
            self.chain_manager.contracts[account.address] = account.contract_type

        return devnet_accounts

    @property
    def accounts(self) -> Iterator[AccountAPI]:
        """
        All accounts.

        Returns:
            Iterator[:class:`~ape.api.accounts.AccountAPI`]
        """
        for test_account in self.test_accounts:
            yield test_account

        for alias, account_data in self.temp_accounts.items():
            yield StarknetDevnetAccount(**account_data)

        for key_file_path in self._key_file_paths:
            if key_file_path.stem == "deployments_map":
                continue

            if key_file_path.stem in self.cached_accounts:
                yield self.cached_accounts[key_file_path.stem]
            else:
                account = StarknetKeyfileAccount.from_file(key_file_path)
                self.cached_accounts[key_file_path.stem] = account
                yield account

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"

    def __len__(self) -> int:
        return len([*self._key_file_paths])

    def __setitem__(self, address: str, account: AccountAPI):
        pass

    def __delitem__(self, address: str):
        pass

    def __getitem__(self, address: Union[str, int]) -> AccountAPI:
        """
        Get an account by address.

        Args:
            address (Union[str, int]): The address to get.

        Raises:
            KeyError: When there is no local account with the given address.

        Returns:
            :class:`~ape.api.accounts.AccountAPI`
        """

        address = to_int(address)

        for account in [a for a in self.accounts if isinstance(a, BaseStarknetAccount)]:
            if to_int(account.public_key) == address:
                # Matched by public key.
                return account

            elif to_int(account.address) == address:
                # Matched by contract address.
                return account

        raise IndexError(f"No local account found for {address}.")

    def __contains__(self, address: Union[str, int]) -> bool:
        """
        Check if the address is an existing account in ``ape``.

        Raises:
            IndexError: When the given account address is not in this container.

        Args:
            address (Union[str, int]): An account address.

        Returns:
            bool: ``True`` if ``ape`` manages the account with the given address.
        """

        try:
            address = to_int(address)
            self.__getitem__(address)
            return True

        except IndexError:
            return False

    def get_account(self, address: Union[str, int]) -> "BaseStarknetAccount":
        """
        Get account based on address.

        Args:
            address (Union[str, int]): Address of the account.

        Returns:
            :class:`~ape_starknet.accounts.BaseStarknetAccount`
        """
        return self[address]  # type: ignore

    def load(self, alias: str) -> "BaseStarknetAccount":
        """
        Loads an account based on alias.

        Args:
            alias (str): A shortened-name for quicker access to the account.

        Return:
            :class:`~ape_starknet.accounts.BaseStarknetAccount`
        """
        if alias in self.temp_accounts:
            return StarknetDevnetAccount(**self.temp_accounts[alias])
        return self._load_key_file_account(alias)

    def _load_key_file_account(self, alias: str) -> "StarknetKeyfileAccount":
        if alias in self.cached_accounts:
            return self.cached_accounts[alias]

        for key_file_path in self._key_file_paths:
            if key_file_path.stem == alias:
                account = StarknetKeyfileAccount.from_file(key_file_path)
                self.cached_accounts[alias] = account
                return account

        raise Exception(f"Starknet account '{alias}' not found.")

    def create_account(
        self,
        alias: str,
        class_hash: int,
        salt: Optional[int] = None,
        private_key: Optional[str] = None,
        constructor_calldata: Optional[List[int]] = None,
        allow_local_file_store: bool = False,
    ) -> "BaseStarknetAccount":
        """
        Create an account within the parameters given or generated.

        **NOTE**: Defaults to ``True`` for all non-local networks and ``False`` for local networks.

        Args:
            alias (str): A shortened-name for quicker access to the account.
            class_hash (int): A hash chain of the definition of the class.
            salt (Optional[int]):
                Contract address salt. Needed if wanting to deploy to a different address.
            private_key (Optional[str]):
                Set private key manually or leave blank to generate one randomly.
            constructor_calldata (Optional[List[int]]): List representing the function parameters.
            allow_local_file_store (bool): Allow for account to be stored on local file.

        Returns:
            :class:`~ape_starknet.accounts.BaseStarknetAccount`
        """
        if alias in self.aliases:
            raise Exception(f"Account with alias '{alias}' already exists.")

        private_key = private_key or get_random_private_key()
        salt = salt or ContractAddressSalt.get_random_value()

        logger.info(f"Creating account using class hash '{class_hash}' ...")
        account = self.import_account(
            alias,
            class_hash,
            private_key,
            salt=salt,
            constructor_calldata=constructor_calldata,
            allow_local_file_store=allow_local_file_store,
        )

        if (
            self.network_manager.active_provider
            and self.provider.network.name == LOCAL_NETWORK_NAME
            and not allow_local_file_store
        ):
            account.deploy_account()

        return account

    def import_account(
        self,
        alias: str,
        class_hash: int,
        private_key: Union[int, str],
        deployments: Optional[List[StarknetAccountDeployment]] = None,
        constructor_calldata: Optional[List] = None,
        salt: Optional[int] = None,
        allow_local_file_store: bool = False,
    ) -> "BaseStarknetAccount":
        """
        Import deployed starknet account.

        Args:
            alias (str): A shortened-name for quicker access to the account.
            class_hash (int): Class hash of contract.
            private_key (Union[int, str]): Private key for account.
            deployments(Optional[List[:class:`~ape_starknet.accounts.StarknetAccountDeployment`]]):
                The network deployment status of the account.
            constructor_calldata (Optional[List[int]]): List representing the function parameters.
            salt (Optional[int]):
                Contract address salt. Needed if wanting to deploy to a different address.
            allow_local_file_store (bool): Allows to store in local file store.

        Returns:
            :class:`~ape_starknet.accounts.BaseStarknetAccount`
        """
        deployments = deployments or []
        key_pair = create_keypair(private_key)
        self._cache_deployments(class_hash, deployments)

        account_data: Dict[str, Any] = {
            "public_key": key_pair.public_key,
            "private_key": key_pair.private_key,
            "class_hash": class_hash,
        }
        account_data["constructor_calldata"] = constructor_calldata or []
        if (
            not allow_local_file_store
            and not deployments
            and self.network_manager.active_provider is not None
            and self.provider.network.name == LOCAL_NETWORK_NAME
        ):
            # Locally simulating keypair creation without any deployments.
            self.temp_accounts[alias] = account_data
            return StarknetDevnetAccount(**account_data)

        new_account: Optional["BaseStarknetAccount"] = None
        local_deployments = [x for x in deployments if x.network_name == LOCAL_NETWORK_NAME]
        local_salt = salt or DEVNET_CONTRACT_SALT
        for local_deployment in local_deployments:
            account_data["salt"] = local_salt
            account_data["address"] = local_deployment.contract_address
            self.temp_accounts[alias] = account_data
            new_account = StarknetDevnetAccount(**account_data)

        live_deployments = [x for x in deployments if x not in local_deployments]
        if not allow_local_file_store and not live_deployments and new_account:
            return new_account

        salt = salt or ContractAddressSalt.get_random_value()
        path = self.data_folder.joinpath(f"{alias}.json")
        is_local = (
            self.network_manager.active_provider
            and self.provider.network.name == LOCAL_NETWORK_NAME
        )
        new_account = StarknetKeyfileAccount._from_import(
            path,
            is_local=is_local,
            get_pass=lambda: self._prompt_for_new_passphrase(alias),
            allow_local_file_store=allow_local_file_store,
            class_hash=class_hash,
            constructor_calldata=constructor_calldata,
            deployments=live_deployments,
            private_key=key_pair.private_key,
            salt=salt,
        )
        self.cached_accounts[alias] = new_account
        return new_account

    def _prompt_for_new_passphrase(self, alias: str):
        return click.prompt(
            f"Create passphrase to encrypt account '{alias}'",
            hide_input=True,
            default="",
            show_choices=False,
            confirmation_prompt=True,
        )

    def delete_account(
        self,
        alias: str,
        address: Optional[Union[str, int]] = None,
        networks: Optional[Union[str, List[str]]] = None,
        leave_unlocked: Optional[bool] = None,
    ):
        """
        Delete an account.

        Args:
            alias (str): A shortened-name for quicker access to the account.
            address (Optional[Union[str, int]]):
                The address of the account to be deleted Defaults to ``None``.
            networks (Optional[Union[str, List[str]]]):
                The network(s) for the accounts to be deleted from. Defaults to ``None``.
            leave_unlocked (bool):
                Option to leave account unlocked for future interactions. Defaults to ``None``.
        """
        if alias in self.temp_accounts:
            # Only 1 local deployment for ephemeral accounts.
            del self.temp_accounts[alias]

        else:
            # Live network - delegate to account.
            account = self._load_key_file_account(alias)
            account.delete(
                networks=networks,
                address=address,
                leave_unlocked=leave_unlocked,
            )

    def _cache_deployments(self, class_hash: int, deployments: List[StarknetAccountDeployment]):
        for deployment in deployments:
            contract_type = None
            address = deployment.contract_address
            # Cache contract type for the account.
            network_obj = self.starknet.get_network(deployment.network_name)
            with network_obj.use_provider(network_obj.default_provider or "starknet"):
                contract_type = self.provider.get_contract_type(address)

            # Only cache of contract type has a name or else caching gets messsed up.
            if contract_type and contract_type.name:
                self.chain_manager.contracts[address] = contract_type  # type: ignore


class StarknetAccountDeployment(BaseModel):
    """Starknet Account Deployment"""

    network_name: Annotated[str, Field(validate_default=True)]
    contract_address: Annotated[str, Field(validate_default=True)]
    salt: Optional[int] = None  # Only should be None when unknown.

    def __eq__(self, other):
        if isinstance(other, StarknetAccountDeployment):
            return self.path_id == other.path_id
        elif isinstance(other, dict):
            return self.path_id == self.make_path_id(
                other.get("network_name"), other.get("contract_address")
            )
        elif isinstance(other, str):
            parts = other.split(":")
            return (
                self.path_id == self.make_path_id(parts[0], parts[1]) if len(parts) == 2 else False
            )
        return False

    @field_validator("contract_address", mode="before")
    @classmethod
    def validate_hex(cls, value):
        return to_hex(value)

    @field_validator("network_name", mode="before")
    @classmethod
    def validate_network_name(cls, value):
        return clean_network_name(value)

    @property
    def path_id(self) -> str:
        return self.make_path_id(self.network_name, self.contract_address)

    @classmethod
    def make_path_id(cls, network: str, address: Union[str, int]) -> str:
        return f"{network}:{to_int(address)}"

    model_config = {
        "arbitrary_types_allowed": True,
        "validate_assignment": True,
    }


class BaseStarknetAccount(AccountAPI, StarknetBase):
    _account: Account = None

    @property
    def starknet_account(self) -> Account:
        if self._account is None:
            self._account = self._create_starknet_account()
        return self._account

    @raises_not_implemented
    def key_pair(self) -> KeyPair:
        pass

    @raises_not_implemented
    def salt(self):
        """
        The salt is used to determine the contract address. If you change the salt
        but keep the class hash and calldata the same, you will get a new address.
        To keep addresses consistent across networks, a single salt value exists
        at the root of an account. However, when deploying an account on live
        networks, you will have the option to change the salt for each deployment.
        """
        pass

    @property
    def class_hash(self) -> int:
        return self.provider.get_class_hash(self.address)

    @property
    def deployed(self, network_name: Optional[str] = None) -> bool:
        """
        Checks to see if the account is deployed on the network.

        Args:
            network_name (Optional[str]): Name of network that address is deployed on.

        Returns:
            bool
        """
        try:
            code = self.provider.get_code(self.address)
            return len(code) > 0

        except Exception:
            return False

    @property
    def address(self) -> str:  # type: ignore
        """
        The address of this account. Subclasses must override and provide this value.
        """
        raise self.starknet_account.address

    @property
    def public_key(self) -> str:
        """
        The public key of the account.
        """
        raise self.starknet_account.signer.public_key

    @raises_not_implemented
    def deployments(self):
        """
        List of deployments

        Returns:
            List[:class:`~ape_starknet.accounts.StarknetAccountDeployment`]
        """
        pass

    @cached_property
    def default_address(self) -> str:
        """
        The contract address if you are to use the default salt for the account.
        """
        return to_hex(self.get_contract_address())

    @property
    def constructor_calldata(self) -> List[Any]:
        """The list representing the function parameters."""
        return [self.key_pair.public_key]

    @cached_property
    def contract_type(self) -> ContractType:
        contract_type = self.provider.get_contract_type(self.address)
        if not contract_type:
            raise Exception(self.address)

        if self.class_hash in [x["class_hash"] for x in OPENZEPPELIN_ACCOUNTS]:
            contract_type.name = "OpenZeppelinAccount"
            contract_type.source_id = "openzeppelin/account/Account.cairo"

        if self.class_hash == [x["class_hash"] for x in ARGENT_ACCOUNTS]:
            contract_type.name = "ArgentAccount"
            contract_type.source_id = "argent/account/Account.cairo"

        if self.class_hash == [x["class_hash"] for x in BRAAVOS_ACCOUNTS]:
            contract_type.name = "BraavosAccount"
            contract_type.source_id = "braavos/account/Account.cairo"

        else:
            contract_type.name = f"Account_{self.class_hash}"
            contract_type.source_id = f"account/Account_{self.class_hash}.cairo"

        return contract_type

    @raises_not_implemented
    def add_deployment(self, network_name: str, contract_address: int, salt: int):
        pass

    def get_contract_address(self, salt: Optional[int] = None) -> int:
        """
        Calculate a contract address for the given account's salt, class hash,
        and constructor data.

        Args:
            salt(Optional[salt]): Salt to use. Defaults to the root account salt.
              Note: accounts use a root default salt to have consistent addresses
              accross different networks.

        Returns:
            int: The contract address.
        """

        return calculate_contract_address_from_hash(
            class_hash=self.class_hash,
            constructor_calldata=self.constructor_calldata,
            deployer_address=0,
            salt=salt or self.salt,
        )

    def _create_starknet_account(self):
        return Account(
            address=to_int(self.address),
            signer=StarkCurveSigner(
                account_address=to_int(self.address),
                key_pair=self.key_pair,
                chain_id=to_int(self.provider.chain_id),
            ),
            client=self.provider.starknet_client,
            chain=to_int(self.provider.chain_id),
        )

    def __repr__(self):
        try:
            suffix = str(self.address)
        except Exception as err:
            suffix = str(err)

        return f"<{self.__class__.__name__} {suffix}>"

    def transfer(  # type: ignore
        self,
        account: Union[str, BaseAddress],
        value: Optional[Union[str, int]],
        data: Optional[Union[bytes, str]] = None,
        **kwargs,
    ) -> ReceiptAPI:
        """
        Send funds to an account.

        Raises:
            Exception: When transfer value is not a valid integer.

        Args:
            account (Union[str, BaseAddress]): The receiver of the funds.
            value (Optional[Union[str, int]]): The amount to send.
            data (Optional[Union[bytes, str]]): Extra data to include in the transaction.

        Returns:
            :class:`~ape_starknet.transactions.ReceiptAPI`
        """

        if isinstance(value, (int, str)):
            try:
                value = int(value)
            except ValueError:
                raise ValueError("Transfer value is expected to be a valid integer.")

        invoke_tx = (
            self.provider.get_fee_token_contract(self.starknet_account)
            .functions["transfer"]
            .invoke_v1_sync(to_int(account), value, max_fee=MAX_FEE)
        )
        return self.starknet.provider.get_receipt(invoke_tx.hash)

    @property
    def eth_balance(self) -> int:
        address = self.provider.fee_token_contract_class.get_address(FeeTokenType.ETH)
        return self.starknet_account.get_balance_sync(address)

    @property
    def strk_balance(self) -> int:
        address = self.provider.fee_token_contract_class.get_address(FeeTokenType.STRK)
        return self.starknet_account.get_balance_sync(address)

    @property
    def balance(self):
        return self.strk_balance

    def sign_transaction(
        self, txn: Union[TransactionAPI, BaseTransactionT], **signer_options
    ) -> TransactionAPI:
        """
        Sign a transaction.

        Args:
          txn (:class:`~ape.api.transactions.TransactionAPI`): The transaction to sign.
          **signer_options: Additional kwargs given to the signer to modify the signing operation.

        Returns:
          :class:`~ape.api.transactions.TransactionAPI` (optional): A signed transaction.
        """
        signature = self.starknet_account.signer.sign_transaction(txn)
        if is_starknet_tx(txn):
            txn.signature = signature

        elif is_base_tx(txn):
            txn = dataclasses.replace(txn, signature=signature)  # type: ignore
        else:
            raise ValueError(f"Unsupported transaction type: {type(txn)}")

        self.check_signature(txn)

        return txn

    def call(self, txn: TransactionAPI, **kwargs) -> ReceiptAPI:  # type: ignore
        txn = self.prepare_transaction(txn, **kwargs)
        if not txn.signature:
            raise ValueError("Transaction must be signed before calling")

        return self.provider.send_transaction(txn)

    def deploy(
        self, contract: ContractContainer, *args, publish: bool = False, **kwargs
    ) -> ContractInstance:
        contract_type = (
            contract.contract_type if isinstance(contract, ContractContainer) else contract
        )

        sierra_code = (
            (contract_type.deployment_bytecode.bytecode or 0)
            if contract_type.deployment_bytecode
            else 0
        )

        sierra_contract_class = create_sierra_compiled_contract(
            compiled_contract=HexBytes(sierra_code)
        )
        class_hash = compute_sierra_class_hash(sierra_contract_class)

        deploy_result = Contract.deploy_contract_v3_sync(
            account=self.starknet_account,
            class_hash=class_hash,
            abi=sierra_contract_class.parsed_abi,
            auto_estimate=True,
            constructor_args=args,
        )

        receipt = self.provider.get_receipt(deploy_result.hash)

        # To interact with just deployed contract get its instance from the deploy_result
        contract = deploy_result.deployed_contract
        instance = self.chain_manager.contracts.instance_from_receipt(receipt, contract_type)
        self.chain_manager.contracts.cache_deployment(instance)

        if publish:
            self.local_project.deployments.track(instance)
            self.provider.network.publish_contract(deploy_result.deployed_contract.address)

        return instance

    @raises_not_implemented
    def deploy_account(self, **kwargs):
        pass

    def declare(self, contract: Union[ContractType, ContractContainer], *args, **kwargs) -> ReceiptAPI:  # type: ignore
        """
        Declares a contract.

        Args:
            contract_type(``ContractType``): Type of contract.
        """
        contract_type = (
            contract.contract_type if isinstance(contract, ContractContainer) else contract
        )
        txn = self.starknet.encode_contract_blueprint(
            contract_type, sender_address=self.starknet_account.address
        )

        return self.call(txn)

    def get_deploy_account_txn(self, salt: Optional[int] = None, calldata: Optional[List] = None):
        txn = StarknetDeployAccountV3.prepare(
            class_hash=self.class_hash,
            contract_address_salt=salt or self.salt,
            constructor_calldata=calldata or self.constructor_calldata,
        )
        return txn

    def prepare_transaction(self, txn: TransactionAPI, **kwargs) -> TransactionAPI:
        """
        Set default values on a transaction.

        Raises:
            ValueError: When transaction is not prepared.

        Args:
            txn (:class:`~ape.api.transactions.TransactionAPI`): The transaction to prepare.

        Returns:
            :class:`~ape.api.transactions.TransactionAPI`
        """

        provider = self.provider  # Assuming this is a method of a class with access to the provider
        # Common operations for all transaction types
        if txn.nonce is None:
            if hasattr(txn, "sender_address"):
                txn.nonce = provider.get_nonce(txn.sender_address)
            else:
                txn.nonce = self.nonce

        auto_estimate = kwargs.get("auto_estimate", True)  # Default to True if not specified

        if isinstance(txn, get_args(DepreciatedTxs)):
            if txn.max_fee is None or txn.max_fee == 0:
                if "max_fee" in kwargs and auto_estimate:
                    raise ValueError("Cannot specify both auto_estimate and max_fee")

                if "max_fee" in kwargs:
                    txn.max_fee = kwargs["max_fee"]
                elif auto_estimate:
                    txn = self.sign_transaction(txn)
                    estimated_fee = self.provider.estimate_gas_cost(txn.to_base_tx())
                    txn.max_fee = int(estimated_fee * 1.5)  # Using 1.5 as a safety multiplier
                else:
                    raise ValueError("Must specify either auto_estimate or max_fee")

        elif isinstance(txn, get_args(NewTxs)):
            if (
                txn.resource_bounds == ResourceBoundsMapping.init_with_zeros()
                or txn.resource_bounds is None
            ):
                if "resource_bounds" in kwargs and auto_estimate:
                    raise ValueError("Cannot specify both auto_estimate and resource_bounds")

                if "resource_bounds" in kwargs:
                    txn.resource_bounds = ResourceBoundsMapping(
                        l1_gas=kwargs["resource_bounds"],
                        l2_gas=ResourceBounds.init_with_zeros(),
                    )
                elif auto_estimate:
                    txn = self.sign_transaction(txn)
                    estimated_fee = self.provider.estimate_gas_cost(txn.to_base_tx())
                    txn.resource_bounds = estimated_fee.to_resource_bounds(
                        amount_multiplier=1.5, unit_price_multiplier=1.5
                    )
                else:
                    raise ValueError("Must specify either auto_estimate or resource_bounds")

        else:
            raise ValueError(f"Unsupported transaction type: {type(txn)}")

        txn.is_prepared = True
        txn = super().prepare_transaction(txn)
        signed_txn = self.sign_transaction(txn)

        return signed_txn

    @property
    def nonce(self) -> int:
        return self.starknet_account.get_nonce_sync()

    def get_fee_estimate(self, txn: TransactionAPI) -> int:
        """
        Get the estimate fee cost for transaction.

        Args:
            txn(``TransactionAPI``): Transaction

        Returns:
            int: Value of estimated gas cost.
        """
        return self.provider.estimate_gas_cost(txn)

    def check_signature(  # type: ignore
        self,
        data: Union[int, List[int], TransactionAPI, StarknetSignableMessage],
        signature: Optional[ECSignature] = None,
    ) -> bool:
        if isinstance(data, TransactionAPI):
            if not signature and data.signature:
                signature = data.signature

            data = to_int(data.txn_hash)

        elif isinstance(data, StarknetSignableMessage):
            data = data.hash

        else:
            data = StarknetSignableMessage(message=data).hash

        signature = [*signature] if signature else []
        if len(signature) == 3:
            # Trim unused version
            signature = signature[1:]

        signature = [to_int(x) for x in signature]
        return verify_ecdsa_sig(to_int(self.starknet_account.signer.public_key), data, signature)


class StarknetDevnetAccount(BaseStarknetAccount):
    contract_address: Optional[str] = Field(default=None, alias="address")
    private_key: str
    pub_key: str = Field(alias="public_key")
    cls_hash: Optional[int] = Field(default=None, alias="class_hash")
    custom_constructor_calldata: Optional[List[Any]] = Field(
        default=None, alias="constructor_calldata"
    )
    custom_salt: Optional[int] = None
    is_deployed: bool = False

    _key_pair: Optional[KeyPair] = None

    @field_validator("contract_address", "pub_key", "private_key", mode="before")
    @classmethod
    def validate_int_to_hex(cls, value):
        return to_hex(value)

    @model_validator(mode="after")
    def initialize_key_pair(self):
        self._key_pair = KeyPair(
            private_key=to_int(self.private_key), public_key=to_int(self.pub_key)
        )
        return self

    @property
    def key_pair(self) -> KeyPair:
        assert self._key_pair is not None, "KeyPair has not been initialized"
        return self._key_pair

    @property
    def public_key(self) -> str:
        return self.pub_key

    @property
    def salt(self) -> int:
        return self.custom_salt if self.custom_salt is not None else DEVNET_CONTRACT_SALT

    @property
    def address(self) -> Optional[str]:  # type: ignore
        if not self.contract_address:
            self.contract_address = self.default_address
        return self.contract_address

    @property
    def class_hash(self) -> Optional[int]:  # type: ignore
        return self.cls_hash

    @property
    def deployments(self) -> List[StarknetAccountDeployment]:
        return (
            [StarknetAccountDeployment(network_name=LOCAL_NETWORK_NAME, contract_address=self.address, salt=self.salt)]  # type: ignore
            if self.is_deployed
            else []
        )

    @property
    def constructor_calldata(self) -> List[Any]:
        return self.custom_constructor_calldata or super().constructor_calldata

    def sign_message(self, msg: StarknetSignableMessage) -> Optional[ECSignature]:  # type: ignore
        msg = StarknetSignableMessage(message=msg)
        signature = message_signature(msg.hash, to_int(self.private_key))
        return signature if self.check_signature(msg, signature) else None

    def add_deployment(self, network_name: str, contract_address: int, salt: int):
        if network_name != LOCAL_NETWORK_NAME:
            raise ValueError("Can only use development accounts on local network.")
        self.contract_address = self.provider.network.ecosystem.decode_address(contract_address)
        self.custom_salt = salt

    class Config:
        arbitrary_types_allowed = True
        populate_by_name = True


class StarknetKeyfileAccount(BaseStarknetAccount):
    """Starknet Keyfile Account"""

    _key_pair: Optional[KeyPair] = None
    key_file_path: Path
    locked: bool = True
    __autosign: bool = False
    __cached_key: Optional[int] = None
    __cached_passphrase: Optional[str] = None

    @classmethod
    def from_file(cls, path: Path):
        """
        Retrieve account from file.

        Args:
            path(``Path``): Location of file where account is saved.
        """
        account_data = json.loads(path.read_text())
        salt = account_data.get("salt")
        if not salt:
            salt = ContractAddressSalt.get_random_value()
            account_data = {**account_data, "salt": salt}
            path.unlink()
            path.write_text(json.dumps(account_data))

        return cls(key_file_path=path, salt=salt)

    @classmethod
    def _from_import(cls, new_path: Path, **kwargs) -> "StarknetKeyfileAccount":
        is_local = kwargs.pop("is_local", False)
        allow_local_file_store = kwargs.pop("allow_local_file_store", False)
        get_pass = kwargs.pop("get_pass")

        if not allow_local_file_store and is_local:
            # NOTE: To create a keyfile account on local networks, use `allow_local_filestore`.
            # Else, it uses a StarknetDevelopmentAccount and no passphrase is necessary.
            passphrase = None

        else:
            passphrase = get_pass()

        kwargs["passphrase"] = passphrase
        new_account = cls(key_file_path=new_path)
        new_account._write(**kwargs)
        return new_account

    @property
    def address(self) -> str:
        if not self.network_manager.active_provider:
            return self.address

        network_name = self.provider.network.name
        deployment = self.get_deployment(network_name)
        if deployment:
            return deployment.contract_address

        # Not yet deployed.
        return self.default_address

    @property
    def alias(self) -> Optional[str]:
        return self.key_file_path.stem

    @cached_property
    def class_hash(self) -> int:
        return self.account_data.get("class_hash", None)  # different

    @cached_property
    def constructor_calldata(self) -> List[Any]:
        return self.account_data.get("constructor_calldata") or super().constructor_calldata

    @cached_property
    def salt(self) -> int:
        if "salt" in self.account_data:
            return self.account_data["salt"]

        # May only get here on outdated accounts.
        return ContractAddressSalt.get_random_value()

    @property
    def public_key(self) -> str:
        if "public_key" not in self.account_data:
            # Migrate keyfile now.
            return self.key_pair.public_key
        return to_hex(self.account_data["public_key"])

    @property
    def key_pair(self) -> KeyPair:
        if self._key_pair is None:
            private_key, passphrase = self.__get_private_key()
            key_pair = create_keypair(private_key)
            self._write(
                passphrase=passphrase,
                public_key=key_pair.public_key,
                private_key=key_pair.private_key,
            )
            self._key_pair = key_pair
        return self._key_pair

    # @property
    # def nonce(self) -> int:
    #     return super().nonce if self.deployed else 0

    def _prompt_to_sign(self, signable: Union[TransactionAPI, StarknetSignableMessage]) -> bool:
        return click.confirm(f"{signable}\n\nSign: ")

    def sign_message(self, msg: StarknetSignableMessage) -> Optional[ECSignature]:  # type: ignore[override]
        msg = StarknetSignableMessage(message=msg)

        if not self.__autosign and not self._prompt_to_sign(msg):
            raise Exception("The message was not signed.")

        private_key, _ = self.__get_private_key()
        signature = message_signature(msg.hash, private_key)
        return signature if self.check_signature(msg, signature) else None

    @property
    def deployments(self) -> List[StarknetAccountDeployment]:
        deployments = self.account_data.get("deployments", [])

        for deployment in deployments:
            if "salt" not in deployment and to_int(deployment["contract_address"]) == to_int(
                self.default_address
            ):
                deployment["salt"] = self.salt

        return [StarknetAccountDeployment(**d) for d in deployments]

    @property
    def deployed(self, network_name: Optional[str] = None) -> bool:
        network_name = network_name or self.provider.network.name
        network_deployments = [d for d in self.deployments if d.network_name == network_name]
        return len(network_deployments) > 0

    @raises_not_implemented
    def prepare_transaction(self, txn: TransactionAPI, **kwargs):
        pass

    def _write(
        self,
        passphrase: Optional[str] = None,
        new_passphrase: Optional[str] = None,
        public_key: Optional[int] = None,
        private_key: Optional[int] = None,
        class_hash: Optional[int] = None,
        deployments: Optional[List[Union[StarknetAccountDeployment]]] = None,
        salt: Optional[int] = None,
        constructor_calldata: Optional[List[Any]] = None,
        leave_unlocked: Optional[bool] = None,
    ):
        if not private_key:
            # Will either prompt or use cached if unlocked.
            private_key, passphrase = self.__get_private_key(
                passphrase=passphrase, leave_unlocked=leave_unlocked
            )

        passphrase_to_use = (
            new_passphrase
            if new_passphrase is not None
            else self.__get_passphrase(passphrase=passphrase)
        )

        key_file_data = self.__encrypt_key_file(
            passphrase_to_use, private_key=private_key, leave_unlocked=leave_unlocked
        )
        account_data = {**self.account_data}
        if public_key or "public_key" not in account_data:
            # Real public key to use is different than the one for the keyfile.
            public_key = public_key or create_keypair(private_key).public_key
            account_data["public_key"] = public_key

        if deployments:
            # Add deployments
            deployments_to_save: List[Dict] = []
            for deployment in deployments:
                if any([d == deployment for d in deployments_to_save]):
                    # Already known.
                    continue

                deployments_to_save.append(deployment.dict())

            account_data["deployments"] = deployments_to_save

        if class_hash:
            account_data["class_hash"] = class_hash
        if salt:
            account_data["salt"] = salt
        if constructor_calldata:
            account_data["constructor_calldata"] = constructor_calldata

        data = {**key_file_data, APP_KEY_FILE_KEY: account_data}
        self.key_file_path.write_text(json.dumps(data))

    @property
    def keyfile_data(self) -> Dict:
        """
        Keyfile data for accounts saved as a dictionary.
        """
        if self.key_file_path.is_file():
            return json.loads(self.key_file_path.read_text())

        return {}

    @property
    def account_data(self) -> Dict:
        """Get the account data of a single account."""
        return self.keyfile_data.get(APP_KEY_FILE_KEY, {})

    def delete(
        self,
        address: Optional[Union[str, int]] = None,
        networks: Optional[Union[str, List[str]]] = None,
        leave_unlocked: Optional[bool] = None,
    ):
        """
        Delete an account.

        Args:
            address(Optional[Union[str, int]]):
                Address of account. Defaults to ``None``.
            networks(Optional[Union[str, List[str]]]):
                List of networks to delete account from. Defaults to ``None``.
            leave_unlocked(Optional[bool]): Option to leave account unlocked. Defaults to ``None``.
        """
        if not self.key_file_path.is_file():
            logger.warning(f"Keyfile for account '{self.alias}' already deleted.")
            return

        passphrase = click.prompt(
            f"Enter passphrase to delete '{self.alias}'",
            hide_input=True,
            default="",  # Allow for empty passphrases.
        )
        self.__decrypt_key_file(passphrase)
        deployments_at_start = len(self.deployments)
        if not networks and not address:
            remaining_deployments: List[StarknetAccountDeployment] = []

        else:
            address = to_int(self.address) if address is None else to_int(address)
            networks = None if networks is None else [clean_network_name(n) for n in networks]

            def deployment_filter(deployment: StarknetAccountDeployment) -> bool:
                deploy_address = to_int(deployment.contract_address)
                if networks and not address:
                    return deployment.network_name in networks

                elif address and not networks:
                    return deploy_address == address

                return deploy_address == address and deployment.network_name in (networks or [])

            remaining_deployments = [
                x for x in self.deployments if x not in filter(deployment_filter, self.deployments)
            ]

        if remaining_deployments and len(remaining_deployments) < deployments_at_start:
            self._write(
                passphrase=passphrase,
                deployments=remaining_deployments,
                leave_unlocked=leave_unlocked,
            )

        elif remaining_deployments:
            err_msg = f"Deletion failed. Deployment(s) not found (alias={self.alias}"
            if address:
                err_msg = f"{err_msg}, address={address}"
            if networks:
                net_str = ",".join(networks)
                err_msg = f"{err_msg}, networks={net_str}"

            # raise StarknetAccountsError(f"{err_msg})")
            raise Exception(f"{err_msg})")

        elif click.confirm(f"Completely delete local key for account '{self.address}'?"):
            # Delete entire account JSON if no more deployments.
            # The user has to agree to an additional prompt since this may be very destructive.
            self.key_file_path.unlink()

    def change_password(self, leave_unlocked: Optional[bool] = None):
        """
        Change password for account.
        **NOTE**: User must enter passphrase even if unlocked

        Args:
            leave_unlocked(Optional[bool]):
                Option to leave account unlocked after changing password.
        """
        # NOTE: User must enter passphrase even if unlocked.
        original_passphrase = self._get_passphrase_from_prompt("Enter original passphrase")
        private_key, _ = self.__get_private_key(
            passphrase=original_passphrase, leave_unlocked=leave_unlocked
        )
        new_passphrase = self._get_passphrase_from_prompt("Enter new passphrase")
        self._write(
            passphrase=original_passphrase,
            new_passphrase=new_passphrase,
            private_key=private_key,
        )

    def add_deployment(
        self,
        network_name: str,
        contract_address: int,
        salt: int,
        leave_unlocked: Optional[bool] = None,
    ):
        """
        Add deployment status for account.

        Args:
            network_name (str): Name of connected network.
            contract_address (str): Contract address for account.
            salt (int): Contract address salt. Needed if wanting to deploy to a different address.
            leave_unlocked (Optional[bool]): Option to leave account unlocked after deployment.
        """
        if any([d == f"{network_name}:{contract_address}" for d in self.deployments]):
            logger.warning("Deployment already added.")
            return

        new_deployment = StarknetAccountDeployment(
            network_name=network_name, contract_address=to_hex(contract_address), salt=salt
        )
        deployments = [*self.deployments, new_deployment]
        self._write(deployments=deployments, leave_unlocked=False)

    def unlock(self, prompt: Optional[str] = None, passphrase: Optional[str] = None):
        """
        Unlock an account.

        Args:
            prompt (Optional[str]): Prompt message. Defaults to ``None``.
            passphrase (Optional[str]): Passphrase that is used to unlock the account.
                Defaults to ``None``.
        """
        if not self.__cached_key or not self.__cached_passphrase:
            # Sets cached keys.
            self.__get_private_key(prompt=prompt, passphrase=passphrase, leave_unlocked=True)

        self.locked = False

    def set_autosign(self, enabled: bool, passphrase: Optional[str] = None):
        """
        Unlock keyfile accounts with set autosign message.

        Args:
            enabled (bool): Set True/False to enable autosign for account.
            passphrase (Optional[str]): Set passphrase to unlock account if password exists.
        """
        if enabled:
            self.unlock(passphrase=passphrase)
            logger.warning("Danger! This account will now sign any transaction its given.")
        else:
            self.lock()

        self.__autosign = enabled

    def lock(self):
        """Lock the account and removes cached key and passphrase."""
        self.locked = True
        self.__cached_key = None
        self.__cached_passphrase = None

    def get_deployment(self, network_name: str) -> Optional[StarknetAccountDeployment]:
        """
        Gets deployment status from the network.

        Args:
            network_name (str): Name of network being looked at for deployment status.

        Returns:
            Optional[:class:`~ape_starknet.accounts.StarknetAccountDeployment`]
        """
        return next(
            filter(lambda d: d is not None and d.network_name in network_name, self.deployments),
            None,
        )

    def __get_private_key(
        self,
        prompt: Optional[str] = None,
        passphrase: Optional[str] = None,
        leave_unlocked: Optional[bool] = None,
    ) -> Tuple[int, Optional[str]]:
        if self.__cached_key is not None:
            if not self.locked:
                click.echo(f"Using cached key for '{self.alias}'")
                return self.__cached_key, self.__cached_passphrase
            else:
                # Only use the cached private key if unlocked.
                self.__cached_key = None
                self.__cached_passphrase = None

        passphrase = self.__get_passphrase(prompt=prompt, passphrase=passphrase)
        if self.key_file_path.is_file():
            key_hex_str = self.__decrypt_key_file(passphrase).hex()
            private_key = to_int(key_hex_str)
        else:
            raise Exception(
                f"Keyfile for account '{self.alias}' missing. "
                "Either replace or use the `delete` command to get rid of this account."
            )

        self.__cached_key = private_key
        if passphrase is not None:
            self.__cached_passphrase = passphrase

        if self.locked:
            self.locked = (
                not click.confirm(f"Leave '{self.alias}' unlocked?")
                if leave_unlocked is None
                else leave_unlocked
            )

        return private_key, passphrase

    def __get_passphrase(
        self, prompt: Optional[str] = None, passphrase: Optional[str] = None
    ) -> str:
        passphrase = passphrase if passphrase is not None else self.__cached_passphrase
        return (
            passphrase
            if passphrase is not None
            else self._get_passphrase_from_prompt(message=prompt)
        )

    def _get_passphrase_from_prompt(self, message: Optional[str] = None) -> str:
        message = message or f"Enter passphrase to unlock '{self.alias}'"
        return click.prompt(
            message,
            hide_input=True,
            default="",  # Just in case there's no passphrase
            show_choices=False,
        )

    def __encrypt_key_file(
        self,
        passphrase: str,
        private_key: Optional[int] = None,
        leave_unlocked: Optional[bool] = None,
    ) -> Dict:
        private_key = (
            self.__get_private_key(passphrase=passphrase, leave_unlocked=leave_unlocked)[0]
            if private_key is None
            else private_key
        )
        key_str = pad_hex_str(HexBytes(private_key).hex())
        passphrase_bytes = text_if_str(to_bytes, passphrase)
        return create_keyfile_json(HexBytes(key_str), passphrase_bytes, kdf="scrypt")  # type: ignore

    def __decrypt_key_file(self, passphrase: str) -> HexBytes:
        key_file_dict = json.loads(self.key_file_path.read_text())
        password_bytes = text_if_str(to_bytes, passphrase)
        decoded_json = decode_keyfile_json(key_file_dict, password_bytes)  # type: ignore
        return HexBytes(decoded_json)
