"""Klavye modülü uçtan uca testleri (Chromium).

KLV-01..10, NFR-04, NFR-05, NFR-06, NFR-16. Bilinen hatalı sayfalar
(``fixtures/known_issues``, Set B) ve temiz sayfalar (``fixtures/clean``,
Set C) her birinin yanındaki ``*.expected.json`` ile karşılaştırılır.
Beklentilere piksel türevli değer yazılmaz; testler Windows, Linux ve macOS'ta
aynı sonucu vermelidir.
"""

from __future__ import annotations

import asyncio
import json
from functools import cache
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yanki.cli import app
from yanki.config import YankiConfig
from yanki.core.keyboard.png import decode_png
from yanki.core.scan import run_scan
from yanki.core.schema import Finding, ScanReport

from conftest import LocalServer

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[1]
SET_B = ROOT / "fixtures" / "known_issues"
SET_C = ROOT / "fixtures" / "clean"
PAGES_B = sorted(p.name for p in SET_B.glob("*.html"))
PAGES_C = sorted(p.name for p in SET_C.glob("*.html"))


def _scan(target: str, config: YankiConfig | None = None) -> ScanReport:
    return asyncio.run(run_scan(target, config))


@cache
def _fixture_report(folder: str, page: str) -> ScanReport:
    """Aynı fixture birkaç testte kullanılır; tarama bir kez yapılır."""
    return _scan(str(ROOT / "fixtures" / folder / page))


def _kb(report: ScanReport) -> list[Finding]:
    return [f for f in report.findings if f.type == "keyboard"]


def _key(item: Finding | dict[str, object]) -> tuple[str, str, str]:
    if isinstance(item, Finding):
        return item.rule, item.evidence.check or "", item.selector
    return str(item["rule"]), str(item["check"]), str(item["selector"])


def _spec(folder: Path, page: str) -> dict[str, list[dict[str, object]]]:
    return json.loads((folder / page).with_suffix(".expected.json").read_text(encoding="utf-8"))


# --- Set B: bilinen hatalar (KLV-03..09, NFR-04) -----------------------------------


@pytest.mark.parametrize("page", PAGES_B)
def test_known_issues_detected(page: str) -> None:
    spec = _spec(SET_B, page)
    report = _fixture_report("known_issues", page)
    got = {_key(f): f for f in _kb(report)}
    for exp in spec["expected"]:
        f = got.get(_key(exp))
        assert f is not None, f"kaçırıldı: {_key(exp)}; bulunanlar: {sorted(got)}"
        assert f.severity == exp["severity"]
        assert f.needs_review == exp["needs_review"]
        assert f.type == exp["type"]
    allowed = {_key(e) for e in spec["expected"]} | {_key(e) for e in spec["allowed_review"]}
    unexpected = [k for k, f in got.items() if k not in allowed and not f.needs_review]
    assert unexpected == [], f"beklenmeyen kesin bulgular: {unexpected}"


def test_nfr04_recall_on_set_b() -> None:
    expected = found = 0
    for page in PAGES_B:
        keys = {_key(f) for f in _kb(_fixture_report("known_issues", page))}
        for exp in _spec(SET_B, page)["expected"]:
            expected += 1
            found += _key(exp) in keys
    assert expected >= 15
    assert found / expected >= 0.90, f"duyarlılık {found}/{expected}"


def test_set_b_issues_are_missed_by_axe() -> None:
    """Ayırt edici iddia: Set B klavye hatalarını axe-core aynı öğede bulmaz."""
    for page in PAGES_B:
        report = _fixture_report("known_issues", page)
        rule_selectors = {f.selector for f in report.findings if f.type == "rule"}
        for exp in _spec(SET_B, page)["expected"]:
            assert exp["selector"] == "html" or exp["selector"] not in rule_selectors


# --- Set C: yanlış alarm (NFR-05) -----------------------------------------------


@pytest.mark.parametrize("page", PAGES_C)
def test_clean_pages_have_no_definite_findings(page: str) -> None:
    report = _fixture_report("clean", page)
    definite = [_key(f) for f in report.findings if not f.needs_review]
    assert definite == [], f"temiz sayfada kesin bulgu: {definite}"
    assert report.keyboard is not None and report.keyboard.completed
    assert report.keyboard.tab_stops > 0
    # NFR-05 oranı: kesin yanlış alarm ÷ sekme durağı ≤ %5 (burada sıfır).
    assert len(definite) / report.keyboard.tab_stops <= 0.05


