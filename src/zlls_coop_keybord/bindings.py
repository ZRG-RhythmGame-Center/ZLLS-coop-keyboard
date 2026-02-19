"""Bindings 模块：监听键与操作序列，根据配置决定是否处理按键及触发的 actions。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 已知特殊键名（与 pynput _normalize_key 输出一致）：无 Key 前缀的补上
_SPECIAL_KEYS = frozenset({
    "enter", "tab", "space", "backspace", "esc", "escape",
    "shift", "ctrl", "control", "alt", "meta", "cmd",
    "up", "down", "left", "right",
    "home", "end", "page_up", "page_down", "insert", "delete",
    "caps_lock", "num_lock", "scroll_lock", "pause", "print_screen",
    *(f"f{i}" for i in range(1, 21)),
})


def _normalize_base_key(key_str: str) -> str:
    """将配置或事件中的键名统一为可比较形式（与 keyboard_events._normalize_key 对齐）。"""
    if not key_str:
        return ""
    s = key_str.strip()
    if not s:
        return ""
    # 单字符（如 'a'）保持小写以便与 "KeyA" 统一
    if len(s) == 1:
        return s.lower()
    if s.startswith("Key") and len(s) > 3:
        rest = s[3:]
        if len(rest) == 1:
            return rest.lower()  # KeyA <-> a
        return s  # KeyF1, Keyenter 等
    low = s.lower()
    if low in _SPECIAL_KEYS:
        return "Key" + (s[0].upper() + s[1:].lower() if len(s) > 1 else s.upper())
    return s


def _parse_trigger(trigger_str: str) -> tuple[frozenset[str], str]:
    """解析 bindings 的 key，如 'Ctrl+KeyA' -> (frozenset({'ctrl'}), 'KeyA')，'KeyF1' -> (frozenset(), 'KeyF1')。"""
    parts = [p.strip() for p in trigger_str.split("+") if p.strip()]
    if not parts:
        return frozenset(), ""
    mods = {"ctrl", "alt", "shift", "meta"}
    modifiers: set[str] = set()
    base_key = ""
    for p in parts:
        low = p.lower()
        if low in ("ctrl", "control"):
            modifiers.add("ctrl")
        elif low == "alt":
            modifiers.add("alt")
        elif low == "shift":
            modifiers.add("shift")
        elif low in ("meta", "win", "cmd"):
            modifiers.add("meta")
        else:
            base_key = _normalize_base_key(p)
    return frozenset(modifiers), base_key


@dataclass
class Action:
    """单条操作：向某 target 发送 key 的 event。"""

    target: str
    key: str
    event: str  # "down" | "up" | "both"


class Bindings:
    """从配置加载 listen_keys 与 bindings，提供 should_handle 与 get_actions。"""

    def __init__(self, config: dict[str, Any]) -> None:
        ctrl = config.get("controller") or {}
        # 未配置 = 不监听（None 或缺失时用空列表）
        raw = ctrl.get("listen_keys")
        self._listen_keys: list[str] | str = raw if raw is not None else []
        raw_bindings = ctrl.get("bindings") or {}
        # 解析为 (modifiers, base_key) -> (on, actions)；同一 base_key 多条时修饰多的优先
        self._by_trigger: dict[tuple[frozenset[str], str], tuple[str, list[Action]]] = {}
        for trigger_str, binding in raw_bindings.items():
            if not isinstance(binding, dict):
                continue
            mods, base = _parse_trigger(trigger_str)
            if not base:
                continue
            on = (binding.get("on") or "down").lower()
            if on not in ("down", "up", "both"):
                on = "down"
            actions: list[Action] = []
            for a in binding.get("actions") or []:
                if isinstance(a, dict) and a.get("target") and a.get("key"):
                    actions.append(Action(
                        target=str(a["target"]),
                        key=str(a["key"]),
                        event=(a.get("event") or "down").lower() or "down",
                    ))
            if actions:
                self._by_trigger[(mods, base)] = (on, actions)
        logger.debug("bindings loaded: listen_keys=%s, triggers=%s", self._listen_keys, list(self._by_trigger.keys()))

    def should_handle(self, key_str: str, modifiers: set[str] | frozenset[str] | None = None) -> bool:
        """当前键是否在 listen_keys 内（modifiers 不参与是否处理的判断）。"""
        if self._listen_keys == "*":
            return True
        if isinstance(self._listen_keys, list):
            norm = _normalize_base_key(key_str)
            for k in self._listen_keys:
                if _normalize_base_key(k) == norm:
                    return True
            return False
        return False

    def get_actions(
        self,
        trigger_key_str: str,
        modifiers: set[str] | frozenset[str] | None,
        event_type: str,
    ) -> list[Action]:
        """根据触发键、当前修饰键、事件类型返回要执行的操作列表。先匹配「修饰+键」，再匹配「仅键」；同一键最多命中一条。"""
        mods = frozenset((modifiers or {}))
        base = _normalize_base_key(trigger_key_str)
        if not base:
            return []
        # 1) 先匹配「修饰+键」精确项
        if (mods, base) in self._by_trigger:
            on, actions = self._by_trigger[(mods, base)]
            if on == "both" or on == event_type:
                return list(actions)
            return []
        # 2) 再匹配「仅键」
        if (frozenset(), base) in self._by_trigger:
            on, actions = self._by_trigger[(frozenset(), base)]
            if on == "both" or on == event_type:
                return list(actions)
            return []
        return []
