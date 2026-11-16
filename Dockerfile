# syntax=docker/dockerfile:1
#
# Yankı çalışma ve geliştirme imajı (NFR-09, NFR-11, NFR-12).
#
# Taban imaj Playwright'ın resmi Python imajıdır. Chromium ve sistem
# bağımlılıkları içinde hazır gelir. Python paketindeki `playwright` sürümü
# bu etiketle AYNI olmalıdır (1.65.2); aksi hâlde tarayıcı ikilileri uyuşmaz.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    # Model ağırlıkları kalıcı volume'de tutulur; ilk indirmeden sonra çevrimdışı çalışır.
    HF_HOME=/models \
    # GPU varsayılmaz (NFR-12). CUDA yalnızca açıkça seçilirse kullanılır.
    YANKI_DEVICE=cpu

# Taban imajda python3-venv yok; sanal ortam için kurulur.
# Sanal ortam, model önbelleği ve uygulama klasörü root olmayan
# kullanıcıya aittir.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv "$VIRTUAL_ENV" \
 && mkdir -p /app /models \
 && chown -R pwuser:pwuser "$VIRTUAL_ENV" /app /models

WORKDIR /app
USER pwuser

# Bağımlılık katmanı: kilitli ve hash'li requirements.lock (NFR-15).
# Kilit dosyası scripts/lock.sh (uv pip compile) ile üretilir.
# README.md, pyproject.toml'daki `readme` alanı için kopyalanır.
COPY --chown=pwuser:pwuser README.md pyproject.toml* requirements*.lock* ./
RUN if [ -f requirements.lock ]; then pip install -r requirements.lock; fi

# Kaynak kod. Kurulum kilitli bağımlılıklarla yapılır (--no-deps).
COPY --chown=pwuser:pwuser . .
RUN if [ -f pyproject.toml ]; then pip install --no-deps -e .; fi

CMD ["bash"]
