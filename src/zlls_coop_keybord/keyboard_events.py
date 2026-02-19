"""键盘事件采集（Controller 端）：pynput 在独立线程，事件放入 queue 供主循环消费。"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

from pynput import keyboard

from .protocol import KeyEvent

logger = logging.getLogger(__name__)

# pynput 回调可能收到 Key、KeyCode 或 None
KeyLike = keyboard.Key | keyboard.KeyCode | None

# 修饰键名（与 bindings 一致）
MODIFIER_NAMES = frozenset({"ctrl", "alt", "shift", "meta"})


def _key_to_modifier_name(key: KeyLike) -> str | None:
    """若 key 为修饰键则返回统一名称 ctrl/alt/shift/meta，否则返回 None。"""
    if key is None:
        return None
    name: str | None = getattr(key, "name", None)
    if name is None and hasattr(key, "char") and getattr(key, "char") is None:
        return None
    if name is not None:
        n = str(name).lower()
        if n in ("ctrl", "control", "ctrl_l", "ctrl_r"):
            return "ctrl"
        if n in ("alt", "alt_l", "alt_r", "alt_gr"):
            return "alt"
        if n in ("shift", "shift_l", "shift_r"):
            return "shift"
        if n in ("cmd", "cmd_l", "cmd_r", "meta", "win"):
            return "meta"
    return None


@dataclass
class RawKeyEvent:
    """内部使用：event_type 为 'down' 或 'up'，key 为 pynput 的 Key/KeyCode 或字符，modifiers 为当前按下修饰键集合。"""

    event_type: str
    key: KeyLike
    modifiers: frozenset[str] = frozenset()


def _normalize_key(key: KeyLike) -> str:
    """将 pynput 的 key 转为协议中的键名字符串。"""
    if key is None:
        return ""
    if isinstance(key, str):
        return key
    char: Any = getattr(key, "char", None)
    if char is not None:
        return char
    name: Any = getattr(key, "name", None)
    if name is not None:
        return name if str(name).startswith("Key") else f"Key{name}"
    return str(key)


def _run_listener(
    event_queue: queue.Queue[RawKeyEvent],
    stop_event: threading.Event,
) -> None:
    """在调用线程中运行 pynput 监听，将事件放入 event_queue，并维护当前修饰键集合。"""
    current_modifiers: set[str] = set()

    def on_press(key: KeyLike) -> None:
        if stop_event.is_set():
            return
        try:
            mod_name = _key_to_modifier_name(key)
            if mod_name:
                current_modifiers.add(mod_name)
            event_queue.put(RawKeyEvent("down", key, frozenset(current_modifiers)))
        except Exception as e:
            logger.debug("on_press: %s", e)

    def on_release(key: KeyLike) -> None:
        if stop_event.is_set():
            return
        try:
            mod_name = _key_to_modifier_name(key)
            if mod_name:
                current_modifiers.discard(mod_name)
            event_queue.put(RawKeyEvent("up", key, frozenset(current_modifiers)))
        except Exception as e:
            logger.debug("on_release: %s", e)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def start_capture(event_queue: queue.Queue[RawKeyEvent]) -> threading.Event:
    """在后台线程启动键盘监听；返回 stop_event，set() 后监听线程会退出。"""
    stop_event = threading.Event()
    t = threading.Thread(
        target=_run_listener,
        args=(event_queue, stop_event),
        daemon=True,
    )
    t.start()
    return stop_event


def raw_to_key_event(raw: RawKeyEvent) -> KeyEvent:
    """将 RawKeyEvent 转为协议 KeyEvent（modifiers 暂不实现，M1 可省略）。"""
    return KeyEvent(
        event=raw.event_type,
        key=_normalize_key(raw.key),
        modifiers=None,
    )


def _key_name_to_pynput(key_str: str):
    """将协议键名字符串转为 pynput 可用的 key（字符或 Key 枚举）。"""
    if not key_str:
        return None
    if len(key_str) == 1:
        return key_str
    name = key_str.lower().replace("key", "", 1) if key_str.lower().startswith("key") else key_str.lower()
    name = name.replace(" ", "_")
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if hasattr(keyboard.Key, key_str.lower()):
        return getattr(keyboard.Key, key_str.lower())
    return None


def inject(ev: KeyEvent) -> None:
    """在本地模拟按键（Receiver 端）：将 KeyEvent 转为 pynput 的 press/release。"""
    key = _key_name_to_pynput(ev.key)
    if key is None:
        logger.debug("inject: unknown key %r, skip", ev.key)
        return
    try:
        ctrl = keyboard.Controller()
        if ev.event == "down":
            ctrl.press(key)
        elif ev.event == "up":
            ctrl.release(key)
        elif ev.event == "both":
            ctrl.press(key)
            ctrl.release(key)
    except Exception as e:
        logger.warning("inject failed for %s %s: %s", ev.event, ev.key, e)
