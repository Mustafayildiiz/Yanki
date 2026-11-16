"""Odak göstergesi görünürlüğü (KLV-03, WCAG 2.4.7).

Gezi sırasında her durakta odaklı öğenin çevresi çekilir (``traverse``).
Gezi bittikten sonra odak boşaltılır, her durağın kaydırma konumuna dönülür
ve aynı bölge odaksız çekilir. İki görüntü arasında değişen piksel yoksa
odak göstergesi görünmüyordur. Odak boşaltılıp yeniden verilmez; sayfanın
focusout işleyicileri gezi sırasını bozmaz.

Odaklı öğe gizli, çok küçük ya da görünüm alanının dışındaysa da gösterge
görünmez sayılır (ör. ekran dışına itilmiş menü bağlantıları).
"""

from __future__ import annotations

from dataclasses import dataclass

from yanki.core.keyboard import js
from yanki.core.keyboard.png import PngError, changed_pixels, decode_png
from yanki.core.keyboard.session import Session
from yanki.core.keyboard.traverse import Traversal


@dataclass(frozen=True)
class FocusVerdict:
    visible: bool | None
    """``None``: değerlendirilemedi (belge dışı, iframe, görüntü yok)."""
    heuristic: bool = False
    """Değişim eşiğin altında ama sıfır değil: elle incelenmeli."""
    reason: str | None = None
    changed: int | None = None


async def assess(session: Session, trav: Traversal) -> list[FocusVerdict]:
    """Her adım için odak göstergesi kararı (``trav.states`` ile aynı sırada)."""
    cfg = session.cfg
    verdicts: list[FocusVerdict] = []
    done: dict[int, FocusVerdict] = {}
    try:
        await session.page.evaluate(js.BLUR)
    except Exception as exc:
        session.warn(f"odak boşaltılamadı: {exc}")
    for state, clips, shots in zip(trav.states, trav.clips, trav.shots, strict=True):
        if state.id is None or state.opaque:
            verdicts.append(FocusVerdict(None))
            continue
        if state.id in done:
            verdicts.append(done[state.id])
            continue
        if not state.visible:
            verdict = FocusVerdict(False, reason=state.reason)
        elif not shots:
            verdict = FocusVerdict(None)
        else:
            verdict = await _compare(session, state.scroll, clips, shots, cfg.focus_pixel_delta)
            if verdict.changed is not None and verdict.changed < cfg.focus_min_changed_pixels:
                verdict = FocusVerdict(
                    False,
                    heuristic=verdict.changed > 0,
                    reason="no_change" if verdict.changed == 0 else "faint",
                    changed=verdict.changed,
                )
        done[state.id] = verdict
        verdicts.append(verdict)
    try:
        await session.page.evaluate(js.SCROLL_TO, [0, 0])
    except Exception:  # noqa: S110 - yalnızca konum düzeltmesi
        pass
    return verdicts


async def _compare(
    session: Session,
    scroll: tuple[float, float],
    clips: list[dict[str, float]],
    focused: list[bytes],
    delta: int,
) -> FocusVerdict:
    try:
        await session.page.evaluate(js.SCROLL_TO, [scroll[0], scroll[1]])
        await session.settle()
        total = 0
        for clip, png in zip(clips, focused, strict=True):
            base = await session.shot(clip)
            if base is None:
                return FocusVerdict(None)
            total += changed_pixels(decode_png(png), decode_png(base), delta)
    except PngError as exc:
        session.warn(f"odak görüntüsü çözülemedi: {exc}")
        return FocusVerdict(None)
    return FocusVerdict(True, changed=total)
