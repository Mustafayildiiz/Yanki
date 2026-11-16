"""Klavye modülünün tarayıcısız birim testleri.

KLV-03 (PNG farkı), KLV-04 (sıra), KLV-05 (tuzak tespiti), KLV-07/08
(etkinleştirme politikası), KLV-10 (kanıt), PUA-03 (tuzak ciddiyeti).
"""

from __future__ import annotations

import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from yanki.config import KEYBOARD_CHECKS, KeyboardConfig, YankiConfig, load_config
from yanki.core.evidence import save_evidence
from yanki.core.keyboard.findings import RULES, WCAG, ArtifactStore, make_finding
from yanki.core.keyboard.order import Rect, is_deviation, order_deviations
from yanki.core.keyboard.png import PngError, changed_pixels, decode_png, encode_png
from yanki.core.keyboard.probes import classify
from yanki.core.keyboard.traverse import TrapDetector
from yanki.core.schema import (
    SCHEMA_VERSION,
    KeyStep,
    PageInfo,
    RulesSummary,
    ScanReport,
    ToolVersions,
    finalize_findings,
)

# --- KLV-03: PNG çözücü ve piksel farkı ----------------------------------------


def _solid(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    return bytes(rgb) * (w * h)


def test_png_roundtrip() -> None:
    img = decode_png(encode_png(3, 2, _solid(3, 2, (10, 20, 30))))
    assert (img.width, img.height, img.channels) == (3, 2, 3)
    assert img.pixels == _solid(3, 2, (10, 20, 30))


def _png_with_filters(rows: list[tuple[int, bytes]], width: int) -> bytes:
    """Her satırı verilen filtre türüyle kodlar (RGBA)."""
    raw = b"".join(bytes([f]) + data for f, data in rows)

    def chunk(t: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body))

    ihdr = struct.pack(">IIBBBBB", width, len(rows), 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_png_all_filter_types_decoded() -> None:
    # 2×5 RGBA; her satır farklı filtreyle, hepsi aynı piksellere çözülmeli.
    px = bytes([100, 50, 25, 255, 110, 60, 35, 255])
    sub = bytes([100, 50, 25, 255, 10, 10, 10, 0])  # Sub: 2. piksel - 1. piksel
    up = bytes(8)  # Up: önceki satırla aynı
    avg = bytes([100 - 50, 50 - 25, 25 - 12, 255 - 127, 110 - 105, 60 - 55, 35 - 30, 0])
    # Paeth (önceki satır aynı): tahmin b, fark 0.
    paeth = bytes(8)
    data = _png_with_filters([(0, px), (1, sub), (2, up), (3, avg), (4, paeth)], 2)
    img = decode_png(data)
    assert img.channels == 4
    for y in range(5):
        assert img.pixels[y * 8 : (y + 1) * 8] == px, f"satır {y}"


def test_png_rejects_garbage() -> None:
    with pytest.raises(PngError):
        decode_png(b"not a png")


def test_changed_pixels_counts_only_above_delta() -> None:
    a = decode_png(encode_png(4, 1, _solid(4, 1, (0, 0, 0))))
    b_rgb = bytearray(_solid(4, 1, (0, 0, 0)))
    b_rgb[0:3] = bytes([200, 0, 0])  # belirgin
    b_rgb[3:6] = bytes([10, 10, 10])  # eşiğin altında
    b = decode_png(encode_png(4, 1, bytes(b_rgb)))
    assert changed_pixels(a, a, 40) == 0
    assert changed_pixels(a, b, 40) == 1


def test_changed_pixels_size_mismatch_counts_all() -> None:
    a = decode_png(encode_png(2, 2, _solid(2, 2, (0, 0, 0))))
    b = decode_png(encode_png(3, 2, _solid(3, 2, (0, 0, 0))))
    assert changed_pixels(a, b, 40) == 6


# --- KLV-04: odak sırası ---------------------------------------------------------


def _dev(a: Rect, b: Rect, rtl: bool = False) -> bool:
    return is_deviation(a, b, rtl=rtl, x_tol=24, y_tol=8)


def test_order_normal_reading_is_not_deviation() -> None:
    assert not _dev(Rect(0, 0, 100, 20), Rect(120, 0, 100, 20))  # sağa, aynı satır
    assert not _dev(Rect(500, 0, 100, 20), Rect(0, 40, 100, 20))  # yeni satır (aşağı-sol)


def test_order_sidebar_to_main_column_is_not_deviation() -> None:
    # Kenar çubuğunun son bağlantısı → ana sütunun tepesi (yukarı-sağ, yeni sütun).
    assert not _dev(Rect(16, 400, 180, 24), Rect(260, 60, 300, 32))


def test_order_jump_back_up_is_deviation() -> None:
    # Altbilgiden başlığa (pozitif tabindex).
    assert _dev(Rect(16, 900, 60, 20), Rect(16, 20, 80, 20))


def test_order_backward_in_row_is_deviation() -> None:
    # Ters sıralanmış araç çubuğu: aynı satırda sola.
    assert _dev(Rect(300, 100, 80, 30), Rect(200, 100, 80, 30))


def test_order_rtl_mirrors_horizontal_direction() -> None:
    assert not _dev(Rect(300, 100, 80, 30), Rect(200, 100, 80, 30), rtl=True)
    assert _dev(Rect(200, 100, 80, 30), Rect(300, 100, 80, 30), rtl=True)


def test_order_deviations_skip_hidden_steps() -> None:
    rects = [Rect(0, 0, 50, 20), None, Rect(0, 0, 0, 0), Rect(100, 0, 50, 20), Rect(0, 0, 50, 20)]
    assert order_deviations(rects, rtl=False, x_tol=24, y_tol=8) == [4]


# --- KLV-05: tuzak tespiti -------------------------------------------------------


def _feed(seq: list[int | None], stuck: int = 3) -> tuple[str, list[int]]:
    det = TrapDetector(stuck)
    for x in seq:
        verdict, cycle = det.feed(x)
        if verdict != "continue":
            return verdict, cycle
    return "continue", []


def test_trap_completed_when_focus_leaves_document() -> None:
    assert _feed([1, 2, 3, None]) == ("completed", [])


def test_trap_empty_page() -> None:
    assert _feed([None]) == ("empty", [])


def test_trap_cycle_not_including_first_element() -> None:
    assert _feed([1, 2, 3, 4, 2]) == ("trap", [2, 3, 4])


def test_trap_stuck_focus() -> None:
    assert _feed([1, 2, 2, 2]) == ("trap", [2])
    assert _feed([1, 2, 2, 3, None]) == ("completed", [])


def test_trap_wrap_to_first_is_reported_as_wrapped() -> None:
    assert _feed([1, 2, 3, 1]) == ("wrapped", [1, 2, 3])


def test_trap_opaque_frame_repeats_are_not_stuck() -> None:
    det = TrapDetector(3)
    assert det.feed(1)[0] == "continue"
    for _ in range(5):
        assert det.feed(9, opaque=True)[0] == "continue"
    assert det.feed(None)[0] == "completed"


# --- KLV-07 / KLV-08: etkinleştirme politikası ----------------------------------


def _info(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tag": "button",
        "role": None,
        "type": "button",
        "native": True,
        "in_form": False,
        "submitish": False,
        "href_kind": None,
        "declared": False,
        "onclick": False,
        "pointer": False,
        "contenteditable": False,
    }
    base.update(kw)
    return base


def test_policy_never_activates_forms_submit_or_real_links() -> None:
    assert classify(_info(in_form=True)) is None
    assert classify(_info(submitish=True)) is None
    assert classify(_info(tag="a", href_kind="real")) is None
    assert classify(_info(tag="a", href_kind="anchor")) is None
    assert classify(_info(tag="input", type="text")) is None


def test_policy_order_declared_then_custom_then_native() -> None:
    assert classify(_info(declared=True)) == 0
    assert classify(_info(tag="div", native=False, role="button")) == 1
    assert classify(_info(tag="span", native=False, onclick=True)) == 1
    assert classify(_info()) == 2
    assert classify(_info(tag="a", href_kind="hash")) == 2


# --- PUA-03 / yapılandırma -------------------------------------------------------


def test_keyboard_trap_is_critical_and_cannot_be_lowered() -> None:
    assert KeyboardConfig().severity["keyboard_trap"] == "critical"
    sev = dict(KeyboardConfig().severity)
    sev["keyboard_trap"] = "important"
    with pytest.raises(ValidationError):
        KeyboardConfig(severity=sev)


def test_keyboard_config_requires_every_check() -> None:
    sev = dict(KeyboardConfig().severity)
    del sev["focus_order"]
    with pytest.raises(ValidationError):
        KeyboardConfig(severity=sev)


def test_keyboard_config_from_toml(tmp_path: Path) -> None:
    cfg_file = tmp_path / "yanki.toml"
    cfg_file.write_text("[keyboard]\nmax_steps = 50\nenabled = false\n", encoding="utf-8")
    cfg = load_config(cfg_file)
    assert cfg.keyboard.max_steps == 50
    assert cfg.keyboard.enabled is False


def test_every_check_has_rule_and_wcag() -> None:
    assert set(RULES) == set(KEYBOARD_CHECKS) == set(WCAG)
    assert all(r.startswith("KLV-") for r in RULES.values())
    assert all(WCAG.values())


# --- KLV-10: bulgu ve kanıt -----------------------------------------------------


def test_make_finding_review_flag_follows_threshold() -> None:
    cfg = KeyboardConfig()
    steps = [KeyStep(n=1, key="Tab", selector="#a")]
    sure = make_finding("keyboard_trap", "#a", cfg, steps=steps, screenshot="evidence/x.png")
    assert sure.rule == "KLV-05" and sure.wcag == ["2.1.2"] and sure.severity == "critical"
    assert sure.confidence == 1.0 and not sure.needs_review
    unsure = make_finding(
        "focus_not_visible", "#a", cfg, steps=steps, screenshot=None, heuristic=True
    )
    assert unsure.confidence == cfg.low_confidence and unsure.needs_review
    missing = make_finding("skip_link_missing", "", cfg, steps=steps, screenshot=None)
    assert missing.selector == "html" and missing.needs_review


def test_finding_steps_are_capped() -> None:
    cfg = KeyboardConfig(evidence_max_steps=3)
    steps = [KeyStep(n=i, key="Tab", selector=f"#e{i}") for i in range(1, 11)]
    f = make_finding("keyboard_trap", "#e1", cfg, steps=steps, screenshot=None)
    assert [s.n for s in f.evidence.steps or []] == [8, 9, 10]


def test_check_discriminates_findings_on_same_selector() -> None:
    cfg = KeyboardConfig()
    a = make_finding("modal_focus_escapes", "#d", cfg, steps=[], screenshot=None)
    b = make_finding("modal_focus_not_moved", "#d", cfg, steps=[], screenshot=None)
    ordered = finalize_findings([a, b])
    assert [f.evidence.check for f in ordered] == ["modal_focus_escapes", "modal_focus_not_moved"]


def _report(artifacts: dict[str, bytes]) -> ScanReport:
    now = datetime.now(UTC)
    return ScanReport(
        tool=ToolVersions(yanki="0", axe_core="0", playwright="0", browser="0"),
        page=PageInfo(requested="x", url="x", final_url="x"),
        started_at=now,
        finished_at=now,
        seed=0,
        config=YankiConfig().model_dump(mode="json"),
        elements=[],
        findings=[],
        rules_summary=RulesSummary(tags=[], violations=0, passes=0, incomplete=0, inapplicable=0),
        artifacts=artifacts,
    )


def test_artifacts_not_in_json_and_saved_by_content_hash(tmp_path: Path) -> None:
    store = ArtifactStore()
    png = encode_png(1, 1, b"\x00\x00\x00")
    name = store.add(png)
    assert name is not None and name.startswith("evidence/") and name.endswith(".png")
    assert store.add(png) == name  # aynı içerik, aynı ad (NFR-06)
    report = _report(store.files)
    assert "artifacts" not in report.model_dump_json()
    assert report.schema_version == SCHEMA_VERSION == "0.2.0"
    written = save_evidence(report, tmp_path)
    assert written == [tmp_path.resolve() / "evidence" / Path(name).name]
    assert written[0].read_bytes() == png


def test_save_evidence_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_evidence(_report({"../kacis.png": b"x"}), tmp_path)


def test_trap_composite_native_controls_use_larger_threshold() -> None:
    # Tarih alanının iç parçaları: activeElement aynı kalır, tuzak değildir.
    det = TrapDetector(3, composite_repeats=20)
    assert det.feed(1)[0] == "continue"
    for _ in range(6):
        assert det.feed(2, composite=True)[0] == "continue"
    assert det.feed(3)[0] == "continue"
    # Gerçekten hiç çıkılamıyorsa yine tuzaktır.
    det = TrapDetector(3, composite_repeats=5)
    det.feed(1)
    verdicts = [det.feed(2, composite=True)[0] for _ in range(5)]
    assert verdicts[-1] == "trap"


def test_policy_skips_arrow_key_widgets() -> None:
    for role in ("slider", "combobox", "listbox", "grid", "tree", "spinbutton"):
        assert classify(_info(tag="div", native=False, role=role, pointer=True)) is None
