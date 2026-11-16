"""axe-core ile kural tabanlı kontrol (KUR-01, KUR-02, KUR-03).

axe-core depoya gömülüdür (``yanki/core/vendor/axe.min.js``, MPL-2.0); çalışma
anında indirilmez. Kaynak, ``<script>`` etiketi yerine ``page.evaluate`` ile
enjekte edilir; böylece sayfanın Content-Security-Policy'si axe'ı engellemez.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import cache
from importlib import resources
from typing import Any

from playwright.async_api import Page

from yanki.core.schema import Evidence, Finding, RulesSummary, Severity

AXE_VERSION = "4.14.0"
"""Gömülü axe-core sürümü. Değişirse ``AXE_SHA256`` ve lisans listesi güncellenir."""
AXE_SHA256 = "20c09fe157a8a34a30e241aaa1fcdade657734f08ab379ecfbeb7d45cc46e878"

_WCAG_TAG = re.compile(r"^wcag(\d)(\d)(\d{1,2})$")

_RUN_JS = """
async (options) => {
  const r = await axe.run(document, options);
  return {
    version: axe.version,
    violations: r.violations.map(v => ({
      id: v.id, impact: v.impact, tags: v.tags, help: v.help, helpUrl: v.helpUrl,
      nodes: v.nodes.map(n => ({
        target: n.target, html: n.html, impact: n.impact, failureSummary: n.failureSummary,
      })),
    })),
    passes: r.passes.length,
    incomplete: r.incomplete.length,
    inapplicable: r.inapplicable.length,
  };
}
"""


@cache
def axe_source() -> str:
    return resources.files("yanki.core.vendor").joinpath("axe.min.js").read_text(encoding="utf-8")


async def inject_axe(page: Page) -> list[str]:
    """axe-core'u ana çerçeveye ve iç çerçevelere enjekte eder.

    Ana çerçevede başarısız olursa istisna yükselir. İç çerçeve hataları
    taramayı durdurmaz, uyarı olarak döner (NFR-16).
    """
    source = axe_source()
    await page.main_frame.evaluate(source)
    warnings: list[str] = []
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        try:
            await frame.evaluate(source)
        except Exception as exc:
            warnings.append(f"axe-core iç çerçeveye enjekte edilemedi ({frame.url}): {exc}")
    return warnings


def wcag_from_tags(tags: Iterable[str]) -> list[str]:
    """axe etiketlerinden WCAG başarı ölçütlerini çıkarır (KUR-02).

    ``wcag111`` → ``1.1.1``, ``wcag1410`` → ``1.4.10``. Düzey etiketleri
    (``wcag2aa``, ``wcag21a``) harf içerdiği için eşleşmez.
    """
    found: set[tuple[int, int, int]] = set()
    for tag in tags:
        m = _WCAG_TAG.match(tag)
        if m:
            found.add((int(m[1]), int(m[2]), int(m[3])))
    return [f"{a}.{b}.{c}" for a, b, c in sorted(found)]


def target_to_selector(target: list[Any]) -> str:
    """axe ``target`` dizisini tek bir seçiciye çevirir.

    Her düzey (iç çerçeve ya da shadow root) `` >>> `` ile ayrılır.
    """
    parts: list[str] = []
    for item in target:
        if isinstance(item, list):
            parts.extend(str(x) for x in item)
        else:
            parts.append(str(item))
    return " >>> ".join(parts)


def violations_to_findings(
    violations: list[Mapping[str, Any]], severity_map: Mapping[str, Severity]
) -> list[Finding]:
    """axe ihlallerini ortak bulgu şemasına çevirir (KUR-01).

    İhlal eden her öğe ayrı bir bulgudur. Kimlikler ``finalize_findings``
    ile sonradan atanır.
    """
    findings: list[Finding] = []
    for v in violations:
        wcag = wcag_from_tags(v.get("tags", []))
        for node in v.get("nodes", []):
            impact = node.get("impact") or v.get("impact") or "minor"
            summary = node.get("failureSummary") or ""
            findings.append(
                Finding(
                    id="f-0000",
                    type="rule",
                    rule=v["id"],
                    severity=severity_map[impact],
                    wcag=wcag,
                    selector=target_to_selector(node.get("target", [])),
                    evidence=Evidence(
                        html=node.get("html"),
                        help=v.get("help"),
                        help_url=v.get("helpUrl"),
                        failure_summary=summary or None,
                        axe_impact=impact,
                    ),
                    confidence=1.0,
                    needs_review=False,
                    recommendation=summary or v.get("help", ""),
                )
            )
    return findings


async def run_axe(
    page: Page, *, tags: list[str], severity_map: Mapping[str, Severity]
) -> tuple[str, list[Finding], RulesSummary]:
    """axe-core'u çalıştırır; (çalışan axe sürümü, bulgular, özet) döner (KUR-03)."""
    options = {"runOnly": {"type": "tag", "values": tags}, "resultTypes": ["violations"]}
    result = await page.evaluate(_RUN_JS, options)
    findings = violations_to_findings(result["violations"], severity_map)
    summary = RulesSummary(
        tags=list(tags),
        violations=len(result["violations"]),
        passes=result["passes"],
        incomplete=result["incomplete"],
        inapplicable=result["inapplicable"],
    )
    return str(result["version"]), findings, summary
