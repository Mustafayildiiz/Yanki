"""Klavye bulgularının ortak şemaya dönüştürülmesi (KLV-03..10, PUA-03).

Denetim türü (``check``) → gereksinim numarası ve WCAG ölçütü eşlemesi bir
olgudur; burada sabittir. Ciddiyet ve güven ``KeyboardConfig``'den gelir.
"""

from __future__ import annotations

import hashlib

from yanki.config import KeyboardConfig
from yanki.core.schema import Evidence, Finding, KeyStep

RULES: dict[str, str] = {
    "keyboard_trap": "KLV-05",
    "focus_not_visible": "KLV-03",
    "focus_order": "KLV-04",
    "not_focusable": "KLV-06",
    "not_activatable": "KLV-07",
    "modal_focus_not_moved": "KLV-08",
    "modal_focus_escapes": "KLV-08",
    "modal_escape_not_closing": "KLV-08",
    "modal_focus_not_returned": "KLV-08",
    "skip_link_missing": "KLV-09",
    "skip_link_broken": "KLV-09",
}

WCAG: dict[str, list[str]] = {
    "keyboard_trap": ["2.1.2"],
    "focus_not_visible": ["2.4.7"],
    "focus_order": ["2.4.3"],
    "not_focusable": ["2.1.1"],
    "not_activatable": ["2.1.1"],
    "modal_focus_not_moved": ["2.4.3"],
    "modal_focus_escapes": ["2.4.3"],
    # APG diyalog deseni. WCAG'de doğrudan karşılığı yoktur; Escape klavyeyle
    # kapatmanın beklenen yoludur (varsayım, CLAUDE.md açık sorular).
    "modal_escape_not_closing": ["2.1.1"],
    "modal_focus_not_returned": ["2.4.3"],
    "skip_link_missing": ["2.4.1"],
    "skip_link_broken": ["2.4.1"],
}

RECOMMENDATIONS: dict[str, str] = {
    "keyboard_trap": (
        "Odağın bileşenden Tab ve Shift+Tab ile çıkabilmesini sağlayın; Tab tuşunu "
        "yakalayan klavye işleyicilerini kaldırın."
    ),
    "focus_not_visible": (
        "Odaklanan öğede görünür bir odak göstergesi kullanın (ör. :focus-visible için "
        "outline ya da box-shadow). Odaklanabilir öğeler ekran dışında ya da gizli olmamalı."
    ),
    "focus_order": (
        "Odak sırasını görsel okuma sırasıyla uyumlu hâle getirin: pozitif tabindex "
        "kullanmayın, CSS ile yeniden sıralanan öğelerin DOM sırasını düzeltin."
    ),
    "not_focusable": (
        "Tıklanabilir öğe için yerel bir <button> ya da <a href> kullanın; kullanılamıyorsa "
        'tabindex="0", uygun role ve Enter/Boşluk işleyicisi ekleyin.'
    ),
    "not_activatable": (
        "Düğme benzeri öğe Enter ve Boşluk ile de çalışmalıdır. Yerel <button> kullanın ya "
        "da keydown işleyicisi ekleyin."
    ),
    "modal_focus_not_moved": (
        "Modal açıldığında odağı pencerenin içine (ilk etkileşimli öğeye ya da başlığa) taşıyın."
    ),
    "modal_focus_escapes": (
        "Modal açıkken Tab ve Shift+Tab pencere içinde dönmelidir. <dialog>.showModal() "
        "kullanın ya da arka planı inert yapın."
    ),
    "modal_escape_not_closing": "Escape tuşu modal pencereyi kapatmalıdır (APG diyalog deseni).",
    "modal_focus_not_returned": "Modal kapandığında odağı pencereyi açan öğeye geri taşıyın.",
    "skip_link_missing": (
        "Sayfanın başına, ana içeriğe giden ve odaklanınca görünen bir "
        '"Ana içeriğe atla" bağlantısı ekleyin.'
    ),
    "skip_link_broken": (
        '"Ana içeriğe atla" bağlantısının hedefi var olmalı ve bağlantı etkinleştirilince '
        "odak ana içeriğe geçmelidir."
    ),
}


class ArtifactStore:
    """Kanıt dosyaları; içerik karmasıyla adlandırılır (KLV-10, NFR-06)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def add(self, png: bytes | None) -> str | None:
        if not png:
            return None
        name = f"evidence/{hashlib.sha256(png).hexdigest()[:16]}.png"
        self.files[name] = png
        return name


def make_finding(
    check: str,
    selector: str,
    cfg: KeyboardConfig,
    *,
    steps: list[KeyStep],
    screenshot: str | None,
    focus: str | None = None,
    heuristic: bool = False,
    trigger: str | None = None,
    previous: str | None = None,
    detail: str | None = None,
    **extra: object,
) -> Finding:
    """Klavye bulgusu üretir. Sezgisel kanıtta güven ``low_confidence`` olur."""
    confidence = cfg.low_confidence if heuristic else cfg.confidence[check]  # type: ignore[index]
    tail = steps[-cfg.evidence_max_steps :]
    return Finding(
        id="f-0000",
        type="keyboard",
        rule=RULES[check],
        severity=cfg.severity[check],  # type: ignore[index]
        wcag=WCAG[check],
        selector=selector or "html",
        evidence=Evidence(
            check=check,
            steps=list(tail),
            screenshot=screenshot,
            focus=focus,
            trigger=trigger,
            previous=previous,
            detail=detail,
            **extra,
        ),
        confidence=confidence,
        needs_review=confidence < cfg.review_threshold,
        recommendation=RECOMMENDATIONS[check],
    )
