"""Receiver 端入口：启动 Host，注册协议，将收到的按键事件 JSON 打印到控制台。"""

from __future__ import annotations

import argparse
import logging
import sys

import trio

from .config import load, DEFAULT_CONFIG_PATH
from .p2p import (
    create_listen_addr,
    create_host,
    get_first_listen_addr,
    register_keyboard_handler,
    read_json_lines,
)
from .protocol import KeyEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def _keyboard_stream_handler(stream) -> None:
    """协议 handler：按行读 JSON，解析为 KeyEvent 并打印。"""
    try:
        async for line in read_json_lines(stream):
            ev = KeyEvent.from_json_line(line)
            if ev:
                print(f"[key_event] {ev.event} key={ev.key} modifiers={ev.modifiers}")
            else:
                logger.debug("ignored line: %s", line[:80])
    except Exception as e:
        logger.exception("stream handler error: %s", e)
    finally:
        try:
            await stream.close_read()
        except Exception:
            pass


def _parse_args():
    p = argparse.ArgumentParser(description="zlls-coop-keyboard Receiver")
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

    if config.get("mode") != "receiver":
        logger.error("Config mode must be 'receiver'")
        sys.exit(1)

    host = create_host()
    register_keyboard_handler(host, _keyboard_stream_handler)

    async with host.run([create_listen_addr(args.port)]):
        addr = get_first_listen_addr(host)
        print(f"Receiver running. Connect Controller to: {addr}", flush=True)
        print("Waiting for key events...", flush=True)
        await trio.sleep_forever()


def main() -> None:
    trio.run(_async_main)


if __name__ == "__main__":
    main()
