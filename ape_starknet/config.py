from __future__ import annotations

from typing import Any, Dict, Optional

from ape.api.config import PluginConfig
from ape.api.networks import LOCAL_NETWORK_NAME
from pydantic import Field, field_validator


class NetworkConfig(PluginConfig):
    default_provider: str = "starknet"


class ProviderConfig(PluginConfig):
    mainnet: Dict[str, Any] = Field(
        default_factory=lambda: {
            "uri": "https://starknet-mainnet.public.blastapi.io/rpc/v0_7",
        },
        description="Configuration for the mainnet network",
    )
    testnet: Dict[str, Any] = Field(
        default_factory=lambda: {
            "uri": "https://starknet-sepolia.public.blastapi.io/rpc/v0_7",
        },
        description="Configuration for the testnet network",
    )

    integration: Dict[str, Any] = Field(
        default_factory=lambda: {"uri": "https://rpc.nethermind.io/integration-sepolia-juno"},
        description="Configuration for the integration network",
    )
    local: Dict[str, Any] = Field(
        default_factory=lambda: {"uri": f"http://127.0.0.1:5050"},
        description="Configuration for the local network",
    )
    custom: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Configuration for a custom network",
    )

    @field_validator("custom")
    def validate_custom_config(cls, v):
        if v is not None:
            if "uri" not in v:
                raise ValueError("Custom network configuration must include a 'uri' field")
        return v


class StarknetConfig(PluginConfig):
    mainnet: NetworkConfig = NetworkConfig()  # type: ignore
    testnet: NetworkConfig = NetworkConfig()  # type: ignore
    local: NetworkConfig = NetworkConfig()  # type: ignore
    default_network: str = LOCAL_NETWORK_NAME
    custom_networks: NetworkConfig = NetworkConfig()  # type: ignore
    provider: ProviderConfig = ProviderConfig()  # type: ignore
