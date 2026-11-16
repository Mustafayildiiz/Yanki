"""Ortak bulgu şeması ve tarama raporu (SRS §6.4, RAP-01, RAP-02, KUR-01).

Üç modül (kural, alt metin, klavye) bulgularını aynı ``Finding`` modeliyle
üretir. Rapor JSON'u ``ScanReport`` modelinin serileştirilmesidir.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "0.3.0"

FindingType = Literal["rule", "alt_text", "keyboard"]
Severity = Literal["critical", "important", "moderate", "low"]
ElementKind = Literal["image", "button", "link", "form_field"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "important": 1, "moderate": 2, "low": 3}

_WCAG_SC = re.compile(r"^[1-4]\.\d{1,2}\.\d{1,2}$")
_FINDING_ID = re.compile(r"^f-\d{4,}$")


class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


class KeyStep(BaseModel):
    """Bulgu kanıtındaki bir klavye ya da fare adımı (KLV-10)."""

    n: int
    key: str
    """Basılan tuş (``Tab``, ``Shift+Tab``, ``Enter``, ``Space``, ``Escape``), ``click``
    ya da düzeneğin kendi müdahalesi (``focus()``: öğe programla odaklandı)."""
    selector: str | None = None
    """Adımdan sonra odaklı öğe. ``None``: odak belgenin dışında."""
    role: str | None = None
    name: str = ""


class Evidence(BaseModel):
    """Bulgunun kanıtı.

    Bilinen alanlar tiplidir; modüle özgü ek alanlara izin verilir
    (``extra="allow"``). Alt metin bulgularında özgün (``alt``) ve çevrilmiş
    (``alt_translated``) metin birlikte saklanır (ALT-04).
    """

    model_config = ConfigDict(extra="allow")

    # Alt metin modülü (ALT-01..12)
    alt: str | None = None
    """Özgün alt metin ya da görselin metin alternatifi (aria-label vb.)."""
    alt_translated: str | None = None
    """İngilizceye çevrilmiş alt metin (ALT-04). Çeviri yapılmadıysa ``None``."""
    caption: str | None = None
    """Görsel açıklama modelinin ürettiği açıklama (ALT-05)."""
    similarity: float | None = Field(None, ge=0, le=1)
    """Birleşik görsel–metin uyum skoru (ALT-03)."""
    e5: float | None = Field(None, ge=0, le=1)
    """Açıklama ile alt metin arasındaki E5 benzerliği, 0–1'e eşlenmiş (ana sinyal)."""
    clip: float | None = Field(None, ge=0, le=1)
    """Görsel ile alt metin arasındaki CLIP benzerliği, 0–1'e eşlenmiş (ek sinyal)."""
    label: str | None = None
    """Alt metin sınıfı (ALT-06): ``uygun``, ``yetersiz``, ``uyumsuz``, ``eksik``."""
    method: Literal["rule", "model"] | None = None
    """Bulgunun kaynağı: kural tabanlı desen ya da yapay zeka modeli (CLAUDE.md §9)."""
    lang: str | None = None
    """ALT-12: alt metnin tespit edilen dili; ``detail`` öğenin bildirilen dilidir."""
    screenshot: str | None = None

    # Kural modülü (axe-core)
    html: str | None = None
    """İhlal eden öğenin HTML parçası (sayfa kaynaklı; raporda kaçışlanır)."""
    help: str | None = None
    help_url: str | None = None
    failure_summary: str | None = None
    axe_impact: str | None = None

    # Klavye modülü (KLV-01..10)
    check: str | None = None
    """Klavye denetim türü (ör. ``keyboard_trap``); aynı öğedeki bulguları ayırır."""
    steps: list[KeyStep] | None = None
    """Bulguya götüren adım dizisi (KLV-10)."""
    focus: str | None = None
    """Bulgu anında odaklı öğenin seçicisi. ``None``: odak belgenin dışında."""
    trigger: str | None = None
    """Modali açan öğe (KLV-08)."""
    previous: str | None = None
    """Odak sırası sapmasında bir önceki öğe (KLV-04)."""
    detail: str | None = None
    """Kısa makine okunur açıklama (ör. ``offscreen``, ``Space``)."""


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
        key=lambda f: (
            f.type,
            f.rule,
            f.evidence.check or "",
            f.selector,
            SEVERITY_ORDER[f.severity],
        ),
    )
    return [f.model_copy(update={"id": f"f-{i:04d}"}) for i, f in enumerate(ordered, start=1)]


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

    lang: str | None = None
    """Öğenin geçerli dili: kendisinde ya da en yakın atasında bildirilen ``lang``."""
    aria_hidden: bool = False
    """Öğe ya da bir atası ``aria-hidden="true"``."""
    in_control: bool = False
    """Öğe bir bağlantı ya da düğmenin içinde (işlevsel görsel)."""

    # Türe özgü alanlar
    alt: str | None = None
    """Görsellerde ``alt`` özniteliği. ``None``: öznitelik yok; ``""``: dekoratif."""
    label: str | None = None
    """Görsellerde ``alt`` dışındaki metin alternatifi: ``aria-labelledby``,
    ``aria-label`` ya da ``title`` (bu sırayla). ``None``: yok."""
    src: str | None = None
    natural_width: int | None = None
    """Görsellerde çözülmüş genişlik; ``0``: yüklenemedi ya da çözülemedi."""
    href: str | None = None
    input_type: str | None = None


