"""Ortak bulgu şeması ve tarama raporu (SRS §6.4, RAP-01, RAP-02, KUR-01).

Üç modül (kural, alt metin, klavye) bulgularını aynı ``Finding`` modeliyle
üretir. Rapor JSON'u ``ScanReport`` modelinin serileştirilmesidir.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "0.1.0"

FindingType = Literal["rule", "alt_text", "keyboard"]
Severity = Literal["critical", "important", "moderate", "low"]
ElementKind = Literal["image", "button", "link", "form_field"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "important": 1, "moderate": 2, "low": 3}

_WCAG_SC = re.compile(r"^[1-4]\.\d{1,2}\.\d{1,2}$")
_FINDING_ID = re.compile(r"^f-\d{4,}$")


class Evidence(BaseModel):
    """Bulgunun kanıtı.

    Bilinen alanlar tiplidir; modüle özgü ek alanlara izin verilir
    (``extra="allow"``). Alt metin bulgularında özgün (``alt``) ve çevrilmiş
    (``alt_translated``) metin birlikte saklanır (ALT-04).
    """

    model_config = ConfigDict(extra="allow")

    # Alt metin modülü (Faz 2)
    alt: str | None = None
    alt_translated: str | None = None
    caption: str | None = None
    similarity: float | None = Field(None, ge=0, le=1)
    screenshot: str | None = None

    # Kural modülü (axe-core)
    html: str | None = None
    """İhlal eden öğenin HTML parçası (sayfa kaynaklı; raporda kaçışlanır)."""
    help: str | None = None
    help_url: str | None = None
    failure_summary: str | None = None
    axe_impact: str | None = None


class Finding(BaseModel):
    """Tek bir erişilebilirlik bulgusu (SRS §6.4)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: FindingType
    rule: str = Field(min_length=1)
    """Kural kimliği. Kural bulgularında axe kural kimliği (ör. ``image-alt``);
    alt metin ve klavye bulgularında Yankı gereksinim numarası (ör. ``ALT-06``)."""
    severity: Severity
    wcag: list[str] = Field(default_factory=list)
    """WCAG 2.2 başarı ölçütleri, ör. ``["1.1.1"]`` (KUR-02)."""
    selector: str = Field(min_length=1)
    evidence: Evidence = Field(default_factory=Evidence)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool
    recommendation: str

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _FINDING_ID.match(value):
            raise ValueError(f"geçersiz bulgu kimliği: {value!r} (beklenen: f-0001)")
        return value

    @field_validator("wcag")
    @classmethod
    def _check_wcag(cls, value: list[str]) -> list[str]:
        bad = [sc for sc in value if not _WCAG_SC.match(sc)]
        if bad:
            raise ValueError(f"geçersiz WCAG başarı ölçütü: {bad}")
        return value

    @model_validator(mode="after")
    def _rule_findings_are_certain(self) -> Finding:
        # Kural tabanlı sonuçlar yapay zeka bulgusu gibi sunulmaz (CLAUDE.md §9):
        # güven skoru 1, "elle incele" işareti yok.
        if self.type == "rule" and (self.confidence != 1.0 or self.needs_review):
            raise ValueError("kural bulgusu confidence=1.0 ve needs_review=false olmalıdır")
        return self


def finalize_findings(findings: list[Finding]) -> list[Finding]:
    """Bulguları belirlenimci biçimde sıralar ve ``f-0001``'den numaralar (NFR-06)."""
    ordered = sorted(
        findings,
        key=lambda f: (f.type, f.rule, f.selector, SEVERITY_ORDER[f.severity]),
    )
    return [f.model_copy(update={"id": f"f-{i:04d}"}) for i, f in enumerate(ordered, start=1)]


class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


class ElementContext(BaseModel):
    """Öğenin sayfa içindeki bağlamı (TAR-03)."""

    nearest_heading: str | None = None
    """Belge sırasında öğeden önce gelen en yakın başlığın metni."""
    heading_level: int | None = Field(None, ge=1, le=6)
    surrounding_text: str = ""
    """Öğeyi çevreleyen bloğun görünür metni (kısaltılmış)."""


class PageElement(BaseModel):
    """Sayfadan toplanan bir öğe: görsel, düğme, bağlantı ya da form alanı (TAR-03)."""

    kind: ElementKind
    selector: str
    tag: str
    role: str | None = None
    accessible_name: str = ""
    visible: bool
    bbox: BoundingBox | None = None
    context: ElementContext = Field(default_factory=ElementContext)

    # Türe özgü alanlar
    alt: str | None = None
    """Görsellerde ``alt`` özniteliği. ``None``: öznitelik yok; ``""``: dekoratif."""
    src: str | None = None
    href: str | None = None
    input_type: str | None = None


class ToolVersions(BaseModel):
    """Taramada kullanılan sürümler (KUR-03, RAP-05, KAR-05)."""

    yanki: str
    axe_core: str
    playwright: str
    browser: str


class PageInfo(BaseModel):
    requested: str
    """Kullanıcının verdiği hedef (URL ya da dosya yolu)."""
    url: str
    """Taranan adres (yerel dosyalarda ``file://`` URI)."""
    final_url: str
    """Yönlendirmelerden sonraki adres."""
    http_status: int | None = None
    title: str = ""
    lang: str | None = None


class BlockedRequest(BaseModel):
    """Ağ politikasının engellediği bağlantı (NFR-08).

    Proxy https isteklerinde yalnızca ``host:port`` görür, tam URL'yi görmez.
    """

    host: str
    port: int
    reason: str


class RulesSummary(BaseModel):
    """axe-core çalıştırma özeti (KUR-01)."""

    tags: list[str]
    violations: int
    """İhlal veren kural sayısı (bulgu sayısı değil)."""
    passes: int
    incomplete: int
    """axe'ın kesin karar veremediği kural sayısı. Bulgu olarak raporlanmaz."""
    inapplicable: int


class ScanReport(BaseModel):
    """Tek bir taramanın JSON raporu (RAP-01)."""

    schema_version: str = SCHEMA_VERSION
    tool: ToolVersions
    page: PageInfo
    started_at: datetime
    finished_at: datetime
    seed: int
    config: dict[str, Any]
    """Taramada kullanılan yapılandırmanın tamamı (PUA-02, NFR-06)."""
    elements: list[PageElement]
    findings: list[Finding]
    rules_summary: RulesSummary
    blocked_requests: list[BlockedRequest] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
