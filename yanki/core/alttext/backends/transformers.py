"""PyTorch + transformers arka ucu (ALT-03..05, NFR-01, NFR-06, NFR-12).

- Görsel açıklama: BLIP (varsayılan) ya da BLIP-2; açgözlü kod çözme.
- Çeviri: MarianMT (``opus-mt-tr-en`` ya da ``opus-mt-tc-big-tr-en``).
- Anlamsal benzerlik: multilingual-e5, ortalama havuzlama (ana sinyal).
- Görsel–metin benzerliği: CLIP; çok dilli yolda metin tarafı
  ``clip-ViT-B-32-multilingual-v1`` (DistilBERT + ortalama havuzlama + doğrusal
  izdüşüm), sentence-transformers olmadan düz transformers ile yüklenir.

Ağırlıklar sabit revizyonla ve varsayılan olarak yalnızca yerelden yüklenir
(``local_files_only``); tarama sırasında indirme yapılmaz. GPU varsayılmaz:
``device="cuda"`` seçilip GPU bulunmazsa CPU kullanılır (NFR-12).

Bu modül torch gerektirir; ``ml`` ekstrası kurulu değilse içe aktarılamaz ve
``load_backend`` bunu ``BackendUnavailable``'a çevirir.
"""

from __future__ import annotations

import io
import json
import os
import threading
import warnings
from collections.abc import Sequence
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import HfApi, snapshot_download, try_to_load_from_cache
from PIL import Image

from yanki.config import AltTextConfig, ModelRef
from yanki.core.alttext.backend import BackendUnavailable, ImageInput

# Ağırlık dosyası seçimi: TF/Flax/ONNX/OpenVINO kopyaları indirilmez; safetensors
# varsa aynı ağırlığın .bin kopyası atlanır.
_KEEP_SUFFIXES = (".json", ".bin", ".safetensors", ".model", ".spm", ".txt")
_SKIP_PREFIXES = ("onnx/", "openvino/", "coreml/")

warnings.filterwarnings("ignore", message="Recommended: pip install sacremoses")


def _quiet_transformers() -> None:
    """Yükleme ilerleme çubukları ve bilgi iletileri rapor çıktısına karışmasın."""
    from transformers.utils import logging as tf_logging

    tf_logging.set_verbosity_error()
    tf_logging.disable_progress_bar()


def model_files(ref: ModelRef) -> list[str]:
    """Bir model deposundan indirilecek dosyalar (çevrimiçi)."""
    files = [s.rfilename for s in HfApi().model_info(ref.id, revision=ref.revision).siblings]
    keep = [f for f in files if f.endswith(_KEEP_SUFFIXES) and not f.startswith(_SKIP_PREFIXES)]
    folders_with_st = {
        f.rsplit("/", 1)[0] if "/" in f else "" for f in keep if f.endswith(".safetensors")
    }
    return [
        f
        for f in keep
        if not (f.endswith(".bin") and (f.rsplit("/", 1)[0] if "/" in f else "") in folders_with_st)
    ]


def models_dir() -> str | None:
    """Model önbelleği: ``YANKI_MODELS_DIR`` ya da huggingface_hub varsayılanı (``HF_HOME``).

    Çağrı anında okunur; testler ağırlıksız ortamı bu değişkenle canlandırır.
    """
    return os.environ.get("YANKI_MODELS_DIR") or None


def fetch_model(ref: ModelRef) -> Path:
    """Modeli sabit revizyonuyla model önbelleğine indirir (NFR-07: yalnızca ağırlık)."""
    return Path(
        snapshot_download(
            ref.id, revision=ref.revision, allow_patterns=model_files(ref), cache_dir=models_dir()
        )
    )


def cached_snapshot(ref: ModelRef) -> Path | None:
    """Sabit revizyonun yerel anlık görüntü dizini; ağırlıklar yoksa ``None`` (ağ kullanmaz).

    ``snapshot_download(local_files_only=True)`` burada kullanılmaz: depodaki tüm
    dosyaları (TF/Flax kopyaları dâhil) ister, bizim seçerek indirdiğimiz
    anlık görüntüyü eksik sayar.
    """
    found = try_to_load_from_cache(
        ref.id, "config.json", revision=ref.revision, cache_dir=models_dir()
    )
    if not isinstance(found, str):
        return None
    folder = Path(found).parent
    if not any(folder.glob("*.safetensors")) and not any(folder.glob("*.bin")):
        return None
    return folder


