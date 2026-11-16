"""Ağ politikası ve denetleyici proxy (NFR-08) birim testleri. Tarayıcı gerekmez."""

from __future__ import annotations

import asyncio
import ipaddress

import pytest

from yanki.core.netguard import GuardProxy, NetworkPolicy, is_non_public

from conftest import ALLOWED_IP, FORBIDDEN_IP, LocalServer, make_policy

CRLF = "\r\n"


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_non_public_addresses(ip: str) -> None:
    assert is_non_public(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["93.184.215.14", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses(ip: str) -> None:
    assert not is_non_public(ipaddress.ip_address(ip))


def _resolver(mapping: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError("NXDOMAIN")
        return mapping[host]

    return resolve


def test_policy_decisions() -> None:
    policy = NetworkPolicy(
        resolver=_resolver(
            {
                "public.test": ["93.184.215.14"],
                "mixed.test": ["93.184.215.14", "10.0.0.5"],
                "localhost": ["127.0.0.1"],
            }
        )
    )

    async def run() -> None:
        assert (await policy.decide("public.test", 443)).ip == "93.184.215.14"
        # Adreslerden biri bile özelse reddedilir.
        assert (await policy.decide("mixed.test", 443)).reason == "private_address"
        assert (await policy.decide("localhost", 80)).reason == "private_address"
        assert (await policy.decide("127.0.0.1", 80)).reason == "private_address"
        assert (await policy.decide("[::1]", 80)).reason == "private_address"
        assert (await policy.decide("yok.test", 80)).reason == "dns_failed"

    asyncio.run(run())


def test_allow_private_skips_ip_check() -> None:
    policy = NetworkPolicy(allow_private=True, resolver=_resolver({"localhost": ["127.0.0.1"]}))
    decision = asyncio.run(policy.decide("localhost", 80))
    assert decision.allowed
    assert decision.ip == "127.0.0.1"


async def _raw_request(proxy_url: str, payload: bytes) -> bytes:
    host, port = proxy_url.removeprefix("http://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), 10)
    writer.close()
    return data


def test_proxy_blocks_forbidden_and_forwards_allowed(http_server: LocalServer) -> None:
    async def run() -> tuple[bytes, bytes, bytes, GuardProxy]:
        proxy = GuardProxy(make_policy())
        url = await proxy.start()
        try:
            port = http_server.port
            ok = await _raw_request(
                url,
                f"GET http://{ALLOWED_IP}:{port}/ok.png HTTP/1.1\r\n"
                f"Host: {ALLOWED_IP}:{port}\r\nProxy-Connection: keep-alive\r\n\r\n".encode(),
            )
            bad = await _raw_request(
                url,
                f"GET http://{FORBIDDEN_IP}:{port}/evil/x HTTP/1.1\r\n"
                f"Host: {FORBIDDEN_IP}:{port}\r\n\r\n".encode(),
            )
            tunnel = await _raw_request(
                url, f"CONNECT {FORBIDDEN_IP}:{port} HTTP/1.1\r\n\r\n".encode()
            )
            return ok, bad, tunnel, proxy
        finally:
            await proxy.close()

    ok, bad, tunnel, proxy = asyncio.run(run())
    assert ok.startswith(b"HTTP/1.0 200") or ok.startswith(b"HTTP/1.1 200")
    assert bad.startswith(b"HTTP/1.1 403")
    assert tunnel.startswith(b"HTTP/1.1 403")
    # Sunucu tarafı gözlem: yasak adrese hiç istek ulaşmadı.
    assert http_server.hits["/ok.png"] == 1
    assert http_server.hits["/evil/x"] == 0
    assert proxy.was_blocked(FORBIDDEN_IP, http_server.port) is not None


def test_proxy_connect_tunnel_success(http_server: LocalServer) -> None:
    # https'in yolu: CONNECT tüneli kurulur, içinden istek geçer.
    async def run() -> bytes:
        proxy = GuardProxy(make_policy())
        url = await proxy.start()
        try:
            host, port = url.removeprefix("http://").split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
            writer.write(f"CONNECT {ALLOWED_IP}:{http_server.port} HTTP/1.1{CRLF}{CRLF}".encode())
            await writer.drain()
            established = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            assert established.startswith(b"HTTP/1.1 200")
            writer.write(
                f"GET /ok.png HTTP/1.1{CRLF}Host: {ALLOWED_IP}{CRLF}"
                f"Connection: close{CRLF}{CRLF}".encode()
            )
            await writer.drain()
            data = await asyncio.wait_for(reader.read(65536), 10)
            writer.close()
            return data
        finally:
            await proxy.close()

    status_line = asyncio.run(run()).split(b"\r\n", 1)[0]
    assert b" 200 " in status_line
    assert http_server.hits["/ok.png"] == 1


def test_proxy_records_failures_separately_from_policy_blocks() -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]

    policy = NetworkPolicy(
        allow_private=True,
        resolver=_resolver({"localhost": ["127.0.0.1"]}),
    )

    async def run() -> GuardProxy:
        proxy = GuardProxy(policy)
        url = await proxy.start()
        try:
            refused = await _raw_request(
                url, f"CONNECT 127.0.0.1:{closed} HTTP/1.1{CRLF}{CRLF}".encode()
            )
            unknown = await _raw_request(url, b"CONNECT yok.test:443 HTTP/1.1\r\n\r\n")
            assert refused.startswith(b"HTTP/1.1 502")
            assert unknown.startswith(b"HTTP/1.1 502")
            return proxy
        finally:
            await proxy.close()

    proxy = asyncio.run(run())
    assert proxy.blocked == []
    assert proxy.failure_for("127.0.0.1", closed).reason == "connection_refused"  # type: ignore[union-attr]
    assert proxy.failure_for("yok.test", 443).reason == "dns_failed"  # type: ignore[union-attr]


def test_proxy_rejects_malformed_requests() -> None:
    async def run() -> bytes:
        proxy = GuardProxy(make_policy())
        url = await proxy.start()
        try:
            return await _raw_request(url, b"GET /relative HTTP/1.1\r\nHost: x\r\n\r\n")
        finally:
            await proxy.close()

    assert asyncio.run(run()).startswith(b"HTTP/1.1 400")
