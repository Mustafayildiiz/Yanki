"""Gerçek modellerle alt metin testleri (``pytest -m models``).

Varsayılan test koşusunda seçilmez. Konteynerde ``yanki models download``
sonrasında elle ya da gece çalışan CI işinde (``.github/workflows/models.yml``)
çalışır. Sahte arka ucu reddeder: doğruluk yalnızca gerçek modellerle ölçülür.

Model gerektiren Set B/C beklentileri (``requires_models: true``) burada
denetlenir. Eşikler kalibre edilmemiş başlangıç değerleridir (Set A bekleniyor);
bu testler kalibrasyon sonrası hedeftir ve başarısızlıkları gizlenmez.
"""

from __future__ import annotations

import asyncio
import json
from functools import cache
from pathlib import Path

import pytest

from yanki.config import YankiConfig
from yanki.core.alttext.backend import BackendUnavailable, load_backend
from yanki.core.scan import run_scan
from yanki.core.schema import Finding, ScanReport

pytestmark = [pytest.mark.models, pytest.mark.browser]

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
MODEL_PAGES = sorted(
    f"{p.parent.name}/{p.name}"
    for p in FIXTURES.glob("*/*.html")
    if any(
        e.get("requires_models")
        for e in json.loads(p.with_suffix(".expected.json").read_text(encoding="utf-8"))[
            "alt_text"
        ]["expected"]
    )
)
CLEAN_PAGES = sorted(f"clean/{p.name}" for p in (FIXTURES / "clean").glob("alt_*.html"))


def _config() -> YankiConfig:
    cfg = YankiConfig.model_validate({"keyboard": {"enabled": False}})
    if cfg.alt_text.backend != "transformers":  # pragma: no cover - koruma
        pytest.fail("model testleri sahte arka uçla çalıştırılamaz")
    return cfg


@pytest.fixture(scope="module", autouse=True)
def _require_models() -> None:
    try:
        load_backend(_config().alt_text, device="cpu", seed=0)
    except BackendUnavailable as exc:
        pytest.fail(f"gerçek modeller yüklenemedi: {exc}")


@cache
def _report(page: str) -> ScanReport:
    return asyncio.run(run_scan(str(FIXTURES / page), _config()))


def _key(item: Finding | dict[str, object]) -> tuple[str, str, str]:
    if isinstance(item, Finding):
        return item.rule, item.evidence.check or "", item.selector
    return str(item["rule"]), str(item["check"]), str(item["selector"])


@pytest.mark.parametrize("page", MODEL_PAGES)
def test_model_expectations(page: str) -> None:
    spec = json.loads((FIXTURES / page).with_suffix(".expected.json").read_text(encoding="utf-8"))
    report = _report(page)
    assert report.tool.models and report.tool.torch
    got = {_key(f): f for f in report.findings if f.type == "alt_text"}
    missed = []
    for exp in spec["alt_text"]["expected"]:
        f = got.get(_key(exp))
        if f is None:
            missed.append(_key(exp))
            continue
        assert f.severity == exp["severity"]
        if "needs_review" in exp:
            assert f.needs_review == exp["needs_review"], _key(exp)
    assert missed == [], f"kaçırıldı: {missed}"


@pytest.mark.parametrize("page", CLEAN_PAGES)
def test_clean_pages_no_definite_alt_findings(page: str) -> None:
    report = _report(page)
    definite = [_key(f) for f in report.findings if f.type == "alt_text" and not f.needs_review]
    assert definite == [], f"temiz sayfada kesin alt metin bulgusu: {definite}"


def test_model_findings_carry_evidence_and_versions() -> None:
    report = _report("known_issues/alt_mismatch.html")
    model = [f for f in report.findings if f.evidence.method == "model"]
    assert model
    for f in model:
        ev = f.evidence
        assert ev.alt and ev.alt_translated and ev.caption and ev.similarity is not None
        assert ev.screenshot in report.artifacts  # PNG'ye yeniden kodlanmış küçük resim
    assert set(report.tool.models) == {"captioner", "clip", "embedder", "translator"}
    assert all("@" in ref for ref in report.tool.models.values())
    alt = report.alt_text
    assert alt is not None and alt.seconds_per_image is not None


def test_real_models_are_deterministic() -> None:
    """NFR-06: aynı girdi, aynı açıklama ve skorlar (önbellek kapalı)."""
    cfg = YankiConfig.model_validate({"keyboard": {"enabled": False}, "alt_text": {"cache": False}})
    page = str(FIXTURES / "known_issues/alt_mismatch.html")
    a = asyncio.run(run_scan(page, cfg)).alt_text
    b = asyncio.run(run_scan(page, cfg)).alt_text
    assert a is not None and b is not None
    pick = lambda r: [(i.caption, i.alt_translated, i.similarity) for i in r.images]  # noqa: E731
    assert pick(a) == pick(b)
