from __future__ import annotations

from datetime import datetime
from enum import Enum

from ape_starknet.utils import to_int

PLUGIN_NAME: str = "starknet"

STARKNET_FEE_TOKEN_SYMBOL: str = "ETH"

MAX_FEE: int = int(1e18)

FEE_MARGIN_OF_ESTIMATION = 1.1

APP_KEY_FILE_KEY = "ape-starknet"


class AccountType(Enum):
    BRAAVOS = "BRAAVOS"
    ARGENT = "ARGENT"
    OPENZEPPELIN = "OPENZEPPELIN"


BRAAVOS_ACCOUNTS = [
    {
        "class_hash": to_int(
            to_int("0x00816dd0297efc55dc1e7559020a3a825e81ef734b558f03c83325d4da7e6253")
        ),
        "cairo_version": "2.5.1",
        "version": "v1.0.0",
        "declared_at": datetime.strptime("February 5, 2024", "%B %d, %Y"),
        "type": AccountType.BRAAVOS,
    }
]


ARGENT_ACCOUNTS = [
    {
        "class_hash": to_int("0x036078334509b514626504edc9fb252328d1a240e4e948bef8d0c08dff45927f"),
        "version": "v0.4.0",
        "cairo_version": "2.6.3",
        "declared_at": datetime.strptime("June 3, 2024", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x029927c8af6bccf3f6fda035981e765a7bdbf18a2dc0d630494f8758aa908e2b"),
        "version": "v0.3.1",
        "cairo_version": "2.4.3",
        "declared_at": datetime.strptime("January 19, 2024", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x01a736d6ed154502257f02b1ccdf4d9d1089f80811cd6acad48e6b6a9d1f2003"),
        "version": "v0.3.0",
        "cairo_version": "2.0.0",
        "declared_at": datetime.strptime("November 23, 2023", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x033434ad846cdd5f23eb73ff09fe6fddd568284a0fb7d1be20ee482f044dabe2"),
        "version": "v0.2.3.1",
        "cairo_version": "0",
        "declared_at": datetime.strptime("November 17, 2022", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x01a7820094feaf82d53f53f214b81292d717e7bb9a92bb2488092cd306f3993f"),
        "version": "v0.2.3",
        "cairo_version": "0",
        "declared_at": datetime.strptime("November 4, 2022", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x03e327de1c40540b98d05cbcb13552008e36f0ec8d61d46956d2f9752c294328"),
        "version": "v0.2.2",
        "cairo_version": "0",
        "declared_at": datetime.strptime("June 16, 2022", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x06a1776964b9f991c710bfe910b8b37578b32b26a7dffd1669a1a59ac94bf82f"),
        "version": "v0.2.1",
        "cairo_version": "0",
        "declared_at": datetime.strptime("April 20, 2022", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x07595b4f7d50010ceb00230d8b5656e3c3dd201b6df35d805d3f2988c69a1432"),
        "version": "v0.2.0",
        "cairo_version": "0",
        "declared_at": datetime.strptime("March 10, 2022", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
    {
        "class_hash": to_int("0x02c3348ad109f7f3967df6494b3c48741d61675d9a7915b265aa7101a631dc33"),
        "version": "v0.1.0",
        "cairo_version": "0",
        "declared_at": datetime.strptime("November 28, 2021", "%B %d, %Y"),
        "type": AccountType.ARGENT,
    },
]


OPENZEPPELIN_ACCOUNTS = [
    {
        "class_hash": to_int("0x005b8b908d16497cd347642c1ab43015b5956e2085aa4c8bb21f0b073015a1c9"),
        "version": "v0.15.1",
        "cairo_version": "2.7.0",
        "declared_at": datetime.strptime("August 09, 2024", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
    {
        "class_hash": to_int("0x04343194a4a6082192502e132d9e7834b5d9bfc7a0c1dd990e95b66f85a66d46"),
        "version": "v0.15.0-rc.0",
        "cairo_version": "2.6.4",
        "declared_at": datetime.strptime("June 15, 2024", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
    {
        "class_hash": to_int("0x00e2eb8f5672af4e6a4e8a8f1b44989685e668489b0a25437733756c5a34a1d6"),
        "version": "v0.14.0",
        "cairo_version": "2.6.3",
        "declared_at": datetime.strptime("May 16, 2024", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
    {
        "class_hash": to_int("0x01e60c8722677cfb7dd8dbea5be86c09265db02cdfe77113e77da7d44c017388"),
        "version": "v0.12.0",
        "cairo_version": "2.6.3",
        "declared_at": datetime.strptime("April 12, 2024", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
    {
        "class_hash": to_int("0x0450f568a8cb6ea1bcce446355e8a1c2e5852a6b8dc3536f495cdceb62e8a7e2"),
        "version": "v0.11.0",
        "cairo_version": "2.6.3",
        "declared_at": datetime.strptime("March 20, 2024", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
    {
        "class_hash": to_int("0x004ca5c0b1af6115858708bd1fabd2e9bb306012b31a741acbf69b8a9f35d688"),
        "version": "v0.10.0",
        "cairo_version": "2.5.3",
        "declared_at": datetime.strptime("February 20, 2024", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
    {
        "class_hash": to_int("0x061dac032f228abef9c6626f995015233097ae253a7f72d68552db02f2971b8f"),
        "version": "v0.8.1",
        "cairo_version": "2.4.1",
        "declared_at": datetime.strptime("January 25, 2024", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
    {
        "class_hash": to_int("0x016c6081eb34ad1e0c5513234ed0c025b3c7f305902d291bad534cd6474c85bc"),
        "version": "v0.8.0",
        "cairo_version": "2.4.1",
        "declared_at": datetime.strptime("December 20, 2023", "%B %d, %Y"),
        "type": AccountType.OPENZEPPELIN,
    },
]
