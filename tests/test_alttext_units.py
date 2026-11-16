"""Alt metin modülü birim testleri; tarayıcı ve ML paketi gerektirmez.

ALT-01, ALT-02, ALT-03, ALT-06, ALT-07, ALT-08, ALT-09, ALT-10, ALT-11, ALT-12,
NFR-06, NFR-13, NFR-16. Eşik ve güven mantığı verilmiş benzerlik değerleriyle
sınanır; sahte arka uç yalnızca hattın tesisatını sınar, doğruluğu değil.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from pydantic import ValidationError

from yanki.config import AltTextConfig, YankiConfig
from yanki.core.alttext import CollectedImages, analyze, plan_images, rules
from yanki.core.alttext.backend import AltTextBackend, BackendUnavailable, ImageInput, load_backend
from yanki.core.alttext.backends.fake import FakeBackend
from yanki.core.alttext.cache import AnalysisCache, key
from yanki.core.alttext.classify import confidence_for, judge, label_for, rescale
from yanki.core.alttext.images import Gathered, decode_data_uri, sniff
from yanki.core.schema import BoundingBox, PageElement

CFG = AltTextConfig()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _img(selector: str = "#a", **kw: object) -> PageElement:
    base: dict[str, object] = {
        "kind": "image",
        "selector": selector,
        "tag": "img",
        "visible": True,
        "bbox": BoundingBox(x=0, y=0, width=200, height=150),
        "src": f"https://ornek.test/{selector.strip('#')}.jpg",
        "natural_width": 200,
    }
    base.update(kw)
    return PageElement.model_validate(base)


def _input(data: bytes = PNG) -> ImageInput:
    return ImageInput(sha256=hashlib.sha256(data).hexdigest(), data=data, mime="image/png")


def _fake_config(tmp_path: object, **alt: object) -> YankiConfig:
    return YankiConfig.model_validate(
        {"alt_text": {"backend": "fake", "cache_dir": str(tmp_path), **alt}}
    )


# --- ALT-02: dekoratif ---------------------------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [{"alt": ""}, {"alt": "  "}, {"role": "presentation"}, {"role": "none"}, {"aria_hidden": True}],
)
def test_decorative(kw: dict[str, object]) -> None:
    assert rules.is_decorative(_img(**kw))


@pytest.mark.parametrize("kw", [{"alt": "Köpek"}, {}, {"alt": "", "label": "Logo adı"}])
def test_not_decorative(kw: dict[str, object]) -> None:
    assert not rules.is_decorative(_img(**kw))


def test_text_alternative_prefers_alt_then_label() -> None:
    assert rules.text_alternative(_img(alt=" Bir  köpek ")) == "Bir köpek"
    assert rules.text_alternative(_img(label="aria adı")) == "aria adı"
    assert rules.text_alternative(_img()) is None


# --- ALT-07: genel ifade ve dosya adı ---------------------------------------------------


@pytest.mark.parametrize(
    ("alt", "src", "reason"),
    [
        ("image1.png", None, "filename"),
        ("IMG_2024.JPG", None, "filename"),
        ("DSC_0012", None, "camera"),
        ("resim", None, "generic_word"),
        ("Foto", None, "generic_word"),
        ("görsel 3", None, "generic_word"),
        ("GÖRSEL", None, "generic_word"),
        ("12345", None, "numeric"),
        ("lighthouse", "https://x.test/img/lighthouse.jpg", "src_name"),
        ("deniz-feneri", "https://x.test/deniz_feneri.png", "src_name"),
    ],
)
def test_generic_alt(alt: str, src: str | None, reason: str) -> None:
    assert rules.is_generic(alt, src, CFG) == reason


@pytest.mark.parametrize(
    "alt",
    ["Hayvanlar", "Bina", "Uyarı", "Yankı Belediyesi ana sayfa", "Fotoğraf sergisinin girişi"],
)
def test_not_generic(alt: str) -> None:
    assert rules.is_generic(alt, "https://x.test/a.jpg", CFG) is None


def test_generic_words_are_configurable() -> None:
    cfg = AltTextConfig(generic_words=["manzara"])
    assert rules.is_generic("Manzara", None, cfg) == "generic_word"
    assert rules.is_generic("resim", None, cfg) is None


# --- ALT-11 ve ALT-12 -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["Tıklayın", "buraya", "Devamı", "Daha fazla", "Read more"])
def test_vague_names(name: str) -> None:
    assert rules.is_vague_name(name, CFG)


@pytest.mark.parametrize("name", ["Etkinlik takvimini inceleyin", "Ana sayfa", ""])
def test_descriptive_names(name: str) -> None:
    assert not rules.is_vague_name(name, CFG)


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("A black dog lying on the grass next to a yellow ball", "en"),
        ("Çimenlerin üzerinde uzanmış sarı tüylü bir köpek", "tr"),
        ("Mavi gökyüzü önünde yükselen beyaz taş deniz feneri", "tr"),
        ("lighthouse", None),
        ("Mustafa Kemal", None),
    ],
)
def test_detect_language(text: str, lang: str | None) -> None:
    assert rules.detect_language(text, CFG) == lang


def test_primary_lang() -> None:
    assert rules.primary_lang("tr-TR") == "tr"
    assert rules.primary_lang("EN_us") == "en"
    assert rules.primary_lang(None) is None


# --- ALT-03, ALT-06, ALT-08: skor, sınıf, güven ------------------------------------------


def test_rescale_clips_to_unit_interval() -> None:
    assert rescale(0.5, (0.7, 0.9)) == 0.0
    assert rescale(0.95, (0.7, 0.9)) == 1.0
    assert rescale(0.8, (0.7, 0.9)) == pytest.approx(0.5)


def test_labels_follow_thresholds() -> None:
    assert label_for(CFG.ok_threshold, CFG) == "uygun"
    assert label_for(CFG.ok_threshold - 1e-6, CFG) == "yetersiz"
    assert label_for(CFG.mismatch_threshold, CFG) == "yetersiz"
    assert label_for(CFG.mismatch_threshold - 1e-6, CFG) == "uyumsuz"


def test_confidence_grows_with_distance_from_threshold() -> None:
    assert confidence_for(CFG.ok_threshold, CFG) == 0.5
    assert confidence_for(0.0, CFG) == 1.0
    near = confidence_for(CFG.mismatch_threshold - 0.02, CFG)
    far = confidence_for(CFG.mismatch_threshold - 0.15, CFG)
    assert 0.5 < near < far <= 1.0


def test_judge_clear_mismatch_is_definite() -> None:
    v = judge(e5_raw=0.70, clip_raw=0.15, cfg=CFG)
    assert (v.label, v.score, v.needs_review) == ("uyumsuz", 0.0, False)
    assert v.confidence == 1.0


def test_judge_near_threshold_needs_review() -> None:
    # e5 ve clip, birleşik skor mismatch eşiğinin hemen üstünde olacak biçimde seçilir.
    e5_raw = CFG.e5_range[0] + 0.31 * (CFG.e5_range[1] - CFG.e5_range[0])
    clip_raw = CFG.clip_range[0] + 0.31 * (CFG.clip_range[1] - CFG.clip_range[0])
    v = judge(e5_raw, clip_raw, CFG)
    assert v.label == "yetersiz"
    assert v.needs_review and v.confidence < CFG.review_threshold


def test_functional_image_and_text_caption_are_capped_to_review() -> None:
    assert judge(0.70, 0.15, CFG, functional=True).review_reason == "functional_image"
    v = judge(0.70, 0.15, CFG, caption="a magazine cover with a portrait")
    assert v.needs_review and v.review_reason == "caption:cover"
    cfg = AltTextConfig(functional_image_review=False)
    assert not judge(0.70, 0.15, cfg, functional=True).needs_review


def test_weights_change_score() -> None:
    only_clip = AltTextConfig(e5_weight=0, clip_weight=1)
    assert judge(0.90, 0.15, only_clip).score == 0.0
    assert judge(0.90, 0.15, CFG).score == pytest.approx(0.7)


# --- yapılandırma (NFR-13) -------------------------------------------------------------


def test_config_rejects_bad_thresholds_and_ranges() -> None:
    with pytest.raises(ValidationError):
        AltTextConfig(ok_threshold=0.2, mismatch_threshold=0.5)
    with pytest.raises(ValidationError):
        AltTextConfig(e5_range=(0.9, 0.7))
    with pytest.raises(ValidationError):
        AltTextConfig(e5_weight=0, clip_weight=0)
    with pytest.raises(ValidationError):
        AltTextConfig(severity={"missing": "critical"})


def test_models_are_pinned_and_swappable_by_config() -> None:
    cfg = YankiConfig.model_validate(
        {
            "alt_text": {
                "models": {
                    "translator": {"id": "Helsinki-NLP/opus-mt-tc-big-tr-en", "revision": "abc"}
                }
            }
        }
    )
    models = cfg.alt_text.models
    assert models.translator.ref() == "Helsinki-NLP/opus-mt-tc-big-tr-en@abc"
    for ref in (models.captioner, models.embedder, models.clip, models.clip_multilingual):
        assert len(ref.revision) == 40  # commit SHA (NFR-06)


# --- görsel baytları -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (PNG, "image/png"),
        (b"GIF89a....", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"  <svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"),
        (b'<?xml version="1.0"?><svg></svg>', "image/svg+xml"),
        (b"Bu bir JPEG degil", None),
        (b"<html><body>404</body></html>", None),
    ],
)
def test_sniff_reads_format_from_bytes(data: bytes, mime: str | None) -> None:
    assert sniff(data) == mime


def test_decode_data_uri() -> None:
    payload = base64.b64encode(PNG).decode()
    assert decode_data_uri(f"data:image/png;base64,{payload}") == PNG
    assert decode_data_uri("data:image/svg+xml,%3Csvg%3E%3C/svg%3E") == b"<svg></svg>"
    assert decode_data_uri("data:bozuk") is None


# --- arka uç sözleşmesi ----------------------------------------------------------------


def test_fake_backend_implements_contract_deterministically() -> None:
    backend = FakeBackend()
    assert isinstance(backend, AltTextBackend)
    img = _input()
    assert backend.caption([img]) == backend.caption([img])
    sims = backend.text_similarity([("a", "b"), ("a", "b")])
    assert sims[0] == sims[1] and -1 <= sims[0] <= 1
    assert backend.image_text_similarity([(img, "x")]) == backend.image_text_similarity(
        [(img, "x")]
    )
    with pytest.raises(ValueError, match="çözülemedi"):
        backend.caption([_input(b"not an image")])
    assert backend.thumbnail_png(img, 64) is None  # sayfa baytları kanıta girmez


def test_load_backend_fake_and_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_backend(AltTextConfig(backend="fake"), device="cpu", seed=0).name == "fake"
    import builtins

    real_import = builtins.__import__

    def no_ml(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("yanki.core.alttext.backends.transformers"):
            raise ImportError("torch yok")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_ml)
    with pytest.raises(BackendUnavailable, match="ML paketleri"):
        load_backend(AltTextConfig(), device="cpu", seed=0)


# --- önbellek (ALT-09) -------------------------------------------------------------------


def test_cache_persists_on_disk(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path))
    c1 = AnalysisCache(path)
    c1.put(key("caption", "m@1", "abc"), '"a dog"')
    c1.close()
    c2 = AnalysisCache(path)
    assert c2.get(key("caption", "m@1", "abc")) == '"a dog"'
    assert c2.get(key("caption", "m@2", "abc")) is None  # model revizyonu anahtarda
    assert c2.hits == 1
    c2.close()


# --- plan --------------------------------------------------------------------------


def test_plan_routes_each_image() -> None:
    els = [
        _img("#deco", alt=""),
        _img("#hidden", visible=False),
        _img("#missing"),
        _img("#generic", alt="resim"),
        _img("#tiny", alt="Uyarı simgesi", bbox=BoundingBox(x=0, y=0, width=16, height=16)),
        _img("#ok", alt="Kumsalda koşan köpek"),
        _img("#en", alt="A dog running on the beach", lang="en"),
        _img("#de", alt="Ein Hund", lang="de"),
        PageElement(kind="link", selector="#l", tag="a", visible=True),
    ]
    plans = {p.el.selector: p for p in plan_images(els, CFG, "tr")}
    assert set(plans) == {e.selector for e in els if e.kind == "image"}
    assert plans["#deco"].status == "decorative"
    assert (plans["#hidden"].status, plans["#hidden"].reason) == ("skipped", "hidden")
    assert (plans["#missing"].status, plans["#missing"].rule_check) == ("rule_only", "missing")
    assert plans["#generic"].rule_check == "generic_text"
    assert (plans["#tiny"].status, plans["#tiny"].reason) == ("skipped", "too_small")
    assert plans["#ok"].status == "model" and plans["#ok"].translate_from == "tr"
    assert plans["#en"].status == "model" and plans["#en"].translate_from is None
    assert plans["#de"].reason == "unsupported_language:de"
    multi = plan_images(els, AltTextConfig(approach="multilingual"), "tr")
    assert {p.el.selector: p for p in multi}["#de"].status == "model"


def test_broken_image_is_not_too_small() -> None:
    """ALT-10: yüklenemeyen <img> satır içi küçük çizilir; "atlandı" değil "analiz edilemedi"."""
    small = BoundingBox(x=0, y=0, width=12, height=12)
    broken = _img("#broken", alt="Kent müzesinin girişi", natural_width=0, bbox=small)
    icon = _img("#icon", alt="Kent müzesinin girişi", natural_width=12, bbox=small)
    plans = {p.el.selector: p for p in plan_images([broken, icon], CFG, "tr")}
    assert plans["#broken"].status == "model"
    assert (plans["#icon"].status, plans["#icon"].reason) == ("skipped", "too_small")


def test_plan_respects_max_images() -> None:
    els = [_img(f"#i{i}", alt=f"Kumsalda koşan köpek {i}") for i in range(5)]
    plans = plan_images(els, AltTextConfig(max_images=2), "tr")
    assert [p.status for p in plans] == ["model", "model", "skipped", "skipped", "skipped"]
    assert plans[-1].reason == "limit"


# --- analiz (sahte arka uç: yalnızca tesisat) -------------------------------------------


def _collected(*selectors: str) -> CollectedImages:
    return CollectedImages(
        inputs={s: Gathered(image=_input(), source="response") for s in selectors}
    )


def test_analyze_records_every_image_and_rule_findings(tmp_path: object) -> None:
    els = [
        _img("#deco", alt=""),
        _img("#missing"),
        _img("#generic", alt="DSC_0012"),
        _img("#ok", alt="Kumsalda koşan köpek"),
        _img("#broken", alt="Kütüphane salonu"),
        PageElement(kind="link", selector="#v", tag="a", visible=True, accessible_name="Tıklayın"),
    ]
    collected = _collected("#ok")
    collected.inputs["#broken"] = Gathered(reason="request_failed: net::ERR_FILE_NOT_FOUND")
    result = analyze(els, collected, _fake_config(tmp_path), page_lang="tr")
    status = {r.selector: r.status for r in result.report.images}
    assert status == {
        "#deco": "decorative",
        "#missing": "rule_only",
        "#generic": "rule_only",
        "#ok": "analyzed",
        "#broken": "failed",
    }
    by_rule = {(f.rule, f.evidence.check, f.selector): f for f in result.findings}
    missing = by_rule[("ALT-01", "missing", "#missing")]
    assert (missing.severity, missing.confidence, missing.needs_review) == ("critical", 1.0, False)
    assert missing.evidence.method == "rule" and missing.wcag == ["1.1.1"]
    assert by_rule[("ALT-07", "generic_text", "#generic")].evidence.detail == "camera"
    vague = by_rule[("ALT-11", "vague_name", "#v")]
    assert vague.needs_review and vague.wcag == ["2.4.4"]
    ok = next(r for r in result.report.images if r.selector == "#ok")
    assert ok.alt == "Kumsalda koşan köpek"
    assert ok.alt_translated == "[tr->en] Kumsalda koşan köpek"  # ALT-04: ikisi birlikte
    assert ok.caption and ok.similarity is not None and ok.label in ("uygun", "yetersiz", "uyumsuz")
    assert result.report.audited_images == 4 and result.report.audited_controls == 1
    assert result.models["captioner"] == "fake"


def test_analyze_model_findings_carry_evidence(tmp_path: object) -> None:
    els = [_img(f"#i{i}", alt=f"Kumsalda koşan köpek {i}") for i in range(12)]
    result = analyze(
        els, _collected(*(e.selector for e in els)), _fake_config(tmp_path), page_lang="tr"
    )
    model = [f for f in result.findings if f.evidence.method == "model"]
    assert model, "sahte arka uç dağınık skorlar üretir; en az bir model bulgusu beklenir"
    for f in model:
        assert f.rule == "ALT-06" and f.evidence.check in ("insufficient", "mismatch")
        assert f.evidence.alt and f.evidence.alt_translated and f.evidence.caption
        assert 0 <= f.evidence.similarity <= 1  # type: ignore[operator]
        assert f.needs_review == (f.confidence < 0.7 or f.evidence.detail is not None)


def test_analyze_is_deterministic_and_cached(tmp_path: object) -> None:
    """NFR-06 ve ALT-09: aynı girdi aynı bulgular; ikinci analiz önbellekten gelir."""
    els = [_img("#a", alt="Kumsalda koşan köpek"), _img("#b", alt="Kırmızı araba")]
    cfg = _fake_config(tmp_path)
    first = analyze(els, _collected("#a", "#b"), cfg, page_lang="tr")
    second = analyze(els, _collected("#a", "#b"), cfg, page_lang="tr")
    dump = lambda r: [f.model_dump() for f in r.findings]  # noqa: E731
    assert dump(first) == dump(second)
    assert [r.cached for r in first.report.images] == [False, False]
    assert [r.cached for r in second.report.images] == [True, True]
    assert second.report.cache_hits == 2


def test_same_image_different_alt_is_rescored(tmp_path: object) -> None:
    """Açıklama görsel karmasıyla önbelleklenir; benzerlik alt metne göre yeniden hesaplanır."""
    cfg = _fake_config(tmp_path)
    a = analyze([_img("#a", alt="Köpek")], _collected("#a"), cfg, page_lang="tr")
    b = analyze([_img("#a", alt="Araba")], _collected("#a"), cfg, page_lang="tr")
    ra, rb = a.report.images[0], b.report.images[0]
    assert ra.caption == rb.caption and ra.sha256 == rb.sha256
    assert not rb.cached  # metne bağlı skorlar önbellekte yoktu


def test_analyze_without_models_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """NFR-16: arka uç yoksa kural denetimleri çalışır, model görselleri 'analiz edilemedi'."""

    def unavailable(*args: object, **kwargs: object) -> object:
        raise BackendUnavailable("ağırlık yok")

    monkeypatch.setattr("yanki.core.alttext.load_backend", unavailable)
    cfg = YankiConfig.model_validate({"alt_text": {"cache_dir": str(tmp_path)}})
    els = [_img("#missing"), _img("#ok", alt="Kumsalda koşan köpek")]
    result = analyze(els, _collected("#ok"), cfg, page_lang="tr")
    assert {r.selector: (r.status, r.reason) for r in result.report.images} == {
        "#missing": ("rule_only", None),
        "#ok": ("failed", "models_unavailable"),
    }
    assert [f.rule for f in result.findings] == ["ALT-01"]
    assert len([w for w in result.warnings if "kullanılamıyor" in w]) == 1
    assert result.models == {}


def test_undecodable_image_is_failed_not_fatal(tmp_path: object) -> None:
    """ALT-10: çözülemeyen görsel atlanır, diğerleri analiz edilir."""
    els = [_img("#bad", alt="Kumsal"), _img("#good", alt="Kumsalda koşan köpek")]
    collected = _collected("#good")
    collected.inputs["#bad"] = Gathered(image=_input(b"GARBAGE"), source="response")
    result = analyze(els, collected, _fake_config(tmp_path), page_lang="tr")
    status = {r.selector: r.status for r in result.report.images}
    assert status == {"#bad": "failed", "#good": "analyzed"}
    assert result.report.images[0].reason.startswith("undecodable")  # type: ignore[union-attr]


def test_lang_mismatch_is_review_only(tmp_path: object) -> None:
    els = [
        _img("#en", alt="A black dog lying on the grass next to a yellow ball"),
        _img("#en-ok", alt="A black dog lying on the grass next to a yellow ball", lang="en"),
    ]
    result = analyze(els, _collected("#en", "#en-ok"), _fake_config(tmp_path), page_lang="tr")
    lang = [f for f in result.findings if f.rule == "ALT-12"]
    assert [f.selector for f in lang] == ["#en"]
    assert lang[0].needs_review and lang[0].wcag == ["3.1.2"]
    assert (lang[0].evidence.lang, lang[0].evidence.detail) == ("en", "tr")
