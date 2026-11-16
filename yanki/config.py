"""Yankı yapılandırması.

Model seçimleri, eşikler, ağırlıklar ve tarama ayarları burada tanımlanır;
kodda sabitlenmez. Tüm değerler bir TOML dosyasıyla (``--config``) ya da
arayüz katmanından geçirilen değerlerle değiştirilebilir ve kullanılan
yapılandırma rapora yazılır (PUA-02, NFR-06, NFR-13).

Puanlama değerleri SRS §7.2'deki başlangıç değerleridir; danışmanla
görüşülüp kalibre edilecektir. Puanlama mantığı Faz 3'te eklenir.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["critical", "important", "moderate", "low"]


class _Strict(BaseModel):
    # Yazım hatalı anahtarlar sessizce yok sayılmasın.
    model_config = ConfigDict(extra="forbid")


class ScanConfig(_Strict):
    """Sayfa yükleme ve tarayıcı ayarları (TAR-02, TAR-04, NFR-08, NFR-09)."""

    navigation_timeout_ms: int = Field(30_000, gt=0)
    """Sayfanın ``load`` olayına kadar beklenecek en uzun süre (TAR-02)."""

    network_idle_timeout_ms: int = Field(10_000, ge=0)
    """``load`` sonrasında ağın durulması için beklenecek en uzun süre (TAR-02).

    Süre dolarsa tarama durmaz; raporda uyarı olarak görünür. Sürekli
    yoklama yapan sayfalar ağ hiç durulmadığı için aksi hâlde taranamazdı.
    """

    viewport_width: int = Field(1280, gt=0)
    viewport_height: int = Field(800, gt=0)
    locale: str = "tr-TR"
    """Tarayıcı dili; ``Accept-Language`` ve ``navigator.language`` (TAR-04)."""

    allow_private_network: bool = False
    """Yerel ağ, ``localhost`` ve özel IP adreslerine izin ver (NFR-08)."""

    fail_on_http_error: bool = True
    """Ana belge 4xx/5xx dönerse taramayı ``http_error`` ile sonlandır (TAR-05)."""

    chromium_sandbox: bool = False
    """Chromium'un kendi sanal alanı. Docker'da ``cap_drop: ALL`` altında
    kullanıcı ad alanı olmadığı için kapalıdır; yalıtım konteynerle sağlanır."""

    context_max_chars: int = Field(300, gt=0)
    """Her öğe için saklanan çevreleyen metnin en fazla uzunluğu (TAR-03)."""


class RulesConfig(_Strict):
    """axe-core ayarları (KUR-01, KUR-02)."""

    axe_tags: list[str] = Field(
        default_factory=lambda: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
    )
    """Çalıştırılacak axe kural etiketleri.

    Varsayılan yalnızca WCAG 2.0/2.1/2.2 A ve AA kurallarıdır. Böylece her
    kural bulgusu bir WCAG başarı ölçütüne bağlanır (KUR-02). ``best-practice``
    eklenirse bu kuralların bulgularında ``wcag`` listesi boş kalır.
    """

    severity_map: dict[str, Severity] = Field(
        default_factory=lambda: {
            "critical": "critical",
            "serious": "important",
            "moderate": "moderate",
            "minor": "low",
        }
    )
    """axe ``impact`` değerinden Yankı ciddiyetine eşleme."""

    @field_validator("severity_map")
    @classmethod
    def _covers_all_impacts(cls, value: dict[str, Severity]) -> dict[str, Severity]:
        missing = {"critical", "serious", "moderate", "minor"} - value.keys()
        if missing:
            raise ValueError(f"severity_map eksik axe impact değerleri: {sorted(missing)}")
        return value


KeyboardCheck = Literal[
    "keyboard_trap",
    "focus_not_visible",
    "focus_order",
    "not_focusable",
    "not_activatable",
    "modal_focus_not_moved",
    "modal_focus_escapes",
    "modal_escape_not_closing",
    "modal_focus_not_returned",
    "skip_link_missing",
    "skip_link_broken",
]
"""Klavye modülünün denetim türleri (``evidence.check``)."""

KEYBOARD_CHECKS: tuple[str, ...] = get_args(KeyboardCheck)


def _default_keyboard_severity() -> dict[str, Severity]:
    return {
        # PUA-03: erişimi engelleyen bulgular en yüksek ciddiyettedir.
        "keyboard_trap": "critical",
        "not_focusable": "critical",
        "not_activatable": "critical",
        "focus_not_visible": "important",
        "modal_focus_not_moved": "important",
        "modal_focus_escapes": "important",
        "modal_escape_not_closing": "moderate",
        "modal_focus_not_returned": "moderate",
        "focus_order": "moderate",
        "skip_link_broken": "moderate",
        "skip_link_missing": "low",
    }


def _default_keyboard_confidence() -> dict[str, float]:
    return {
        "keyboard_trap": 1.0,
        "not_focusable": 0.95,
        "not_activatable": 0.95,
        "focus_not_visible": 0.9,
        "modal_focus_not_moved": 0.95,
        "modal_focus_escapes": 0.95,
        "modal_escape_not_closing": 0.95,
        "modal_focus_not_returned": 0.95,
        "focus_order": 0.8,
        "skip_link_broken": 0.9,
        "skip_link_missing": 0.6,
    }


class KeyboardConfig(_Strict):
    """Klavye testi ayarları (KLV-01..10, SRS §9.1).

    Eşikler, ciddiyetler ve güven skorları başlangıç değerleridir; Set B/C
    ölçümleriyle (NFR-04, NFR-05) kalibre edilir.
    """

    enabled: bool = True

    max_steps: int = Field(300, gt=0)
    """Bir yöndeki en fazla Tab basımı. Aşılırsa bulgu değil uyarı üretilir (KLV-05)."""

    stuck_repeats: int = Field(3, ge=2)
    """Odak bu kadar art arda aynı öğede kalırsa klavye tuzağıdır (KLV-05)."""

    composite_stuck_repeats: int = Field(20, ge=2)
    """Bileşik yerel kontrollerde (tarih/saat alanı, ``controls`` taşıyan medya) eşik.
    Chromium bu kontrollerin iç parçaları arasında Tab'lar; ``activeElement`` aynı kalır."""

    settle_ms: int = Field(0, ge=0)
    """Her tuştan sonra iki animasyon karesine ek olarak beklenecek süre."""

    time_budget_s: float = Field(180.0, gt=0)
    """Klavye modülünün toplam süre sınırı. Aşılırsa kalan sondalar atlanır, uyarı yazılır."""

    focus_clip_padding: int = Field(8, ge=0)
    """Odak göstergesi için odaklı öğenin çevresinde kırpılan pay (piksel, KLV-03)."""

    focus_pixel_delta: int = Field(40, ge=1, le=255)
    """Bir pikselin "değişti" sayılması için en az kanal farkı (KLV-03)."""

    focus_min_changed_pixels: int = Field(12, ge=1)
    """Odak göstergesi "görünür" sayılmak için en az değişen piksel sayısı (KLV-03)."""

    order_x_tolerance: int = Field(24, ge=0)
    order_y_tolerance: int = Field(8, ge=0)
    """Odak sırası sapması için konum toleransları (piksel, KLV-04)."""

    max_probes: int = Field(30, ge=0)
    """Enter/Boşluk ve modal sondası yapılacak en fazla öğe (KLV-07, KLV-08)."""

    max_click_probes: int = Field(30, ge=0)
    """Tıklanabilir ama odaklanamaz aday için en fazla tıklama sondası (KLV-06)."""

    probe_wait_ms: int = Field(300, ge=0)
    """Bir tuş ya da tıklamadan sonra etkisini gözlemek için beklenecek süre."""

    modal_focus_wait_ms: int = Field(500, ge=0)
    """Modal açıldıktan sonra odağın içeri taşınması için beklenecek süre (KLV-08)."""

    modal_min_cover: float = Field(0.3, gt=0, le=1)
    """Rolü olmayan sabit bir katmanın modal sayılması için görünüm alanını kaplama oranı."""

    skip_link_min_stops: int = Field(5, ge=1)
    """``main`` öncesinde bu kadar durak varsa atla bağlantısı beklenir (KLV-09)."""

    evidence_max_steps: int = Field(20, ge=1)
    """Bulgu kanıtında saklanan en fazla adım (KLV-10)."""

    review_threshold: float = Field(0.8, ge=0, le=1)
    """Bu güvenin altındaki bulgular ``needs_review`` olur."""

    low_confidence: float = Field(0.6, ge=0, le=1)
    """Sezgisel kanıta dayanan bulguların güveni (ör. rolü olmayan modal)."""

    severity: dict[KeyboardCheck, Severity] = Field(default_factory=_default_keyboard_severity)
    confidence: dict[KeyboardCheck, float] = Field(default_factory=_default_keyboard_confidence)

    @field_validator("severity")
    @classmethod
    def _severity_complete(cls, value: dict[str, Severity]) -> dict[str, Severity]:
        missing = set(KEYBOARD_CHECKS) - value.keys()
        if missing:
            raise ValueError(f"keyboard.severity eksik denetimler: {sorted(missing)}")
        if value["keyboard_trap"] != "critical":
            raise ValueError("klavye tuzağı 'critical' olmalıdır (PUA-03)")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_complete(cls, value: dict[str, float]) -> dict[str, float]:
        missing = set(KEYBOARD_CHECKS) - value.keys()
        if missing:
            raise ValueError(f"keyboard.confidence eksik denetimler: {sorted(missing)}")
        bad = sorted(k for k, v in value.items() if not 0 <= v <= 1)
        if bad:
            raise ValueError(f"keyboard.confidence 0–1 arasında olmalı: {bad}")
        return value


