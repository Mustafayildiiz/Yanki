"""axe sonucu → ortak şema eşlemesi (KUR-01, KUR-02) birim testleri."""

from __future__ import annotations

from yanki.config import RulesConfig
from yanki.core.rules import target_to_selector, violations_to_findings, wcag_from_tags


def test_wcag_tags_parsed_and_level_tags_ignored() -> None:
    tags = [
        "cat.color",
        "wcag2aa",
        "wcag21aa",
        "wcag22aa",
        "wcag1410",
        "wcag143",
        "wcag2411",
        "best-practice",
        "EN-9.1.4.3",
    ]
    assert wcag_from_tags(tags) == ["1.4.3", "1.4.10", "2.4.11"]


def test_wcag_tags_empty_for_best_practice() -> None:
    assert wcag_from_tags(["cat.keyboard", "best-practice"]) == []


def test_target_to_selector_handles_frames_and_shadow_dom() -> None:
    assert target_to_selector(["#a"]) == "#a"
    assert target_to_selector(["iframe#f", "#b"]) == "iframe#f >>> #b"
    assert target_to_selector([["#host", "button"]]) == "#host >>> button"


def test_one_finding_per_node_with_severity_mapping() -> None:
    violations = [
        {
            "id": "image-alt",
            "impact": "critical",
            "tags": ["wcag2a", "wcag111"],
            "help": "Images must have alternative text",
            "helpUrl": "https://dequeuniversity.com/rules/axe/4.14/image-alt",
            "nodes": [
                {
                    "target": ["#a"],
                    "html": "<img id=a>",
                    "impact": "critical",
                    "failureSummary": "Fix any of the following: ...",
                },
                {"target": ["#b"], "html": "<img id=b>", "impact": None, "failureSummary": None},
            ],
        },
        {
            "id": "color-contrast",
            "impact": "serious",
            "tags": ["wcag2aa", "wcag143"],
            "help": "Contrast",
            "helpUrl": "u",
            "nodes": [{"target": ["p"], "html": "<p>", "impact": "serious", "failureSummary": "x"}],
        },
    ]
    findings = violations_to_findings(violations, RulesConfig().severity_map)
    assert [(f.rule, f.selector, f.severity, f.wcag) for f in findings] == [
        ("image-alt", "#a", "critical", ["1.1.1"]),
        ("image-alt", "#b", "critical", ["1.1.1"]),
        ("color-contrast", "p", "important", ["1.4.3"]),
    ]
    for f in findings:
        assert f.type == "rule"
        assert f.confidence == 1.0
        assert f.needs_review is False
        assert f.recommendation
        assert f.evidence.help_url
    # failureSummary yoksa öneri axe "help" metnine düşer.
    assert findings[1].recommendation == "Images must have alternative text"


def test_all_axe_impacts_mapped() -> None:
    mapping = RulesConfig().severity_map
    assert mapping == {
        "critical": "critical",
        "serious": "important",
        "moderate": "moderate",
        "minor": "low",
    }
