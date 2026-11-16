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
from typing import Literal

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
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


def load_config(path: Path | None = None) -> YankiConfig:
    """Varsayılanları, isteğe bağlı bir TOML dosyasıyla birleştirerek yükler."""
    if path is None:
        return YankiConfig()
    with Path(path).open("rb") as fh:
        data = tomllib.load(fh)
    return YankiConfig.model_validate(data)
