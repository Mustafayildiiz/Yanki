"""Ağ politikası ve denetleyici proxy (NFR-08: SSRF koruması).

Chromium'un tüm ağ trafiği (ana belge, yönlendirmeler, alt kaynaklar,
WebSocket, https CONNECT tünelleri) süreç içinde çalışan bu proxy'den geçer.
Proxy her bağlantıda:

1. sunucu adını kendisi çözer;
2. dönen adreslerden biri bile genel (global) değilse bağlantıyı reddeder;
3. izin verirse sunucu adına değil, **doğruladığı IP'ye** bağlanır.

Böylece yönlendirme zincirleri ve DNS rebinding de denetlenir. Playwright'ın
``page.route`` yöntemi yönlendirme adımlarını yakalamadığı için tek başına
yeterli değildir (deneyle doğrulandı).

Bilinen sınır: WebRTC UDP trafiği HTTP proxy'den geçmez; tarayıcı
``--force-webrtc-ip-handling-policy=disable_non_proxied_udp`` ile başlatılır.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

_BLOCK_RESPONSE = (
    b"HTTP/1.1 403 Forbidden\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: 0\r\n"
    b"Connection: close\r\n\r\n"
)
_BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_HOP_HEADERS = ("proxy-", "connection:", "keep-alive:")


def is_non_public(ip: IPAddress) -> bool:
    """Loopback, özel, link-local, paylaşımlı, ayrılmış ve çoklu yayın adresleri."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # Sıra korunur ve tekrarlar atılır (belirlenimcilik).
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


@dataclass(frozen=True)
class Decision:
    ip: str | None
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.reason is None


class NetworkPolicy:
    """Hangi adreslere bağlanılabileceğine karar verir.

    ``is_forbidden`` testlerde değiştirilebilir; varsayılanı ``is_non_public``'tir.
    ``allow_private=True`` ise IP denetimi yapılmaz (ad çözümleme yine yapılır).
    """

    def __init__(
        self,
        *,
        allow_private: bool = False,
        resolver: Resolver = system_resolver,
        is_forbidden: Callable[[IPAddress], bool] = is_non_public,
    ) -> None:
        self.allow_private = allow_private
        self._resolver = resolver
        self._is_forbidden = is_forbidden

    async def decide(self, host: str, port: int) -> Decision:
        host = host.strip("[]")
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                addresses = await self._resolver(host, port)
            except (OSError, UnicodeError):
                return Decision(None, "dns_failed")
        if not addresses:
            return Decision(None, "dns_failed")
        if not self.allow_private:
            for addr in addresses:
                if self._is_forbidden(ipaddress.ip_address(addr)):
                    return Decision(None, "private_address")
        return Decision(addresses[0])


@dataclass(frozen=True)
class ProxyEvent:
    """Proxy'nin kurmadığı ya da kuramadığı bir bağlantı."""

    host: str
    port: int
    reason: str
    """``private_address`` (politika) ya da ``dns_failed``, ``connection_refused``,
    ``connection_timeout``, ``connection_failed`` (ağ hatası)."""


class GuardProxy:
    """Politikayı uygulayan küçük bir HTTP/CONNECT proxy'si.

    Yalnızca ``127.0.0.1`` üzerinde, rastgele bir portta dinler. Düz HTTP
    isteklerinde her istemci bağlantısı tek bir istek taşır
    (``Connection: close``); böylece aynı bağlantıda başka bir sunucuya
    istek kaçırılamaz.
    """

    def __init__(self, policy: NetworkPolicy, *, connect_timeout_s: float = 30.0) -> None:
        self.policy = policy
        self.connect_timeout_s = connect_timeout_s
        self.blocked: list[ProxyEvent] = []
        """Politika gereği reddedilen bağlantılar (NFR-08)."""
        self.failed: list[ProxyEvent] = []
        """İzin verilen ama kurulamayan bağlantılar (TAR-05 hata kodları için)."""
        self._server: asyncio.base_events.Server | None = None
        self._tasks: set[asyncio.Task[object]] = set()

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("proxy başlatılmadı")
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.url

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    def was_blocked(self, host: str, port: int) -> ProxyEvent | None:
        return _find(self.blocked, host, port)

    def failure_for(self, host: str, port: int) -> ProxyEvent | None:
        return _find(self.failed, host, port)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        upstream: asyncio.StreamWriter | None = None
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                return
            lines = head.decode("latin-1").split("\r\n")
            try:
                method, target, version = lines[0].split(" ", 2)
            except ValueError:
                writer.write(_BAD_REQUEST)
                return

            if method.upper() == "CONNECT":
                host, _, port_text = target.rpartition(":")
                if not host or not port_text.isdigit():
                    writer.write(_BAD_REQUEST)
                    return
                port = int(port_text)
                first_bytes = b"HTTP/1.1 200 Connection Established\r\n\r\n"
                request_head = b""
            else:
                parts = urlsplit(target)
                if parts.scheme.lower() != "http" or not parts.hostname:
                    writer.write(_BAD_REQUEST)
                    return
                host, port = parts.hostname, parts.port or 80
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                headers = [h for h in lines[1:] if h and not h.lower().startswith(_HOP_HEADERS)]
                headers.append("Connection: close")
                request_head = (
                    f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\n\r\n"
                ).encode("latin-1")
                first_bytes = b""

            host = host.strip("[]")
            decision = await self.policy.decide(host, port)
            if decision.reason == "dns_failed":
                self.failed.append(ProxyEvent(host, port, "dns_failed"))
                writer.write(_BAD_GATEWAY)
                return
            if not decision.allowed:
                self.blocked.append(ProxyEvent(host, port, decision.reason or "blocked"))
                writer.write(_BLOCK_RESPONSE)
                return

            try:
                up_reader, upstream = await asyncio.wait_for(
                    asyncio.open_connection(decision.ip, port), self.connect_timeout_s
                )
            except (OSError, TimeoutError) as exc:
                reason = (
                    "connection_refused"
                    if isinstance(exc, ConnectionRefusedError)
                    else "connection_timeout"
                    if isinstance(exc, TimeoutError)
                    else "connection_failed"
                )
                self.failed.append(ProxyEvent(host, port, reason))
                writer.write(_BAD_GATEWAY)
                return

            if first_bytes:
                writer.write(first_bytes)
            if request_head:
                upstream.write(request_head)
            await asyncio.gather(_pipe(reader, upstream), _pipe(up_reader, writer))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            for w in (upstream, writer):
                if w is not None:
                    with contextlib.suppress(Exception):
                        w.close()
            if task is not None:
                self._tasks.discard(task)


def _find(events: list[ProxyEvent], host: str, port: int) -> ProxyEvent | None:
    host = host.strip("[]").lower()
    for entry in events:
        if entry.host.lower() == host and entry.port == port:
            return entry
    return None


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()
