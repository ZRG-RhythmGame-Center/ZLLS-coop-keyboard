"""P2P 封装：libp2p Host、协议、连接与流。使用 trio（与 py-libp2p 一致）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

import multiaddr
import trio

import libp2p
from libp2p.custom_types import TProtocol
from libp2p.peer.peerinfo import info_from_p2p_addr

from .protocol import KeyEvent, PROTOCOL_ID_STR

if TYPE_CHECKING:
    from libp2p.abc import INetStream

logger = logging.getLogger(__name__)

# py-libp2p 使用 trio；主循环为 trio.run()
PROTOCOL_ID = TProtocol(PROTOCOL_ID_STR)


def create_listen_addr(port: int = 0) -> multiaddr.Multiaddr:
    """构造监听 multiaddr。port=0 表示随机端口。"""
    return multiaddr.Multiaddr(f"/ip4/0.0.0.0/tcp/{port}")


def create_host():
    """创建未启动的 libp2p Host（TCP + 默认安全与多路复用）。"""
    return libp2p.new_host()


async def run_host(
    host,
    listen_port: int = 0,
) -> AsyncIterator[None]:
    """在给定端口启动 host 并保持运行。listen_port=0 为随机端口。"""
    listen_addrs = [create_listen_addr(listen_port)]
    async with host.run(listen_addrs):
        addrs = host.get_addrs()
        for a in addrs:
            logger.info("Listening on: %s", a)
        yield


def get_first_listen_addr(host) -> str:
    """返回第一个完整监听地址（含 /p2p/peer_id），便于打印或配置。"""
    addrs = host.get_addrs()
    return str(addrs[0]) if addrs else ""


def register_keyboard_handler(host, async_handler):
    """注册键盘协议 stream handler。handler(stream) 为 async，内读 JSON 行并处理。"""
    host.set_stream_handler(PROTOCOL_ID, async_handler)


def peer_info_from_multiaddr_str(addr_str: str):
    """从完整 multiaddr 字符串（含 /p2p/peer_id）得到 PeerInfo。"""
    addr = multiaddr.Multiaddr(addr_str)
    return info_from_p2p_addr(addr)


async def read_json_lines(stream: "INetStream") -> AsyncIterator[bytes]:
    """从 stream 按行读取，每 yield 一行（含换行符的 bytes）。"""
    from libp2p.network.stream.exceptions import StreamEOF
    buf = b""
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                yield line + b"\n"
    except StreamEOF:
        pass


async def _write_key_event_to_stream(stream: "INetStream", key_event: KeyEvent) -> None:
    """向已打开的 stream 写一条 JSON 行（不关闭 stream）。"""
    line = key_event.to_json_line()
    await stream.write(line)


async def connect_and_send_key_event(
    host,
    multiaddr_str: str,
    key_event: KeyEvent,
    stream_cache: dict[str, Any] | None = None,
) -> None:
    """连接指定 multiaddr，发送一条按键事件。若提供 stream_cache 则按 peer 复用 stream，避免频繁 new_stream 导致卡住。"""
    peer_info = peer_info_from_multiaddr_str(multiaddr_str)
    peer_id_str = str(peer_info.peer_id)
    logger.debug("p2p: connect_and_send 开始 peer_id=%s event=%s key=%s", peer_id_str[:20], key_event.event, key_event.key)

    if stream_cache is not None:
        if peer_id_str not in stream_cache:
            stream_cache[peer_id_str] = {"lock": trio.Lock(), "stream": None}
        entry = stream_cache[peer_id_str]
        async with entry["lock"]:
            stream = entry["stream"]
            if stream is not None:
                try:
                    await _write_key_event_to_stream(stream, key_event)
                    logger.debug("p2p: 复用 stream 写入完成")
                    return
                except Exception as e:
                    logger.debug("p2p: 复用 stream 写入失败，将重建: %s", e)
                    entry["stream"] = None
            logger.debug("p2p: host.connect peer_id=%s ...", peer_id_str[:20])
            await host.connect(peer_info)
            logger.debug("p2p: host.connect 完成，new_stream ...")
            stream = await host.new_stream(peer_info.peer_id, [PROTOCOL_ID])
            logger.debug("p2p: new_stream 完成，写入并缓存")
            entry["stream"] = stream
            await _write_key_event_to_stream(stream, key_event)
        logger.debug("p2p: connect_and_send 结束（已缓存 stream）")
        return

    # 无 cache：每次新建 stream 并关闭（旧行为，易在多次按键后卡在 new_stream）
    logger.debug("p2p: host.connect peer_id=%s ...", peer_id_str[:20])
    await host.connect(peer_info)
    logger.debug("p2p: host.connect 完成，new_stream ...")
    stream = await host.new_stream(peer_info.peer_id, [PROTOCOL_ID])
    logger.debug("p2p: new_stream 完成，write ...")
    try:
        await _write_key_event_to_stream(stream, key_event)
        logger.debug("p2p: write 完成，close_write ...")
    finally:
        await stream.close_write()
        logger.debug("p2p: connect_and_send 结束")
