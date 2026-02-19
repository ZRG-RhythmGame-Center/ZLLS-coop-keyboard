"""Targets 模块：按机器标识（target）从 controller.peers 解析出 multiaddr。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve(config: dict[str, Any], target_id: str) -> str | None:
    """从 config.controller.peers 中按 target 解析出 address（multiaddr）。未配置或不存在返回 None。"""
    controller = config.get("controller") or {}
    peers = controller.get("peers") or {}
    peer = peers.get(target_id)
    if isinstance(peer, dict) and peer.get("address"):
        return peer["address"]
    return None
