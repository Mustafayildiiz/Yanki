"""Bulgu şeması (SRS §6.4, RAP-02, KUR-01) birim testleri."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yanki.core.schema import Evidence, Finding, finalize_findings


def _finding(**overrides: object) -> Finding:
    data: dict[str, object] = {
        "id": "f-0001",
        "type": "rule",
        "rule": "image-alt",
        "severity": "critical",
        "wcag": ["1.1.1"],
        "selector": "#hero",
        "evidence": {"html": "<img id=hero>"},
        "confidence": 1.0,
        "needs_review": False,
        "recommendation": "Alt metin ekleyin.",
    }
    data.update(overrides)
    return Finding.model_validate(data)


def test_srs_example_validates_with_alt_translated() -> None:
    # SRS §6.4 örneği + ALT-04 gereği alt_translated alanı.
    f = _finding(
        id="f-0042",
        type="alt_text",
        rule="ALT-06",
        severity="important",
        selector="main > section:nth-of-type(2) img",
        evidence={
            "alt": "image1.png",
            "alt_translated": "image1.png",
            "caption": "a woman speaking at a podium",
            "similarity": 0.18,
            "screenshot": "evidence/f-0042.png",
        },
        confidence=0.91,
    )
    assert f.evidence.alt == "image1.png"
    assert f.evidence.alt_translated == "image1.png"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "ai"),
        ("severity", "serious"),
        ("confidence", 1.5),
        ("wcag", ["1.1"]),
        ("id", "42"),
        ("selector", ""),
    ],
)
def test_invalid_values_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _finding(**{field: value})


def test_unknown_top_level_field_rejected() -> None:
    with pytest.raises(ValidationError):
        _finding(extra_field=1)


def test_rule_findings_cannot_look_like_ai_findings() -> None:
    # CLAUDE.md §9: kural sonuçları yapay zeka bulgusu gibi sunulmaz.
    with pytest.raises(ValidationError):
        _finding(confidence=0.7)
    with pytest.raises(ValidationError):
        _finding(needs_review=True)


def test_evidence_allows_module_specific_fields() -> None:
    ev = Evidence.model_validate({"cycle": ["#a", "#b"], "alt": "x"})
    assert ev.model_dump()["cycle"] == ["#a", "#b"]


def test_keyboard_steps_are_typed() -> None:
    # KLV-10: adım dizisi tiplidir (tuş, odaklı öğe).
    ev = Evidence.model_validate({"check": "keyboard_trap", "steps": [{"n": 1, "key": "Tab"}]})
    assert ev.steps is not None and ev.steps[0].key == "Tab" and ev.steps[0].selector is None
    with pytest.raises(ValidationError):
        Evidence.model_validate({"steps": ["Tab"]})


def test_finalize_findings_is_deterministic() -> None:
    a = _finding(rule="label", selector="#b")
    b = _finding(rule="image-alt", selector="#z")
    c = _finding(rule="image-alt", selector="#a")
    first = finalize_findings([a, b, c])
    second = finalize_findings([c, a, b])
    assert [f.model_dump() for f in first] == [f.model_dump() for f in second]
    assert [(f.id, f.rule, f.selector) for f in first] == [
        ("f-0001", "image-alt", "#a"),
        ("f-0002", "image-alt", "#z"),
        ("f-0003", "label", "#b"),
    ]