# --- KLV-01 / KLV-02: adım kaydı ---------------------------------------------------


def test_forward_steps_record_type_name_and_position() -> None:
    report = _fixture_report("clean", "basic.html")
    kb = report.keyboard
    assert kb is not None
    steps = [s for s in kb.steps if s.selector is not None]
    assert [s.selector for s in steps[:3]] == ["#skip", "#n-home", "#n-services"]
    skip = steps[0]
    assert skip.name == "Ana içeriğe atla"
    assert skip.role == "link" and skip.tag == "a"
    assert skip.bbox is not None and skip.bbox.width > 0
    assert skip.focus_visible is True
    assert kb.steps[-1].selector is None  # odak belgeden çıktı: gezi tamamlandı
    assert kb.tab_stops == len(steps) == 12
    assert {s.direction for s in kb.steps} == {"forward"}


def test_backward_steps_reverse_forward_order() -> None:
    kb = _fixture_report("clean", "basic.html").keyboard
    assert kb is not None
    fwd = [s.selector for s in kb.steps if s.selector]
    bwd = [s.selector for s in kb.backward_steps if s.selector]
    assert bwd == list(reversed(fwd))
    assert {s.direction for s in kb.backward_steps} == {"backward"}


# --- KLV-05 ayrıntı ------------------------------------------------------------


def test_trap_evidence_lists_cycle_and_direction() -> None:
    report = _fixture_report("known_issues", "keyboard_trap.html")
    traps = {f.selector: f for f in _kb(report) if f.evidence.check == "keyboard_trap"}
    fwd = traps["#t-bold"].evidence.model_dump()
    assert fwd["cycle"] == ["#t-bold", "#t-italic", "#t-link"]
    assert fwd["detail"].startswith("forward")
    bwd = traps["#tag-b"].evidence.model_dump()
    assert bwd["detail"].startswith("backward")
    assert report.keyboard is not None and not report.keyboard.completed


# --- KLV-10: kanıt ---------------------------------------------------------------


def test_every_keyboard_finding_has_steps_and_screenshot() -> None:
    total = 0
    for page in PAGES_B:
        report = _fixture_report("known_issues", page)
        for f in _kb(report):
            total += 1
            assert f.evidence.steps, f"{page} {f.selector}: adım dizisi yok"
            shot = f.evidence.screenshot
            assert shot and shot in report.artifacts, f"{page} {f.selector}: ekran görüntüsü yok"
            img = decode_png(report.artifacts[shot])
            assert img.width > 0 and img.height > 0
            assert f.wcag and 0 <= f.confidence <= 1
    assert total >= 15


# --- NFR-06: belirlenimcilik ---------------------------------------------------


@pytest.mark.parametrize("page", ["keyboard_trap.html", "modal_broken.html"])
def test_same_input_same_keyboard_result(page: str) -> None:
    a = _scan(str(SET_B / page))
    b = _scan(str(SET_B / page))
    assert [f.model_dump() for f in a.findings] == [f.model_dump() for f in b.findings]
    assert a.keyboard is not None and b.keyboard is not None
    assert a.keyboard.model_dump() == b.keyboard.model_dump()


# --- Yapılandırma -----------------------------------------------------------------


def test_keyboard_can_be_disabled() -> None:
    cfg = YankiConfig()
    cfg = cfg.model_copy(update={"keyboard": cfg.keyboard.model_copy(update={"enabled": False})})
    report = _scan(str(SET_B / "keyboard_trap.html"), cfg)
    assert report.keyboard is None
    assert _kb(report) == []
    assert report.artifacts == {}


# --- NFR-16: dayanıklılık -----------------------------------------------------------


def _write(tmp_path: Path, name: str, body: str) -> Path:
    page = tmp_path / name
    page.write_text(
        "<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>T</title></head>"
        f"<body><main><h1>T</h1>{body}</main></body></html>",
        encoding="utf-8",
    )
    return page


