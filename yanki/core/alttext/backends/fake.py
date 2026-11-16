"""Belirlenimci sahte arka uç (yalnızca test ve geliştirme).

Gerçek arka uçla aynı sözleşmeyi uygular ve çıktılarını girdinin SHA-256
karmasından türetir. **Fixture cevaplarını kodlamaz:** benzerlikler girdiyle
anlamsal olarak ilişkisizdir. Bu yüzden doğruluk ölçümünde kullanılamaz;
``experiments/`` betikleri ve ``-m models`` testleri bu arka ucu reddeder.

ML paketleri gerektirmez; Windows ve macOS CI işlerinde de çalışır.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from yanki.core.alttext.backend import ImageInput

_RASTER_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")


def _unit(*parts: str) -> float:
    """Girdiden türetilen, [0, 1) aralığında belirlenimci sayı."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class FakeBackend:
    name = "fake"

    def versions(self) -> dict[str, str]:
        return {
            role: "fake"
            for role in ("captioner", "translator", "embedder", "clip", "clip_multilingual")
        }

    def library_versions(self) -> dict[str, str]:
        return {}

    def load(self) -> None:
        return None

    def caption(self, images: Sequence[ImageInput]) -> list[str]:
        out: list[str] = []
        for img in images:
            is_webp = img.data[:4] == b"RIFF" and img.data[8:12] == b"WEBP"
            if not (img.data.startswith(_RASTER_MAGIC) or is_webp):
                raise ValueError("görsel çözülemedi")
            out.append(f"fake caption {img.sha256[:8]}")
        return out

    def translate(self, texts: Sequence[str], source: str) -> list[str]:
        return [f"[{source}->en] {t}" for t in texts]

    def text_similarity(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        # Kosinüs aralığına yayılmış değerler: eşiklerin her iki tarafına düşebilir.
        return [round(0.5 + 0.5 * _unit("e5", a, b), 6) for a, b in pairs]

    def image_text_similarity(
        self, items: Sequence[tuple[ImageInput, str]], *, multilingual: bool = False
    ) -> list[float]:
        tag = "mclip" if multilingual else "clip"
        return [round(0.4 * _unit(tag, img.sha256, text), 6) for img, text in items]

    def thumbnail_png(self, image: ImageInput, max_px: int) -> bytes | None:
        # Sayfa baytları kanıta yeniden kodlanmadan konmaz; sahte arka uç kodlayamaz.
        return None
