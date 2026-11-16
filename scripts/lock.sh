#!/usr/bin/env bash
# Kilit dosyasını (requirements.lock) yeniden üretir (NFR-15).
#
# Kilit aracı: uv (pip compile kipi). Çıktı tüm platformlar için geçerlidir
# (--universal) ve her paket hash ile doğrulanır (--generate-hashes).
# Docker imajı bu dosyadan kurar; geliştirme araçları (pytest, ruff) da
# konteynerde gerektiği için `dev` ekstrası kilide dahildir.
#
# Ana makineye Python kurmadan, taban imajda çalıştırın:
#
#   MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/src" -w /src \
#       mcr.microsoft.com/playwright/python:v1.65.2-noble bash scripts/lock.sh
set -euo pipefail

UV_VERSION="0.13.8"

# Taban imajda python3-venv yok; uv geçici bir dizine kurulur.
python3 -m pip install --quiet --target /tmp/uvpkg "uv==${UV_VERSION}"
/tmp/uvpkg/bin/uv pip compile pyproject.toml \
    --extra dev \
    --universal \
    --python-version 3.11 \
    --generate-hashes \
    --no-header \
    --output-file requirements.lock

echo "requirements.lock güncellendi (uv ${UV_VERSION})."
