"""Alt metin için kural tabanlı denetimler (ALT-01, ALT-02, ALT-07, ALT-11, ALT-12).

Bu denetimler model gerektirmez; torch kurulu olmasa da çalışır. Sonuçları
yapay zeka bulgusu gibi sunulmaz (``evidence.method = "rule"``, CLAUDE.md §9).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from yanki.config import AltTextConfig
from yanki.core.schema import PageElement

_PUNCT = re.compile(r"[\s\"'“”‘’.,;:!?()\[\]{}…\-–—_/\\|*#]+")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_CAMERA = re.compile(r"^(img|dsc|dscn|dscf|dcim|pxl|p|photo|image|imagen|screenshot|sam|gopr)\d+$")
_TR_CHARS = set("çğışöüÇĞİŞÖÜ")


def normalize(text: str) -> str:
    """Karşılaştırma için metni sadeleştirir: küçük harf, noktalama ve boşluk birleşik.

    Türkçe büyük ``İ``'nin küçültülmesinde oluşan birleşik nokta (U+0307) atılır.
    """
    folded = unicodedata.normalize("NFC", text.casefold()).replace("̇", "")
    return _PUNCT.sub(" ", folded).strip()


def is_decorative(el: PageElement) -> bool:
    """ALT-02: ``alt=""``, ``role=presentation|none`` ya da ``aria-hidden`` ile işaretli."""
    role = (el.role or "").strip().split(" ")[0].lower()
    if el.aria_hidden or role in ("presentation", "none"):
        return True
    return el.alt is not None and el.alt.strip() == "" and not el.label


def text_alternative(el: PageElement) -> str | None:
    """Görselin değerlendirilecek metin alternatifi: boş olmayan ``alt``, yoksa
    ``aria-labelledby`` / ``aria-label`` / ``title``."""
    if el.alt is not None and el.alt.strip():
        return " ".join(el.alt.split())
    return el.label or None


def _src_stem(src: str | None) -> str | None:
    if not src or src.startswith("data:"):
        return None
    path = PurePosixPath(unquote(urlsplit(src).path))
    return normalize(path.stem) if path.stem else None


def is_generic(alt: str, src: str | None, cfg: AltTextConfig) -> str | None:
    """ALT-07: dosya adı ya da genel ifade ise eşleşen nedeni döner, değilse ``None``.

    Nedenler: ``filename`` (``image1.png``), ``camera`` (``DSC_0012``),
    ``generic_word`` (``resim``, ``foto 2``), ``src_name`` (görselin dosya adı),
    ``numeric`` (yalnızca rakam).
    """
    raw = alt.strip().casefold()
    exts = "|".join(re.escape(e.casefold()) for e in cfg.image_extensions)
    if exts and re.fullmatch(rf"[^\s/\\]+\.({exts})", raw):
        return "filename"
    norm = normalize(alt)
    if not norm:
        return "generic_word"
    compact = norm.replace(" ", "")
    if compact.isdigit():
        return "numeric"
    if _CAMERA.fullmatch(compact):
        return "camera"
    generic = {normalize(w) for w in cfg.generic_words}
    # "resim", "foto 2", "görsel 3": genel sözcük ve isteğe bağlı numara.
    head = re.sub(r"\s*\d+$", "", norm)
    if head in generic:
        return "generic_word"
    stem = _src_stem(src)
    if stem and norm == stem:
        return "src_name"
    return None


def is_vague_name(name: str, cfg: AltTextConfig) -> bool:
    """ALT-11: bağlantı ya da düğmenin adı tek başına işlevi anlatmayan bir ifade mi."""
    norm = normalize(name)
    return bool(norm) and norm in {normalize(v) for v in cfg.vague_names}


def detect_language(text: str, cfg: AltTextConfig) -> str | None:
    """ALT-12 için sezgisel dil tespiti (yalnızca ``cfg.stopwords`` dilleri).

    Yeni bağımlılık eklemeden durak sözcükleri ve Türkçeye özgü harflerle karar
    verir. ``cfg.lang_min_words`` sözcükten kısa ya da kararsız metinlerde
    ``None`` döner. Sonuç her zaman "elle incele" bulgusu üretir.
    """
    words = [w.casefold() for w in _WORD.findall(text)]
    if len(words) < cfg.lang_min_words:
        return None
    scores: dict[str, float] = {}
    for lang, stops in cfg.stopwords.items():
        stopset = {s.casefold() for s in stops}
        scores[lang] = sum(1 for w in words if w in stopset)
    if "tr" in scores:
        scores["tr"] += sum(1 for ch in text if ch in _TR_CHARS)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if not ranked or ranked[0][1] < 2:
        return None
    if len(ranked) > 1 and ranked[1][1] > 0:
        return None
    return ranked[0][0]


def primary_lang(tag: str | None) -> str | None:
    """``tr-TR`` → ``tr``."""
    if not tag:
        return None
    head = tag.strip().split("-")[0].split("_")[0].lower()
    return head or None
