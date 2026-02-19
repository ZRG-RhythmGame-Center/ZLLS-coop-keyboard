"""协议与消息格式：按键事件 JSON 行。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOL_ID_STR = "/zlls-coop-keyboard/1.0.0"


@dataclass
class KeyEvent:
    """统一按键事件（设计中的 KeyEvent）。"""

    event: str  # "down" | "up"
    key: str
    modifiers: list[str] | None = None
    type: str = "key_event"

    def to_json_line(self) -> bytes:
        d = asdict(self)
        return (json.dumps(d, ensure_ascii=False) + "\n").encode("utf-8")

    @classmethod
    def from_json_line(cls, line: bytes) -> KeyEvent | None:
        try:
            d = json.loads(line.decode("utf-8").strip())
            if d.get("type") != "key_event":
                return None
            return cls(
                event=d.get("event", "down"),
                key=d.get("key", ""),
                modifiers=d.get("modifiers"),
                type=d.get("type", "key_event"),
            )
        except Exception as e:
            logger.debug("parse key_event failed: %s", e)
            return None
