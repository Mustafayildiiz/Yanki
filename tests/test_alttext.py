"""Alt metin modülü uçtan uca testleri (Chromium, ağırlıksız).

ALT-01, ALT-02, ALT-07, ALT-09, ALT-10, ALT-11, ALT-12, NFR-06, NFR-08, NFR-16.

Bu testler model ağırlığı olmadan çalışır (``conftest._hermetic_models``).
Fixture beklentilerinden yalnızca ``requires_models: false`` olanlar burada
denetlenir; model gerektiren beklentiler ``test_alttext_models.py`` (``-m
models``) ve ``experiments/alt_eval.py`` ile gerçek modellerle ölçülür. Sahte
arka uç yalnızca tesisatı (bayt toplama, önbellek, belirlenimcilik) sınar.
"""

from __future__ import annotations

import asyncio
import base64
import json
from functools import cache
from pathlib import Path

import pytest

from yanki.config import YankiConfig
from yanki.core.keyboard.png import decode_png
from yanki.core.scan import run_scan
from yanki.core.schema import SCHEMA_VERSION, Finding, ScanReport

from conftest import FORBIDDEN_IP, LocalServer, make_policy

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
PAGES = sorted(
    f"{p.parent.name}/{p.name}" for p in FIXTURES.glob("*/*.html") if p.parent.name != "images"
)
NO_KEYBOARD = {"keyboard": {"enabled": False}}


def _scan(target: str, config: YankiConfig | None = None, **kw: object) -> ScanReport:
    return asyncio.run(run_scan(target, config, **kw))  # type: ignore[arg-type]


@cache
def _fixture_report(page: str) -> ScanReport:
    """Varsayılan yapılandırma, ağırlık yok (CI ile aynı koşul); klavye kapalı."""
    return _scan(str(FIXTURES / page), YankiConfig.model_validate(NO_KEYBOARD))


def _spec(page: str) -> dict[str, list[dict[str, object]]]:
    data = json.loads((FIXTURES / page).with_suffix(".expected.json").read_text(encoding="utf-8"))
    return data["alt_text"]


def _key(item: Finding | dict[str, object]) -> tuple[str, str, str]:
    if isinstance(item, Finding):
        return item.rule, item.evidence.check or "", item.selector
    return str(item["rule"]), str(item["check"]), str(item["selector"])


def _alt(report: ScanReport) -> list[Finding]:
    return [f for f in report.findings if f.type == "alt_text"]


def _fake(tmp_path: Path, **alt: object) -> YankiConfig:
    return YankiConfig.model_validate(
        {**NO_KEYBOARD, "alt_text": {"backend": "fake", "cache_dir": str(tmp_path), **alt}}
    )


# --- Set B / Set C beklentileri (kural tabanlı) -------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_rule_based_expectations(page: str) -> None:
    """Model gerektirmeyen beklenen bulgular bulunur; beklenmeyen kesin bulgu yoktur."""
    spec = _spec(page)
    report = _fixture_report(page)
    assert report.alt_text is not None
    got = {_key(f): f for f in _alt(report)}
    for exp in spec["expected"]:
        if exp.get("requires_models", True):
            continue
        f = got.get(_key(exp))
        assert f is not None, f"kaçırıldı: {_key(exp)}; bulunanlar: {sorted(got)}"
        assert f.severity == exp["severity"]
        assert f.needs_review == exp["needs_review"]
        assert f.evidence.method == "rule"
    allowed = {_key(e) for e in spec["expected"]} | {_key(e) for e in spec["allowed_review"]}
    unexpected = [k for k, f in got.items() if k not in allowed and not f.needs_review]
    assert unexpected == [], f"beklenmeyen kesin alt metin bulguları: {unexpected}"


@pytest.mark.parametrize("page", PAGES)
def test_image_status_expectations(page: str) -> None:
    """ALT-02 dekoratif, ALT-10 analiz edilemedi ve atlanan görseller raporda doğru görünür."""
    spec = _spec(page)
    images = {r.selector: r for r in _fixture_report(page).alt_text.images}  # type: ignore[union-attr]
    for sel in spec.get("decorative", []):
        assert images[str(sel)].status == "decorative"
    for sel in spec.get("skipped", []):
        assert images[str(sel)].status == "skipped"
    for sel in spec.get("failed", []):
        rec = images[str(sel)]
        # Ağırlık olmasa da bu görseller model aşamasından önce başarısız olur.
        assert rec.status == "failed" and rec.reason != "models_unavailable", (sel, rec.reason)


def test_alt_findings_are_missed_by_axe_except_missing() -> None:
    """Ayırt edici iddia: ALT-07 bulgularını axe-core kaçırır; ALT-01 ile örtüşme beklenir."""
    generic = _fixture_report("known_issues/alt_generic.html")
    rule_selectors = {f.selector for f in generic.findings if f.type == "rule"}
    assert all(f.selector not in rule_selectors for f in _alt(generic))
    missing = _fixture_report("known_issues/alt_missing.html")
    axe = {f.selector for f in missing.findings if f.type == "rule"}
    assert {"#m-dog", "#m-input"} <= axe  # image-alt / input-image-alt (açık soru: çift sayım)


