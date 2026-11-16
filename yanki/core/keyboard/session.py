"""Klavye modülünün sayfa oturumu: ortak yardımcılar, zaman bütçesi, sıfırlama.

Sayfa yalnızca durum değiştiyse (DOM değişimi, gezinme, açılan dialog)
yeniden yüklenir. Yeniden yükleme ``reset`` geri çağrısıyla yapılır; tarama
akışı bunu aynı proxy denetiminden geçirir (NFR-08).
"""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

from yanki.config import KeyboardConfig
from yanki.core.keyboard import js
from yanki.core.keyboard.findings import ArtifactStore
from yanki.core.schema import Finding, KeyStep

ResetFn = Callable[[], Awaitable[None]]


class Aborted(Exception):
    """Zaman bütçesi doldu ya da sayfa sıfırlanamadı; kalan sondalar atlanır."""


@dataclass(frozen=True)
class FocusState:
    """Bir tuştan sonra odağın durumu."""

    id: int | None
    """Sayfa içi öğe kimliği. ``None``: odak belgenin dışında."""
    opaque: bool = False
    tag: str | None = None
    rect: tuple[float, float, float, float] | None = None
    """Görünüm alanı koordinatında (x, y, genişlik, yükseklik)."""
    scroll: tuple[float, float] = (0.0, 0.0)
    modal: int | None = None
    visible: bool = False
    reason: str | None = None

    @classmethod
    def from_js(cls, data: dict[str, Any]) -> FocusState:
        rect = data.get("rect")
        return cls(
            id=data.get("id"),
            opaque=bool(data.get("opaque")),
            tag=data.get("tag"),
            rect=(rect["x"], rect["y"], rect["width"], rect["height"]) if rect else None,
            scroll=(float(data["scroll"][0]), float(data["scroll"][1])),
            modal=data.get("modal"),
            visible=bool(data.get("visible")),
            reason=data.get("reason"),
        )


@dataclass(frozen=True)
class ElementInfo:
    selector: str
    tag: str | None = None
    role: str | None = None
    name: str = ""


@dataclass
class Session:
    page: Page
    cfg: KeyboardConfig
    reset_fn: ResetFn
    deadline: float
    viewport: tuple[int, int]
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    warnings: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    probes: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    dirty: bool = False
    resets: int = 0

    # --- temel ------------------------------------------------------------

    def out_of_time(self) -> bool:
        return time.monotonic() > self.deadline

    def check_time(self) -> None:
        if self.out_of_time():
            raise Aborted(f"klavye testi zaman bütçesi ({self.cfg.time_budget_s:g} sn) doldu")

    async def install(self) -> None:
        await self.page.evaluate(js.INSTALL)

    async def settle(self) -> None:
        await self.page.evaluate(js.SETTLE)
        if self.cfg.settle_ms:
            await self.page.wait_for_timeout(self.cfg.settle_ms)

    async def wait_effect(self) -> None:
        if self.cfg.probe_wait_ms:
            await self.page.wait_for_timeout(self.cfg.probe_wait_ms)

    async def press(self, key: str) -> FocusState:
        await self.page.keyboard.press(key)
        await self.settle()
        return await self.read_focus()

    async def read_focus(self) -> FocusState:
        return FocusState.from_js(await self.page.evaluate(js.READ_FOCUS))

    async def describe(self, ids: list[int]) -> dict[int, ElementInfo]:
        if not ids:
            return {}
        raw: dict[str, dict[str, Any]] = await self.page.evaluate(js.DESCRIBE, sorted(set(ids)))
        return {
            int(k): ElementInfo(v["selector"], v.get("tag"), v.get("role"), v.get("name") or "")
            for k, v in raw.items()
        }

    async def resolve(self, selector: str) -> int | None:
        return await self.page.evaluate(js.RESOLVE, selector)

    async def focus(self, element_id: int) -> bool:
        return bool(await self.page.evaluate(js.FOCUS_ELEMENT, element_id))

    async def key_step(self, n: int, key: str, state: FocusState | None = None) -> KeyStep:
        """Kanıt adımı: ``key`` sonrasında odaklı öğe."""
        if state is None:
            state = await self.read_focus()
        if state.id is None:
            return KeyStep(n=n, key=key, selector=None)
        info = (await self.describe([state.id])).get(state.id)
        if info is None:
            return KeyStep(n=n, key=key, selector=None)
        return KeyStep(n=n, key=key, selector=info.selector, role=info.role, name=info.name)

    # --- ekran görüntüsü -----------------------------------------------------

    def clip_for(
        self, rect: tuple[float, float, float, float], pad: int
    ) -> dict[str, float] | None:
        vw, vh = self.viewport
        x, y, w, h = rect
        x0 = max(0, math.floor(x - pad))
        y0 = max(0, math.floor(y - pad))
        x1 = min(vw, math.ceil(x + w + pad))
        y1 = min(vh, math.ceil(y + h + pad))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}

    async def shot(self, clip: dict[str, float] | None = None) -> bytes | None:
        """Görünüm alanının ya da bir bölgesinin PNG görüntüsü; hata uyarıya dönüşür."""
        try:
            return await self.page.screenshot(
                clip=clip,
                animations="disabled",
                caret="hide",
                timeout=10_000,  # type: ignore[arg-type]
            )
        except Exception as exc:
            self.warn(f"ekran görüntüsü alınamadı: {_first_line(exc)}")
            return None

    async def evidence_shot(self, clip: dict[str, float] | None = None) -> str | None:
        return self.artifacts.add(await self.shot(clip))

    # --- durum ---------------------------------------------------------------

    async def reset(self) -> None:
        """Sayfayı yeniden yükler ve yardımcıları yeniden kurar."""
        self.check_time()
        try:
            await self.reset_fn()
            await self.install()
        except Exception as exc:
            raise Aborted(f"sayfa yeniden yüklenemedi: {_first_line(exc)}") from exc
        self.dirty = False
        self.resets += 1

    async def ensure_clean(self) -> None:
        if self.dirty:
            await self.reset()

    def warn(self, message: str) -> None:
        text = f"Klavye testi: {message}"
        if text not in self.warnings:
            self.warnings.append(text)

    def count(self, kind: str) -> None:
        self.probes[kind] = self.probes.get(kind, 0) + 1


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__
