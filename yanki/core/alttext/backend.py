"""Model arka ucu sözleşmesi (ALT-03..05, NFR-12, NFR-13).

Hat yalnızca bu sözleşmeyi bilir. Gerçek arka uç (``transformers``) ile
belirlenimci sahte arka uç (``fake``) aynı sözleşmeyi uygular; böylece CI'da
sahte arka uçla çalışan testler gerçek hat kodunu sınar. Model değiştirmek
yapılandırma değişikliğidir (NFR-13).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from yanki.config import AltTextConfig


class BackendUnavailable(Exception):
    """Arka uç yüklenemedi: ML paketleri kurulu değil ya da ağırlıklar yerelde yok."""


@dataclass(frozen=True)
class ImageInput:
    """Modele verilecek raster görsel. ``data`` biçimi ``mime`` ile belirtilir
    (JPEG, PNG, GIF, WebP, BMP); SVG'ler önceden ekran görüntüsüne çevrilir."""

    sha256: str
    data: bytes
    mime: str


@runtime_checkable
class AltTextBackend(Protocol):
    """Alt metin hattının model arka ucu.

    Benzerlikler ham kosinüs değeridir (−1..1); 0–1'e eşleme
    ``classify.rescale`` ile yapılır. Tüm yöntemler toplu (batch) çalışır ve
    aynı girdi için aynı çıktıyı verir (NFR-06).
    """

    name: str

    def versions(self) -> dict[str, str]:
        """Rol → ``kimlik@revizyon``; rapora yazılır."""
        ...

    def library_versions(self) -> dict[str, str]:
        """``torch``, ``transformers`` sürümleri (varsa)."""
        ...

    def load(self) -> None:
        """Modelleri belleğe yükler; görsel başına süre ölçümüne yükleme girmesin (NFR-01)."""
        ...

    def caption(self, images: Sequence[ImageInput]) -> list[str]:
        """Görsel açıklaması (ALT-05). Çözülemeyen görselde ``ValueError``."""
        ...

    def translate(self, texts: Sequence[str], source: str) -> list[str]:
        """``source`` dilinden İngilizceye çeviri (ALT-04)."""
        ...

    def text_similarity(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """E5 kosinüs benzerliği (ana sinyal)."""
        ...

    def image_text_similarity(
        self, items: Sequence[tuple[ImageInput, str]], *, multilingual: bool = False
    ) -> list[float]:
        """CLIP görsel–metin kosinüs benzerliği (ek sinyal). ``multilingual``:
        metin, çok dilli CLIP metin kodlayıcısıyla kodlanır."""
        ...

    def thumbnail_png(self, image: ImageInput, max_px: int) -> bytes | None:
        """Kanıt için PNG'ye yeniden kodlanmış küçük resim; desteklenmiyorsa ``None``."""
        ...


def load_backend(cfg: AltTextConfig, *, device: str, seed: int) -> AltTextBackend:
    """Yapılandırmadaki arka ucu yükler.

    Raises:
        BackendUnavailable: ML paketleri kurulu değil ya da model ağırlıkları
            yerelde yok (ve ``allow_download`` kapalı).
    """
    if cfg.backend == "fake":
        from yanki.core.alttext.backends.fake import FakeBackend

        return FakeBackend()
    try:
        from yanki.core.alttext.backends.transformers import TransformersBackend
    except ImportError as exc:
        raise BackendUnavailable(
            "ML paketleri kurulu değil (torch, transformers). Docker imajını kullanın "
            "ya da `pip install yanki[ml]` ile kurun."
        ) from exc
    return TransformersBackend(cfg, device=device, seed=seed)
