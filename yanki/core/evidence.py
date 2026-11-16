"""Kanıt dosyaları (KLV-10, ALT-08, SRS §6.4).

Rapordaki ``evidence.screenshot`` yolları rapor dosyasına görelidir
(``evidence/<karma>.png``). Dosyalar içerik karmasıyla adlandırıldığı için
aynı klasöre yazılan raporlar birbirinin kanıtını bozmaz ve aynı girdi aynı
adı üretir (NFR-06).
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from yanki.core.schema import ScanReport


class ArtifactStore:
    """Kanıt PNG'leri, içerik karmasıyla adlandırılır (klavye ve alt metin modülleri)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def add(self, png: bytes | None) -> str | None:
        if not png:
            return None
        name = f"evidence/{hashlib.sha256(png).hexdigest()[:16]}.png"
        self.files[name] = png
        return name


def save_evidence(report: ScanReport, report_dir: Path) -> list[Path]:
    """``report.artifacts`` içeriğini ``report_dir`` altına yazar; yazılan yolları döner."""
    written: list[Path] = []
    base = Path(report_dir).resolve()
    for name, data in sorted(report.artifacts.items()):
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"geçersiz kanıt yolu: {name}")
        path = base.joinpath(*rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not (path.is_file() and path.read_bytes() == data):
            path.write_bytes(data)
        written.append(path)
    return written
