from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ape.utils.basemodel import ManagerAccessMixin

if TYPE_CHECKING:
    from ape_starknet.config import StarknetConfig
    from ape_starknet.ecosystems import Starknet
    from ape_starknet.provider import StarknetProvider


class StarknetBase(ManagerAccessMixin):
    """
    Starknet Base Model
    """

    @property
    def starknet_config(self) -> "StarknetConfig":
        return cast("StarknetConfig", self.config_manager.get_config("starknet"))

    @property
    def starknet(self) -> "Starknet":
        return cast("Starknet", self.network_manager.starknet)

    @property
    def provider(self) -> "StarknetProvider":  # type: ignore
        return cast("StarknetProvider", super().provider)
