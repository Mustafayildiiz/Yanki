"""Klavye ile gezinme testi (KLV-01..10).

Giriş noktası ``run_keyboard``. Tarama akışı (``yanki.core.scan``) bu modülü
axe ve öğe toplamadan **sonra** çağırır; çünkü klavye testi sayfanın durumunu
değiştirir (odak, açılan pencereler, yeniden yükleme).

Sıra:

1. İleri Tab gezisi; her durakta odak bölgesinin görüntüsü (KLV-01, KLV-05).
2. Shift+Tab gezisi (KLV-02); yalnızca geri yönde oluşan tuzaklar.
3. Odak göstergesi karşılaştırması (KLV-03) ve odak sırası analizi (KLV-04).
4. Sondalar: atla bağlantısı (KLV-09), Enter/Boşluk ve modal (KLV-07, KLV-08),
   tıklanabilir ama odaklanamaz öğeler (KLV-06).

Hiçbir hata taramayı durdurmaz: kalan adımlar atlanır, uyarı yazılır ve o ana
kadarki sonuç raporlanır (NFR-16, SRS §9.1).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Dialog, Page

from yanki.config import KeyboardConfig
from yanki.core.keyboard import js
from yanki.core.keyboard.findings import make_finding
from yanki.core.keyboard.focus import FocusVerdict, assess
from yanki.core.keyboard.order import Rect, order_deviations
from yanki.core.keyboard.probes import Prober
from yanki.core.keyboard.session import Aborted, ElementInfo, ResetFn, Session
from yanki.core.keyboard.traverse import Traversal, traverse
from yanki.core.schema import BoundingBox, Finding, KeyboardReport, KeyboardStep, KeyStep

__all__ = ["KeyboardResult", "run_keyboard"]


@dataclass
class KeyboardResult:
    report: KeyboardReport
    findings: list[Finding] = field(default_factory=list)
    artifacts: dict[str, bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


async def run_keyboard(page: Page, cfg: KeyboardConfig, *, reset: ResetFn) -> KeyboardResult:
    """Klavye testini çalıştırır; hata olsa da kısmi sonuç döner (NFR-16)."""
    size = page.viewport_size or {"width": 1280, "height": 800}
    session = Session(
        page=page,
        cfg=cfg,
        reset_fn=reset,
        deadline=time.monotonic() + cfg.time_budget_s,
        viewport=(size["width"], size["height"]),
    )
    report = KeyboardReport()
    background: set[asyncio.Task[Any]] = set()

    async def on_dialog(dialog: Dialog) -> None:
        try:
            await dialog.dismiss()
        except Exception:  # noqa: S110 - dialog zaten kapanmış olabilir
            pass

    def on_popup(popup: Page) -> None:
        task = asyncio.ensure_future(popup.close())
        background.add(task)
        task.add_done_callback(background.discard)

    page.on("dialog", on_dialog)
    page.context.on("page", on_popup)
    try:
        await _run(session, report)
    except Aborted as exc:
        session.warn(f"{exc}; kalan denetimler atlandı")
    except Exception as exc:
        session.warn(f"beklenmeyen hata, kalan denetimler atlandı: {_line(exc)}")
    finally:
        page.remove_listener("dialog", on_dialog)
        page.context.remove_listener("page", on_popup)
        if background:
            await asyncio.gather(*background, return_exceptions=True)

    report.probes = dict(sorted(session.probes.items()))
    report.skipped = session.skipped
    return KeyboardResult(
        report=report,
        findings=_dedupe(session.findings),
        artifacts=dict(session.artifacts.files),
        warnings=session.warnings,
    )


async def _run(session: Session, report: KeyboardReport) -> None:
    cfg = session.cfg
    page = session.page
    await session.install()
    page_info = await page.evaluate(js.PAGE_INFO)

    # 1. İleri gezi (KLV-01, KLV-05).
    fwd = await traverse(session, "forward", capture=True)
    fwd_info = await session.describe(fwd.element_ids)
    report.tab_stops = len(set(fwd.element_ids))
    report.completed = fwd.outcome in ("completed", "empty")
    report.step_limit_reached = fwd.outcome == "limit"
    trap_sets: list[set[str]] = []
    fwd_trapped = await _trap_findings(session, fwd, fwd_info, trap_sets)
    if fwd_trapped:
        report.completed = False

    # Gezi sonrası, kimlikler hâlâ geçerliyken toplanan bilgiler.
    visited = sorted(set(fwd.element_ids))
    skip = await _find_skip_link(session, fwd, fwd_info)
    stops_before_main = await page.evaluate(js.STOPS_BEFORE_MAIN, fwd.element_ids)
    click_ids: list[int] = await page.evaluate(js.CLICK_CANDIDATES, visited)
    click_info = await session.describe(click_ids)
    click_selectors = [click_info[i].selector for i in click_ids if i in click_info]

    # 2. Geri gezi (KLV-02). İleri gezi tuzağa düştüyse sayfa önce sıfırlanır.
    if fwd_trapped or fwd.outcome == "error":
        session.dirty = True
    await session.ensure_clean()
    bwd = await traverse(session, "backward", capture=False)
    bwd_info = await session.describe(bwd.element_ids)
    await _trap_findings(session, bwd, bwd_info, trap_sets)

    # 3. Odak göstergesi (KLV-03) ve sıra (KLV-04).
    verdicts = await assess(session, fwd)
    await _focus_findings(session, fwd, fwd_info, verdicts)
    _order_findings(session, fwd, fwd_info, rtl=bool(page_info["rtl"]))
    report.steps = _steps(fwd, fwd_info, verdicts)
    report.backward_steps = _steps(bwd, bwd_info, None)

    # 4. Sondalar (KLV-09, KLV-07, KLV-08, KLV-06).
    session.check_time()
    prober = Prober(session)
    await session.ensure_clean()
    await prober.measure_noise()
    if skip is not None:
        link, target, upto = skip
        await prober.skip_link(link, target, _key_steps(fwd, fwd_info, upto))
    elif stops_before_main is not None and stops_before_main >= cfg.skip_link_min_stops:
        await session.ensure_clean()
        await page.evaluate(js.SCROLL_TO, [0, 0])
        session.findings.append(
            make_finding(
                "skip_link_missing",
                "html",
                cfg,
                steps=_key_steps(fwd, fwd_info, stops_before_main),
                screenshot=await session.evidence_shot(),
                detail=f"main öncesinde {stops_before_main} durak",
            )
        )
    tab_stops = [fwd_info[i] for i in dict.fromkeys(fwd.element_ids) if i in fwd_info]
    await prober.activation_and_modals(tab_stops)
    await prober.clickables(click_selectors)


# --- KLV-05 -------------------------------------------------------------------


async def _trap_findings(
    session: Session,
    trav: Traversal,
    info: dict[int, ElementInfo],
    trap_sets: list[set[str]],
) -> bool:
    """Tuzak bulgusu üretir; gezi tuzakla bittiyse ``True`` döner."""
    key = "Tab" if trav.direction == "forward" else "Shift+Tab"
    if trav.outcome == "limit":
        session.warn(
            f"{key} gezisi {session.cfg.max_steps} adım sınırına ulaştı; "
            "tuzak değerlendirilemedi (KLV-05)"
        )
        return False
    if trav.outcome == "budget":
        session.warn(f"{key} gezisi zaman bütçesi nedeniyle yarıda kaldı")
        return False
    if trav.outcome not in ("trap", "wrapped") or not trav.cycle:
        return False

    cycle_states = [s for s in trav.states if s.id in set(trav.cycle)]
    modals = {s.modal for s in cycle_states}
    if len(modals) == 1 and None not in modals:
        session.warn(f"{key} gezisi açık bir modal pencerenin içinde döndü; tuzak sayılmadı")
        return True

    heuristic = False
    if trav.outcome == "wrapped":
        unvisited = await session.page.evaluate(
            js.UNVISITED_AFTER, [trav.cycle, sorted(set(trav.element_ids))]
        )
        if not unvisited:
            return False  # sayfa başa sarıldı, atlanan durak yok
        heuristic = True

    selectors = [info[i].selector for i in trav.cycle if i in info]
    if not selectors:
        return True
    if any(set(selectors) & seen for seen in trap_sets):
        return True  # aynı tuzak diğer yönde zaten raporlandı
    trap_sets.append(set(selectors))
    last = trav.states[-1]
    session.findings.append(
        make_finding(
            "keyboard_trap",
            selectors[0],
            session.cfg,
            steps=_key_steps(trav, info, len(trav.states)),
            screenshot=session.artifacts.add(trav.trap_shot),
            focus=info[last.id].selector if last.id in info else None,
            heuristic=heuristic,
            detail=f"{trav.direction}: " + ", ".join(selectors),
            cycle=selectors,
        )
    )
    return True


# --- KLV-03 / KLV-04 ------------------------------------------------------------


async def _focus_findings(
    session: Session,
    trav: Traversal,
    info: dict[int, ElementInfo],
    verdicts: list[FocusVerdict],
) -> None:
    reported: set[int] = set()
    for i, (state, verdict) in enumerate(zip(trav.states, verdicts, strict=True)):
        if verdict.visible is not False or state.id is None or state.id in reported:
            continue
        if state.id not in info:
            continue
        reported.add(state.id)
        png = trav.shots[i][0] if trav.shots[i] else trav.views[i]
        session.findings.append(
            make_finding(
                "focus_not_visible",
                info[state.id].selector,
                session.cfg,
                steps=_key_steps(trav, info, i + 1),
                screenshot=session.artifacts.add(png),
                focus=info[state.id].selector,
                heuristic=verdict.heuristic,
                detail=verdict.reason,
            )
        )


def _order_findings(
    session: Session, trav: Traversal, info: dict[int, ElementInfo], *, rtl: bool
) -> None:
    cfg = session.cfg
    indices: list[int] = []
    rects: list[Rect | None] = []
    seen: set[int] = set()
    for i, state in enumerate(trav.states):
        if state.id is None or state.id in seen:
            continue
        seen.add(state.id)
        indices.append(i)
        if state.rect is None or not state.visible or state.opaque:
            rects.append(None)
        else:
            x, y, w, h = state.rect
            rects.append(Rect(x + state.scroll[0], y + state.scroll[1], w, h))
    deviations = order_deviations(
        rects, rtl=rtl, x_tol=cfg.order_x_tolerance, y_tol=cfg.order_y_tolerance
    )
    for k in deviations:
        i = indices[k]
        prev_k = max(j for j in range(k) if rects[j] is not None)
        cur, prev = trav.states[i], trav.states[indices[prev_k]]
        if cur.id not in info or prev.id not in info:
            continue
        png = trav.shots[i][0] if trav.shots[i] else None
        session.findings.append(
            make_finding(
                "focus_order",
                info[cur.id].selector,
                cfg,
                steps=_key_steps(trav, info, i + 1),
                screenshot=session.artifacts.add(png),
                focus=info[cur.id].selector,
                previous=info[prev.id].selector,
            )
        )


# --- KLV-09 -------------------------------------------------------------------


async def _find_skip_link(
    session: Session, trav: Traversal, info: dict[int, ElementInfo]
) -> tuple[str, str | None, int] | None:
    """İlk üç durakta atla bağlantısı: (bağlantı, hedef seçicisi, adım sayısı)."""
    first = list(dict.fromkeys(trav.element_ids))[:3]
    for element_id in first:
        data = await session.page.evaluate(js.SKIP_INFO, element_id)
        if data is None or element_id not in info:
            continue
        target_sel = None
        if data["target"] is not None:
            target_sel = (await session.describe([data["target"]]))[data["target"]].selector
        upto = next(i for i, s in enumerate(trav.states) if s.id == element_id) + 1
        return info[element_id].selector, target_sel, upto
    return None


# --- yardımcılar --------------------------------------------------------------


def _key_steps(trav: Traversal, info: dict[int, ElementInfo], upto: int) -> list[KeyStep]:
    out: list[KeyStep] = []
    for n, (key, state) in enumerate(zip(trav.keys[:upto], trav.states[:upto], strict=True), 1):
        el = info.get(state.id) if state.id is not None else None
        out.append(
            KeyStep(
                n=n,
                key=key,
                selector=el.selector if el else None,
                role=el.role if el else None,
                name=el.name if el else "",
            )
        )
    return out


def _steps(
    trav: Traversal, info: dict[int, ElementInfo], verdicts: list[FocusVerdict] | None
) -> list[KeyboardStep]:
    out: list[KeyboardStep] = []
    for i, state in enumerate(trav.states):
        el = info.get(state.id) if state.id is not None else None
        bbox = None
        if state.rect is not None:
            x, y, w, h = state.rect
            bbox = BoundingBox(
                x=round(x + state.scroll[0]),
                y=round(y + state.scroll[1]),
                width=round(w),
                height=round(h),
            )
        out.append(
            KeyboardStep(
                index=i + 1,
                direction=trav.direction,
                selector=el.selector if el else None,
                tag=state.tag,
                role=el.role if el else None,
                name=el.name if el else "",
                bbox=bbox,
                in_frame=state.opaque,
                focus_visible=verdicts[i].visible if verdicts is not None else None,
            )
        )
    return out


def _dedupe(findings: list[Finding]) -> list[Finding]:
    out: dict[tuple[str, str], Finding] = {}
    for f in findings:
        out.setdefault((f.evidence.check or "", f.selector), f)
    return list(out.values())


def _line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__