AltCheck = Literal[
    "missing",
    "generic_text",
    "insufficient",
    "mismatch",
    "vague_name",
    "lang_mismatch",
]
"""Alt metin modülünün denetim türleri (``evidence.check``)."""

ALT_CHECKS: tuple[str, ...] = get_args(AltCheck)

AltRuleCheck = Literal["missing", "generic_text", "vague_name", "lang_mismatch"]
"""Kural tabanlı (model gerektirmeyen) alt metin denetimleri."""


class ModelRef(_Strict):
    """Hugging Face model kimliği ve sabit revizyonu (commit SHA; NFR-06, NFR-15)."""

    id: str = Field(min_length=1)
    revision: str = Field(min_length=1)

    def ref(self) -> str:
        return f"{self.id}@{self.revision}"


class AltTextModels(_Strict):
    """Alt metin hattının modelleri (SRS §3.1, §8.3).

    Varsayılanlar SRS'teki varsayılan adaylardır; seçim Set A üzerinde
    §8.3 protokolüyle yapılacaktır. Model değiştirmek kod değişikliği
    gerektirmez (NFR-13): ``captioner`` BLIP ya da BLIP-2, ``translator``
    herhangi bir MarianMT modeli (ör. ``opus-mt-tc-big-tr-en``), ``embedder``
    E5 ailesinden bir model olabilir.
    """

    captioner: ModelRef = Field(
        default_factory=lambda: ModelRef(
            id="Salesforce/blip-image-captioning-base",
            revision="82a37760796d32b1411fe092ab5d4e227313294b",
        )
    )
    translator: ModelRef = Field(
        default_factory=lambda: ModelRef(
            id="Helsinki-NLP/opus-mt-tr-en",
            revision="19c65427cc2af5f191337d4899e0348c4af25902",
        )
    )
    embedder: ModelRef = Field(
        default_factory=lambda: ModelRef(
            id="intfloat/multilingual-e5-small",
            revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
        )
    )
    clip: ModelRef = Field(
        default_factory=lambda: ModelRef(
            id="openai/clip-vit-base-patch32",
            revision="3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        )
    )
    clip_multilingual: ModelRef = Field(
        default_factory=lambda: ModelRef(
            id="sentence-transformers/clip-ViT-B-32-multilingual-v1",
            revision="58edf8cada9e398793dca955574a48cbb7f18be2",
        )
    )
    """Yalnızca ``approach = "multilingual"`` iken kullanılan çok dilli metin kodlayıcı.
    Görsel tarafı ``clip`` modelinin görsel kodlayıcısıdır."""


def _default_alt_severity() -> dict[str, Severity]:
    return {
        "missing": "critical",
        "generic_text": "important",
        "mismatch": "important",
        "insufficient": "moderate",
        "vague_name": "low",
        "lang_mismatch": "low",
    }


def _default_alt_rule_confidence() -> dict[str, float]:
    return {
        "missing": 1.0,
        "generic_text": 0.95,
        # Bağlam WCAG 2.4.4'ü karşılayabilir; sezgisel dil tespiti. Her zaman "elle incele".
        "vague_name": 0.5,
        "lang_mismatch": 0.5,
    }


def _default_generic_words() -> list[str]:
    return [
        # Türkçe
        "resim", "foto", "fotoğraf", "fotograf", "görsel", "gorsel", "görüntü", "imaj",
        "grafik", "simge", "ikon", "logo", "afiş", "banner", "adsız", "başlıksız", "boş",
        # İngilizce
        "image", "img", "photo", "picture", "pic", "graphic", "icon", "untitled",
        "placeholder", "spacer", "blank", "thumbnail", "screenshot",
    ]  # fmt: skip


def _default_vague_names() -> list[str]:
    return [
        # Türkçe
        "tıklayın", "tıkla", "tıklayınız", "buraya", "burada", "buraya tıklayın",
        "buraya tıklayınız", "devam", "devamı", "devamını oku", "devamını okuyun",
        "daha fazla", "daha fazlası", "detay", "detaylar", "ayrıntı", "ayrıntılar",
        "incele", "git", "bağlantı", "link", "tamamı",
        # İngilizce
        "click", "click here", "here", "more", "read more", "learn more", "continue",
        "details",
    ]  # fmt: skip


def _default_review_caption_keywords() -> list[str]:
    # Açıklama modelinin tasarım gereği zayıf olduğu içerikler: metin, grafik, logo.
    return [
        "text", "sign", "chart", "graph", "diagram", "screenshot", "logo", "poster",
        "cover", "magazine", "newspaper", "book", "map", "document", "illustration",
        "drawing", "cartoon", "menu", "label", "icon", "letter", "words",
    ]  # fmt: skip


def _default_stopwords() -> dict[str, list[str]]:
    return {
        "tr": [
            "ve", "bir", "ile", "bu", "şu", "için", "olan", "gibi", "çok", "daha",
            "veya", "ya", "da", "de", "ki", "ne", "üzerinde", "önünde", "yanında",
            "arasında", "içinde", "altında", "üstünde",
        ],
        "en": [
            "the", "a", "an", "of", "with", "and", "on", "in", "at", "is", "are", "to",
            "for", "from", "by", "next", "near", "its", "his", "her", "their", "this",
            "that", "lying", "standing", "sitting",
        ],
    }  # fmt: skip


class AltTextConfig(_Strict):
    """Alt metin ve içerik analizi ayarları (ALT-01..12, NFR-01, NFR-12, NFR-13).

    Eşikler, ağırlıklar ve aralıklar **kalibre edilmemiş başlangıç değerleridir**;
    Set A hazır olduğunda SRS §8.3 protokolüyle belirlenecektir (NFR-03).
    """

    enabled: bool = True

    backend: Literal["transformers", "fake"] = "transformers"
    """Model arka ucu. ``fake``: belirlenimci sahte arka uç (yalnızca test ve
    geliştirme; doğruluk ölçümünde kullanılmaz)."""

    approach: Literal["translate", "multilingual"] = "translate"
    """ALT-04: ``translate`` alt metni önce İngilizceye çevirir, standart CLIP
    kullanır; ``multilingual`` çeviri yapmaz, çok dilli CLIP metin kodlayıcısı
    kullanır. E5 her iki yolda da çok dillidir."""

    models: AltTextModels = Field(default_factory=AltTextModels)

    allow_download: bool = False
    """Model ağırlıkları yerelde yoksa tarama sırasında indirilsin mi. Kapalıyken
    eksik ağırlık "analiz edilemedi" olur; indirme ``yanki models download`` ile
    yapılır. Ağa yalnızca model ağırlıkları için çıkılır (NFR-07)."""

    cache: bool = True
    """Aynı görsel ve metin için sonuçlar önbellekten gelir (ALT-09)."""

    cache_dir: str | None = None
    """Önbellek dizini. ``None``: ``YANKI_CACHE_DIR`` ortam değişkeni ya da
    çalışma dizinindeki ``.yanki/cache``. Silmek için dizinin silinmesi yeterlidir."""

    default_lang: str = "tr"
    """Sayfa ve öğe ``lang`` bildirmiyorsa varsayılan dil."""

    translate_languages: list[str] = Field(default_factory=lambda: ["tr"])
    """Çeviri modelinin kaynak dilleri. İngilizce olmayan ve burada bulunmayan
    dillerde ``translate`` yolu görseli "atlandı" olarak işaretler."""

    max_images: int = Field(100, ge=0)
    """Model ile analiz edilecek en fazla görsel (belge sırasıyla)."""

    max_image_bytes: int = Field(10_000_000, gt=0)
    max_pixels: int = Field(40_000_000, gt=0)
    """Çözülecek en büyük görsel (bayt ve piksel; bellek ve sıkıştırma bombası koruması)."""

    min_size_px: int = Field(24, ge=1)
    """Görünür genişlik ya da yüksekliği bunun altındaki görseller model analizine girmez."""

    screenshot_timeout_ms: int = Field(5_000, gt=0)
    evidence_thumbnail_px: int = Field(512, ge=64)

    caption_max_tokens: int = Field(30, ge=5)
    translate_num_beams: int = Field(4, ge=1)
    e5_prefix: str = "query: "
    batch_size: int = Field(8, ge=1)
    num_threads: int | None = Field(None, ge=1)
    """torch iş parçacığı sayısı. ``None``: torch varsayılanı."""

    e5_range: tuple[float, float] = (0.70, 0.90)
    """E5 kosinüs benzerliğinin 0–1'e eşlendiği aralık. E5 benzerlikleri dar
    bir bantta toplanır (ilgisiz ≈0,70, ilgili ≈0,86)."""

    clip_range: tuple[float, float] = (0.15, 0.32)
    """CLIP kosinüs benzerliğinin 0–1'e eşlendiği aralık."""

    e5_weight: float = Field(0.7, ge=0)
    clip_weight: float = Field(0.3, ge=0)
    """Uyum skoru = ağırlıklı ortalama; E5 ana sinyal, CLIP ek sinyal (ALT-04)."""

    ok_threshold: float = Field(0.55, ge=0, le=1)
    mismatch_threshold: float = Field(0.30, ge=0, le=1)
    """Sınıf eşikleri (ALT-06): skor ≥ ok → uygun; ≥ mismatch → yetersiz; altı → uyumsuz."""

    confidence_margin: float = Field(0.2, gt=0)
    """Skorun en yakın eşiğe uzaklığı bu değere ulaştığında güven 1 olur."""

    review_threshold: float = Field(0.7, ge=0, le=1)
    """Bu güvenin altındaki bulgular ``needs_review`` olur (ALT-08)."""

    functional_image_review: bool = True
    """Bağlantı ya da düğme içindeki görsellerin alt metni işlevi anlatır; model
    bulguları en fazla "elle incele" olur."""

    review_caption_keywords: list[str] = Field(default_factory=_default_review_caption_keywords)
    """Açıklamada bu sözcükler geçerse (metin, grafik, logo …) model bulgusu en fazla
    "elle incele" olur."""

    generic_words: list[str] = Field(default_factory=_default_generic_words)
    image_extensions: list[str] = Field(
        default_factory=lambda: "png jpg jpeg gif webp svg bmp tif tiff avif heic ico".split()
    )
    vague_names: list[str] = Field(default_factory=_default_vague_names)
    stopwords: dict[str, list[str]] = Field(default_factory=_default_stopwords)
    lang_min_words: int = Field(3, ge=1)
    """Dil tespiti (ALT-12) için en az sözcük sayısı."""

    severity: dict[AltCheck, Severity] = Field(default_factory=_default_alt_severity)
    rule_confidence: dict[AltRuleCheck, float] = Field(default_factory=_default_alt_rule_confidence)
    """Kural tabanlı alt metin bulgularının güveni (CLAUDE.md §9)."""

    @field_validator("severity")
    @classmethod
    def _alt_severity_complete(cls, value: dict[str, Severity]) -> dict[str, Severity]:
        missing = set(ALT_CHECKS) - value.keys()
        if missing:
            raise ValueError(f"alt_text.severity eksik denetimler: {sorted(missing)}")
        return value

    @field_validator("e5_range", "clip_range")
    @classmethod
    def _range_ordered(cls, value: tuple[float, float]) -> tuple[float, float]:
        if not value[0] < value[1]:
            raise ValueError("aralığın alt sınırı üst sınırından küçük olmalı")
        return value

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> AltTextConfig:
        if self.mismatch_threshold > self.ok_threshold:
            raise ValueError("mismatch_threshold, ok_threshold'dan büyük olamaz")
        if self.e5_weight + self.clip_weight <= 0:
            raise ValueError("e5_weight + clip_weight pozitif olmalı")
        return self


class ScoringConfig(_Strict):
    """Puanlama parametreleri (SRS §7.2, PUA-02). Faz 3'te kullanılır."""

    severity_weights: dict[Severity, float] = Field(
        default_factory=lambda: {
            "critical": 1.0,
            "important": 0.6,
            "moderate": 0.3,
            "low": 0.1,
        }
    )
    module_weights: dict[Literal["rule", "alt_text", "keyboard"], float] = Field(
        default_factory=lambda: {"rule": 0.30, "alt_text": 0.30, "keyboard": 0.40}
    )
    critical_cap: float = Field(60.0, ge=0, le=100)
    """Kritik bulgu varsa genel puanın üst sınırı."""


def _default_device() -> Literal["cpu", "cuda"]:
    value = os.environ.get("YANKI_DEVICE", "cpu").strip().lower()
    return "cuda" if value == "cuda" else "cpu"


class YankiConfig(_Strict):
    """Kök yapılandırma."""

    seed: int = 0
    """Rastgelelik tohumu; rapora yazılır (NFR-06, KAR-05)."""

    device: Literal["cpu", "cuda"] = Field(default_factory=_default_device)
    """Model çıkarım aygıtı (NFR-12). ``cuda`` seçili ama GPU yoksa CPU kullanılır
    ve uyarı yazılır."""

    scan: ScanConfig = Field(default_factory=ScanConfig)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    keyboard: KeyboardConfig = Field(default_factory=KeyboardConfig)
    alt_text: AltTextConfig = Field(default_factory=AltTextConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


def load_config(path: Path | None = None) -> YankiConfig:
    """Varsayılanları, isteğe bağlı bir TOML dosyasıyla birleştirerek yükler."""
    if path is None:
        return YankiConfig()
    with Path(path).open("rb") as fh:
        data = tomllib.load(fh)
    return YankiConfig.model_validate(data)
