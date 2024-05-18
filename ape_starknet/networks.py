from __future__ import annotations

from ape.api.networks import LOCAL_NETWORK_NAME, NetworkAPI
from starknet_py.net.models import StarknetChainId

NETWORKS: dict = {
    "mainnet": StarknetChainId.MAINNET,
    "testnet": StarknetChainId.SEPOLIA,
    "integration": StarknetChainId.SEPOLIA_INTEGRATION,
    LOCAL_NETWORK_NAME: StarknetChainId.SEPOLIA,  # Use testnet for local network
}


def create_starknet_network(chain_id: int, network_id: int):
    class StarknetNetwork(NetworkAPI):
        @property
        def chain_id(self) -> int:
            return chain_id

        @property
        def network_id(self) -> int:
            return network_id

    return StarknetNetwork
