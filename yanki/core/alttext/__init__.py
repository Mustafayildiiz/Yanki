"""Alt metin ve içerik analizi (ALT-01..12, NFR-01, NFR-12, NFR-13, NFR-16).

İki aşama:

1. ``collect_images`` (async, sayfa canlıyken; klavye testinden önce): her
   görsel için plan çıkarılır, model analizine girecek görsellerin baytları
   sayfanın kendi yanıtlarından toplanır, kural bulguları için öğe ekran
   görüntüsü alınır. Yeni ağ isteği yapılmaz (NFR-08).
2. ``analyze`` (eşzamanlı, tarayıcı kapandıktan sonra ``asyncio.to_thread``
   ile): kural denetimleri ve model analizi; bulgular ve ``AltTextReport``.

Görsel başına plan:

- dekoratif (ALT-02) → kayıt, bulgu yok;
- görünmez → atlandı;
- metin alternatifi yok → "eksik" (ALT-01, kural);
- dosya adı ya da genel ifade → ALT-07 (kural);
- çok küçük, sınır aşıldı ya da desteklenmeyen dil → atlandı;
- kalanlar → model analizi (ALT-03..06, ALT-08).

ALT-11 (belirsiz bağlantı/düğme adı) ve ALT-12 (alt metin dili) kural tabanlı
ve her zaman "elle incele"dir. Hiçbir hata taramayı durdurmaz (NFR-16).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from yanki.config import AltTextConfig, YankiConfig
from yanki.core.alttext import rules
from yanki.core.alttext.backend import (
    AltTextBackend,
    BackendUnavailable,
    ImageInput,
    load_backend,
)
from yanki.core.alttext.cache import AnalysisCache, default_cache_dir, key
from yanki.core.alttext.classify import judge
from yanki.core.evidence import ArtifactStore
from yanki.core.schema import AltTextReport, Evidence, Finding, ImageAnalysis, PageElement

__all__ = [
    "AltTextResult",
    "CollectedImages",
    "ImagePlan",
    "analyze",
    "collect_images",
    "download_models",
    "plan_images",
]

RULES = {
    "missing": "ALT-01",
    "generic_text": "ALT-07",
    "insufficient": "ALT-06",
    "mismatch": "ALT-06",
    "vague_name": "ALT-11",
    "lang_mismatch": "ALT-12",
}

RECOMMENDATIONS = {
    "missing": (
        "Görsele içeriğini ya da işlevini anlatan bir alt metin ekleyin. Görsel yalnızca "
        'süs amaçlıysa alt="" ile dekoratif olarak işaretleyin.'
    ),
    "generic_text": (
        "Alt metin dosya adı ya da genel bir ifade. Görselin içeriğini ve sayfadaki "
        "işlevini anlatacak şekilde yeniden yazın."
    ),
    "insufficient": (
        "Alt metin görselle ilgili ama içeriğini aktarmayacak kadar genel. Görseldeki "
        "önemli ayrıntıları ve sayfadaki amacını ekleyin."
    ),
    "mismatch": (
        "Alt metin görselin içeriğiyle uyuşmuyor. Görselin gerçekte neyi gösterdiğini "
        "ya da sayfadaki işlevini anlatacak şekilde yeniden yazın."
    ),
    "vague_name": (
        "Bağlantı ya da düğmenin adı tek başına işlevini anlatmıyor. Adı, gideceği yeri "
        "ya da yapacağı işi belirtecek şekilde değiştirin (ör. 'Bahar indirimlerini görün')."
    ),
    "lang_mismatch": (
        "Alt metin sayfa dilinden farklı bir dilde görünüyor. Metni sayfa diline çevirin "
        "ya da öğeyi doğru lang özniteliğiyle işaretleyin."
    ),
}


def _wcag(check: str, el: PageElement) -> list[str]:
    if check == "vague_name":
        return ["2.4.4"] if el.kind == "link" else ["2.4.6"]
    if check == "lang_mismatch":
        return ["3.1.2"]
    return ["1.1.1"]


# --- Plan -------------------------------------------------------------------------


@dataclass
class ImagePlan:
    el: PageElement
    status: str
    """``decorative``, ``skipped``, ``rule_only`` ya da ``model``."""
    reason: str | None = None
    alt: str | None = None
    rule_check: str | None = None
    detected_lang: str | None = None
    element_lang: str = "tr"
    translate_from: str | None = None
    """Çeviri kaynağı; ``None``: çeviri yapılmaz."""

    @property
    def needs_bytes(self) -> bool:
        return self.status == "model"

    @property
    def needs_shot(self) -> bool:
        return self.status == "rule_only" and self.el.visible


def plan_images(
    elements: list[PageElement], cfg: AltTextConfig, page_lang: str | None
) -> list[ImagePlan]:
    """Her görsel için yapılacak analizi belirler (belirlenimci, model gerektirmez)."""
    plans: list[ImagePlan] = []
    page_primary = rules.primary_lang(page_lang) or cfg.default_lang
    model_count = 0
    for el in elements:
        if el.kind != "image":
            continue
        element_lang = rules.primary_lang(el.lang) or page_primary
        plan = ImagePlan(el=el, status="model", element_lang=element_lang)
        plans.append(plan)
        if rules.is_decorative(el):
            plan.status = "decorative"
            continue
        if not el.visible:
            plan.status, plan.reason = "skipped", "hidden"
            continue
        plan.alt = rules.text_alternative(el)
        if plan.alt is None:
            plan.status, plan.rule_check = "rule_only", "missing"
            continue
        generic = rules.is_generic(plan.alt, el.src, cfg)
        plan.detected_lang = rules.detect_language(plan.alt, cfg)
        if generic:
            plan.status, plan.rule_check, plan.reason = "rule_only", "generic_text", generic
            continue
        box = el.bbox
        # Yüklenemeyen <img> alt metniyle satır içi çizilir ve küçük görünür; boyutu
        # anlamsızdır. "Çok küçük" sayılmaz, bayt toplamada "analiz edilemedi" olur (ALT-10).
        broken = el.tag == "img" and el.natural_width == 0
        if not broken and (box is None or min(box.width, box.height) < cfg.min_size_px):
            plan.status, plan.reason = "skipped", "too_small"
            continue
        source = plan.detected_lang or element_lang
        if cfg.approach == "translate" and source != "en":
            if source not in cfg.translate_languages:
                plan.status, plan.reason = "skipped", f"unsupported_language:{source}"
                continue
            plan.translate_from = source
        if model_count >= cfg.max_images:
            plan.status, plan.reason = "skipped", "limit"
            continue
        model_count += 1
    return plans


# --- Aşama 1: tarayıcı ---------------------------------------------------------------


@dataclass
class CollectedImages:
    """Tarayıcı aşamasının çıktısı: model girdileri ve kural bulgusu ekran görüntüleri."""

    inputs: dict[str, Any] = field(default_factory=dict)
    """Seçici → ``images.Gathered``."""
    shots: dict[str, bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


async def collect_images(
    page: Any,
    elements: list[PageElement],
    capture: Any,
    cfg: AltTextConfig,
    page_lang: str | None,
) -> CollectedImages:
    """Model analizine girecek görsellerin baytlarını toplar (NFR-08: yeni istek yok)."""
    from yanki.core.alttext.images import Gathered, gather, screenshot

    out = CollectedImages()
    for plan in plan_images(elements, cfg, page_lang):
        sel = plan.el.selector
        try:
            if plan.needs_bytes:
                out.inputs[sel] = await gather(page, plan.el, capture, cfg)
            elif plan.needs_shot:
                png = await screenshot(page, sel, cfg)
                if png:
                    out.shots[sel] = png
        except Exception as exc:  # NFR-16: tek görselin hatası taramayı durdurmaz
            out.inputs[sel] = Gathered(reason=f"collect_error: {type(exc).__name__}")
            out.warnings.append(f"Alt metin: {sel} görseli toplanamadı ({type(exc).__name__}).")
    return out


# --- Aşama 2: analiz ----------------------------------------------------------------


@dataclass
class AltTextResult:
    report: AltTextReport
    findings: list[Finding] = field(default_factory=list)
    artifacts: dict[str, bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)
    libraries: dict[str, str] = field(default_factory=dict)


def _finding(
    check: str,
    el: PageElement,
    cfg: AltTextConfig,
    *,
    confidence: float,
    needs_review: bool,
    evidence: Evidence,
) -> Finding:
    return Finding(
        id="f-0000",
        type="alt_text",
        rule=RULES[check],
        severity=cfg.severity[check],  # type: ignore[index]
        wcag=_wcag(check, el),
        selector=el.selector,
        evidence=evidence,
        confidence=confidence,
        needs_review=needs_review,
        recommendation=RECOMMENDATIONS[check],
    )


def _rule_finding(
    check: str, el: PageElement, cfg: AltTextConfig, *, always_review: bool = False, **ev: Any
) -> Finding:
    confidence = cfg.rule_confidence[check]  # type: ignore[index]
    return _finding(
        check,
        el,
        cfg,
        confidence=confidence,
        needs_review=always_review or confidence < cfg.review_threshold,
        evidence=Evidence(check=check, method="rule", **ev),
    )


class _Analyzer:
    """Bir sayfanın model analizi; önbellek ve arka uç tembel açılır."""

    def __init__(self, config: YankiConfig, backend: AltTextBackend | None) -> None:
        self.cfg = config.alt_text
        self.config = config
        self._backend = backend
        self._backend_error: str | None = None
        self._cache: AnalysisCache | None = None
        self.warnings: list[str] = []

    @property
    def backend(self) -> AltTextBackend | None:
        if self._backend is None and self._backend_error is None:
            try:
                self._backend = load_backend(
                    self.cfg, device=self.config.device, seed=self.config.seed
                )
                self.warnings.extend(getattr(self._backend, "warnings", []))
            except BackendUnavailable as exc:
                self._backend_error = str(exc)
                self.warnings.append(
                    f"Alt metin modelleri kullanılamıyor; model analizi yapılmadı: {exc}"
                )
        return self._backend

    @property
    def cache(self) -> AnalysisCache:
        if self._cache is None:
            path = default_cache_dir(self.cfg.cache_dir) if self.cfg.cache else None
            try:
                self._cache = AnalysisCache(path)
            except OSError as exc:
                self.warnings.append(f"Alt metin önbelleği açılamadı ({exc}); bellekte tutuluyor.")
                self._cache = AnalysisCache(None)
        return self._cache

    def _cached(self, k: str, compute: Any) -> tuple[Any, bool]:
        hit = self.cache.get(k)
        if hit is not None:
            return json.loads(hit), True
        value = compute()
        self.cache.put(k, json.dumps(value))
        return value, False

    def run(self, plan: ImagePlan, image: ImageInput) -> tuple[dict[str, Any], bool]:
        """Bir görselin model analizi. Döner: (sonuçlar, tamamen önbellekten mi)."""
        backend = self.backend
        if backend is None:  # çağıran önce denetler
            raise BackendUnavailable(self._backend_error or "arka uç yok")
        refs = backend.versions()
        alt = plan.alt or ""
        caption, c1 = self._cached(
            key("caption", refs.get("captioner", ""), image.sha256),
            lambda: backend.caption([image])[0],
        )
        alt_en: str | None = None
        c2 = True
        if plan.translate_from:
            src = plan.translate_from
            alt_en, c2 = self._cached(
                key("translate", refs.get("translator", ""), src, alt),
                lambda: backend.translate([alt], src)[0],
            )
        multilingual = self.cfg.approach == "multilingual"
        text = alt if multilingual or alt_en is None else alt_en
        e5_raw, c3 = self._cached(
            key("e5", refs.get("embedder", ""), self.cfg.e5_prefix, caption, text),
            lambda: backend.text_similarity([(caption, text)])[0],
        )
        clip_role = "clip_multilingual" if multilingual else "clip"
        clip_raw, c4 = self._cached(
            key("clip", refs.get("clip", ""), refs.get(clip_role, ""), image.sha256, text),
            lambda: backend.image_text_similarity([(image, text)], multilingual=multilingual)[0],
        )
        values = {"caption": caption, "alt_en": alt_en, "e5": e5_raw, "clip": clip_raw}
        return values, c1 and c2 and c3 and c4

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()


def analyze(
    elements: list[PageElement],
    collected: CollectedImages,
    config: YankiConfig,
    *,
    page_lang: str | None,
    backend: AltTextBackend | None = None,
) -> AltTextResult:
    """Kural denetimleri ve model analizi (eşzamanlı; CPU yoğun)."""
    cfg = config.alt_text
    analyzer = _Analyzer(config, backend)
    artifacts = ArtifactStore()
    findings: list[Finding] = []
    records: list[ImageAnalysis] = []
    warnings = list(collected.warnings)
    started = time.perf_counter()
    model_seconds = 0.0
    model_count = 0
    load_seconds: float | None = None
    plans = plan_images(elements, cfg, page_lang)
    controls: list[PageElement] = []

    try:
        # Model analizine girecek ve baytı toplanmış görsel varsa modeller önceden
        # yüklenir; görsel başına süre (NFR-01) yüklemeyi içermesin.
        if any(
            p.needs_bytes and getattr(collected.inputs.get(p.el.selector), "image", None)
            for p in plans
        ):
            t0 = time.perf_counter()
            backend_obj = analyzer.backend
            if backend_obj is not None:
                try:
                    backend_obj.load()
                    load_seconds = round(time.perf_counter() - t0, 3)
                except Exception as exc:  # NFR-16
                    warnings.append(f"Alt metin modelleri yüklenemedi: {exc}")
                    analyzer._backend, analyzer._backend_error = None, str(exc)

        for plan in plans:
            el = plan.el
            rec = ImageAnalysis(
                selector=el.selector, src=_short_src(el.src), status="analyzed", alt=plan.alt
            )
            records.append(rec)

            # ALT-12: dil uyuşmazlığı (model analiziyle birlikte olabilir).
            if (
                plan.status in ("model", "rule_only", "skipped")
                and plan.detected_lang
                and plan.detected_lang != plan.element_lang
            ):
                findings.append(
                    _rule_finding(
                        "lang_mismatch",
                        el,
                        cfg,
                        always_review=True,
                        alt=plan.alt,
                        lang=plan.detected_lang,
                        detail=plan.element_lang,
                    )
                )

            if plan.status == "decorative":
                rec.status = "decorative"
                continue
            if plan.status == "skipped":
                rec.status, rec.reason = "skipped", plan.reason
                continue
            if plan.status == "rule_only":
                rec.status, rec.reason = "rule_only", plan.reason
                check = plan.rule_check or "missing"
                rec.label = "eksik" if check == "missing" else "yetersiz"
                rec.confidence = cfg.rule_confidence[check]  # type: ignore[index]
                findings.append(
                    _rule_finding(
                        check,
                        el,
                        cfg,
                        alt=plan.alt,
                        label=rec.label,
                        detail=plan.reason,
                        screenshot=artifacts.add(collected.shots.get(el.selector)),
                    )
                )
                continue

            # Model analizi.
            gathered = collected.inputs.get(el.selector)
            image: ImageInput | None = getattr(gathered, "image", None)
            if image is None:
                rec.status = "failed"
                rec.reason = getattr(gathered, "reason", None) or "not_collected"
                continue
            rec.sha256, rec.source = image.sha256, getattr(gathered, "source", None)
            if analyzer.backend is None:
                rec.status, rec.reason = "failed", "models_unavailable"
                continue
            t0 = time.perf_counter()
            try:
                values, cached = analyzer.run(plan, image)
            except ValueError as exc:
                rec.status, rec.reason = "failed", f"undecodable: {exc}"
                continue
            except Exception as exc:  # NFR-16
                rec.status, rec.reason = "failed", f"analysis_error: {type(exc).__name__}"
                warnings.append(f"Alt metin: {el.selector} analiz edilemedi ({exc}).")
                continue
            if not cached:
                model_seconds += time.perf_counter() - t0
                model_count += 1
            verdict = judge(
                values["e5"],
                values["clip"],
                cfg,
                functional=el.in_control,
                caption=values["caption"],
            )
            rec.cached = cached
            rec.alt_translated = values["alt_en"]
            rec.caption = values["caption"]
            rec.e5, rec.clip, rec.similarity = verdict.e5, verdict.clip, verdict.score
            rec.label, rec.confidence = verdict.label, verdict.confidence
            if verdict.label == "uygun":
                continue
            check = "mismatch" if verdict.label == "uyumsuz" else "insufficient"
            thumb = analyzer.backend.thumbnail_png(image, cfg.evidence_thumbnail_px)
            findings.append(
                _finding(
                    check,
                    el,
                    cfg,
                    confidence=verdict.confidence,
                    needs_review=verdict.needs_review,
                    evidence=Evidence(
                        check=check,
                        method="model",
                        alt=plan.alt,
                        alt_translated=values["alt_en"],
                        caption=values["caption"],
                        similarity=verdict.score,
                        e5=verdict.e5,
                        clip=verdict.clip,
                        label=verdict.label,
                        detail=verdict.review_reason,
                        screenshot=artifacts.add(thumb),
                    ),
                )
            )

        # ALT-11: belirsiz bağlantı ve düğme adları.
        controls = [e for e in elements if e.kind in ("link", "button") and e.visible]
        for el in controls:
            if rules.is_vague_name(el.accessible_name, cfg):
                findings.append(
                    _rule_finding(
                        "vague_name", el, cfg, always_review=True, detail=el.accessible_name
                    )
                )
    finally:
        analyzer.close()

    warnings.extend(analyzer.warnings)
    backend_obj = analyzer._backend
    audited_images = sum(1 for r in records if r.status not in ("decorative", "skipped"))
    report = AltTextReport(
        backend=cfg.backend,
        approach=cfg.approach,
        images=records,
        audited_images=audited_images,
        audited_controls=len(controls),
        audited=audited_images + len(controls),
        cache_hits=sum(1 for r in records if r.cached),
        seconds_total=round(time.perf_counter() - started, 3),
        seconds_load=load_seconds,
        seconds_per_image=round(model_seconds / model_count, 3) if model_count else None,
    )
    return AltTextResult(
        report=report,
        findings=findings,
        artifacts=dict(artifacts.files),
        warnings=warnings,
        models=backend_obj.versions() if backend_obj else {},
        libraries=backend_obj.library_versions() if backend_obj else {},
    )


def _short_src(src: str | None) -> str | None:
    """Rapora yazılan kaynak: ``data:`` URI'ler kısaltılır."""
    if src and src.startswith("data:"):
        return src[:40] + "…" if len(src) > 40 else src
    return src


def download_models(config: YankiConfig) -> list[str]:
    """Yapılandırmadaki modelleri sabit revizyonlarıyla indirir (NFR-07: yalnızca ağırlık).

    Raises:
        BackendUnavailable: ML paketleri kurulu değil.
    """
    try:
        from yanki.core.alttext.backends.transformers import fetch_model
    except ImportError as exc:
        raise BackendUnavailable("ML paketleri kurulu değil (torch, transformers).") from exc
    models = config.alt_text.models
    refs = [models.captioner, models.embedder, models.clip]
    if config.alt_text.approach == "translate":
        refs.insert(1, models.translator)
    else:
        refs.append(models.clip_multilingual)
    done: list[str] = []
    for ref in refs:
        fetch_model(ref)
        done.append(ref.ref())
    return done
