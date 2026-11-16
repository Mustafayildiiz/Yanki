"""Chromium ile uçtan uca tarama testleri.

TAR-01, TAR-02, TAR-03, TAR-04, TAR-05, KUR-01, KUR-02, KUR-03, NFR-06, NFR-08.
İnternete çıkılmaz: ``file://`` sayfaları ve yerel test sunucusu kullanılır.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from yanki.config import YankiConfig
from yanki.core.errors import ScanError, TargetError
from yanki.core.netguard import NetworkPolicy
from yanki.core.rules import AXE_VERSION, axe_source, wcag_from_tags
from yanki.core.scan import run_scan
from yanki.core.schema import ScanReport

from conftest import ALLOWED_IP, FORBIDDEN_IP, PAGES, LocalServer, make_policy

pytestmark = pytest.mark.browser


def _scan(
    target: str, config: YankiConfig | None = None, policy: NetworkPolicy | None = None
) -> ScanReport:
    return asyncio.run(run_scan(target, config, policy=policy))


def _cfg(**scan: object) -> YankiConfig:
    cfg = YankiConfig()
    return cfg.model_copy(update={"scan": cfg.scan.model_copy(update=scan)})


def _rule_keys(report: ScanReport) -> list[tuple[str, str, str, list[str], str]]:
    # Klavye bulguları (Faz 1) bu sayfaların kural beklentisinin parçası değildir.
    return sorted(
        (f.type, f.rule, f.selector, f.wcag, f.severity)
        for f in report.findings
        if f.type == "rule"
    )


# --- KUR-01, KUR-02, KUR-03, TAR-01 ---------------------------------------


def test_known_violations_exactly_detected() -> None:
    expected = json.loads((PAGES / "rules_violations.expected.json").read_text(encoding="utf-8"))
    report = _scan(str(PAGES / "rules_violations.html"))
    want = sorted(
        (e["type"], e["rule"], e["selector"], e["wcag"], e["severity"])
        for e in expected["expected"]
    )
    assert _rule_keys(report) == want
    for f in (f for f in report.findings if f.type == "rule"):
        assert f.wcag, f"WCAG ölçütü olmayan kural bulgusu: {f.rule}"  # KUR-02
        assert f.evidence.html and f.evidence.help_url
    assert report.tool.axe_core == AXE_VERSION  # KUR-03
    assert report.tool.playwright == "1.65.2"
    assert report.tool.browser.startswith("chromium ")


def test_clean_page_has_no_findings() -> None:
    report = _scan((PAGES / "clean.html").as_uri())
    assert report.findings == []
    assert report.rules_summary.violations == 0
    assert report.rules_summary.passes > 0
    assert report.page.lang == "tr"
    assert report.page.title == "Temiz test sayfası"


# --- TAR-03 -----------------------------------------------------------------


def test_elements_collected_with_context() -> None:
    report = _scan(str(PAGES / "clean.html"))
    by_sel = {e.selector: e for e in report.elements}
    assert {e.kind for e in report.elements} == {"image", "button", "link", "form_field"}

    campus = by_sel["#campus"]
    assert campus.kind == "image"
    assert campus.alt == "Kampüsün kuşbakışı görünümü"
    assert campus.accessible_name == "Kampüsün kuşbakışı görünümü"
    assert campus.context.nearest_heading == "Kampüs"
    assert campus.context.heading_level == 2
    assert "Ana kampüs, 2026 baharı." in campus.context.surrounding_text
    assert campus.src and campus.src.startswith("data:image/svg+xml")
    assert campus.bbox is not None and campus.bbox.width == 40

    assert by_sel["#divider"].alt == ""  # dekoratif: boş alt korunur (ALT-02 için)
    assert by_sel["#name"].kind == "form_field"
    assert by_sel["#name"].accessible_name == "Adınız"  # <label for> ile
    assert by_sel["#name"].context.nearest_heading == "İletişim"
    assert by_sel["#send"].kind == "button"
    assert by_sel["#home-link"].kind == "link"
    assert by_sel["#home-link"].context.nearest_heading is None


def test_missing_alt_is_none_not_empty() -> None:
    report = _scan(str(PAGES / "rules_violations.html"))
    hero = next(e for e in report.elements if e.selector == "#hero")
    assert hero.alt is None
    assert hero.context.nearest_heading == "Öne çıkanlar"


# --- NFR-06 -----------------------------------------------------------------


def test_same_input_same_findings_and_elements() -> None:
    a = _scan(str(PAGES / "rules_violations.html"))
    b = _scan(str(PAGES / "rules_violations.html"))
    assert [f.model_dump() for f in a.findings] == [f.model_dump() for f in b.findings]
    assert [e.model_dump() for e in a.elements] == [e.model_dump() for e in b.elements]


# --- TAR-04 -----------------------------------------------------------------


def test_viewport_and_locale_applied() -> None:
    report = _scan(str(PAGES / "clean.html"), _cfg(viewport_width=400, locale="en-US"))
    assert report.config["scan"]["viewport_width"] == 400
    assert report.config["scan"]["locale"] == "en-US"
    form = next(e for e in report.elements if e.selector == "#name")
    assert form.bbox is not None and form.bbox.x < 400


# --- TAR-02 / HTTP ----------------------------------------------------------


def test_http_scan_and_csp_does_not_block_axe(http_server: LocalServer) -> None:
    report = _scan(http_server.url("/csp"), policy=make_policy())
    assert report.page.http_status == 200
    assert {f.rule for f in report.findings} >= {"image-alt", "button-name"}


def test_network_idle_timeout_is_warning_not_failure(http_server: LocalServer) -> None:
    report = _scan(
        http_server.url("/poll.html"), _cfg(network_idle_timeout_ms=1000), policy=make_policy()
    )
    assert any("durulmadı" in w for w in report.warnings)
    assert report.page.title == "Yoklama"


# --- TAR-05 -----------------------------------------------------------------


def test_navigation_timeout(http_server: LocalServer) -> None:
    with pytest.raises(ScanError) as exc:
        _scan(http_server.url("/slow"), _cfg(navigation_timeout_ms=1000), policy=make_policy())
    assert exc.value.code == "navigation_timeout"


def test_http_error_status(http_server: LocalServer) -> None:
    with pytest.raises(ScanError) as exc:
        _scan(http_server.url("/status/404"), policy=make_policy())
    assert exc.value.code == "http_error"


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_connection_refused(scheme: str) -> None:
    # Kapalı bir geçici port. Proxy'nin 502'si "http_error" olarak değil,
    # proxy kaydından "connection_refused" olarak raporlanmalı.
    with pytest.raises(ScanError) as exc:
        _scan(f"{scheme}://127.0.0.1:{_closed_port()}/", _cfg(allow_private_network=True))
    assert exc.value.code == "connection_refused"


# --- KUR-02: tüm varsayılan kurallar WCAG ölçütüne bağlanır ---------------------


def test_every_default_axe_rule_maps_to_wcag_criterion() -> None:
    from playwright.async_api import async_playwright

    async def rules() -> list[dict[str, object]]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.evaluate(axe_source())
                return await page.evaluate(
                    "(tags) => axe.getRules(tags).map(r => ({id: r.ruleId, tags: r.tags}))",
                    YankiConfig().rules.axe_tags,
                )
            finally:
                await browser.close()

    all_rules = asyncio.run(rules())
    assert len(all_rules) > 50
    unmapped = [r["id"] for r in all_rules if not wcag_from_tags(r["tags"])]  # type: ignore[arg-type]
    assert unmapped == []


# --- NFR-08: SSRF -----------------------------------------------------------


def test_localhost_blocked_by_default(http_server: LocalServer) -> None:
    with pytest.raises(TargetError) as exc:
        _scan(f"http://localhost:{http_server.port}/pages/clean.html")
    assert exc.value.code == "blocked_target"
    assert sum(http_server.hits.values()) == 0


def test_localhost_allowed_with_explicit_setting(http_server: LocalServer) -> None:
    report = _scan(http_server.url("/pages/clean.html"), _cfg(allow_private_network=True))
    assert report.findings == []
    assert http_server.hits["/pages/clean.html"] == 1


def test_direct_navigation_to_forbidden_host(http_server: LocalServer) -> None:
    with pytest.raises(TargetError):
        _scan(http_server.url("/evil/direct", ip=FORBIDDEN_IP), policy=make_policy())
    assert http_server.hits["/evil/direct"] == 0


def test_redirect_to_forbidden_host_blocked(http_server: LocalServer) -> None:
    evil = http_server.url("/evil/one-hop", ip=FORBIDDEN_IP)
    with pytest.raises(ScanError) as exc:
        _scan(http_server.url(f"/redirect?to={evil}"), policy=make_policy())
    assert exc.value.code == "blocked_by_policy"
    assert http_server.hits["/redirect"] == 1
    assert http_server.hits["/evil/one-hop"] == 0


def test_two_hop_redirect_to_forbidden_host_blocked(http_server: LocalServer) -> None:
    evil = http_server.url("/evil/two-hop", ip=FORBIDDEN_IP)
    hop2 = http_server.url(f"/redirect?to={evil}")
    with pytest.raises(ScanError) as exc:
        _scan(http_server.url(f"/redirect?to={hop2}"), policy=make_policy())
    assert exc.value.code == "blocked_by_policy"
    assert http_server.hits["/redirect"] == 2
    assert http_server.hits["/evil/two-hop"] == 0


def test_subresources_to_forbidden_host_blocked(http_server: LocalServer) -> None:
    report = _scan(http_server.url("/subresource.html"), policy=make_policy())
    assert http_server.hits["/ok.png"] == 1
    assert http_server.hits["/evil/direct.png"] == 0
    assert http_server.hits["/evil/redirected.png"] == 0
    assert [(b.host, b.port) for b in report.blocked_requests] == [(FORBIDDEN_IP, http_server.port)]


def test_file_page_cannot_reach_local_network(http_server: LocalServer, tmp_path: Path) -> None:
    page = tmp_path / "yerel.html"
    page.write_text(
        "<!doctype html><html lang='tr'><title>Yerel</title><main><h1>Yerel</h1>"
        f"<img src='http://{ALLOWED_IP}:{http_server.port}/evil/from-file.png' alt='x'>"
        "</main></html>",
        encoding="utf-8",
    )
    report = _scan(str(page))  # varsayılan politika: yerel ağ yasak
    assert http_server.hits["/evil/from-file.png"] == 0
    assert report.blocked_requests[0].reason == "private_address"
