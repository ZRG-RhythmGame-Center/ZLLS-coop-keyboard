"""Controller 端入口：启动 Host，按 bindings 将本地按键转发到多台 Receiver。"""

from __future__ import annotations

import argparse
import logging
import queue
import sys

import trio

from .bindings import Bindings, Action
from .config import load, DEFAULT_CONFIG_PATH
from .discovery import DiscoveryTable
from .keyboard_events import raw_to_key_event, start_capture
from .p2p import (
    connect_and_send_action_event,
    connect_and_send_key_event,
    create_listen_addr,
    create_host,
    get_first_listen_addr,
)
from .protocol import ActionEvent, KeyEvent
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
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="打印详细日志（DEBUG）便于排查发送卡住等问题",
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

    if args.verbose:
        logging.getLogger("zlls_coop_keybord").setLevel(logging.DEBUG)
        logger.debug("verbose: 已开启 DEBUG 日志")

    bindings = Bindings(config)
    discovery: DiscoveryTable | None = None
    discovery_cfg = (config.get("controller") or {}).get("discovery") or {}
    if discovery_cfg.get("enable") and discovery_cfg.get("type") == "zeroconf":
        discovery = DiscoveryTable()
        discovery.start_browser()

    event_queue: queue.Queue = queue.Queue()
    start_capture(event_queue)
    host = create_host()
    # 按 peer 复用 stream，避免每次按键都 new_stream 导致卡住
    stream_cache: dict = {}

    async with host.run([create_listen_addr(args.port)]):
        logger.info("Controller listening on %s", get_first_listen_addr(host))
        while True:
            logger.debug("主循环: 等待键盘事件 event_queue.get() ...")
            raw = await trio.to_thread.run_sync(event_queue.get)
            key_str = raw_to_key_event(raw).key
            logger.debug("主循环: 收到 raw event_type=%s key_str=%s modifiers=%s", raw.event_type, key_str, raw.modifiers)
            if not bindings.should_handle(key_str, raw.modifiers):
                logger.debug("主循环: should_handle=False 跳过 key_str=%s", key_str)
                continue
            actions = bindings.get_actions(key_str, raw.modifiers, raw.event_type)
            if not actions:
                logger.debug("主循环: get_actions 为空 跳过 key_str=%s event_type=%s", key_str, raw.event_type)
                continue
            await _handle_actions(config, bindings, actions, discovery, host, stream_cache)


async def _handle_actions(
    config: dict,
    bindings: Bindings,
    actions: list[Action],
    discovery: DiscoveryTable | None,
    host,
    stream_cache: dict,
) -> None:
    """根据 Action.type 执行 send_key / run_command / http_request。"""
    send_key_events: list[tuple[str, KeyEvent]] = []
    for a in actions:
        if a.type == "send_key":
            if not a.target or not a.key:
                continue
            addr = resolve_target(config, a.target, discovery_table=discovery)
            if not addr:
                logger.warning("target %r not resolved, skip send_key key=%s", a.target, a.key)
                continue
            if a.event == "both":
                send_key_events.append((addr, KeyEvent(event="down", key=a.key, modifiers=None)))
                send_key_events.append((addr, KeyEvent(event="up", key=a.key, modifiers=None)))
            else:
                send_key_events.append((addr, KeyEvent(event=a.event or "down", key=a.key, modifiers=None)))
            continue

        if a.type == "run_command":
            await _handle_run_command(a, config, discovery, host, stream_cache)
            continue

        if a.type == "http_request":
            await _handle_http_request(a, config, discovery, host, stream_cache)
            continue

    if send_key_events:
        logger.debug(
            "主循环: 待发送 %d 条 key_event -> %s",
            len(send_key_events),
            [ev.event + " " + ev.key for _, ev in send_key_events],
        )
        for i, (addr, ev) in enumerate(send_key_events):
            try:
                logger.debug("主循环: 发送 key_event [%d/%d] %s %s -> %s", i + 1, len(send_key_events), ev.event, ev.key, addr[:60])
                await connect_and_send_key_event(host, addr, ev, stream_cache=stream_cache)
                logger.debug("主循环: 发送 key_event [%d/%d] 完成", i + 1, len(send_key_events))
            except Exception as e:
                logger.warning("Send key_event to %s failed: %s", addr[:50], e)


