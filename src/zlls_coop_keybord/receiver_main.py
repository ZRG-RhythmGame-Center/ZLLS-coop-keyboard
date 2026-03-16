"""Receiver 端入口：启动 Host，注册协议，将收到的按键事件 JSON 打印到控制台；可选 zeroconf 注册。"""

from __future__ import annotations

import argparse
import logging
import socket
import sys

import trio

from .config import load, DEFAULT_CONFIG_PATH
from .discovery import register_receiver, unregister_receiver
from .keyboard_events import inject
from .p2p import (
    create_listen_addr,
    create_host,
    get_first_listen_addr,
    register_keyboard_handler,
    read_json_lines,
)
from .protocol import ActionEvent, KeyEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _make_stream_handler(keyboard_enabled: bool):
    """根据配置返回 stream handler：keyboard_enabled 时注入按键，否则仅打印。"""

    async def _keyboard_stream_handler(stream) -> None:
        try:
            async for line in read_json_lines(stream):
                ev = KeyEvent.from_json_line(line)
                if ev:
                    if keyboard_enabled:
                        inject(ev)
                    else:
                        print(f"[key_event] {ev.event} key={ev.key} modifiers={ev.modifiers}")
                    continue
                act = ActionEvent.from_json_line(line)
                if act:
                    await _handle_action_event(act)
                    continue
                logger.debug("ignored line: %s", line[:80])
        except Exception as e:
            logger.exception("stream handler error: %s", e)
        finally:
            try:
                await stream.close_read()
            except Exception:
                pass

    return _keyboard_stream_handler


async def _handle_action_event(act: ActionEvent) -> None:
    """根据 ActionEvent 执行 run_command / http_request。"""
    kind = (act.kind or "").lower()
    payload = act.payload or {}
    if kind == "run_command":
        import subprocess
        command = payload.get("command")
        if not command:
            logger.warning("ActionEvent run_command 缺少 command，忽略")
            return
        args = payload.get("args") or []
        cwd = payload.get("cwd") or None
        shell = bool(payload.get("shell", False))
        try:
            cmd_list = [command] + list(args)
            logger.info("ActionEvent run_command: %s (cwd=%r shell=%s)", cmd_list, cwd, shell)
            # 在后台启动进程，不等待完成
            subprocess.Popen(
                cmd_list if not shell else " ".join(cmd_list),
                cwd=cwd,
                shell=shell,
            )
        except Exception as e:
            logger.warning("ActionEvent run_command 执行失败: %s", e)
        return

    if kind == "http_request":
        try:
            import httpx
        except ImportError:
            logger.warning("ActionEvent http_request: httpx 未安装，忽略该动作")
            return
        method = (payload.get("method") or "GET").upper()
        url = payload.get("url")
        if not url:
            logger.warning("ActionEvent http_request 缺少 url，忽略")
            return
        headers = payload.get("headers") or None
        body = payload.get("body", None)

        async def _do_request() -> None:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        content=body if body is not None else None,
                    )
                if 200 <= resp.status_code < 300:
                    logger.info("ActionEvent http_request %s %s -> %s", method, url, resp.status_code)
                else:
                    logger.warning(
                        "ActionEvent http_request 非 2xx: %s %s -> %s",
                        method,
                        url,
                        resp.status_code,
                    )
            except Exception as e:
                logger.warning("ActionEvent http_request 请求失败 %s %s: %s", method, url, e)

        await _do_request()


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

    keyboard_enabled = ((config.get("receiver") or {}).get("keyboard") or {}).get("enable", False)
    identity_id = (
        (config.get("receiver") or {}).get("identity") or {}
    ).get("id") or socket.gethostname()
    host = create_host()
    register_keyboard_handler(host, _make_stream_handler(keyboard_enabled))

    zc, service_info = None, None
    try:
        async with host.run([create_listen_addr(args.port)]):
            addr = get_first_listen_addr(host)
            print(f"Receiver running (id={identity_id!r}). Connect Controller to: {addr}", flush=True)
            zc, service_info = register_receiver(identity_id, addr)
            print("Waiting for key events...", flush=True)
            await trio.sleep_forever()
    finally:
        unregister_receiver(zc, service_info)


def main() -> None:
    trio.run(_async_main)


if __name__ == "__main__":
    main()
