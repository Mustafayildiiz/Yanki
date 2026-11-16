"""Görsel baytlarının toplanması (ALT-09, ALT-10, NFR-07, NFR-08).

**Yeni ağ isteği yapılmaz.** Baytlar şu sırayla aranır:

1. Sayfanın yüklenirken aldığı görsel yanıtı (``page.on("response")``). Bu
   yanıtlar denetleyici proxy'den geçmiştir; engellenen adresler yanıt üretmez.
2. ``data:`` URI'ler yerelde çözülür.
3. Raster olmayan (SVG) ya da yanıtı yakalanamayan ama tarayıcıda çizilmiş
   görseller için öğenin ekran görüntüsü alınır. Yüklenemeyen ``<img>``
   (``naturalWidth == 0``) için ekran görüntüsü alınmaz; aksi hâlde kırık görsel
   simgesi analiz edilirdi.

Hiçbiri olmazsa görsel "analiz edilemedi" olur (ALT-10). Baytlar
``max_image_bytes`` ile sınırlıdır.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Request, Response

from yanki.config import AltTextConfig
from yanki.core.alttext.backend import ImageInput
from yanki.core.schema import PageElement

RASTER_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}


def sniff(data: bytes) -> str | None:
    """İçerikten görsel biçimi (MIME). Bilinmiyorsa ``None``.

    Sunucunun ``Content-Type`` başlığına güvenilmez; biçim baytlardan okunur.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    head = data[:512].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml")) and b"<svg" in data[:4096].lower():
        return "image/svg+xml"
    return None


def decode_data_uri(uri: str) -> bytes | None:
    """``data:[<mime>][;base64],<veri>`` URI'sinin baytları."""
    try:
        meta, payload = uri[5:].split(",", 1)
    except ValueError:
        return None
    try:
        if meta.lower().endswith(";base64"):
            return base64.b64decode(payload, validate=False)
        return unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        return None


class ImageCapture:
    """Sayfanın görsel yanıtlarını ve başarısız görsel isteklerini kaydeder.

    Gezinmeden **önce** bağlanmalı ve klavye testinden önce ``detach`` edilmelidir
    (klavye modülü sayfayı yeniden yükleyebilir).
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self.responses: dict[str, Response] = {}
        self.failures: dict[str, str] = {}
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_failed)

    def _on_response(self, response: Response) -> None:
        if response.request.resource_type == "image":
            self.responses[response.url] = response

    def _on_failed(self, request: Request) -> None:
        if request.resource_type == "image":
            self.failures[request.url] = request.failure or "request_failed"

    def detach(self) -> None:
        self.page.remove_listener("response", self._on_response)
        self.page.remove_listener("requestfailed", self._on_failed)


@dataclass
class Gathered:
    """Bir görsel için toplanan baytlar ya da başarısızlık nedeni."""

    image: ImageInput | None = None
    source: str | None = None
    reason: str | None = None


def _input(data: bytes, mime: str) -> ImageInput:
    return ImageInput(sha256=hashlib.sha256(data).hexdigest(), data=data, mime=mime)


async def screenshot(page: Page, selector: str, cfg: AltTextConfig) -> bytes | None:
    """Öğenin PNG ekran görüntüsü; alınamazsa ``None``."""
    try:
        return await page.locator(selector).first.screenshot(
            timeout=cfg.screenshot_timeout_ms, animations="disabled", type="png"
        )
    except PlaywrightError:
        return None


async def gather(
    page: Page, el: PageElement, capture: ImageCapture, cfg: AltTextConfig
) -> Gathered:
    """Bir görselin model girdisini toplar (yalnızca sayfanın kendi verisinden)."""
    src = el.src or ""
    data: bytes | None = None
    source: str | None = None
    reason: str | None = None

    if src.startswith("data:"):
        data, source = decode_data_uri(src), "data_uri"
        if data is None:
            reason = "undecodable_data_uri"
    elif src in capture.responses:
        response = capture.responses[src]
        length = response.headers.get("content-length", "")
        if response.status >= 400:
            reason = f"http_{response.status}"
        elif length.isdigit() and int(length) > cfg.max_image_bytes:
            reason = "too_large"
        else:
            try:
                data, source = await response.body(), "response"
            except PlaywrightError:
                reason = "body_unavailable"
    elif src in capture.failures:
        reason = f"request_failed: {capture.failures[src]}"

    if data is not None and len(data) > cfg.max_image_bytes:
        return Gathered(reason="too_large")

    mime = sniff(data) if data else None
    if data is not None and mime in RASTER_MIMES:
        return Gathered(image=_input(data, mime), source=source)  # type: ignore[arg-type]

    # Raster değil (SVG), koklanamayan ama tarayıcının çözdüğü biçim (ör. AVIF) ya
    # da yanıt yok: tarayıcının çizdiği hâli kullanılır; yüklenemeyen <img> için asla.
    loaded = el.tag != "img" or (el.natural_width or 0) > 0
    if loaded and reason is None:
        png = await screenshot(page, el.selector, cfg)
        if png:
            return Gathered(image=_input(png, "image/png"), source="screenshot")
        return Gathered(reason="screenshot_failed")
    if reason is None:
        reason = "undecodable" if data is not None else "not_loaded"
    return Gathered(reason=reason)
