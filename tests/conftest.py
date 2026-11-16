"""Ortak test araçları.

Testler internete çıkmaz. Ağ davranışı, ``0.0.0.0`` üzerinde dinleyen yerel
bir HTTP sunucusuyla sınanır: aynı sunucuya ``127.0.0.1`` ("izinli") ve
``127.0.0.2`` ("yasak") adresleriyle ulaşılır. Sunucu, her yola gelen isteği
sayar; SSRF testleri raporun değil, sunucunun gözlemine dayanır.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from yanki.core.netguard import NetworkPolicy

PAGES = Path(__file__).parent / "pages"
IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"

ALLOWED_IP = "127.0.0.1"
FORBIDDEN_IP = "127.0.0.2"


@pytest.fixture(autouse=True)
def _hermetic_models(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """``models`` işareti olmayan testlerde model ağırlıkları yokmuş gibi davranılır.

    Geliştirme makinesinde ``/models`` volume'ünde ağırlık bulunsa da varsayılan
    testler CI'daki ağırlıksız ortamı görür; doğruluk gerçek modellerle yalnızca
    ``-m models`` ve ``experiments/`` ile ölçülür. Önbellek de depoya yazılmaz.
    """
    monkeypatch.setenv("YANKI_CACHE_DIR", str(tmp_path_factory.mktemp("alt-cache")))
    if request.node.get_closest_marker("models") is None:
        monkeypatch.setenv("YANKI_MODELS_DIR", str(tmp_path_factory.mktemp("no-models")))


def forbid_only_127_0_0_2(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return str(ip) == FORBIDDEN_IP


def make_policy() -> NetworkPolicy:
    """127.0.0.1'e izin veren, 127.0.0.2'yi yasaklayan politika."""
    return NetworkPolicy(allow_private=False, is_forbidden=forbid_only_127_0_0_2)


@dataclass
class LocalServer:
    port: int
    hits: Counter[str] = field(default_factory=Counter)

    def url(self, path: str, ip: str = ALLOWED_IP) -> str:
        return f"http://{ip}:{self.port}{path}"


def _make_handler(state: LocalServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _send(
            self,
            status: int,
            body: bytes,
            ctype: str = "text/html; charset=utf-8",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            path = parts.path
            query = parse_qs(parts.query)
            state.hits[path] += 1

            if path.startswith("/pages/"):
                file = PAGES / path.removeprefix("/pages/")
                if file.is_file() and file.parent == PAGES:
                    self._send(200, file.read_bytes())
                else:
                    self._send(404, b"<html lang='tr'><title>yok</title></html>")
            elif path == "/csp":
                self._send(
                    200,
                    (PAGES / "rules_violations.html").read_bytes(),
                    headers={"Content-Security-Policy": "script-src 'none'"},
                )
            elif path == "/redirect":
                self._send(302, b"", headers={"Location": query["to"][0]})
            elif path == "/status/404":
                self._send(404, b"<html lang='tr'><title>Yok</title><h1>404</h1></html>")
            elif path == "/slow":
                time.sleep(3)
                self._send(200, b"<html lang='tr'><title>Yavas</title></html>")
            elif path == "/hang":
                time.sleep(8)
                self._send(200, b"{}", "application/json")
            elif path == "/poll.html":
                self._send(
                    200,
                    (
                        b"<!doctype html><html lang='tr'><title>Yoklama</title>"
                        b"<main><h1>Yoklama</h1></main>"
                        b"<script>fetch('/hang').catch(() => {});</script></html>"
                    ),
                )
            elif path == "/subresource.html":
                # Alt kaynak, yasak adrese doğrudan ve yönlendirmeyle gider.
                evil = f"http://{FORBIDDEN_IP}:{state.port}"
                ok = f"http://{ALLOWED_IP}:{state.port}"
                self._send(
                    200,
                    (
                        "<!doctype html><html lang='tr'><title>Alt kaynak</title><main>"
                        "<h1>Alt kaynak</h1>"
                        f"<img src='{evil}/evil/direct.png' alt='doğrudan'>"
                        f"<img src='{ok}/redirect?to={evil}/evil/redirected.png'"
                        " alt='yönlendirmeli'>"
                        f"<img src='{ok}/ok.png' alt='izinli'>"
                        "</main></html>"
                    ).encode(),
                )
            elif path == "/alt.html":
                # Alt metin modülü (NFR-08): görsel baytları yalnızca sayfanın kendi
                # yanıtlarından alınır; yasak adrese giden görsel analiz edilemez.
                evil = f"http://{FORBIDDEN_IP}:{state.port}"
                ok = f"http://{ALLOWED_IP}:{state.port}"
                size = "width='160' height='120'"
                self._send(
                    200,
                    (
                        "<!doctype html><html lang='tr'><title>Alt metin</title><main>"
                        "<h1>Görseller</h1>"
                        f"<img id='direct' src='{evil}/evil/alt-direct.jpg' {size}"
                        " alt='Kent müzesinin giriş kapısı'>"
                        f"<img id='redirected' src='{ok}/redirect?to={evil}/evil/alt-redir.jpg'"
                        f" {size} alt='Gölde kano yapan iki kişi'>"
                        f"<img id='allowed' src='{ok}/img/car.jpg' {size}"
                        " alt='Arnavut kaldırımlı sokakta kırmızı bir araba'>"
                        "</main></html>"
                    ).encode(),
                )
            elif path == "/img/car.jpg":
                self._send(200, (IMAGES / "red_car.jpg").read_bytes(), "image/jpeg")
            elif path.startswith("/evil") or path == "/ok.png":
                self._send(200, b"", "image/png")
            else:
                self._send(404, b"")

    return Handler


@pytest.fixture
def http_server() -> Iterator[LocalServer]:
    server = ThreadingHTTPServer(("0.0.0.0", 0), _make_handler(LocalServer(0)))
    server.daemon_threads = True
    server.block_on_close = False
    state = LocalServer(server.server_port)
    server.RequestHandlerClass = _make_handler(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
