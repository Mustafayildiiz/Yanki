"""Alt metin modülünün ölçümü (NFR-01, NFR-03, ALT-01..12, SRS §8.2, §8.3).

Kullanım (konteynerde, ``yanki models download`` sonrasında):

    python experiments/alt_eval.py                  # Set B/C + varsa Set A
    python experiments/alt_eval.py --verbose        # sayfa ve satır ayrıntıları
    python experiments/alt_eval.py --grid           # Set A'da §8.3 aday ızgarası
    python experiments/alt_eval.py --grid --download  # adayların ağırlıklarını indir
    python experiments/alt_eval.py --json out.json

Yalnızca gerçek arka uçla çalışır; sahte arka uç reddedilir (doğruluk sahte
arka uçla ölçülmez). Önbellek kapalıdır, böylece görsel başına süre
(NFR-01) gerçek çıkarım süresidir; model yükleme süresi ayrı yazılır.

**Set B/C** (``fixtures/known_issues``, ``fixtures/clean``): beklenen alt metin
bulgularının yakalanma oranı ve beklenmeyen kesin bulgular (yanlış alarm).
Eşleme anahtarı ``(rule, check, selector)``; tanımlar ``keyboard_eval`` ile aynı.

**Set A** (``fixtures/alt_text_labeled/labels.csv``, hazır olduğunda): biçim
``fixtures/alt_text_labeled/README.md`` içindedir. İki etiketleyici arasında
Cohen kappa, sınıf başına precision/recall/F1 ve makro F1 (NFR-03 hedefi ≥ 0,80).
``--grid`` §8.3 adaylarını aynı eşiklerle karşılaştırır.

Eşikler kalibre edilmemiş başlangıç değerleridir; hedef tutmazsa sonuç
gizlenmez, sapma raporlanır.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from yanki.config import YankiConfig
from yanki.core.alttext import CollectedImages, analyze
from yanki.core.alttext.backend import BackendUnavailable, ImageInput, load_backend
from yanki.core.alttext.images import RASTER_MIMES, Gathered, sniff
from yanki.core.scan import run_scan
from yanki.core.schema import BoundingBox, Finding, PageElement

ROOT = Path(__file__).resolve().parents[1]
SET_B = ROOT / "fixtures" / "known_issues"
SET_C = ROOT / "fixtures" / "clean"
SET_A = ROOT / "fixtures" / "alt_text_labeled"
LABELS = ("uygun", "yetersiz", "uyumsuz", "eksik")

F1_TARGET = 0.80  # NFR-03
SECONDS_TARGET = 3.0  # NFR-01

# SRS §8.3 adayları. Revizyonlar sabittir (NFR-06); lisanslar docs/THIRD_PARTY_LICENSES.md.
CANDIDATES: dict[str, dict[str, Any]] = {
    "translator": {
        "opus-mt-tr-en": {"id": "Helsinki-NLP/opus-mt-tr-en",
                          "revision": "19c65427cc2af5f191337d4899e0348c4af25902"},
        "opus-mt-tc-big-tr-en": {"id": "Helsinki-NLP/opus-mt-tc-big-tr-en",
                                 "revision": "2261c8fc7b1af59caee87f8ff0ecf3fbccfe8391"},
    },
    "embedder": {
        "e5-small": {"id": "intfloat/multilingual-e5-small",
                     "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3"},
        "e5-base": {"id": "intfloat/multilingual-e5-base",
                    "revision": "d128750597153bb5987e10b1c3493a34e5a4502a"},
    },
    "captioner": {
        "blip-base": {"id": "Salesforce/blip-image-captioning-base",
                      "revision": "82a37760796d32b1411fe092ab5d4e227313294b"},
        "blip2-opt-2.7b": {"id": "Salesforce/blip2-opt-2.7b",
                           "revision": "59a1ef6c1e5117b3f65523d1c6066825bcf315e3"},
    },
}  # fmt: skip


def real_config(overrides: dict[str, Any] | None = None) -> YankiConfig:
    """Gerçek arka uç, önbellek kapalı, klavye kapalı."""
    alt = {"cache": False, **(overrides or {})}
    cfg = YankiConfig.model_validate({"keyboard": {"enabled": False}, "alt_text": alt})
    if cfg.alt_text.backend != "transformers":
        raise SystemExit("alt_eval yalnızca gerçek arka uçla çalışır (sahte arka uç reddedildi).")
    return cfg


# --- Set B / C --------------------------------------------------------------------------


def key(item: dict[str, Any] | Finding) -> tuple[str, str, str]:
    if isinstance(item, Finding):
        return item.rule, item.evidence.check or "", item.selector
    return str(item["rule"]), str(item["check"]), str(item["selector"])


@dataclass
class PageResult:
    set: str
    page: str
    expected: int
    found: int
    expected_model: int
    found_model: int
    missed: list[str] = field(default_factory=list)
    false_alarms: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    audited: int = 0
    seconds_per_image: float | None = None
    seconds_load: float | None = None
    analyzed: int = 0


async def evaluate_page(html: Path, set_name: str, config: YankiConfig) -> PageResult:
    spec = json.loads(html.with_suffix(".expected.json").read_text(encoding="utf-8"))["alt_text"]
    report = await run_scan(str(html), config)
    alt = report.alt_text
    got = {key(f): f for f in report.findings if f.type == "alt_text"}
    expected = {key(e): e for e in spec["expected"]}
    model_keys = {k for k, e in expected.items() if e.get("requires_models")}
    allowed = {key(e) for e in spec.get("allowed_review", [])}
    result = PageResult(
        set=set_name,
        page=html.name,
        expected=len(expected),
        found=sum(1 for k in expected if k in got),
        expected_model=len(model_keys),
        found_model=sum(1 for k in model_keys if k in got),
        missed=[" ".join(k) for k in expected if k not in got],
        audited=alt.audited if alt else 0,
        seconds_per_image=alt.seconds_per_image if alt else None,
        seconds_load=alt.seconds_load if alt else None,
        analyzed=sum(1 for r in alt.images if r.status == "analyzed") if alt else 0,
    )
    for k, f in got.items():
        if k in expected:
            continue
        ev = f.evidence
        label = f"{' '.join(k)} (c={f.confidence:.2f}, sim={ev.similarity}, {ev.detail or ''})"
        if f.needs_review or k in allowed:
            result.review.append(label)
        else:
            result.false_alarms.append(label)
    return result


async def evaluate_bc(config: YankiConfig) -> list[PageResult]:
    out: list[PageResult] = []
    for set_name, folder in (("B", SET_B), ("C", SET_C)):
        for html in sorted(folder.glob("*.html")):
            out.append(await evaluate_page(html, set_name, config))
    return out


def summarize_bc(results: list[PageResult]) -> dict[str, Any]:
    b = [r for r in results if r.set == "B"]
    c = [r for r in results if r.set == "C"]
    timed = [r for r in results if r.seconds_per_image is not None and r.analyzed]
    images = sum(r.analyzed for r in timed)
    per_image = sum(r.seconds_per_image * r.analyzed for r in timed) / images if images else None  # type: ignore[operator]
    exp = sum(r.expected for r in b)
    exp_m = sum(r.expected_model for r in b)
    return {
        "recall": sum(r.found for r in b) / exp if exp else 1.0,
        "expected": exp,
        "found": sum(r.found for r in b),
        "recall_model": sum(r.found_model for r in b) / exp_m if exp_m else 1.0,
        "expected_model": exp_m,
        "found_model": sum(r.found_model for r in b),
        "false_alarms_c": sum(len(r.false_alarms) for r in c),
        "false_alarms_b": sum(len(r.false_alarms) for r in b),
        "review_c": sum(len(r.review) for r in c),
        "audited_c": sum(r.audited for r in c),
        "seconds_per_image": round(per_image, 3) if per_image is not None else None,
        "images_timed": images,
    }


# --- Set A ----------------------------------------------------------------------------


def cohen_kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def prf(gold: list[str], pred: list[str]) -> dict[str, Any]:
    per: dict[str, dict[str, float]] = {}
    for label in LABELS:
        tp = sum(g == label and p == label for g, p in zip(gold, pred, strict=True))
        fp = sum(g != label and p == label for g, p in zip(gold, pred, strict=True))
        fn = sum(g == label and p != label for g, p in zip(gold, pred, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    present = [lbl for lbl in LABELS if per[lbl]["support"]]
    macro = sum(per[lbl]["f1"] for lbl in present) / len(present) if present else 0.0
    return {"per_class": per, "macro_f1": macro}


def load_set_a() -> list[dict[str, str]] | None:
    path = SET_A / "labels.csv"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    bad = [r for r in rows if r.get("label") not in LABELS]
    if bad:
        raise SystemExit(
            f"labels.csv: geçersiz 'label' değeri ({len(bad)} satır), izinli: {LABELS}"
        )
    return rows


def classify_rows(rows: list[dict[str, str]], config: YankiConfig) -> dict[str, Any]:
    """Set A satırlarını hattın kendisiyle (tarayıcısız) sınıflandırır."""
    elements: list[PageElement] = []
    collected = CollectedImages()
    for i, row in enumerate(rows):
        sel = f"#a{i:04d}"
        alt = row.get("alt", "")
        elements.append(
            PageElement(
                kind="image",
                selector=sel,
                tag="img",
                visible=True,
                bbox=BoundingBox(x=0, y=0, width=512, height=512),
                alt=alt if alt.strip() else None,  # boş = alt yok (eksik)
                lang=row.get("page_lang") or None,
                src=row["image"],
                natural_width=512,
            )
        )
        data = (SET_A / row["image"]).read_bytes()
        mime = sniff(data)
        if mime in RASTER_MIMES:
            img = ImageInput(sha256=hashlib.sha256(data).hexdigest(), data=data, mime=mime)  # type: ignore[arg-type]
            collected.inputs[sel] = Gathered(image=img, source="response")
        else:
            collected.inputs[sel] = Gathered(reason=f"unsupported_format:{mime}")
    # Sayfa taramasındaki görsel sınırı (max_images) Set A'yı kesmesin.
    alt_cfg = config.alt_text.model_copy(update={"max_images": max(len(rows), 1)})
    config = config.model_copy(update={"alt_text": alt_cfg})
    t0 = time.perf_counter()
    result = analyze(elements, collected, config, page_lang=None)
    wall = time.perf_counter() - t0
    pred: list[str] = []
    failed = 0
    for rec in result.report.images:
        if rec.status in ("failed", "skipped"):
            failed += 1
            pred.append("analiz_edilemedi")
        else:
            pred.append(rec.label or "uygun")
    gold = [r["label"] for r in rows]
    scored = [(g, p) for g, p in zip(gold, pred, strict=True) if p != "analiz_edilemedi"]
    metrics = prf([g for g, _ in scored], [p for _, p in scored])
    return {
        **metrics,
        "n": len(rows),
        "failed": failed,
        "seconds_per_image": result.report.seconds_per_image,
        "seconds_load": result.report.seconds_load,
        "seconds_wall": round(wall, 3),
        "models": result.models,
        "warnings": result.warnings,
    }


def grid() -> list[tuple[str, dict[str, Any]]]:
    """§8.3: iki yaklaşım × E5 boyutu × çeviri modeli × açıklama modeli."""
    out: list[tuple[str, dict[str, Any]]] = []
    for cap_name, cap in CANDIDATES["captioner"].items():
        for e5_name, e5 in CANDIDATES["embedder"].items():
            for tr_name, tr in CANDIDATES["translator"].items():
                name = f"translate/{tr_name}/{e5_name}/{cap_name}"
                models = {"captioner": cap, "embedder": e5, "translator": tr}
                out.append((name, {"approach": "translate", "models": models}))
            name = f"multilingual/{e5_name}/{cap_name}"
            models = {"captioner": cap, "embedder": e5}
            out.append((name, {"approach": "multilingual", "models": models}))
    return out


# --- ana akış ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--grid", action="store_true", help="Set A'da §8.3 aday ızgarası.")
    parser.add_argument("--download", action="store_true", help="Eksik aday ağırlıklarını indir.")
    parser.add_argument("--skip-bc", action="store_true", help="Set B/C ölçümünü atla.")
    parser.add_argument("--json", type=Path, help="Sonuçları JSON olarak yaz.")
    args = parser.parse_args()

    config = real_config()
    try:
        load_backend(config.alt_text, device=config.device, seed=config.seed)
    except BackendUnavailable as exc:
        print(f"Gerçek modeller yüklenemedi: {exc}", file=sys.stderr)
        return 2

    out: dict[str, Any] = {"seed": config.seed, "config": config.alt_text.model_dump(mode="json")}
    ok = True
    if not args.skip_bc:
        results = asyncio.run(evaluate_bc(config))
        summary = summarize_bc(results)
        out["set_bc"] = {"summary": summary, "pages": [asdict(r) for r in results]}
        print(f"{'Set':<4}{'Sayfa':<28}{'Bek.':>5}{'Bul.':>5}{'YA':>4}{'İnc.':>5}{'sn/görsel':>11}")
        for r in results:
            spi = f"{r.seconds_per_image:.2f}" if r.seconds_per_image is not None else "-"
            print(
                f"{r.set:<4}{r.page:<28}{r.expected:>5}{r.found:>5}"
                f"{len(r.false_alarms):>4}{len(r.review):>5}{spi:>11}"
            )
            for m in r.missed:
                print(f"      KAÇIRILDI    {m}")
            for fa in r.false_alarms:
                print(f"      YANLIŞ ALARM {fa}")
            if args.verbose:
                for rv in r.review:
                    print(f"      ELLE İNCELE  {rv}")
        print()
        print(
            f"Set B alt metin duyarlılığı: {summary['recall']:.0%} "
            f"({summary['found']}/{summary['expected']}); yalnızca model gerektirenler: "
            f"{summary['recall_model']:.0%} ({summary['found_model']}/{summary['expected_model']})"
        )
        print(
            f"Set C kesin yanlış alarm: {summary['false_alarms_c']} "
            f"({summary['audited_c']} denetlenen öğe), elle incele: {summary['review_c']}; "
            f"Set B'de beklenmeyen kesin bulgu: {summary['false_alarms_b']}"
        )
        spi = summary["seconds_per_image"]
        if spi is not None:
            print(
                f"NFR-01 görsel başına süre (CPU, önbellek dışı): {spi:.2f} sn "
                f"({summary['images_timed']} görsel), hedef ≤ {SECONDS_TARGET:.0f} sn"
            )
            ok = ok and spi <= SECONDS_TARGET

    rows = load_set_a()
    if rows is None:
        print("\nSet A yok (fixtures/alt_text_labeled/labels.csv): NFR-03 ölçülemedi.")
        out["set_a"] = None
    else:
        kappa = cohen_kappa([r["label_a"] for r in rows], [r["label_b"] for r in rows])
        print(f"\nSet A: {len(rows)} satır, etiketleyiciler arası Cohen kappa = {kappa:.3f}")
        candidates = grid() if args.grid else [("varsayılan", {})]
        out["set_a"] = {"kappa": kappa, "candidates": {}}
        for name, overrides in candidates:
            cfg = real_config({**overrides, "allow_download": args.download})
            try:
                res = classify_rows(rows, cfg)
            except BackendUnavailable as exc:
                print(f"  {name}: atlandı ({exc})")
                continue
            out["set_a"]["candidates"][name] = res
            spi = res["seconds_per_image"]
            print(
                f"  {name}: makro F1 = {res['macro_f1']:.3f} (hedef ≥ {F1_TARGET}), "
                f"analiz edilemedi = {res['failed']}, sn/görsel = {spi}"
            )
            if args.verbose:
                for label, m in res["per_class"].items():
                    print(
                        f"      {label:<9} P={m['precision']:.2f} R={m['recall']:.2f} "
                        f"F1={m['f1']:.2f} n={m['support']}"
                    )
        if not args.grid and "varsayılan" in out["set_a"]["candidates"]:
            ok = ok and out["set_a"]["candidates"]["varsayılan"]["macro_f1"] >= F1_TARGET

    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
