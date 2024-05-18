from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from ape.exceptions import ContractLogicError, NetworkError
from ape.utils.misc import cached_property
from starknet_py.contract import Contract
from starknet_py.net.account.account import Account

from ape_starknet.basemodel import StarknetBase
from ape_starknet.utils import FeeTokenType, NetworkType

if TYPE_CHECKING:
    from ape_starknet.accounts import BaseStarknetAccount
from typing import Union

from starknet_py.contract import ContractFunction
from starknet_py.net.account.base_account import BaseAccount
from starknet_py.net.client import Client
from starknet_py.proxy.contract_abi_resolver import ProxyConfig


class StarknetContract(Contract):
    """
    A wrapper for starknet_py's Contract class that provides dot notation access to contract methods.
    """

    def __init__(
        self,
        address: str,
        abi: list,
        provider: Union[BaseAccount, Client],
        *,
        cairo_version: int = 0,
    ):
        """
        Initialize the StarknetContract.

        Args:
            address: Contract's address
            abi: Contract's ABI
            provider: BaseAccount or Client used to perform transactions
            cairo_version: Version of Cairo the contract is written in
        """

        from ape_starknet.accounts import BaseStarknetAccount
        from ape_starknet.provider import StarknetProvider

        if isinstance(provider, BaseStarknetAccount):
            provider = provider.starknet_account

        elif isinstance(provider, StarknetProvider):
            provider = provider.starknet_client

        elif isinstance(provider, Client):
            provider = provider

        elif isinstance(provider, BaseAccount):
            provider = provider

        else:
            raise ValueError("Provider must be an Account or Provider instance")

        super().__init__(address=address, abi=abi, provider=provider, cairo_version=cairo_version)

        # Set up contract functions as attributes for dot notation access
        for func_name, func in self._functions.items():
            if not hasattr(self, func_name):
                setattr(self, func_name, DotNotationFunction(func))

    @staticmethod
    def from_address(
        address: str,
        provider: Union[BaseAccount, Client] = None,
        proxy_config: Union[bool, ProxyConfig] = False,
    ) -> "StarknetContract":
        """
        Create contract instance from address. Will fetch the ABI from the network.

        Args:
            address: Contract's address
            provider: BaseAccount or Client instance
            proxy_config: Proxy configuration for resolving proxied contracts

        Returns:
            StarknetContract instance
        """

        from ape_starknet.accounts import BaseStarknetAccount
        from ape_starknet.provider import StarknetProvider

        if isinstance(provider, BaseStarknetAccount):
            provider = provider.starknet_account

        elif isinstance(provider, StarknetProvider):
            provider = provider.starknet_client

        elif isinstance(provider, Client):
            provider = provider

        elif isinstance(provider, BaseAccount):
            provider = provider

        else:
            raise ValueError("Provider must be an Account or Provider instance")

        contract = Contract.from_address_sync(
            address=address, provider=provider, proxy_config=proxy_config
        )

        return StarknetContract(
            address=contract.address,
            abi=contract.data.abi,
            provider=provider,
            cairo_version=contract.data.cairo_version,
        )


class DotNotationFunction:
    """
    Wrapper for ContractFunction that enables dot notation access.
    """

    def __init__(self, contract_function: ContractFunction):
        self._function = contract_function

    def __call__(self, *args, **kwargs):
        """
        Handle function calls with both positional and keyword arguments.
        Intelligently routes to the appropriate invoke method based on kwargs.
        """
        # Extract transaction-specific kwargs
        tx_kwargs = {}
        for key in ["max_fee", "auto_estimate", "l1_resource_bounds"]:
            if key in kwargs:
                tx_kwargs[key] = kwargs.pop(key)

        # Check if it's a view function
        is_view = self._function.abi.get("stateMutability") == "view"

        if is_view:
            return self._function.call_sync(*args, **kwargs)

        # Handle state-modifying functions
        if "version" in kwargs and kwargs.pop("version") == 1:
            return self._function.invoke_v1_sync(*args, **{**kwargs, **tx_kwargs})
        else:
            return self._function.invoke_v3_sync(*args, **{**kwargs, **tx_kwargs})

    def call(self, *args, **kwargs):
        """
        Explicit call method for view functions.
        """
        return self._function.call_sync(*args, **kwargs)

    def invoke_v1(self, *args, **kwargs):
        """
        Explicit invoke_v1 method.
        """
        return self._function.invoke_v1_sync(*args, **kwargs)

    def invoke_v3(self, *args, **kwargs):
        """
        Explicit invoke_v3 method.
        """
        return self._function.invoke_v3_sync(*args, **kwargs)

    def prepare_invoke_v1(self, *args, **kwargs):
        """
        Prepare invoke v1 transaction without sending it.
        """
        return self._function.prepare_invoke_v1_sync(*args, **kwargs)

    def prepare_invoke_v3(self, *args, **kwargs):
        """
        Prepare invoke v3 transaction without sending it.
        """
        return self._function.prepare_invoke_v3_sync(*args, **kwargs)

    def prepare_call(self, *args, **kwargs):
        """
        Prepare call without executing it.
        """
        return self._function.prepare_call_sync(*args, **kwargs)


