"""Klavye modülünün Set B / Set C ölçümü (NFR-04, NFR-05, SRS §8.2).

Kullanım (konteynerde):

    python experiments/keyboard_eval.py            # özet tablo
    python experiments/keyboard_eval.py --verbose  # sayfa başına tüm bulgular
    python experiments/keyboard_eval.py --json out.json

Tanımlar (kullanıcı kararı, 2026-11-16):

- Duyarlılık (NFR-04): Set B'deki beklenen klavye bulgularından yakalananların
  oranı. Eşleme anahtarı ``(rule, check, selector)``.
- Yanlış alarm (NFR-05): temiz sayfadaki ``needs_review=false`` klavye bulgusu.
  Oran = yanlış alarm ÷ o sayfada ziyaret edilen sekme durağı. "Elle incele"
  bulguları ayrı sayılır, hedefe katılmaz.
- Set B'de beklenmeyen kesin bulgular da yanlış alarm olarak raporlanır.

Ayrıca her beklenen bulgu için axe-core'un aynı öğede bir ihlal bulup
bulmadığı yazılır ("kural tabanlı aracın kaçırdığı").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from yanki.config import YankiConfig
from yanki.core.scan import run_scan
from yanki.core.schema import Finding

ROOT = Path(__file__).resolve().parents[1]
SET_B = ROOT / "fixtures" / "known_issues"
SET_C = ROOT / "fixtures" / "clean"

RECALL_TARGET = 0.90  # NFR-04
FALSE_ALARM_TARGET = 0.05  # NFR-05


def key(item: dict[str, object] | Finding) -> tuple[str, str, str]:
    if isinstance(item, Finding):
        return item.rule, item.evidence.check or "", item.selector
    return str(item["rule"]), str(item["check"]), str(item["selector"])


@dataclass
class PageResult:
    set: str
    page: str
    tab_stops: int
    expected: int
    found: int
    missed: list[str] = field(default_factory=list)
    false_alarms: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    axe_missed: int = 0
    rule_findings: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def false_alarm_rate(self) -> float:
        return len(self.false_alarms) / self.tab_stops if self.tab_stops else 0.0


async def evaluate_page(html: Path, set_name: str, config: YankiConfig) -> PageResult:
    spec = json.loads(html.with_suffix(".expected.json").read_text(encoding="utf-8"))
    report = await run_scan(str(html), config)
    keyboard = [f for f in report.findings if f.type == "keyboard"]
    rules = [f for f in report.findings if f.type == "rule"]
    expected = {key(e): e for e in spec["expected"]}
    allowed = {key(e) for e in spec.get("allowed_review", [])}
    got = {key(f): f for f in keyboard}

    rule_selectors = {f.selector for f in rules}
    result = PageResult(
        set=set_name,
        page=html.name,
        tab_stops=report.keyboard.tab_stops if report.keyboard else 0,
        expected=len(expected),
        found=sum(1 for k in expected if k in got),
        missed=[" ".join(k) for k in expected if k not in got],
        axe_missed=sum(1 for k in expected if k[2] not in rule_selectors),
        rule_findings=len(rules),
        warnings=report.warnings,
    )
    for k, f in got.items():
        if k in expected:
            continue
        label = f"{' '.join(k)} ({f.evidence.detail or ''})"
        if f.needs_review or k in allowed:
            result.review.append(label)
        else:
            result.false_alarms.append(label)
    return result


async def evaluate(config: YankiConfig) -> list[PageResult]:
    results: list[PageResult] = []
    for set_name, folder in (("B", SET_B), ("C", SET_C)):
        for html in sorted(folder.glob("*.html")):
            results.append(await evaluate_page(html, set_name, config))
    return results


def summarize(results: list[PageResult]) -> dict[str, float]:
    b = [r for r in results if r.set == "B"]
    c = [r for r in results if r.set == "C"]
    expected = sum(r.expected for r in b)
    found = sum(r.found for r in b)
    return {
        "recall": found / expected if expected else 1.0,
        "expected": expected,
        "found": found,
        "axe_missed": sum(r.axe_missed for r in b),
        "max_false_alarm_rate_c": max((r.false_alarm_rate for r in c), default=0.0),
        "false_alarms_c": sum(len(r.false_alarms) for r in c),
        "false_alarms_b": sum(len(r.false_alarms) for r in b),
        "review_c": sum(len(r.review) for r in c),
        "rule_findings_c": sum(r.rule_findings for r in c),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", type=Path, help="Sonuçları JSON olarak yaz.")
    args = parser.parse_args()

    config = YankiConfig()
    results = asyncio.run(evaluate(config))
    summary = summarize(results)

    print(f"{'Set':<4}{'Sayfa':<32}{'Durak':>6}{'Bek.':>6}{'Bul.':>6}{'YA':>4}{'İnc.':>6}")
    for r in results:
        print(
            f"{r.set:<4}{r.page:<32}{r.tab_stops:>6}{r.expected:>6}{r.found:>6}"
            f"{len(r.false_alarms):>4}{len(r.review):>6}"
        )
        if args.verbose or r.missed or r.false_alarms:
            for m in r.missed:
                print(f"      KAÇIRILDI   {m}")
            for fa in r.false_alarms:
                print(f"      YANLIŞ ALARM {fa}")
            if args.verbose:
                for rv in r.review:
                    print(f"      ELLE İNCELE {rv}")
                for w in r.warnings:
                    print(f"      uyarı       {w}")
    print()
    print(
        f"NFR-04 duyarlılık (Set B): {summary['recall']:.2%} "
        f"({summary['found']}/{summary['expected']}), hedef ≥ {RECALL_TARGET:.0%}"
    )
    print(
        f"NFR-05 en yüksek yanlış alarm oranı (Set C): {summary['max_false_alarm_rate_c']:.2%}, "
        f"hedef ≤ {FALSE_ALARM_TARGET:.0%} (kesin YA: {summary['false_alarms_c']}, "
        f"elle incele: {summary['review_c']})"
    )
    print(f"Set B'de beklenmeyen kesin bulgu: {summary['false_alarms_b']}")
    print(
        f"axe-core'un aynı öğede ihlal bulmadığı beklenen bulgu: "
        f"{summary['axe_missed']}/{summary['expected']}"
    )
    if args.json:
        args.json.write_text(
            json.dumps(
                {"summary": summary, "pages": [asdict(r) for r in results]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    ok = (
        summary["recall"] >= RECALL_TARGET
        and summary["max_false_alarm_rate_c"] <= FALSE_ALARM_TARGET
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