def test_rule_findings_have_screenshot_evidence() -> None:
    report = _fixture_report("known_issues/alt_missing.html")
    for f in _alt(report):
        assert f.evidence.screenshot, f.selector
        png = report.artifacts[f.evidence.screenshot]
        image = decode_png(png)
        assert image.width > 0 and image.height > 0


def test_without_models_scan_continues_with_warning() -> None:
    """NFR-16: ağırlık yoksa tarama sürer; model görselleri 'analiz edilemedi'."""
    report = _fixture_report("known_issues/alt_mismatch.html")
    statuses = {r.status for r in report.alt_text.images}  # type: ignore[union-attr]
    assert statuses == {"failed"}
    assert all(r.reason == "models_unavailable" for r in report.alt_text.images)  # type: ignore[union-attr]
    assert sum("modelleri kullanılamıyor" in w for w in report.warnings) == 1
    assert report.tool.models == {} and report.tool.torch is None


def test_report_schema() -> None:
    report = _fixture_report("clean/alt_ok.html")
    data = json.loads(report.model_dump_json())
    assert data["schema_version"] == SCHEMA_VERSION == "0.3.0"
    alt = data["alt_text"]
    assert alt["backend"] == "transformers" and alt["approach"] == "translate"
    assert alt["audited_images"] == 7 and alt["audited"] >= alt["audited_images"]
    assert data["config"]["alt_text"]["models"]["captioner"]["revision"]


# --- sahte arka uçla tesisat ----------------------------------------------------------------


def test_bytes_sources(tmp_path: Path) -> None:
    """Raster görseller yanıttan, SVG'ler ekran görüntüsünden, data: URI'ler yerelde."""
    report = _scan(str(FIXTURES / "clean/alt_tricky_ok.html"), _fake(tmp_path))
    source = {r.selector: r.source for r in report.alt_text.images}  # type: ignore[union-attr]
    assert source["#t-people"] == "response"
    assert source["#t-logo"] == source["#t-chart"] == source["#t-notice"] == "screenshot"

    jpg = base64.b64encode((FIXTURES / "images" / "red_car.jpg").read_bytes()).decode()
    page = tmp_path / "data_uri.html"
    page.write_text(
        "<!doctype html><html lang='tr'><title>Veri</title><main><h1>Araba</h1>"
        f"<img id='d' src='data:image/jpeg;base64,{jpg}' width='200' height='125'"
        " alt='Arnavut kaldırımlı sokakta kırmızı bir araba'></main></html>",
        encoding="utf-8",
    )
    rec = _scan(str(page), _fake(tmp_path)).alt_text.images[0]  # type: ignore[union-attr]
    assert (rec.status, rec.source) == ("analyzed", "data_uri")
    assert rec.src is not None and len(rec.src) <= 41  # data: URI rapora kısaltılarak yazılır


def test_no_new_requests_and_blocked_images_fail(http_server: LocalServer, tmp_path: Path) -> None:
    """NFR-08: görsel baytları yeni istekle alınmaz; yasak adrese giden görsel
    (doğrudan ya da yönlendirmeyle) analiz edilemez ve sunucuya ulaşmaz."""
    report = _scan(http_server.url("/alt.html"), _fake(tmp_path), policy=make_policy())
    status = {r.selector: (r.status, r.reason) for r in report.alt_text.images}  # type: ignore[union-attr]
    assert status["#allowed"] == ("analyzed", None), status
    assert status["#direct"][0] == "failed", status
    assert status["#redirected"][0] == "failed"
    assert http_server.hits["/img/car.jpg"] == 1  # yalnızca sayfanın kendi yüklemesi
    assert not any(p.startswith("/evil") for p in http_server.hits)
    assert any(b.host == FORBIDDEN_IP for b in report.blocked_requests)


def test_cache_across_scans(tmp_path: Path) -> None:
    """ALT-09: aynı görsel ikinci taramada önbellekten gelir; sonuçlar aynıdır (NFR-06)."""
    page = str(FIXTURES / "known_issues/alt_insufficient.html")
    first = _scan(page, _fake(tmp_path))
    second = _scan(page, _fake(tmp_path))
    analyzed = [r for r in second.alt_text.images if r.status == "analyzed"]  # type: ignore[union-attr]
    assert analyzed and all(r.cached for r in analyzed)
    assert second.alt_text.cache_hits == len(analyzed)  # type: ignore[union-attr]
    dump = lambda r: [f.model_dump() for f in _alt(r)]  # noqa: E731
    assert dump(first) == dump(second)
    assert first.tool.models["captioner"] == "fake"


def test_alt_text_can_be_disabled(tmp_path: Path) -> None:
    cfg = YankiConfig.model_validate({**NO_KEYBOARD, "alt_text": {"enabled": False}})
    report = _scan(str(FIXTURES / "known_issues/alt_missing.html"), cfg)
    assert report.alt_text is None and _alt(report) == []