def local_model(ref: ModelRef, *, allow_download: bool) -> Path:
    """Yerel önbellekteki model dizini; yoksa ``allow_download`` ile indirir."""
    folder = cached_snapshot(ref)
    if folder is not None:
        return folder
    if not allow_download:
        raise BackendUnavailable(
            f"model ağırlıkları yerelde yok: {ref.ref()} (`yanki models download` ile indirin)"
        )
    try:
        return fetch_model(ref)
    except Exception as exc:
        raise BackendUnavailable(f"model indirilemedi: {ref.ref()}: {exc}") from exc


def _mean_pool(hidden: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    m = mask.unsqueeze(-1).to(hidden.dtype)
    return (hidden * m).sum(1) / m.sum(1).clamp(min=1e-9)


def _features(out: Any) -> torch.Tensor:
    """``get_*_features`` çıktısı: tensör ya da (transformers 5) çıktı nesnesi."""
    if isinstance(out, torch.Tensor):
        return out
    for attr in ("image_embeds", "text_embeds", "pooler_output"):
        value = getattr(out, attr, None)
        if isinstance(value, torch.Tensor):
            return value
    raise TypeError(f"beklenmeyen özellik çıktısı: {type(out).__name__}")


class TransformersBackend:
    name = "transformers"

    def __init__(self, cfg: AltTextConfig, *, device: str, seed: int) -> None:
        self.cfg = cfg
        self.warnings: list[str] = []
        _quiet_transformers()
        if device == "cuda" and not torch.cuda.is_available():
            self.warnings.append("CUDA istendi ama GPU bulunamadı; CPU kullanılıyor (NFR-12).")
            device = "cpu"
        self.device = torch.device(device)
        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True, warn_only=True)
        if cfg.num_threads:
            torch.set_num_threads(cfg.num_threads)
        Image.MAX_IMAGE_PIXELS = cfg.max_pixels
        models = cfg.models
        self.roles: dict[str, ModelRef] = {
            "captioner": models.captioner,
            "embedder": models.embedder,
        }
        self.roles["clip"] = models.clip
        if cfg.approach == "translate":
            self.roles["translator"] = models.translator
        else:
            self.roles["clip_multilingual"] = models.clip_multilingual
        # Ağırlıkların varlığı baştan denetlenir; yükleme ilk kullanımda yapılır.
        self.paths = {
            role: local_model(ref, allow_download=cfg.allow_download)
            for role, ref in self.roles.items()
        }
        self._loaded: dict[str, Any] = {}
        self._lock = threading.Lock()

    # --- sürümler ---------------------------------------------------------------

    def versions(self) -> dict[str, str]:
        return {role: ref.ref() for role, ref in sorted(self.roles.items())}

    def library_versions(self) -> dict[str, str]:
        return {"torch": torch.__version__, "transformers": dist_version("transformers")}

    def load(self) -> None:
        for role in self.roles:
            self._get(role)

    # --- yükleme ----------------------------------------------------------------

    def _get(self, role: str) -> Any:
        with self._lock:
            if role not in self._loaded:
                self._loaded[role] = self._load(role)
            return self._loaded[role]

    def _load(self, role: str) -> Any:
        import transformers as tf

        path = self.paths[role]
        if role == "captioner":
            model_type = json.loads((path / "config.json").read_text(encoding="utf-8"))[
                "model_type"
            ]
            if model_type == "blip-2":
                proc = tf.Blip2Processor.from_pretrained(path)
                model = tf.Blip2ForConditionalGeneration.from_pretrained(path)
            else:
                proc = tf.BlipProcessor.from_pretrained(path)
                model = tf.BlipForConditionalGeneration.from_pretrained(path)
        elif role == "translator":
            proc = tf.MarianTokenizer.from_pretrained(path)
            model = tf.MarianMTModel.from_pretrained(path)
        elif role == "embedder":
            proc = tf.AutoTokenizer.from_pretrained(path)
            model = tf.AutoModel.from_pretrained(path)
        elif role == "clip":
            proc = tf.CLIPProcessor.from_pretrained(path)
            model = tf.CLIPModel.from_pretrained(path)
        elif role == "clip_multilingual":
            from safetensors.torch import load_file

            proc = tf.AutoTokenizer.from_pretrained(path)
            model = tf.AutoModel.from_pretrained(path)
            dense_cfg = json.loads((path / "2_Dense" / "config.json").read_text(encoding="utf-8"))
            weights = load_file(str(path / "2_Dense" / "model.safetensors"))
            dense = torch.nn.Linear(
                dense_cfg["in_features"], dense_cfg["out_features"], bias=dense_cfg["bias"]
            )
            dense.load_state_dict({k.removeprefix("linear."): v for k, v in weights.items()})
            dense.eval().to(self.device)
            model.eval().to(self.device)
            return proc, model, dense
        else:  # pragma: no cover - roller sabit
            raise KeyError(role)
        model.eval().to(self.device)
        return proc, model

    # --- görsel ------------------------------------------------------------------

    def _pil(self, image: ImageInput) -> Image.Image:
        if len(image.data) > self.cfg.max_image_bytes:
            raise ValueError("görsel boyut sınırını aşıyor")
        try:
            with Image.open(io.BytesIO(image.data)) as im:
                im.seek(0)
                if im.width * im.height > self.cfg.max_pixels:
                    raise ValueError("görsel piksel sınırını aşıyor")
                rgba = im.convert("RGBA")
        except (OSError, Image.DecompressionBombError, SyntaxError) as exc:
            raise ValueError(f"görsel çözülemedi: {exc}") from exc
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas

    def thumbnail_png(self, image: ImageInput, max_px: int) -> bytes | None:
        try:
            pil = self._pil(image)
        except ValueError:
            return None
        pil.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        pil.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    # --- modeller ---------------------------------------------------------------

    @torch.inference_mode()
    def caption(self, images: Sequence[ImageInput]) -> list[str]:
        proc, model = self._get("captioner")
        out: list[str] = []
        for start in range(0, len(images), self.cfg.batch_size):
            batch = [self._pil(img) for img in images[start : start + self.cfg.batch_size]]
            inputs = proc(images=batch, return_tensors="pt").to(self.device)
            ids = model.generate(
                **inputs,
                max_new_tokens=self.cfg.caption_max_tokens,
                num_beams=1,
                do_sample=False,
            )
            out.extend(t.strip() for t in proc.batch_decode(ids, skip_special_tokens=True))
        return out

    @torch.inference_mode()
    def translate(self, texts: Sequence[str], source: str) -> list[str]:
        tok, model = self._get("translator")
        out: list[str] = []
        for start in range(0, len(texts), self.cfg.batch_size):
            batch = list(texts[start : start + self.cfg.batch_size])
            enc = tok(batch, return_tensors="pt", padding=True, truncation=True, max_length=256)
            ids = model.generate(
                **enc.to(self.device),
                num_beams=self.cfg.translate_num_beams,
                do_sample=False,
                max_new_tokens=128,
            )
            out.extend(t.strip() for t in tok.batch_decode(ids, skip_special_tokens=True))
        return out

    def _e5(self, texts: list[str]) -> torch.Tensor:
        tok, model = self._get("embedder")
        enc = tok(
            [self.cfg.e5_prefix + t for t in texts],
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="pt",
        ).to(self.device)
        hidden = model(**enc).last_hidden_state
        return torch.nn.functional.normalize(_mean_pool(hidden, enc["attention_mask"]), dim=-1)

    @torch.inference_mode()
    def text_similarity(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        a = self._e5([p[0] for p in pairs])
        b = self._e5([p[1] for p in pairs])
        return [round(float(x), 6) for x in (a * b).sum(-1)]

    @torch.inference_mode()
    def image_text_similarity(
        self, items: Sequence[tuple[ImageInput, str]], *, multilingual: bool = False
    ) -> list[float]:
        if not items:
            return []
        proc, model = self._get("clip")
        pixels = proc(images=[self._pil(img) for img, _ in items], return_tensors="pt").to(
            self.device
        )
        img = torch.nn.functional.normalize(_features(model.get_image_features(**pixels)), dim=-1)
        texts = [t for _, t in items]
        if multilingual:
            tok, text_model, dense = self._get("clip_multilingual")
            enc = tok(texts, padding=True, truncation=True, max_length=128, return_tensors="pt").to(
                self.device
            )
            pooled = _mean_pool(text_model(**enc).last_hidden_state, enc["attention_mask"])
            txt = dense(pooled)
        else:
            enc = proc.tokenizer(
                texts, padding=True, truncation=True, max_length=77, return_tensors="pt"
            ).to(self.device)
            txt = _features(model.get_text_features(**enc))
        txt = torch.nn.functional.normalize(txt, dim=-1)
        return [round(float(x), 6) for x in (img * txt).sum(-1)]
