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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    """Model çıkarım aygıtı (NFR-12). Faz 2'de kullanılır."""

    scan: ScanConfig = Field(default_factory=ScanConfig)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    keyboard: KeyboardConfig = Field(default_factory=KeyboardConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


def load_config(path: Path | None = None) -> YankiConfig:
    """Varsayılanları, isteğe bağlı bir TOML dosyasıyla birleştirerek yükler."""
    if path is None:
        return YankiConfig()
    with Path(path).open("rb") as fh:
        data = tomllib.load(fh)
    return YankiConfig.model_validate(data)
