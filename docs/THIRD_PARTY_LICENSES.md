# Üçüncü taraf bileşenler ve lisanslar

Bu liste NFR-15 için tutulur. Bağımlılıklar `requirements.lock` dosyasında kesin sürüm ve hash ile sabitlenmiştir. Yeni bir bağımlılık eklendiğinde lisansı kontrol edilir ve bu tabloya eklenir. `tests/test_project.py`, kilitli her paketin burada listelenmesini zorunlu kılar.

Tablo `scripts/list_licenses.py` ile üretilir; belirsiz satırlar elle doğrulanır.

## Gömülü bileşenler

| Bileşen | Sürüm | Lisans | Konum | Doğrulama |
| --- | --- | --- | --- | --- |
| axe-core | 4.14.0 | MPL-2.0 | `yanki/core/vendor/axe.min.js` (lisans metni: `axe-core.LICENSE`) | npm tarball `dist.integrity` (sha512) ile doğrulandı. Dosyanın SHA-256 değeri `yanki/core/rules.py` içinde sabitlidir ve testle denetlenir. |

axe-core değiştirilmeden, özgün lisans başlığıyla dağıtılır. MPL-2.0 dosya düzeyinde bir copyleft lisansıdır. Dosyanın kendisi değiştirilirse değişiklikler aynı lisansla yayımlanmalıdır.

## Çalışma ortamı

| Bileşen | Sürüm | Lisans | Not |
| --- | --- | --- | --- |
| Playwright Docker imajı | `mcr.microsoft.com/playwright/python:v1.65.2-noble` | Apache-2.0 (Playwright). İmajdaki Ubuntu paketleri kendi lisanslarıyla gelir. | Chromium 159.0.8154.6 içerir (BSD-3-Clause ve bileşenleri). |

## Python paketleri (`requirements.lock`)

| Paket | Sürüm | Lisans | Not |
| --- | --- | --- | --- |
| annotated-doc | 0.0.6 | MIT |  |
| annotated-types | 0.8.0 | MIT |  |
| colorama | 0.4.6 | BSD-3-Clause | Yalnızca Windows (`sys_platform == 'win32'`); lisans elle doğrulandı |
| coverage | 7.17.3 | Apache-2.0 | geliştirme |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |  |
| iniconfig | 2.3.1 | MIT | geliştirme |
| markdown-it-py | 4.3.0 | MIT |  |
| mdurl | 0.1.2 | MIT |  |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | geliştirme |
| playwright | 1.65.2 | Apache-2.0 | Docker taban imajıyla aynı sürüm |
| pluggy | 1.7.0 | MIT | geliştirme |
| pydantic | 2.14.3 | MIT |  |
| pydantic-core | 2.50.4 | MIT |  |
| pygments | 2.21.0 | BSD-2-Clause |  |
| pytest | 9.2.0 | MIT | geliştirme |
| pytest-cov | 7.1.0 | MIT | geliştirme |
| rich | 15.0.0 | MIT |  |
| ruff | 0.17.5 | MIT | geliştirme |
| shellingham | 1.5.4 | ISC |  |
| typer | 0.29.1 | MIT |  |
| typing-extensions | 4.16.0 | PSF-2.0 |  |
| typing-inspection | 0.4.5 | MIT |  |

## Python paketleri, ML ekstrası (`requirements-ml.lock`, yalnızca Linux)

Docker imajı `requirements-ml.lock` dosyasını kurar. Bu kilit yukarıdaki paketlere ek olarak alt metin modelleri (Faz 2) için aşağıdakileri içerir. `torch`, CPU indeksinden (`download.pytorch.org/whl/cpu`) gelir; CUDA paketleri kurulmaz. Ham metadata'daki belirsiz lisans adları SPDX'e çevrilip elle doğrulandı.

