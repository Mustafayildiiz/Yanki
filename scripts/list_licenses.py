"""Kilit dosyasındaki paketlerin lisans tablosunu üretir (NFR-15).

Konteynerde çalıştırın (paketler kurulu olmalı):

    docker compose run --rm -T yanki python scripts/list_licenses.py > /tmp/licenses.md
    docker compose run --rm -T yanki python scripts/list_licenses.py requirements-ml.lock

Çıktı docs/THIRD_PARTY_LICENSES.md içindeki Python tablosuna yapıştırılır.
Lisans, paket metadata'sındaki License-Expression, License ya da
"License ::" sınıflandırıcılarından okunur; belirsiz olanlar elle doğrulanır.
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_LINE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)(?:\s*;\s*([^\\]+?))?\s*\\?$")


def license_of(name: str) -> str:
    try:
        meta = metadata(name)
    except PackageNotFoundError:
        return "(kurulu değil; elle doğrulayın)"
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return ", ".join(classifiers)
    text = (meta.get("License") or "").strip().splitlines()
    return text[0][:60] if text else "(belirtilmemiş)"


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "requirements.lock"
    lock = (ROOT / name).read_text(encoding="utf-8")
    rows = []
    for line in lock.splitlines():
        m = LOCK_LINE.match(line)
        if m:
            name, ver, marker = m.group(1).lower(), m.group(2), (m.group(3) or "").strip()
            rows.append((name, ver, license_of(name), marker))
    out = ["| Paket | Sürüm | Lisans | Not |", "| --- | --- | --- | --- |"]
    out += [f"| {n} | {v} | {lic} | {mk.replace('|', '/')} |" for n, v, lic, mk in rows]
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
