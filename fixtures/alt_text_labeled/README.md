# Set A: etiketli alt metin seti (SRS §8.1)

Bu klasör henüz boş. Set A hazır olduğunda buraya konur. Model seçimi (SRS §8.3) ve NFR-03 ölçümü (makro F1 ≥ 0,80) bu setle yapılır:

```bash
docker compose run --rm yanki python experiments/alt_eval.py --skip-bc --verbose
docker compose run --rm yanki python experiments/alt_eval.py --skip-bc --grid --download
```

## Biçim

- Görseller bu klasörün altında durur (ör. `images/0001.jpg`). Desteklenen biçimler JPEG, PNG, GIF, WebP ve BMP'dir.
- Etiketler `labels.csv` dosyasındadır. Dosya UTF-8 kodlu, başlık satırlı ve virgülle ayrılmıştır.

| Sütun | Açıklama |
| --- | --- |
| `image` | Bu klasöre göre görsel yolu |
| `alt` | Değerlendirilen alt metin. **Boş bırakılırsa** "alt özniteliği yok" sayılır; beklenen sınıf `eksik` olur. |
| `page_lang` | Sayfanın dili (`tr`, `en` …). Boşsa `tr` sayılır. |
| `context` | Görselin sayfadaki bağlamı (başlık ya da çevreleyen metin). Şimdilik yalnızca kayıt içindir. |
| `label_a` | Birinci etiketleyicinin sınıfı |
| `label_b` | İkinci etiketleyicinin sınıfı |
| `label` | Uzlaşılmış son sınıf; ölçümde bu kullanılır |

Sınıflar `uygun`, `yetersiz`, `uyumsuz` ve `eksik` değerlerinden biridir (ALT-06). Etiketleyiciler arası uyuşma Cohen kappa ile raporlanır (`label_a` ve `label_b`).

## Sınıf tanımları (varsayım, danışmanla doğrulanmalı)

SRS sınıfları tanımlamıyor. `fixtures/` beklentileri ve etiketleme için şu tanımlar kullanılır:

- **uygun:** Alt metin görselin içeriğini ya da sayfadaki işlevini aktarıyor. Bağlantılı logoda "X ana sayfa" uygundur.
- **yetersiz:** Alt metin görselle ilgili ama içeriği ya da işlevi aktarmayacak kadar genel ("Hayvanlar", "Bina"). Dosya adı ve genel ifadeler ("resim", "image1.png") de bu sınıftadır.
- **uyumsuz:** Alt metin görselle anlamca ilgisiz (köpek fotoğrafına "kırmızı spor araba").
- **eksik:** Bilgi verici görselin alt metni ve erişilebilir adı yok.

Dekoratif görseller (`alt=""`) Set A'ya konmaz (ALT-02).

## Lisans

Görsellerin kaynağı ve lisansı ayrı bir `SOURCES.md` dosyasında belgelenmelidir. Yalnızca lisansı izin veren görseller kullanılır.
