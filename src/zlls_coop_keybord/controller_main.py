"""Controller 端入口：启动 Host，连接配置中的 Receiver，将本地按键转为 JSON 发送。"""

from __future__ import annotations

import argparse
import logging
import queue
import sys

import trio

from .config import load, DEFAULT_CONFIG_PATH
from .keyboard_events import raw_to_key_event, start_capture
from .p2p import (
    connect_and_send_key_event,
    create_listen_addr,
    create_host,
    get_first_listen_addr,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _first_peer_address(config: dict) -> str | None:
    """从 controller.peers 中取第一个 peer 的 address。"""
    controller = config.get("controller") or {}
    peers = controller.get("peers") or {}
    for v in peers.values():
        if isinstance(v, dict) and v.get("address"):
            return v["address"]
    return None


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

    peer_addr = _first_peer_address(config)
    if not peer_addr:
        logger.error("controller.peers must contain at least one peer with 'address'")
        sys.exit(1)

    event_queue: queue.Queue = queue.Queue()
    start_capture(event_queue)
    host = create_host()

    async with host.run([create_listen_addr(args.port)]):
        logger.info("Controller listening on %s", get_first_listen_addr(host))
        logger.info("Sending key events to %s", peer_addr)
        while True:
            raw = await trio.to_thread.run_sync(event_queue.get)
            ev = raw_to_key_event(raw)
            try:
                await connect_and_send_key_event(host, peer_addr, ev)
            except Exception as e:
                logger.warning("Send failed: %s", e)


def main() -> None:
    trio.run(_async_main)


if __name__ == "__main__":
    main()
