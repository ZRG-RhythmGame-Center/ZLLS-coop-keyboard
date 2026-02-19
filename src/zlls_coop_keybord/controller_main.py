"""Controller 端入口：启动 Host，按 bindings 将本地按键转发到多台 Receiver。"""

from __future__ import annotations

import argparse
import logging
import queue
import sys

import trio

from .bindings import Bindings
from .config import load, DEFAULT_CONFIG_PATH
from .keyboard_events import raw_to_key_event, start_capture
from .p2p import (
    connect_and_send_key_event,
    create_listen_addr,
    create_host,
    get_first_listen_addr,
)
from .protocol import KeyEvent
from .targets import resolve as resolve_target

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_args():
    p = argparse.ArgumentParser(description="zlls-coop-keyboard Controller")
    p.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    p.add_argument(
        "--port",
        type=int,
        default=0,
        help="Listen port (0 = random)",
    )
    return p.parse_args()


async def _async_main() -> None:
    args = _parse_args()
    config_path = args.config or DEFAULT_CONFIG_PATH
    try:
        config = load(config_path)
    except FileNotFoundError:
        logger.error("Config not found: %s", config_path)
        sys.exit(1)
    except ValueError as e:
        logger.error("Invalid config: %s", e)
        sys.exit(1)

    if config.get("mode") != "controller":
        logger.error("Config mode must be 'controller'")
        sys.exit(1)

    bindings = Bindings(config)
    event_queue: queue.Queue = queue.Queue()
    start_capture(event_queue)
    host = create_host()

    async with host.run([create_listen_addr(args.port)]):
        logger.info("Controller listening on %s", get_first_listen_addr(host))
        while True:
            raw = await trio.to_thread.run_sync(event_queue.get)
            key_str = raw_to_key_event(raw).key
            if not bindings.should_handle(key_str, raw.modifiers):
                continue
            actions = bindings.get_actions(key_str, raw.modifiers, raw.event_type)
            if not actions:
                continue
            send_list: list[tuple[str, KeyEvent]] = []
            for a in actions:
                addr = resolve_target(config, a.target)
                if not addr:
                    logger.warning("target %r not resolved, skip action key=%s", a.target, a.key)
                    continue
                if a.event == "both":
                    send_list.append((addr, KeyEvent(event="down", key=a.key, modifiers=None)))
                    send_list.append((addr, KeyEvent(event="up", key=a.key, modifiers=None)))
                else:
                    send_list.append((addr, KeyEvent(event=a.event, key=a.key, modifiers=None)))
            for addr, ev in send_list:
                try:
                    await connect_and_send_key_event(host, addr, ev)
                except Exception as e:
                    logger.warning("Send to %s failed: %s", addr[:50], e)


def main() -> None:
    trio.run(_async_main)


if __name__ == "__main__":
    main()
