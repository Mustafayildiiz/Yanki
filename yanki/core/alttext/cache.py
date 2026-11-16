"""Alt metin analiz önbelleği (ALT-09, NFR-01).

Görsele bağlı çıktılar (açıklama) görselin içerik karması ve model
revizyonuyla; metne bağlı çıktılar (çeviri, benzerlikler) ayrıca metnin
karmasıyla anahtarlanır. Böylece aynı görsel farklı alt metinlerle
kullanıldığında açıklama yeniden üretilmez, benzerlikler ise doğru metinle
hesaplanır.

Depolama stdlib ``sqlite3``'tür; ek bağımlılık yoktur. Önbellek dizini
silinerek tamamen temizlenir (SRS §7.1).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path

_SCHEMA = "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


def default_cache_dir(configured: str | None) -> Path:
    if configured:
        return Path(configured)
    env = os.environ.get("YANKI_CACHE_DIR")
    return Path(env) if env else Path.cwd() / ".yanki" / "cache"


def key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class AnalysisCache:
    """Basit anahtar–değer önbelleği. ``path=None`` yalnızca bellekte tutar."""

    def __init__(self, path: Path | None) -> None:
        self._mem: dict[str, str] = {}
        self._db: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self.hits = 0
        if path is not None:
            path.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path / "alttext.sqlite3", check_same_thread=False)
            self._db.execute(_SCHEMA)
            self._db.commit()

    def get(self, k: str) -> str | None:
        with self._lock:
            if k in self._mem:
                self.hits += 1
                return self._mem[k]
            if self._db is not None:
                row = self._db.execute("SELECT value FROM kv WHERE key = ?", (k,)).fetchone()
                if row is not None:
                    self._mem[k] = row[0]
                    self.hits += 1
                    return str(row[0])
        return None

    def put(self, k: str, value: str) -> None:
        with self._lock:
            self._mem[k] = value
            if self._db is not None:
                self._db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (k, value))
                self._db.commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
