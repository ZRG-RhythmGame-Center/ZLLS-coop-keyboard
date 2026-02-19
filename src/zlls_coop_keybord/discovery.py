"""局域网发现（zeroconf/mDNS）：Receiver 注册服务，Controller 浏览并维护「机器标识 → multiaddr」表。"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 服务类型（mDNS 首标签 ≤15 字节，故缩短）
SERVICE_TYPE = "_zlls-coop-kb._tcp.local."


def _parse_multiaddr_parts(multiaddr_str: str) -> dict[str, str]:
    """从 multiaddr 字符串解析出 ip4、tcp、p2p 等部分。"""
    parts = multiaddr_str.strip().split("/")
    result: dict[str, str] = {}
    i = 0
    while i < len(parts) - 1:
        if parts[i] and parts[i] in ("ip4", "ip6", "tcp", "udp", "p2p"):
            proto, value = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
            result[proto] = value
            i += 2
        else:
            i += 1
    return result


def _host_ip_for_zeroconf(multiaddr_str: str) -> str:
    """从 multiaddr 得到用于 zeroconf 广播的 IP；若为 0.0.0.0 则用本机 hostname 解析。"""
    parts = _parse_multiaddr_parts(multiaddr_str)
    ip = parts.get("ip4") or parts.get("ip6") or ""
    if ip and ip != "0.0.0.0":
        return ip
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _port_from_multiaddr(multiaddr_str: str) -> int:
    """从 multiaddr 解析 TCP 端口。"""
    parts = _parse_multiaddr_parts(multiaddr_str)
    try:
        return int(parts.get("tcp") or "0")
    except ValueError:
        return 0


def _multiaddr_with_connectable_ip(multiaddr_str: str) -> str:
    """把 multiaddr 中的 0.0.0.0 换成可连接的本机 IP，供 Controller 拨号用。"""
    parts = _parse_multiaddr_parts(multiaddr_str)
    ip = parts.get("ip4") or parts.get("ip6") or ""
    if ip and ip != "0.0.0.0":
        return multiaddr_str
    connectable_ip = _host_ip_for_zeroconf(multiaddr_str)
    # 重建 multiaddr：/ip4/<ip>/tcp/<port>/p2p/<peer_id>
    result = f"/ip4/{connectable_ip}"
    if parts.get("tcp"):
        result += f"/tcp/{parts['tcp']}"
    if parts.get("p2p"):
        result += f"/p2p/{parts['p2p']}"
    return result or multiaddr_str


def register_receiver(identity_id: str, full_multiaddr: str) -> Any:
    """在 zeroconf 上注册本机为 Receiver 服务。返回 (zeroconf, service_info)，退出时需 unregister_service + close。"""
    try:
        from zeroconf import IPVersion, ServiceInfo, Zeroconf
    except ImportError as e:
        logger.warning("zeroconf 未安装，跳过 mDNS 注册: %s", e)
        return None, None

    port = _port_from_multiaddr(full_multiaddr)
    if not port:
        logger.warning("无法从 multiaddr 解析端口，跳过 zeroconf 注册: %s", full_multiaddr[:80])
        return None, None

    ip = _host_ip_for_zeroconf(full_multiaddr)
    # 服务名：机器标识.类型，需唯一；含点的主机名在 mDNS 中合法
    safe_name = identity_id.replace(" ", "-").strip() or "receiver"
    name = f"{safe_name}.{SERVICE_TYPE}"

    # TXT 中存「可连接」的 multiaddr（0.0.0.0 换成实际 IP），否则 Controller 无法连接
    multiaddr_for_txt = _multiaddr_with_connectable_ip(full_multiaddr)
    try:
        info = ServiceInfo(
            SERVICE_TYPE,
            name,
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={"multiaddr": multiaddr_for_txt},
            server=f"{safe_name}.local.",
        )
        zc = Zeroconf(ip_version=IPVersion.V4Only)
        zc.register_service(info)
        logger.info("zeroconf 已注册: %s -> %s:%s", safe_name, ip, port)
        return zc, info
    except Exception as e:
        logger.warning("zeroconf 注册失败: %s", e)
        return None, None


def unregister_receiver(zc: Any, info: Any) -> None:
    """反注册并关闭 Zeroconf。"""
    if zc is None or info is None:
        return
    try:
        zc.unregister_service(info)
        zc.close()
        logger.info("zeroconf 已反注册")
    except Exception as e:
        logger.debug("zeroconf 反注册: %s", e)


class DiscoveryTable:
    """Controller 端：维护「机器标识 → multiaddr」发现表，由 ServiceBrowser 回调更新。"""

    def __init__(self) -> None:
        self._table: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, target_id: str) -> str | None:
        with self._lock:
            return self._table.get(target_id)

    def set(self, target_id: str, multiaddr: str) -> None:
        with self._lock:
            self._table[target_id] = multiaddr
            logger.debug("discovery: %s -> %s", target_id, multiaddr[:60])

    def remove(self, target_id: str) -> None:
        with self._lock:
            self._table.pop(target_id, None)

    def start_browser(self) -> None:
        """在后台线程启动 zeroconf ServiceBrowser，发现 _zlls-coop-keyboard._tcp.local。"""
        try:
            from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
        except ImportError as e:
            logger.warning("zeroconf 未安装，跳过发现: %s", e)
            return

        table = self

        class Listener(ServiceListener):
            def add_service(self, zc: Any, type_: str, name: str) -> None:
                info = zc.get_service_info(type_, name)
                if not info:
                    return
                # 机器标识：服务名去掉 ._zlls-coop-keyboard._tcp.local.
                machine_id = name[: -len(SERVICE_TYPE)] if name.endswith(SERVICE_TYPE) else name
                machine_id = machine_id.rstrip(".")
                # 优先用 TXT 里的 multiaddr
                multiaddr = None
                if hasattr(info, "decoded_properties") and info.decoded_properties:
                    multiaddr = (info.decoded_properties.get("multiaddr") or "").strip()
                if not multiaddr and info.properties:
                    raw = info.properties.get(b"multiaddr")
                    if raw is not None:
                        multiaddr = raw.decode("utf-8", errors="replace").strip()
                if not multiaddr and info.parsed_addresses():
                    addr = info.parsed_addresses()[0]
                    port = info.port
                    peer_id = (info.properties or {}).get(b"peer_id")
                    peer_id = peer_id.decode("utf-8", errors="replace") if peer_id else ""
                    if peer_id:
                        multiaddr = f"/ip4/{addr}/tcp/{port}/p2p/{peer_id}"
                if multiaddr:
                    table.set(machine_id, multiaddr)

            def remove_service(self, zc: Any, type_: str, name: str) -> None:
                machine_id = name[: -len(SERVICE_TYPE)] if name.endswith(SERVICE_TYPE) else name
                machine_id = machine_id.rstrip(".")
                table.remove(machine_id)

            def update_service(self, zc: Any, type_: str, name: str) -> None:
                self.add_service(zc, type_, name)

        def run_browser() -> None:
            zc = Zeroconf()
            browser = ServiceBrowser(zc, SERVICE_TYPE, listener=Listener())
            try:
                browser.join()
            except Exception as e:
                logger.debug("zeroconf browser: %s", e)
            finally:
                try:
                    browser.cancel()
                    zc.close()
                except Exception:
                    pass

        t = threading.Thread(target=run_browser, daemon=True)
        t.start()
        logger.info("zeroconf 发现已启动（类型 %s）", SERVICE_TYPE)