def test_alert_and_navigation_during_probes_do_not_stop_scan(tmp_path: Path) -> None:
    other = _write(tmp_path, "baska.html", "<p>Başka sayfa</p>")
    page = _write(
        tmp_path,
        "sonda.html",
        f"""
        <span id="nav" role="button" tabindex="0">Git</span>
        <span id="alert" role="button" tabindex="0">Uyar</span>
        <span id="ok" role="button" tabindex="0">Çalışır</span>
        <p id="out"></p>
        <script>
          document.getElementById('nav').addEventListener('keydown', (e) => {{
            if (e.key === 'Enter') location.href = {json.dumps(other.as_uri())};
          }});
          document.getElementById('nav').addEventListener('click', () => {{}});
          document.getElementById('alert').addEventListener('click', () => {{
            alert('merhaba'); document.getElementById('out').textContent = 'uyarıldı';
          }});
          const ok = document.getElementById('ok');
          ok.addEventListener('click', () => document.getElementById('out').textContent = 'ok');
        </script>
        """,
    )
    report = _scan(str(page))
    kb = report.keyboard
    assert kb is not None
    assert kb.probes.get("activation", 0) == 3  # gezinmeden sonra kalan sondalar da yapıldı
    keys = {_key(f) for f in _kb(report)}
    # #alert yalnızca tıklamayla çalışır (alert otomatik kapatılır); #ok da öyle.
    assert ("KLV-07", "not_activatable", "#alert") in keys
    assert ("KLV-07", "not_activatable", "#ok") in keys
    assert ("KLV-07", "not_activatable", "#nav") not in keys
    assert report.page.final_url.endswith("sonda.html")


def test_time_budget_returns_partial_report() -> None:
    cfg = YankiConfig()
    cfg = cfg.model_copy(
        update={"keyboard": cfg.keyboard.model_copy(update={"time_budget_s": 0.01})}
    )
    report = _scan(str(SET_C / "basic.html"), cfg)
    assert report.keyboard is not None
    assert any("zaman bütçesi" in w for w in report.warnings)


def test_element_missing_after_reload_is_skipped(tmp_path: Path) -> None:
    # Her yüklemede kimliği değişen öğe: sıfırlamadan sonra seçici bulunamaz.
    page = _write(
        tmp_path,
        "rastgele.html",
        """
        <span id="first" role="button" tabindex="0">Birinci</span>
        <p id="slot"></p><p id="out"></p>
        <script>
          const s = document.createElement('span');
          s.id = 'r' + Math.random().toString(36).slice(2);
          s.setAttribute('role', 'button'); s.tabIndex = 0; s.textContent = 'Rastgele';
          s.addEventListener('click', () => document.getElementById('out').textContent = 'x');
          document.getElementById('slot').append(s);
          document.getElementById('first').addEventListener('keydown',
            (e) => { if (e.key === 'Enter' || e.key === ' ')
              document.getElementById('out').textContent = e.key; });
        </script>
        """,
    )
    report = _scan(str(page))
    assert report.keyboard is not None
    assert not any("beklenmeyen hata" in w for w in report.warnings)
    assert all(not f.selector.startswith("#r") for f in _kb(report))


def test_probes_never_submit_forms(http_server: LocalServer) -> None:
    # Temkinli politika: form içindeki gönder düğmesi etkinleştirilmez; sayfa bir kez istenir.
    cfg = YankiConfig()
    scan_cfg = cfg.scan.model_copy(update={"allow_private_network": True})
    cfg = cfg.model_copy(update={"scan": scan_cfg})
    report = _scan(http_server.url("/pages/clean.html"), cfg)
    assert report.findings == []
    assert http_server.hits["/pages/clean.html"] == 1


# --- CLI: kanıt dosyaları (KLV-10) ----------------------------------------------


def test_cli_writes_evidence_next_to_report(tmp_path: Path) -> None:
    out = tmp_path / "rapor" / "sonuc.json"
    result = CliRunner().invoke(
        app, ["scan", str(SET_B / "focus_indicator_missing.html"), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    shots = {f["evidence"]["screenshot"] for f in data["findings"] if f["type"] == "keyboard"}
    assert shots
    for shot in shots:
        assert (out.parent / shot).is_file()
    assert "klavye" in result.output


def test_cli_stdout_warns_that_evidence_is_not_written() -> None:
    result = CliRunner().invoke(app, ["scan", str(SET_B / "focus_indicator_missing.html")])
    assert result.exit_code == 0
    assert "kanıt görüntüsü yazılmadı" in result.output
