"""Tarama hedefinin ayrıştırılması (TAR-01, NFR-08).

Kabul edilen girdiler:

- ``http://`` veya ``https://`` URL;
- yerel bir HTML dosyasının yolu ya da ``file://`` URI'si.

Diğer tüm şemalar (``ftp:``, ``javascript:``, ``data:`` …) reddedilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import url2pathname

from yanki.core.errors import TargetError

_NETWORK_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class Target:
    kind: Literal["url", "file"]
    url: str
    """Tarayıcıya verilecek adres (dosyalarda ``file://`` URI)."""
    requested: str
    """Kullanıcının verdiği özgün girdi."""
    host: str | None = None
    port: int | None = None


def parse_target(raw: str) -> Target:
    text = raw.strip()
    if not text:
        raise TargetError("invalid_target", "Hedef boş olamaz.")

    parts = urlsplit(text)
    scheme = parts.scheme.lower()

    if scheme in _NETWORK_SCHEMES:
        try:
            host = parts.hostname
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise TargetError("invalid_target", f"Geçersiz URL: {text}") from exc
        if not host:
            raise TargetError("invalid_target", f"URL'de sunucu adı yok: {text}")
        return Target(kind="url", url=text, requested=raw, host=host, port=port)

    if scheme == "file":
        if parts.netloc not in ("", "localhost"):
            raise TargetError("invalid_target", "Uzak file:// adresleri desteklenmez.")
        return _file_target(Path(url2pathname(parts.path)), raw)

    # Şema yok ya da tek harf (Windows sürücü harfi, ör. C:\sayfa.html): dosya yolu.
    if len(scheme) <= 1:
        return _file_target(Path(text).expanduser(), raw)

    raise TargetError(
        "unsupported_scheme",
        f"Desteklenmeyen şema: '{scheme}:'. Yalnızca http, https ve yerel dosya kabul edilir.",
    )


def _file_target(path: Path, raw: str) -> Target:
    if not path.is_file():
        raise TargetError("file_not_found", f"Dosya bulunamadı: {path}")
    return Target(kind="file", url=path.resolve().as_uri(), requested=raw)