| Paket | Sürüm | Lisans | Not |
| --- | --- | --- | --- |
| anyio | 4.16.0 | MIT | huggingface-hub bağımlılığı |
| click | 8.5.1 | BSD-3-Clause | |
| filelock | 4.2.10 | MIT | |
| fsspec | 2026.9.0 | BSD-3-Clause | |
| h11 | 0.16.0 | MIT | |
| hf-xet | 1.8.0 | Apache-2.0 | Model indirme |
| httpcore2 | 2.16.2 | BSD-3-Clause | |
| httpx2 | 2.16.2 | BSD-3-Clause | |
| huggingface-hub | 2.4.0 | Apache-2.0 | Model indirme; yalnızca `yanki models download` ağa çıkar |
| idna | 3.20 | BSD-3-Clause | |
| jinja2 | 3.1.6 | BSD-3-Clause | torch bağımlılığı |
| markupsafe | 3.0.4 | BSD-3-Clause | |
| mpmath | 1.3.0 | BSD-3-Clause | |
| networkx | 3.6.1 / 3.7 | BSD-3-Clause | Python sürümüne göre |
| numpy | 2.4.6 / 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Python sürümüne göre |
| pillow | 13.0.0 | MIT-CMU | Görsel çözme ve küçük resim |
| pyyaml | 6.0.3 | MIT | |
| regex | 2026.11.15 | Apache-2.0 AND CNRI-Python | |
| safetensors | 0.8.0 | Apache-2.0 | |
| sentencepiece | 0.2.3 | Apache-2.0 | MarianMT ve E5 belirteçleyicisi |
| setuptools | 84.2.1 | MIT | |
| sympy | 1.14.0 | BSD-3-Clause | |
| tokenizers | 0.23.3 | Apache-2.0 | |
| torch | 2.15.0+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib | Metadata `License-Expression` |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | |
| transformers | 5.23.0 | Apache-2.0 | |
| truststore | 0.10.5 | MIT | |

## Model ağırlıkları (dağıtılmaz; `yanki models download` ile indirilir)

Ağırlıklar imaja ve depoya girmez; `HF_HOME` (`/models` volume'ü) altına indirilir. Her model sabit bir revizyonla (commit SHA) yüklenir; revizyon `yanki/config.py` içindedir ve rapora yazılır (NFR-06). Lisanslar 2026-11-16'da model kartlarından ve kaynak depolardan doğrulandı.

| Model | Revizyon | Lisans | Kullanım ve kart notları |
| --- | --- | --- | --- |
| `Salesforce/blip-image-captioning-base` | `82a3776` | BSD-3-Clause | Varsayılan görsel açıklama (ALT-05). Kart: "This release is for research purposes only in support of an academic paper." Kaynak depo (salesforce/BLIP) Mart 2026'da arşivlendi. |
| `Helsinki-NLP/opus-mt-tr-en` | `19c6542` | Apache-2.0 | Varsayılan çeviri (ALT-04). |
| `Helsinki-NLP/opus-mt-tc-big-tr-en` | `2261c8f` | CC-BY-4.0 | Aday çeviri modeli (SRS §8.3), varsayılan değil. Kullanılırsa atıf zorunlu: Tiedemann & Thottingal (2020), "OPUS-MT — Building open translation services for the World"; kart "Please, cite if you use this model" der. |
| `intfloat/multilingual-e5-small` | `614241f` | MIT | Varsayılan anlamsal benzerlik (ALT-04, ana sinyal). |
| `intfloat/multilingual-e5-base` | `d128750` | MIT | Aday (SRS §8.3), varsayılan değil. |
| `openai/clip-vit-base-patch32` | `3d74acf` | MIT (openai/CLIP deposu, "Copyright (c) 2021 OpenAI") | Görsel–metin benzerliği (ALT-04, ek sinyal). HF kartında lisans alanı yok. Model kartı: "Any deployed use case of the model - whether commercial or not - is currently out of scope." ve yalnızca İngilizce. Danışmanla değerlendirilmeli (açık soru). |
| `sentence-transformers/clip-ViT-B-32-multilingual-v1` | `58edf8c` | Apache-2.0 | Doğrudan çok dilli yol (ALT-04, `approach = "multilingual"`). Görsel kodlayıcısı OpenAI CLIP'tir; yukarıdaki kart notları geçerli. sentence-transformers kütüphanesi olmadan, düz transformers ile yüklenir. |

## Fixture görselleri

`fixtures/images/` altındaki fotoğraflar Wikimedia Commons'tan alınan CC0 ya da kamu malı dosyalardır. Kaynak, yazar ve lisansları [fixtures/images/SOURCES.md](../fixtures/images/SOURCES.md) dosyasında listelenir. SVG'ler proje katkısıdır (CC0).

## Araçlar (dağıtılmaz)

| Araç | Sürüm | Lisans | Kullanım |
| --- | --- | --- | --- |
| uv | 0.13.8 | MIT OR Apache-2.0 | Kilit dosyasını üretir (`scripts/lock.sh`) |
| hatchling | 1.33.0 | MIT | Derleme arka ucu (`pyproject.toml` `[build-system]`) |
