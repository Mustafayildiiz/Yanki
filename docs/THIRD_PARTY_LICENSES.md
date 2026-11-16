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

## Araçlar (dağıtılmaz)

| Araç | Sürüm | Lisans | Kullanım |
| --- | --- | --- | --- |
| uv | 0.13.8 | MIT OR Apache-2.0 | Kilit dosyasını üretir (`scripts/lock.sh`) |
| hatchling | 1.33.0 | MIT | Derleme arka ucu (`pyproject.toml` `[build-system]`) |
