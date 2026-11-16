"""Uyum skoru, sınıf ve güven hesabı (ALT-03, ALT-04, ALT-06, ALT-08).

Saf fonksiyonlardır; model gerektirmez ve verilen benzerlik değerleriyle test
edilir. Eşikler ``AltTextConfig``'ten gelir (kalibre edilmemiş başlangıç değerleri).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from yanki.config import AltTextConfig

Label = Literal["uygun", "yetersiz", "uyumsuz"]


def rescale(value: float, bounds: tuple[float, float]) -> float:
    """Ham kosinüs benzerliğini ``bounds`` aralığından 0–1'e doğrusal eşler ve kırpar."""
    lo, hi = bounds
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def combine(e5: float, clip: float, cfg: AltTextConfig) -> float:
    """Birleşik uyum skoru (ALT-03, ALT-04): 0–1'e eşlenmiş E5 ve CLIP'in ağırlıklı ortalaması."""
    total = cfg.e5_weight + cfg.clip_weight
    return (cfg.e5_weight * e5 + cfg.clip_weight * clip) / total


def label_for(score: float, cfg: AltTextConfig) -> Label:
    """ALT-06 sınıfı: ``≥ ok`` uygun, ``≥ mismatch`` yetersiz, altı uyumsuz."""
    if score >= cfg.ok_threshold:
        return "uygun"
    if score >= cfg.mismatch_threshold:
        return "yetersiz"
    return "uyumsuz"


def confidence_for(score: float, cfg: AltTextConfig) -> float:
    """Skorun en yakın karar eşiğine uzaklığından güven (0,5–1,0).

    Eşiğin tam üzerinde 0,5; ``confidence_margin`` kadar uzakta 1,0.
    """
    distance = min(abs(score - cfg.ok_threshold), abs(score - cfg.mismatch_threshold))
    return round(0.5 + 0.5 * min(1.0, distance / cfg.confidence_margin), 4)


@dataclass(frozen=True)
class Verdict:
    e5: float
    clip: float
    score: float
    label: Label
    confidence: float
    needs_review: bool
    review_reason: str | None = None


def judge(
    e5_raw: float,
    clip_raw: float,
    cfg: AltTextConfig,
    *,
    functional: bool = False,
    caption: str = "",
) -> Verdict:
    """Ham benzerliklerden sınıf ve güven üretir.

    İşlevsel görsellerde (bağlantı/düğme içi) ve açıklaması metin, grafik ya
    da logo gibi içerik belirten görsellerde bulgu en fazla "elle incele" olur:
    bu görsellerde alt metin görünüşü değil işlevi ya da metni anlatır.
    """
    e5 = rescale(e5_raw, cfg.e5_range)
    clip = rescale(clip_raw, cfg.clip_range)
    score = round(combine(e5, clip, cfg), 4)
    label = label_for(score, cfg)
    confidence = confidence_for(score, cfg)
    reason: str | None = None
    if functional and cfg.functional_image_review:
        reason = "functional_image"
    else:
        words = set(caption.casefold().replace(",", " ").split())
        hit = sorted(words & {k.casefold() for k in cfg.review_caption_keywords})
        if hit:
            reason = f"caption:{hit[0]}"
    needs_review = confidence < cfg.review_threshold or reason is not None
    return Verdict(
        e5=round(e5, 4),
        clip=round(clip, 4),
        score=score,
        label=label,
        confidence=confidence,
        needs_review=needs_review,
        review_reason=reason,
    )