class ToolVersions(BaseModel):
    """Taramada kullanılan sürümler (KUR-03, RAP-05, KAR-05)."""

    yanki: str
    axe_core: str
    playwright: str
    browser: str
    torch: str | None = None
    transformers: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    """Alt metin modelleri: rol → ``kimlik@revizyon`` (NFR-06). Sahte arka uçta ``fake``."""


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


class KeyboardStep(BaseModel):
    """Tab gezisinin bir adımı (SRS §7.1 KeyboardStep, KLV-01, KLV-02)."""

    index: int
    direction: Literal["forward", "backward"]
    selector: str | None
    """Odaklı öğe. ``None``: odak belgeden çıktı (son duraktan sonra)."""
    tag: str | None = None
    role: str | None = None
    name: str = ""
    bbox: BoundingBox | None = None
    """Sayfa koordinatında konum."""
    in_frame: bool = False
    """Odak içine girilemeyen (başka kökenli) bir iframe'de."""
    focus_visible: bool | None = None
    """Odak göstergesi görünür mü (KLV-03). ``None``: değerlendirilemedi."""


class KeyboardReport(BaseModel):
    """Klavye modülünün özeti (KLV-01..10)."""

    steps: list[KeyboardStep] = Field(default_factory=list)
    backward_steps: list[KeyboardStep] = Field(default_factory=list)
    tab_stops: int = 0
    """İleri gezide ziyaret edilen farklı öğe sayısı (puanlamada ``N_klavye``)."""
    completed: bool = False
    """İleri gezi belgenin sonuna ulaştı (tuzak ya da adım sınırı yok)."""
    step_limit_reached: bool = False
    probes: dict[str, int] = Field(default_factory=dict)
    """Denetim türüne göre yapılan sonda sayısı."""
    skipped: list[str] = Field(default_factory=list)
    """Atlanan denetimler ve nedenleri."""


ImageStatus = Literal["analyzed", "decorative", "skipped", "failed", "rule_only"]


class ImageAnalysis(BaseModel):
    """Bir görselin alt metin analizi kaydı (SRS §7.1 ImageAnalysis, ALT-01..10).

    Her görsel için bir kayıt vardır; bulgu üretmeyen (uygun, dekoratif,
    atlanan, analiz edilemeyen) görseller de burada görünür.
    """

    selector: str
    src: str | None = None
    sha256: str | None = None
    """Analiz edilen görsel baytlarının SHA-256 karması (ALT-09 önbellek anahtarı)."""
    source: Literal["response", "data_uri", "screenshot"] | None = None
    """Baytların kaynağı: sayfanın kendi yanıtı, ``data:`` URI ya da öğe ekran görüntüsü."""
    status: ImageStatus
    """``analyzed``: model analizi yapıldı. ``decorative``: ALT-02, puanlamaya
    katılmaz. ``skipped``: görünmez, çok küçük, sınır aşıldı ya da desteklenmeyen dil.
    ``failed``: indirilemedi ya da çözülemedi (ALT-10, "analiz edilemedi").
    ``rule_only``: yalnızca kural tabanlı sonuç (eksik ya da genel ifade)."""
    reason: str | None = None
    cached: bool = False
    """Model sonuçları önbellekten geldi (ALT-09)."""
    alt: str | None = None
    alt_translated: str | None = None
    caption: str | None = None
    e5: float | None = None
    clip: float | None = None
    similarity: float | None = None
    label: Literal["uygun", "yetersiz", "uyumsuz", "eksik"] | None = None
    confidence: float | None = None


class AltTextReport(BaseModel):
    """Alt metin modülünün özeti (ALT-01..12)."""

    backend: str
    approach: str
    images: list[ImageAnalysis] = Field(default_factory=list)
    audited_images: int = 0
    """Bilgi verici ve görünür görsel sayısı (dekoratif ve atlananlar hariç)."""
    audited_controls: int = 0
    """Erişilebilir adı denetlenen bağlantı ve düğme sayısı (ALT-11)."""
    audited: int = 0
    """Toplam denetlenen öğe (Faz 3'te ``N_icerik``)."""
    cache_hits: int = 0
    seconds_total: float = 0.0
    """Alt metin analizinin toplam süresi (model yükleme ve önbellek dâhil)."""
    seconds_load: float | None = None
    """Modellerin belleğe yüklenme süresi."""
    seconds_per_image: float | None = None
    """Önbellek dışı, model analizi yapılan görsel başına ortalama süre (NFR-01)."""


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
    keyboard: KeyboardReport | None = None
    alt_text: AltTextReport | None = None
    blocked_requests: list[BlockedRequest] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifacts: dict[str, bytes] = Field(default_factory=dict, exclude=True)
    """Kanıt dosyaları (ör. ``evidence/<karma>.png`` → PNG). JSON'a yazılmaz;
    ``yanki.core.evidence.save_evidence`` ile diske yazılır (KLV-10)."""
