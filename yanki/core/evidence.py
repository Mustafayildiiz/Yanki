"""Kanıt dosyalarının diske yazılması (KLV-10, SRS §6.4).

Rapordaki ``evidence.screenshot`` yolları rapor dosyasına görelidir
(``evidence/<karma>.png``). Dosyalar içerik karmasıyla adlandırıldığı için
aynı klasöre yazılan raporlar birbirinin kanıtını bozmaz.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from yanki.core.schema import ScanReport


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
