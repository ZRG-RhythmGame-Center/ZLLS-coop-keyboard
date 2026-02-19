"""Targets 模块：按机器标识（target）从 controller.peers 或 zeroconf 发现表解析出 multiaddr。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .discovery import DiscoveryTable


def resolve(
    config: dict[str, Any],
    target_id: str,
    discovery_table: "DiscoveryTable | None" = None,
) -> str | None:
    """按 target 解析 address：优先 config.controller.peers，再查 discovery 发现表（若传入）。"""
    controller = config.get("controller") or {}
    peers = controller.get("peers") or {}
    peer = peers.get(target_id)
    if isinstance(peer, dict) and peer.get("address"):
        return peer["address"]
    if discovery_table is not None:
        addr = discovery_table.get(target_id)
        if addr:
            return addr
    return None
