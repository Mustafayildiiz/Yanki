"""Etkileşim sondaları: Enter/Boşluk (KLV-07), modal (KLV-08), tıklanabilir ama
odaklanamaz öğeler (KLV-06) ve atla bağlantısı (KLV-09).

Etkinleştirme politikası temkinlidir (kullanıcı kararı, 2026-11-16):

- submit/reset düğmeleri, ``<form>`` içindeki kontroller ve gerçek ``href``
  taşıyan bağlantılar asla etkinleştirilmez;
- sıra: beyan edilmiş modal tetikleyicileri (``aria-haspopup="dialog"``,
  ``aria-controls`` → dialog), yerel olmayan düğme benzerleri, diğer düğmeler
  ve ``href="#"`` / ``javascript:`` bağlantıları;
- toplam sonda sayısı ``max_probes`` ile sınırlıdır.

Bir eylemin "etkisi" DOM değişimi, belge dışı gezinme ya da yeni bir dialogdur.
Yalnızca adres parçası (``#``) değişimi etki sayılır ama durumu kirletmez.
Odak değişimi etki sayılmaz (tıklama, ``tabindex="-1"`` öğeyi de odaklar).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from yanki.core.keyboard import js
from yanki.core.keyboard.findings import make_finding
from yanki.core.keyboard.session import Aborted, ElementInfo, Session
from yanki.core.schema import KeyStep

BUTTON_ROLES = {"button"}
LINK_ROLES = {"link"}
TOGGLE_ROLES = {"checkbox", "switch", "radio", "menuitemcheckbox", "menuitemradio"}
ITEM_ROLES = {"menuitem", "tab", "option", "treeitem"}
BUTTONLIKE_ROLES = BUTTON_ROLES | LINK_ROLES | TOGGLE_ROLES | ITEM_ROLES


@dataclass(frozen=True)
class Effect:
    happened: bool
    dirty: bool
    navigated: bool = False
    dialogs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    selector: str
    info: dict[str, Any]
    category: int
    """0: beyan edilmiş modal tetikleyicisi, 1: yerel olmayan düğme benzeri, 2: diğer."""

    @property
    def non_native(self) -> bool:
        return self.category == 1 or (self.category == 0 and not self.info["native"])

    def required_keys(self) -> tuple[list[str], bool]:
        """(denenecek tuşlar, hepsi mi gerekli). APG desenlerine göre."""
        role = self.info.get("role") or ""
        if role in BUTTON_ROLES:
            return ["Enter", "Space"], True
        if role in LINK_ROLES:
            return ["Enter"], True
        if role in TOGGLE_ROLES:
            return ["Space"], True
        return ["Enter", "Space"], False


def classify(info: dict[str, Any] | None) -> int | None:
    """Etkinleştirme politikası: sonda kategorisi ya da ``None`` (dokunma)."""
    if info is None or info["in_form"] or info["submitish"]:
        return None
    tag, role, href = info["tag"], info.get("role"), info.get("href_kind")
    if href in ("real", "empty"):
        return None
    if info["contenteditable"] or tag in (
        "input",
        "select",
        "textarea",
        "iframe",
        "audio",
        "video",
        "label",
        "details",
    ):
        return None
    if info["declared"]:
        return 0
    if not info["native"]:
        # Ok tuşlarıyla çalışan bileşenler (slider, combobox, listbox …) Enter/Boşluk
        # beklemez; yalnızca düğme benzeri roller ve rolsüz tıklanabilir öğeler sınanır.
        if role in BUTTONLIKE_ROLES or (not role and (info["onclick"] or info["pointer"])):
            return 1
        return None
    if tag == "button" or href in ("hash", "js") or role == "button":
        return 2
    return None


class Prober:
    def __init__(self, session: Session) -> None:
        self.s = session
        self.cfg = session.cfg
        self.noisy = False
        self.tested_dialogs: set[str] = set()

    # --- gözlem ------------------------------------------------------------

    async def measure_noise(self) -> None:
        """Eylem yokken DOM değişiyorsa mutasyon sinyali güvenilmezdir."""
        await self.s.page.evaluate(js.WATCH_START, self.cfg.modal_min_cover)
        await self.s.wait_effect()
        data = await self.s.page.evaluate(js.WATCH_READ)
        self.noisy = bool(data and data["mutations"] > 0)
        if self.noisy:
            self.s.warn(
                "sayfa kendiliğinden değişiyor; etki tespiti yalnızca gezinme ve dialoga "
                "dayanıyor (KLV-06/07 bulguları eksik kalabilir)"
            )

    async def _hover(self, point: list[float]) -> None:
        """Fareyi önce öğenin üzerine getirir; hover kaynaklı değişim tıklama etkisi sayılmaz."""
        await self.s.page.mouse.move(point[0], point[1])
        await self.s.settle()
        await self.s.wait_effect()

    async def watch(self) -> None:
        await self.s.page.evaluate(js.WATCH_START, self.cfg.modal_min_cover)

    async def effect(self, url_before: str) -> Effect:
        await self.s.wait_effect()
        try:
            data = await self.s.page.evaluate(js.WATCH_READ)
        except Exception:
            data = None
        if data is None or self.s.page.url.split("#")[0] != url_before.split("#")[0]:
            # Belge değişti (gezinme): yardımcılar kayboldu.
            self.s.dirty = True
            return Effect(True, True, navigated=True)
        dialogs = list(data["new_dialogs"])
        mutated = data["mutations"] > 0 and not self.noisy
        dirty = mutated or bool(dialogs) or (data["url_changed"] and not data["hash_only"])
        happened = dirty or data["url_changed"]
        if dirty:
            self.s.dirty = True
        return Effect(happened, dirty, dialogs=dialogs)

    # --- KLV-07 / KLV-08 -----------------------------------------------------

    async def activation_and_modals(self, tab_stops: list[ElementInfo]) -> None:
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for el in tab_stops:
            if el.selector in seen or " >>> " in el.selector:
                continue
            seen.add(el.selector)
            element_id = await self.s.resolve(el.selector)
            if element_id is None:
                continue
            info = await self.s.page.evaluate(js.PROBE_INFO, element_id)
            category = classify(info)
            if category is not None:
                candidates.append(Candidate(el.selector, info, category))
        candidates.sort(key=lambda c: c.category)  # kararlı: aynı kategoride gezi sırası
        limit = self.cfg.max_probes
        if len(candidates) > limit:
            self.s.skipped.append(
                f"etkinleştirme sondası: {len(candidates) - limit} aday sınır nedeniyle atlandı"
            )
        for cand in candidates[:limit]:
            self.s.check_time()
            try:
                await self._probe(cand)
            except Aborted:
                raise
            except Exception as exc:
                self.s.dirty = True
                self.s.warn(f"{cand.selector} sondası yapılamadı: {_line(exc)}")

    async def _probe(self, cand: Candidate) -> None:
        self.s.count("activation")
        keys, need_all = cand.required_keys() if cand.non_native else (["Enter"], False)
        failed: list[str] = []
        for key in keys:
            outcome = await self._activate(cand, key)
            if outcome is None:
                return  # öğe artık bulunamıyor
            if not outcome.happened:
                failed.append(key)
        if not cand.non_native or not failed:
            return
        if not need_all and len(failed) < len(keys):
            return
        # Tuşlar işe yaramadı. Fareyle tıklama bir şey yapıyorsa KLV-07 bulgusudur.
        await self.s.ensure_clean()
        element_id = await self.s.resolve(cand.selector)
        if element_id is None:
            return
        point = await self.s.page.evaluate(js.CLICK_POINT, element_id)
        if point is None:
            return
        await self.s.focus(element_id)  # kanıt: odağın son durumu öğenin üzerinde
        shot = await self.s.evidence_shot()
        url = self.s.page.url
        await self._hover(point)
        await self.watch()
        await self.s.page.mouse.click(point[0], point[1])
        clicked = await self.effect(url)
        await self.s.page.mouse.move(0, 0)
        if clicked.dialogs and not clicked.navigated:
            steps = [KeyStep(n=1, key="click", selector=cand.selector)]
            await self._modal_checks(cand, clicked.dialogs[0], steps, element_id)
        if clicked.happened:
            steps = [KeyStep(n=1, key="focus()", selector=cand.selector)]
            steps += [
                KeyStep(n=i, key=k, selector=cand.selector) for i, k in enumerate(failed, start=2)
            ]
            steps.append(KeyStep(n=len(steps) + 1, key="click", selector=cand.selector))
            self.s.findings.append(
                make_finding(
                    "not_activatable",
                    cand.selector,
                    self.cfg,
                    steps=steps,
                    screenshot=shot,
                    focus=cand.selector,
                    detail="+".join(failed),
                )
            )

    async def _activate(self, cand: Candidate, key: str) -> Effect | None:
        await self.s.ensure_clean()
        element_id = await self.s.resolve(cand.selector)
        if element_id is None:
            return None
        if not await self.s.focus(element_id):
            return None
        steps = [KeyStep(n=1, key="focus()", selector=cand.selector)]
        url = self.s.page.url
        await self.watch()
        await self.s.page.keyboard.press(key)
        outcome = await self.effect(url)
        if outcome.dialogs and not outcome.navigated:
            steps.append(await self.s.key_step(2, key))
            await self._modal_checks(cand, outcome.dialogs[0], steps, element_id)
        return outcome

    async def _modal_checks(
        self,
        cand: Candidate,
        dialog: dict[str, Any],
        steps: list[KeyStep],
        trigger_id: int,
    ) -> None:
        """APG diyalog deseni: odak içeri, Tab içeride, Escape kapatır, odak geri döner."""
        s, cfg = self.s, self.cfg
        d_id: int = dialog["id"]
        # Modal olmayan dialog (aria-modal yok, show() ile açılmış) APG modal desenine
        # uymak zorunda değildir: bulguları elle incelenir.
        heuristic: bool = dialog["heuristic"] or not dialog.get("modal", False)
        info = (await s.describe([d_id])).get(d_id)
        if info is None or info.selector in self.tested_dialogs:
            return
        self.tested_dialogs.add(info.selector)
        s.count("modal")
        d_sel = info.selector
        common = {"trigger": cand.selector, "heuristic": heuristic}

        # 1. Odak pencerenin içine taşındı mı?
        moved = await self._wait_in_dialog(d_id)
        if not moved:
            state = await s.read_focus()
            focus_sel = (await s.key_step(0, "", state)).selector
            s.findings.append(
                make_finding(
                    "modal_focus_not_moved",
                    d_sel,
                    cfg,
                    steps=list(steps),
                    screenshot=await s.evidence_shot(),
                    focus=focus_sel,
                    **common,
                )
            )

        # 2. Tab pencere içinde dönüyor mu? İlk öğe programla odaklanır.
        inside = await s.page.evaluate(js.DIALOG_TABBABLES, d_id)
        # Modal olmayan (anlamsal) dialogdan Tab ile çıkmak serbesttir.
        contain = dialog.get("modal", False) or dialog["heuristic"]
        if contain and inside and await s.focus(inside[0]):
            tab_steps = [*steps, await s.key_step(len(steps) + 1, "focus()")]
            for _ in range(len(inside) + 2):
                state = await s.press("Tab")
                tab_steps.append(await s.key_step(len(tab_steps) + 1, "Tab", state))
                if state.id is None:
                    continue  # showModal: belge dışı adım normaldir
                if not await s.page.evaluate(js.IN_DIALOG, d_id):
                    s.findings.append(
                        make_finding(
                            "modal_focus_escapes",
                            d_sel,
                            cfg,
                            steps=tab_steps,
                            screenshot=await s.evidence_shot(),
                            focus=tab_steps[-1].selector,
                            detail=tab_steps[-1].selector,
                            **common,
                        )
                    )
                    break

        # 3. Escape kapatıyor mu? Escape pencerenin içinden basılır.
        esc_steps = list(steps)
        if inside and await s.focus(inside[0]):
            esc_steps.append(await s.key_step(len(esc_steps) + 1, "focus()"))
        await s.page.keyboard.press("Escape")
        await s.wait_effect()
        esc_steps.append(await s.key_step(len(esc_steps) + 1, "Escape"))
        if await s.page.evaluate(js.DIALOG_OPEN, d_id):
            s.findings.append(
                make_finding(
                    "modal_escape_not_closing",
                    d_sel,
                    cfg,
                    steps=esc_steps,
                    screenshot=await s.evidence_shot(),
                    focus=esc_steps[-1].selector,
                    **common,
                )
            )
            s.dirty = True
            return  # 4. denetim kapanmaya bağlıdır

        # 4. Odak tetikleyiciye döndü mü?
        active = await s.page.evaluate(js.ACTIVE_ID)
        if active != trigger_id:
            s.findings.append(
                make_finding(
                    "modal_focus_not_returned",
                    d_sel,
                    cfg,
                    steps=esc_steps,
                    screenshot=await s.evidence_shot(),
                    focus=esc_steps[-1].selector,
                    detail=esc_steps[-1].selector,
                    **common,
                )
            )
        s.dirty = True

    async def _wait_in_dialog(self, d_id: int) -> bool:
        waited = 0
        while True:
            if await self.s.page.evaluate(js.IN_DIALOG, d_id):
                return True
            if waited >= self.cfg.modal_focus_wait_ms:
                return False
            await self.s.page.wait_for_timeout(50)
            waited += 50

    # --- KLV-06 -------------------------------------------------------------

    async def clickables(self, candidates: list[str]) -> None:
        limit = self.cfg.max_click_probes
        if len(candidates) > limit:
            self.s.skipped.append(
                f"tıklama sondası: {len(candidates) - limit} aday sınır nedeniyle atlandı"
            )
        for selector in candidates[:limit]:
            self.s.check_time()
            try:
                await self._click_probe(selector)
            except Aborted:
                raise
            except Exception as exc:
                self.s.dirty = True
                self.s.warn(f"{selector} tıklama sondası yapılamadı: {_line(exc)}")

    async def _click_probe(self, selector: str) -> None:
        await self.s.ensure_clean()
        element_id = await self.s.resolve(selector)
        if element_id is None:
            return
        point = await self.s.page.evaluate(js.CLICK_POINT, element_id)
        if point is None:
            return
        self.s.count("click")
        shot = await self.s.evidence_shot()
        url = self.s.page.url
        await self._hover(point)
        await self.watch()
        await self.s.page.mouse.click(point[0], point[1])
        outcome = await self.effect(url)
        await self.s.page.mouse.move(0, 0)
        if outcome.happened:
            self.s.findings.append(
                make_finding(
                    "not_focusable",
                    selector,
                    self.cfg,
                    steps=[KeyStep(n=1, key="click", selector=selector)],
                    screenshot=shot,
                    focus=None,
                    detail="Tab gezisinde ulaşılamadı",
                )
            )

    # --- KLV-09 -------------------------------------------------------------

    async def skip_link(self, link: str, target: str | None, steps: list[KeyStep]) -> None:
        s = self.s
        s.count("skip_link")
        if target is None:
            s.findings.append(
                make_finding(
                    "skip_link_broken",
                    link,
                    self.cfg,
                    steps=steps,
                    screenshot=await s.evidence_shot(),
                    focus=link,
                    detail="target_missing",
                )
            )
            return
        await s.ensure_clean()
        link_id = await s.resolve(link)
        target_id = await s.resolve(target)
        if link_id is None or target_id is None or not await s.focus(link_id):
            return
        probe = [KeyStep(n=1, key="focus()", selector=link)]
        url = s.page.url
        await self.watch()
        await s.page.keyboard.press("Enter")
        outcome = await self.effect(url)
        if outcome.navigated:
            s.findings.append(
                make_finding(
                    "skip_link_broken",
                    link,
                    self.cfg,
                    steps=probe,
                    screenshot=await s.evidence_shot(),
                    focus=None,
                    detail="navigated",
                )
            )
            return
        probe.append(await s.key_step(2, "Enter"))
        if await s.page.evaluate(js.SKIP_LANDED, target_id):
            return
        state = await s.press("Tab")
        probe.append(await s.key_step(3, "Tab", state))
        if not await s.page.evaluate(js.SKIP_LANDED, target_id):
            s.findings.append(
                make_finding(
                    "skip_link_broken",
                    link,
                    self.cfg,
                    steps=probe,
                    screenshot=await s.evidence_shot(),
                    focus=probe[-1].selector,
                    detail="focus_not_moved",
                    target=target,
                )
            )


def _line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__
