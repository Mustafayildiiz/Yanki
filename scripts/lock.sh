#!/usr/bin/env bash
# Kilit dosyalarını yeniden üretir (NFR-15).
#
# Kilit aracı: uv (pip compile kipi). Her paket hash ile doğrulanır
# (--generate-hashes). İki kilit üretilir:
#
# - requirements.lock: temel + dev. Tüm platformlar (--universal). Windows,
#   Linux ve macOS native CI işleri bunu kurar (NFR-11).
# - requirements-ml.lock: temel + dev + ml (torch CPU, transformers ...).
#   Yalnızca Linux (Docker imajı, x86_64 ve aarch64). torch, pyproject'teki
#   [tool.uv.sources] gereği CPU indeksinden gelir; CUDA paketleri çekilmez.
#   Platform kısıtı ([tool.uv] environments) temel kilidi daraltmasın diye
#   pyproject'in geçici bir kopyasına eklenir.
#
# Ana makineye Python kurmadan, taban imajda çalıştırın:
#
#   MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/src" -w /src \
#       mcr.microsoft.com/playwright/python:v1.65.2-noble bash scripts/lock.sh
set -euo pipefail

UV_VERSION="0.13.8"

# Taban imajda python3-venv yok; uv geçici bir dizine kurulur.
python3 -m pip install --quiet --root-user-action=ignore --target /tmp/uvpkg "uv==${UV_VERSION}"
UV=/tmp/uvpkg/bin/uv

"$UV" pip compile pyproject.toml \
    --extra dev \
    --universal \
    --python-version 3.11 \
    --generate-hashes \
    --no-header \
    --output-file requirements.lock

ML_DIR="$(mktemp -d)"
trap 'rm -rf "$ML_DIR"' EXIT
cp pyproject.toml README.md "$ML_DIR/"
printf '\n[tool.uv]\nenvironments = ["sys_platform == '"'"'linux'"'"'"]\n' >> "$ML_DIR/pyproject.toml"
# uv, [tool.uv] ayarlarını çalışma dizinindeki projeden okur; bu yüzden
# derleme geçici dizinin içinde yapılır.
OUT="$PWD/requirements-ml.lock"
(
    cd "$ML_DIR"
    "$UV" pip compile pyproject.toml \
        --extra dev \
        --extra ml \
        --universal \
        --python-version 3.11 \
        --generate-hashes \
        --no-header \
        --output-file "$OUT"
)
# pip, `torch==X+cpu` paketini bulmak için CPU indeksini bilmeli. Her paket
# hash ile sabit olduğundan ek indeks başka bir dosyanın kurulmasına yol açmaz.
sed -i '1i --extra-index-url https://download.pytorch.org/whl/cpu' "$OUT"
if grep -q "sys_platform == 'darwin'\|sys_platform == 'win32'" "$OUT"; then
    echo "HATA: requirements-ml.lock yalnızca Linux için olmalı." >&2
    exit 1
fi

echo "requirements.lock ve requirements-ml.lock güncellendi (uv ${UV_VERSION})."