async def _handle_run_command(
    a: Action,
    config: dict | None = None,
    discovery: DiscoveryTable | None = None,
    host=None,
    stream_cache: dict | None = None,
) -> None:
    """执行 run_command 动作：where=local 在本机执行，where=target 通过 ActionEvent 发往 Receiver。"""
    payload = a.payload or {}
    where = (payload.get("where") or "local").lower()
    command = payload.get("command")
    if not command:
        logger.warning("run_command 动作缺少 command，忽略")
        return
    if where == "local":
        import subprocess
        import shlex
        args = payload.get("args") or []
        cwd = payload.get("cwd") or None
        shell = bool(payload.get("shell", False))
        try:
            cmd_list = [command] + list(args)
            logger.info("run_command local: %s (cwd=%r shell=%s)", cmd_list, cwd, shell)
            subprocess.Popen(
                cmd_list if not shell else " ".join(shlex.quote(x) for x in cmd_list),
                cwd=cwd,
                shell=shell,
            )
        except Exception as e:
            logger.warning("run_command local 执行失败: %s", e)
        return
    # where == target: 需要 target，通过 ActionEvent 发给 Receiver
    if not a.target:
        logger.warning("run_command where=target 但未提供 target，忽略")
        return
    if not config or host is None or stream_cache is None:
        logger.warning("run_command where=target 缺少上下文（config/host/stream_cache），忽略")
        return
    addr = resolve_target(config, a.target, discovery_table=discovery)
    if not addr:
        logger.warning("run_command target %r not resolved, skip", a.target)
        return
    act = ActionEvent(
        kind="run_command",
        payload={
            "command": command,
            "args": payload.get("args") or [],
            "cwd": payload.get("cwd") or None,
            "shell": bool(payload.get("shell", False)),
        },
    )
    try:
        logger.debug("主循环: 发送 run_command ActionEvent -> %s %s", a.target, addr[:60])
        await connect_and_send_action_event(host, addr, act, stream_cache=stream_cache)
        logger.debug("主循环: 发送 run_command ActionEvent 完成")
    except Exception as e:
        logger.warning("Send run_command ActionEvent to %s failed: %s", addr[:50], e)


async def _handle_http_request(
    a: Action,
    config: dict,
    discovery: DiscoveryTable | None,
    host,
    stream_cache: dict,
) -> None:
    """执行 http_request 动作：local 直接用 httpx，请求 target 则通过 ActionEvent。"""
    payload = a.payload or {}
    where = (payload.get("where") or "local").lower()
    method = (payload.get("method") or "GET").upper()
    url = payload.get("url")
    headers = payload.get("headers") or None
    body = payload.get("body", None)
    if not url:
        logger.warning("http_request 动作缺少 url，忽略")
        return
    if where == "local":
        try:
            import httpx
        except ImportError:
            logger.warning("http_request local: httpx 未安装，忽略该动作")
            return

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
                    logger.info("http_request local %s %s -> %s", method, url, resp.status_code)
                else:
                    logger.warning(
                        "http_request local 非 2xx: %s %s -> %s",
                        method,
                        url,
                        resp.status_code,
                    )
            except Exception as e:
                logger.warning("http_request local 请求失败 %s %s: %s", method, url, e)

        await _do_request()
        return

    # where == target：发 ActionEvent 给 Receiver
    if not a.target:
        logger.warning("http_request where=target 但未提供 target，忽略")
        return
    addr = resolve_target(config, a.target, discovery_table=discovery)
    if not addr:
        logger.warning("http_request target %r not resolved, skip", a.target)
        return
    act = ActionEvent(
        kind="http_request",
        payload={
            "method": method,
            "url": url,
            "headers": headers,
            "body": body,
        },
    )
    try:
        logger.debug("主循环: 发送 http_request ActionEvent -> %s %s", a.target, addr[:60])
        await connect_and_send_action_event(host, addr, act, stream_cache=stream_cache)
        logger.debug("主循环: 发送 http_request ActionEvent 完成")
    except Exception as e:
        logger.warning("Send http_request ActionEvent to %s failed: %s", addr[:50], e)


def main() -> None:
    trio.run(_async_main)


if __name__ == "__main__":
    main()
