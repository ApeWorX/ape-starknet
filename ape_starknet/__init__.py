from ape import plugins
from ape.api.networks import LOCAL_NETWORK_NAME, NetworkAPI
from ape.types.address import AddressType

from ape_starknet.accounts import StarknetAccountContainer, StarknetKeyfileAccount
from ape_starknet.config import StarknetConfig
from ape_starknet.constants import PLUGIN_NAME
from ape_starknet.conversion import StarknetAccountConverter, StarknetAddressConverter
from ape_starknet.ecosystems import Starknet
from ape_starknet.networks import NETWORKS, create_starknet_network
from ape_starknet.provider import StarknetDevnetProvider, StarknetProvider


@plugins.register(plugins.ConversionPlugin)
def converters():
    yield AddressType, StarknetAddressConverter
    yield int, StarknetAccountConverter


@plugins.register(plugins.Config)
def config_class():
    return StarknetConfig


@plugins.register(plugins.EcosystemPlugin)
def ecosystems():
    yield Starknet


@plugins.register(plugins.NetworkPlugin)
def networks():
    for network_name, network_id in NETWORKS.items():
        yield PLUGIN_NAME, network_name, create_starknet_network(
            chain_id=network_id, network_id=network_id
        )

    yield PLUGIN_NAME, LOCAL_NETWORK_NAME, NetworkAPI  # local


@plugins.register(plugins.ProviderPlugin)
def providers():
    for network_name in list(NETWORKS.keys()):
        yield PLUGIN_NAME, network_name, StarknetProvider
    yield PLUGIN_NAME, LOCAL_NETWORK_NAME, StarknetDevnetProvider  # local


@plugins.register(plugins.AccountPlugin)
def account_types():
    return StarknetAccountContainer, StarknetKeyfileAccount
