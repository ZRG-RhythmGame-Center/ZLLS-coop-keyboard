"""加载与校验配置文件（YAML）。默认 config.yaml，支持 -c/--config 覆盖。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.yaml")


def load(path: Path | str | None = None) -> dict[str, Any]:
    """加载 YAML 配置。path 为 None 时使用默认 config.yaml。"""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Config root must be a YAML mapping")
    _validate(data)
    return data


def _validate(data: dict[str, Any]) -> None:
    """基本校验：mode 必为 controller 或 receiver。"""
    mode = data.get("mode")
    if mode not in ("controller", "receiver"):
        raise ValueError(f"config.mode must be 'controller' or 'receiver', got: {mode!r}")
    if mode == "controller":
        controller = data.get("controller") or {}
        peers = controller.get("peers") or {}
        bindings = controller.get("bindings") or {}
        for trigger_key, binding in bindings.items():
            if not isinstance(binding, dict):
                continue
            actions = binding.get("actions") or []
            for action in actions:
                if isinstance(action, dict) and "target" in action:
                    t = action["target"]
                    if t and t not in peers:
                        logger.warning(
                            "binding target %r not in controller.peers (may rely on discovery)",
                            t,
                        )