class PredeployedContracts:
    UDC: Dict[str, str] = {
        "mainnet": "0x041a78e741e5af2fec34b695679bc6891742439f7afb8484ecd7766661ad02bf",
        "testnet": "0x041a78e741e5af2fec34b695679bc6891742439f7afb8484ecd7766661ad02bf",
        "local": "0x41A78E741E5AF2FEC34B695679BC6891742439F7AFB8484ECD7766661AD02BF",
    }
    FEETOKEN_ETH: Dict[str, str] = {
        "mainnet": "0x049d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7",
        "testnet": "0x049d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7",
        "local": "0x49D36570D4E46F48E99674BD3FCC84644DDD6B96F7C741B1562B82F9E004DC7",  # hardcoded in devnet
    }
    FEETOKEN_STRK: Dict[str, str] = {
        "mainnet": "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
        "testnet": "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
        "local": "0x4718F5A0FC34CC1AF16A1CDEE98FFB20C31F5CD61D6AB07201858F4287C938D",  # hardcoded in devnet
    }

    @classmethod
    def set_local_contract_address(cls, contract_name: str, address: str) -> None:
        """
        Set the local contract address for the given contract name.

        Args:
            contract_name (str): The name of the contract.
            address (str): The address to set.
        """
        if not hasattr(cls, contract_name):
            raise ValueError(f"Unknown contract name '{contract_name}'")
        getattr(cls, contract_name)["local"] = address

    @classmethod
    def get_address(cls, contract_name: str, network_name: str) -> str:
        """
        Get the contract address for a given contract name and network.

        Args:
            contract_name (str): The name of the contract.
            network_name (str): The name of the network.

        Returns:
            str: The contract address.
        """
        if not hasattr(cls, contract_name):
            raise ValueError(f"Unknown contract name '{contract_name}'")
        contract_dict = getattr(cls, contract_name)
        address = contract_dict.get(network_name)
        if address is None:
            raise ValueError(
                f"Contract address for '{contract_name}' is not set for network '{network_name}'"
            )
        return address


class StarknetContractBase(StarknetBase):
    @cached_property
    def network_type(self) -> NetworkType:
        network_name = self.network_manager.provider.network.name
        try:
            return NetworkType(network_name)
        except ValueError:
            raise NetworkError(f"Unsupported Starknet network: '{network_name}'")

    def get_contract(
        self,
        contract_name: str,
        account: Optional[Account | BaseStarknetAccount] = None,
    ) -> Contract:
        try:
            # Get the contract address from StarknetContracts
            network_name = self.provider.network.name
            address = PredeployedContracts.get_address(contract_name, network_name)
        except AttributeError:
            raise ContractLogicError(f"Contract '{contract_name}' not found in StarknetContracts.")
        except ValueError as e:
            raise ContractLogicError(str(e)) from e

        return StarknetContract.from_address(address=address, provider=account or self.provider)


class StarknetUDCContractClass(StarknetContractBase):
    def udc_contract(self, account: Optional[Account | BaseStarknetAccount] = None) -> Contract:
        """
        Fetch the UDC contract for the current network.

        Args:
            account (Optional[Account | BaseStarknetAccount]): The account to use as the provider.

        Returns:
            Contract: The UDC contract.
        """
        return self.get_contract("UDC", account)

    def get_address(self) -> int:
        """
        Get the UDC contract address for the current network.

        Returns:
            int: The UDC contract address.
        """
        contract = self.udc_contract()
        return contract.address


class StarknetFeeTokenContractClass(StarknetContractBase):
    def feetoken_contract(
        self,
        account: Optional[Account | BaseStarknetAccount] = None,
        token_type: FeeTokenType = FeeTokenType.STRK,
    ) -> Contract:
        """
        Fetch the fee token contract for the current network.

        Args:
            account (Optional[Account | BaseStarknetAccount]): The account to use as the provider.
            token_type (FeeTokenType): The type of token to fetch (ETH or STRK).

        Returns:
            Contract: The fee token contract.
        """
        contract_name = "FEETOKEN_ETH" if token_type == FeeTokenType.ETH else "FEETOKEN_STRK"
        return self.get_contract(contract_name, account)

    def get_address(self, token_type: FeeTokenType = FeeTokenType.STRK) -> int:
        """
        Get the fee token contract address for the current network.

        Args:
            token_type (FeeTokenType): The type of token to fetch (ETH or STRK).

        Returns:
            int: The fee token contract address.
        """
        contract = self.feetoken_contract(token_type=token_type)
        return contract.address
