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


@dataclass
class RawKeyEvent:
    """内部使用：event_type 为 'down' 或 'up'，key 为 pynput 的 Key/KeyCode 或字符。"""

    event_type: str
    key: KeyLike


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
    """在调用线程中运行 pynput 监听，将事件放入 event_queue，直到 stop_event 被 set。"""

    def on_press(key: KeyLike) -> None:
        if stop_event.is_set():
            return
        try:
            event_queue.put(RawKeyEvent("down", key))
        except Exception as e:
            logger.debug("on_press: %s", e)

    def on_release(key: KeyLike) -> None:
        if stop_event.is_set():
            return
        try:
            event_queue.put(RawKeyEvent("up", key))
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
