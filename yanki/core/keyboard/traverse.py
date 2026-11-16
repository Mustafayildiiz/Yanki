"""Tab / Shift+Tab gezisi ve klavye tuzağı tespiti (KLV-01, KLV-02, KLV-05).

Headless Chromium'da gözlenen davranış (Faz 1, Aşama 0 sondası):

- Son duraktan sonra odak belgeden çıkar (``document.hasFocus()`` yanlış);
  sonraki Tab ilk durağa döner. Gezi bu noktada tamamlanmış sayılır.
- Başka kökenli bir iframe'in içindeki her durakta ``activeElement`` iframe'dir;
  bu tekrarlar takılma sayılmaz.
- ``<dialog>.showModal()`` içinde Tab, belge dışı bir adım üzerinden döner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from yanki.core.keyboard.session import FocusState, Session

Verdict = Literal["continue", "completed", "empty", "trap", "wrapped"]


class TrapDetector:
    """Odak dizisini adım adım izler (KLV-05).

    - Belge dışı adım: en az bir durak görüldüyse gezi tamamlandı.
    - Aynı öğe ``stuck_repeats`` kez art arda: odak ilerlemiyor (tuzak).
    - Belgeden çıkmadan, ilk öğe dışındaki bir öğe tekrar ederse döngü (tuzak).
    - İlk öğeye belgeden çıkmadan dönülürse sarma (``wrapped``); çağıran,
      ziyaret edilmemiş durak kalıp kalmadığına bakarak karar verir.
    """

    def __init__(self, stuck_repeats: int) -> None:
        self.stuck_repeats = stuck_repeats
        self.seq: list[int | None] = []
        self.first_index: dict[int, int] = {}
        self.first: int | None = None
        self.run = 0

    def feed(self, element_id: int | None, *, opaque: bool = False) -> tuple[Verdict, list[int]]:
        i = len(self.seq)
        prev = self.seq[-1] if self.seq else None
        self.seq.append(element_id)
        if element_id is None:
            return ("empty", []) if self.first is None else ("completed", [])
        if element_id == prev:
            if opaque:
                return "continue", []
            self.run += 1
            if self.run >= self.stuck_repeats - 1:
                return "trap", [element_id]
            return "continue", []
        self.run = 0
        if self.first is None:
            self.first = element_id
        if element_id in self.first_index:
            start = self.first_index[element_id]
            cycle = [x for x in dict.fromkeys(self.seq[start:i]) if x is not None]
            return ("wrapped" if element_id == self.first else "trap"), cycle
        self.first_index[element_id] = i
        return "continue", []


@dataclass
class Traversal:
    direction: Literal["forward", "backward"]
    states: list[FocusState] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    clips: list[list[dict[str, float]]] = field(default_factory=list)
    """Her adımda odak göstergesi için karşılaştırılan bölgeler (KLV-03)."""
    shots: list[list[bytes]] = field(default_factory=list)
    views: list[bytes | None] = field(default_factory=list)
    """Odaklı öğe görünmüyorsa görünüm alanı görüntüsü (KLV-10 kanıtı)."""
    outcome: str = "completed"
    """``completed``, ``empty``, ``trap``, ``wrapped``, ``limit``, ``budget``, ``error``."""
    cycle: list[int] = field(default_factory=list)
    trap_shot: bytes | None = None

    @property
    def element_ids(self) -> list[int]:
        return [s.id for s in self.states if s.id is not None]


_LARGE_CLIP = 250_000
"""Bu alandan büyük bölgelerde yalnızca dört kenar şeridi karşılaştırılır."""


def compare_clips(session: Session, state: FocusState) -> list[dict[str, float]]:
    """Odak göstergesi karşılaştırması için bölgeler (görünüm alanı koordinatı)."""
    if state.rect is None or not state.visible or state.opaque:
        return []
    pad = session.cfg.focus_clip_padding
    full = session.clip_for(state.rect, pad)
    if full is None:
        return []
    if full["width"] * full["height"] <= _LARGE_CLIP:
        return [full]
    # Büyük öğe: odak göstergesi kenarlarda görünür.
    band = 2 * pad + 4
    x, y, w, h = full["x"], full["y"], full["width"], full["height"]
    strips = [
        {"x": x, "y": y, "width": w, "height": min(band, h)},
        {"x": x, "y": y + max(0, h - band), "width": w, "height": min(band, h)},
        {"x": x, "y": y, "width": min(band, w), "height": h},
        {"x": x + max(0, w - band), "y": y, "width": min(band, w), "height": h},
    ]
    return [s for s in strips if s["width"] >= 1 and s["height"] >= 1]


async def traverse(
    session: Session,
    direction: Literal["forward", "backward"],
    *,
    capture: bool,
) -> Traversal:
    """Bir yönde gezer; her adımda odağı kaydeder (KLV-01, KLV-02)."""
    key = "Tab" if direction == "forward" else "Shift+Tab"
    result = Traversal(direction)
    detector = TrapDetector(session.cfg.stuck_repeats)
    try:
        start = await session.read_focus()
        if start.id is not None:
            # Sayfa açılışta bir öğeyi odaklamış (ör. autofocus): gezi oradan başlar.
            await _record(session, result, "başlangıç", start, capture)
            detector.feed(start.id, opaque=start.opaque)
        for _ in range(session.cfg.max_steps):
            if session.out_of_time():
                result.outcome = "budget"
                return result
            state = await session.press(key)
            await _record(session, result, key, state, capture)
            verdict, cycle = detector.feed(state.id, opaque=state.opaque)
            if verdict == "continue":
                continue
            result.outcome = verdict
            result.cycle = cycle
            if verdict in ("trap", "wrapped"):
                result.trap_shot = await session.shot()
            return result
        result.outcome = "limit"
    except Exception as exc:
        # NFR-16: gezi yarıda kalsa da kaydedilen adımlar raporlanır.
        result.outcome = "error"
        session.warn(f"{key} gezisi yarıda kaldı: {str(exc).splitlines()[0] if str(exc) else exc}")
    return result


async def _record(
    session: Session, result: Traversal, key: str, state: FocusState, capture: bool
) -> None:
    result.states.append(state)
    result.keys.append(key)
    clips = compare_clips(session, state) if capture else []
    shots: list[bytes] = []
    for clip in clips:
        png = await session.shot(clip)
        if png is None:
            shots = []
            break
        shots.append(png)
    result.clips.append(clips if shots else [])
    result.shots.append(shots)
    hidden = capture and state.id is not None and not state.opaque and not state.visible
    result.views.append(await session.shot() if hidden else None)
