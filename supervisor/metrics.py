"""Local image checks; learned metrics use explicitly configured local checkpoints."""

import os
from functools import lru_cache

import numpy as np
from PIL import Image


def image_checks(path):
    with Image.open(path) as source:
        gray = np.asarray(source.convert("L").resize((256, 256)), dtype=float)
        small = np.asarray(source.convert("L").resize((32, 32)), dtype=float)
    laplace = -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    variance = float(laplace.var())
    axis = np.arange(32)
    basis = np.cos(np.pi * (2 * axis[None, :] + 1) * axis[:, None] / 64)
    dct = (basis @ small @ basis.T)[:8, :8].flatten()[1:]
    bits = dct > np.median(dct)
    fingerprint = sum(int(bit) << i for i, bit in enumerate(bits))
    return {"blur_variance": round(variance, 3), "blur_score": round(min(100, variance / 100 * 100), 2),
            "phash": f"{fingerprint:016x}", "texture": float(gray.std())}


@lru_cache(maxsize=3)
def learned_model(kind, checkpoint):
    from transformers import CLIPModel, AutoModelForImageClassification, AutoProcessor
    processor = AutoProcessor.from_pretrained(checkpoint, local_files_only=True)
    cls = CLIPModel if kind == "clip" else AutoModelForImageClassification
    return processor, cls.from_pretrained(checkpoint, local_files_only=True).eval()


def learned_checks(path, prompt):
    result = {}
    for kind in ("clip", "aesthetic", "nsfw"):
        checkpoint = os.environ.get("SUPERVISOR_" + kind.upper() + "_MODEL")
        if not checkpoint:
            result[kind + "_status"] = "not_configured"
            continue
        try:
            import torch
            processor, model = learned_model(kind, checkpoint)
            with Image.open(path) as source, torch.inference_mode():
                image = source.convert("RGB")
                if kind == "clip":
                    inputs = processor(text=[prompt], images=image, return_tensors="pt", padding=True, truncation=True)
                    output = model(**inputs)
                    similarity = torch.nn.functional.cosine_similarity(output.image_embeds, output.text_embeds).item()
                    result["clip_score"] = round(100 * max(0, min(1, 2.5 * similarity)), 2)
                else:
                    output = model(**processor(images=image, return_tensors="pt")).logits[0]
                    if kind == "aesthetic":
                        if output.numel() != 1:
                            raise ValueError("Aesthetic checkpoint must regress a 0-10 score")
                        result["aesthetic_score"] = round(max(0, min(100, output.item() * 10)), 2)
                    else:
                        labels = model.config.id2label
                        indices = [i for i in range(output.numel()) if str(labels.get(i, "")).lower() in ("nsfw", "porn", "sexy", "hentai")]
                        if not indices:
                            raise ValueError("NSFW classifier labels unsupported")
                        result["nsfw_probability"] = round(float(output.softmax(-1)[indices].sum()), 4)
            result[kind + "_status"] = "ready"
        except Exception as exc:
            result[kind + "_status"] = "unavailable:" + type(exc).__name__
    return result


def traditional_checks(path, prompt):
    return {**image_checks(path), **learned_checks(path, prompt)}
