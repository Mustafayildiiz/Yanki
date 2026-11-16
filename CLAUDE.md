# CLAUDE.md

Bu dosya, Yankı deposunda çalışan Claude Code (ve diğer agent'lar) için çalışma talimatıdır.

## Proje

**Yankı**, web sayfalarının erişilebilirliğini (WCAG 2.2) denetleyen ve sonucu tek bir puanda toplayan, yapay zeka destekli bir araçtır. Üç modülü vardır:

1. **Kural kontrolü:** axe-core ile baz çizgisi.
2. **Alt metin analizi:** Alt metnin görsele ve sayfaya anlamsal uyumu.
3. **Klavye testi:** Tab, Enter ve Escape ile gezinme; odak sırası, klavye tuzağı, modal davranışı.

Hedef kitle profesyonellerdir. Bu yüzden her bulgu kanıt, güven skoru ve WCAG ölçütü taşır. Ayırt edici iddia, kural tabanlı araçların kaçırdığı hataları **yeni yanlış alarm üretmeden** yakalamaktır. Her tasarım kararı bu iddiayı ölçülebilir kılacak şekilde verilir.

## Ana kaynak

- **[docs/SRS.md](docs/SRS.md) bağlayıcıdır.** Gereksinim numaraları (TAR-01, ALT-04, KLV-08, NFR-03 …) oradan gelir.
- Bu dosya ile SRS çelişirse **SRS geçerlidir**. Çelişkiyi kullanıcıya bildir.
- SRS'te olmayan bir özellik ekleme. Gerekli görüyorsan önce öner.

## Mevcut durum

- **Aktif faz: Faz 0 (iskelet).** Docker ortamı (`Dockerfile`, `compose.yaml`) hazır; uygulama kodu henüz yok.
- Her fazın sonunda testler geçmelidir. Ardından kısa bir özet ver: ne yapıldı, hangi gereksinimler karşılandı, hangi sorular açık kaldı. Bir faz bitmeden sonrakine geçme.

| Faz | Kapsam | Gereksinimler |
| --- | --- | --- |
| 0 | Repo, `pyproject.toml`, kilit dosyası, CI, pytest; `yanki scan <url>` → Playwright + axe-core → ortak şema (JSON). Docker ortamı hazır. | TAR-01..05, KUR-01..03, RAP-01 (JSON), NFR-08, NFR-11, NFR-15 |
| 1 | Klavye modülü; `fixtures/known_issues` ve `fixtures/clean`, beklenen bulgularla birlikte | KLV-01..10, NFR-04, NFR-05 |
| 2 | Alt metin hattı (çeviri, BLIP, CLIP, E5), önbellek, sınıflandırma; `experiments/` karşılaştırma betiği | ALT-01..12, NFR-01, NFR-03, NFR-12, NFR-13 |
| 3 | Puanlama, HTML/PDF rapor, `--min-score` / `--fail-on`, çıkış kodları | PUA-01..05, RAP-01..06, CLI-01..04 |
| 4 | FastAPI + SQLite, arka plan taraması, ilerleme bilgisi; yalnızca API'yi çağıran Streamlit | API-01..05, UI-01, UI-03..06 |
| 5 | `yanki batch`, Lighthouse/WAVE içe aktarma, "yalnızca Yankı'nın bulduğu bulgular", CSV, tohum ve sürüm kaydı | KAR-01..05 |
| 6 | React + Vite (UI-02). **Yalnızca kullanıcı istediğinde başla.** | UI-02 |

## Mimari: altın kural

`yanki/core` **hiçbir arayüze bağımlı olmaz.** CLI (Typer), API (FastAPI) ve Streamlit yalnızca çekirdeği çağıran ince katmanlardır. React'e geçiş çekirdeğe dokunmayı gerektirmemelidir.

Hedeflenen yapı:

```text
yanki/core/{scan.py, rules.py, alttext/, keyboard/, scoring.py, report/, schema.py}
yanki/{cli.py, api/, ui/streamlit_app.py, config.py}
fixtures/{known_issues/, clean/, alt_text_labeled/}   # Set B, C, A
experiments/   tests/   docs/
```

## Teknoloji kararları

**Kesinleşmiş:**

- Python 3.11+
- Playwright (Chromium) ve enjekte edilen axe-core
- FastAPI, Typer, SQLite
- Jinja2 (autoescape açık) ve `page.pdf(tagged=True, outline=True)`
- Streamlit
- Docker, pytest
- Karşılaştırma için Lighthouse CLI ve WAVE API (yalnızca deneylerde)

**Henüz kesinleşmemiş. Kararı sen verme, yapılandırmayla değiştirilebilir kıl:**

- Görsel açıklama: BLIP-base (varsayılan) veya BLIP-2 (yalnızca GPU/Colab)
- Uyum yöntemi: (a) önce İngilizceye çevirip standart CLIP veya (b) doğrudan çok dilli CLIP
- E5 boyutu: small veya base
- Çeviri modeli: `opus-mt-tr-en` (varsayılan) veya `opus-mt-tc-big-tr-en` (lisansı kullanmadan önce kontrol edilmeli)

Seçim, SRS §8.3'teki protokolle Set A üzerinde yapılır.

## Çalışma kuralları

1. **İzlenebilirlik:** Her modül, test ve commit mesajı ilgili gereksinim numarasını anar (ör. `KLV-05`).
2. **Test önce:** Her "Z" öncelikli gereksinim için en az bir otomatik test yazılır. Klavye ve alt metin modülleri `fixtures/` altındaki bilinen hatalı sayfalara karşı test edilir. Her fixture'ın yanında beklenen bulgular (`*.expected.json`) bulunur.
3. **CPU öncelikli:** Kod GPU olmadan çalışır. GPU varsa `--device cuda` ile seçilir. Geliştirme makinesinde GPU olduğunu varsayma (AMD RX 6700S, ROCm belirsiz).
4. **Yerel çıkarım (NFR-07):** Taranan sayfanın içeriği ve görselleri üçüncü taraf servislere gönderilmez. İnternetten yalnızca model ağırlıkları indirilir.
5. **Belirlenimcilik (NFR-06):** Aynı sürüm ve girdi aynı bulguları ve aynı puanı üretir. Tohumları sabitle. Tohum, araç sürümleri ve model sürümleri rapora yazılır.
6. **Dayanıklılık (NFR-16):** Tek bir görselin ya da öğenin hatası taramayı durdurmaz. Hata raporda "analiz edilemedi" olarak görünür.
7. **Güvenlik (NFR-08, NFR-09):**
   - Yalnızca `http` ve `https` kabul edilir.
   - Yerel ağ ve `localhost` varsayılan olarak engellidir (SSRF); açık bir ayarla izin verilir.
   - Taranan sayfanın kodu ana süreçte çalışmaz.
   - Raporlardaki sayfa kaynaklı metin her zaman kaçışlanır.
8. **Kendi erişilebilirliği (NFR-10):** Arayüz ve HTML rapor WCAG 2.2 AA'ya uyar. Yankı'nın kendi çıktısı Yankı ile taranır ve kritik ya da önemli bulgu kalmaz. Renk tek başına bilgi taşımaz.
9. **Yapay zeka bulguları:** Her bulgu 0–1 arası `confidence` taşır. Eşiğin altındakiler `needs_review: true` olur ve kesin bulgulardan ayrı gösterilir. Kural tabanlı sonuçlar yapay zeka bulgusu gibi sunulmaz; tersi de geçerlidir.
10. **Belirsizlikte dur:** Gereksinim eksik ya da çelişkiliyse varsayımını yaz ve kullanıcıya sor. Sayısal hedefleri (F1, duyarlılık vb.) kendin değiştirme.
11. **Bağımlılıklar (NFR-15):** Sürümler kilitlenir (`uv.lock` veya `requirements.lock`). Yeni bağımlılığın lisansı kontrol edilip belgelenir.

## Bulgu şeması

Bulgular pydantic ile doğrulanır (`yanki/core/schema.py`). Alanlar:

- `id`, `type`, `rule`, `severity`
- `wcag[]`, `selector`
- `evidence{alt, alt_translated, caption, similarity, screenshot, …}`
- `confidence`, `needs_review`, `recommendation`

Değer kümeleri:

- `type`: `rule` | `alt_text` | `keyboard`
- `severity`: `critical` | `important` | `moderate` | `low`

Özgün ve çevrilmiş alt metin kanıtta birlikte saklanır. Tam örnek [README.md](README.md#bulgu-şeması) ve SRS §6.4'te bulunur.

## Puanlama

```text
S_m = 100 * (1 - min(1, sum(w_i * c_i) / N_m))
P   = a_kural * S_kural + a_icerik * S_icerik + a_klavye * S_klavye
```

Başlangıç değerleri:

- `w` (critical / important / moderate / low): 1,0 / 0,6 / 0,3 / 0,1
- `a` (kural / içerik / klavye): 0,30 / 0,30 / 0,40
- Kritik bulgu tavanı: P ≤ 60

Hepsi `yanki/config.py` içinde yapılandırılabilir olur ve rapora yazılır. **Kodda sabitleme.** Değerler danışmanla görüşülüp güncellenecek.

## Kabul hedefleri (başlangıç değerleri)

- Set A'da makro F1 ≥ 0,80
- Set B'de klavye hatalarının ≥ %90'ı yakalanır
- Set C'de yanlış alarm sayfa başına ≤ %5
- CPU'da görsel başına ≤ 3 sn; ortalama sayfa ≤ 5 dk (8 çekirdek, 16 GB RAM)
- Puan sıralaması bilinen hata yüküyle uyumlu olmalı (Spearman)

Bir hedef tutmazsa gizleme; sapmayı ve olası nedenini raporla.

## Yapma

- Kullanıcıya sormadan model, kütüphane ya da mimari değiştirme.
- Bulguları sayfa verisiyle birlikte dış servise gönderme.
- GPU'ya bağımlı kod yazma.
- Test geçsin diye fixture beklentilerini gevşetme.
- Kapsam dışı özellik ekleme: Flutter arayüz, mobil uygulama denetimi, otomatik düzeltme, gerçek ekran okuyucu testi.

## Docker: birincil ortam

Proje **Docker içinde** çalıştırılır ve test edilir. Python, Playwright ve modelleri ana makineye (Windows) kurma. Testleri, taramaları ve betikleri konteynerde çalıştır.

- **Taban imaj:** `mcr.microsoft.com/playwright/python:v1.65.2-noble`. Faz 0'da `pyproject.toml` / kilit dosyasındaki `playwright` sürümü imaj etiketiyle **aynı** sabitlenmeli (`1.65.2`). Biri yükseltilirse diğeri de yükseltilir.
- **Python ortamı:** `/opt/venv` içindedir. `requirements.lock` varsa ondan kurulur, ardından paket `pip install --no-deps -e .` ile kurulur. Kilit aracı (uv veya pip-tools) Faz 0'da seçilecek; seçilen araç kilit dosyasını `requirements.lock` olarak dışa aktarmalı ya da Dockerfile buna göre güncellenmeli.
- **Kaynak kod:** `/app` dizinine bind mount ile bağlanır. Bağımlılık değişikliğinden sonra `docker compose build` gerekir.
- **Modeller:** `HF_HOME=/models` (`yanki-models` volume'ü).
- **Kullanıcı ve yetkiler:** Konteyner root olmayan `pwuser` ile, `cap_drop: ALL` ve `no-new-privileges` altında çalışır. Bu ayarları gevşetme. Gerekiyorsa önce sor.
- **`/dev/shm`:** `shm_size: 1gb` Chromium için gereklidir.
- **Yerel ağ:** Konteynerde `localhost` konteynerin kendisidir. Ana makinedeki bir sayfa `host.docker.internal` ile ve SSRF izin ayarı açılarak taranır. Fixture sayfaları ağ yerine dosya yoluyla (`file://`, TAR-01) ya da konteyner içi sunucuyla test edilir.
- **Satır sonları:** `.gitattributes` dosyası LF'yi zorlar; konteynerde çalışan betikler CRLF ile bozulmaz.

## Komutlar

```bash
docker compose build                                   # imajı oluştur / bağımlılık değişince yeniden oluştur
docker compose run --rm yanki                          # konteynerde kabuk
docker compose run --rm yanki pytest                   # testler (Faz 0 sonrası)
docker compose run --rm yanki yanki scan <url>         # tarama (Faz 0 sonrası)
```

Lint ve kapsam komutları Faz 0'da eklenecek.

## Ortam

- Geliştirme makinesi Windows'tur. Docker sayesinde Linux konteynerinde çalışılır; kod yine de Windows, Linux ve macOS'ta çalışmalıdır (NFR-11). Yol işlemlerinde `pathlib` kullan.
- Docker Desktop AMD GPU'yu (RX 6700S) konteynere geçirmez. Konteyner her zaman CPU ile çalışır.
- Ağır model deneyleri Colab veya Kaggle GPU'sunda yapılır. Seçilen yapının CPU süresi ayrıca ölçülür.

## Açık sorular

1. SRS §6.4'teki şema örneğinde `evidence.alt_translated` yok. Oysa ALT-04 özgün ve çevrilmiş metnin birlikte saklanmasını istiyor ve agent talimatındaki şema bu alanı içeriyor. Şimdilik alan şemaya dahil edildi; SRS'in güncellenmesi gerekebilir.
2. CLI-04'ün (`--device cpu|cuda`) önceliği kaynak SRS'te belirtilmemiş.
3. `opus-mt-tc-big-tr-en` modelinin lisansı kullanılmadan önce kontrol edilmeli.
4. Proje lisansı henüz belirlenmedi.
