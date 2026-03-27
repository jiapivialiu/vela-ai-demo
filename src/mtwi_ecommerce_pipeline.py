"""MTWI → ecommerce pipeline (see agent.md for architecture, src/README.md for runbook).

Steps: text removal (local and/or model) → optional harmonize → quality (local and/or model) →
vision understanding → bilingual copy → optional extra same-product images.

Do not duplicate long usage blocks here; run `python src/mtwi_ecommerce_pipeline.py --help`
or `./scripts/run_one_deliverable_example.sh` (see src/README.md).
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

try:
    from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageStat  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageChops = None
    ImageDraw = None
    ImageEnhance = None
    ImageFilter = None
    ImageStat = None


@dataclass
class OCRTextSpan:
    text: str
    quad: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]


@dataclass
class LocalizedListing:
    title: str
    description: str
    category: str
    key_attributes: Dict[str, str]


@dataclass
class EcommerceArtifact:
    product_id: str
    source_image_path: str
    erased_image_path: Optional[str]
    final_image_path: Optional[str]
    extracted_text: List[str]
    structured_attributes: Dict[str, Any]
    canadian_english: LocalizedListing
    canadian_french: LocalizedListing
    warnings: List[str]
    erased_spans: Optional[List[Dict[str, Any]]] = None
    additional_generated_images: Optional[List[str]] = None


class RequestQueueClient:
    """Generic Request Queue client for BRIA-style image editing models."""

    def __init__(self, api_key: str, mock: bool = False, max_attempts: int = 1):
        self.api_key = api_key
        self.mock = mock
        self.max_attempts = max(1, int(max_attempts))
        self.base_url = os.getenv(
            "GMI_MEDIA_BASE_URL",
            "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey",
        )

    def run_model(self, model: str, payload: Dict[str, Any], timeout_s: float = 180.0) -> Dict[str, Any]:
        if self.mock:
            return {"status": "success", "outcome": {}}

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        enqueue_url = f"{self.base_url}/requests"
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = requests.post(
                    enqueue_url,
                    headers=headers,
                    json={"model": model, "payload": payload},
                    timeout=60,
                )
                resp.raise_for_status()
                break
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    raise
                time.sleep(min(2 * attempt, 6))
        if last_exc and "resp" not in locals():
            raise last_exc
        request_id = resp.json().get("request_id")
        if not request_id:
            raise RuntimeError(f"Request Queue returned no request_id: {resp.text}")

        status_url = f"{self.base_url}/requests/{request_id}"
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            poll = requests.get(status_url, headers=headers, timeout=30)
            poll.raise_for_status()
            data = poll.json()
            status = data.get("status")
            if status in {"success", "failed"}:
                if status == "failed":
                    raise RuntimeError(f"Request failed for {model}: {data}")
                outcome = data.get("outcome")
                if not isinstance(outcome, dict):
                    return {}
                return outcome
            time.sleep(2.0)
        raise TimeoutError(f"Timed out waiting for model {model}")

    def run_image_edit(
        self,
        model: str,
        source_image: Path,
        prompt: str,
        mask_image: Optional[Path] = None,
    ) -> Optional[bytes]:
        """Run image editing and return image bytes if available.

        Payload is intentionally generic to support model-side schema changes.
        """
        if self.mock:
            return source_image.read_bytes()

        image_data_url = path_to_data_url(source_image)
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "image": image_data_url,
            "input_image": image_data_url,
            "image_url": image_data_url,
            "output_format": "png",
            "watermark": False,
        }
        if mask_image and mask_image.exists():
            mask_data_url = path_to_data_url(mask_image, force_png=True)
            payload["mask"] = mask_data_url
            payload["mask_image"] = mask_data_url

        outcome = self.run_model(model=model, payload=payload, timeout_s=float(os.getenv("GMI_IMAGE_TIMEOUT", "240")))
        result_url = extract_media_url(outcome)
        if not result_url:
            return None
        resp = requests.get(result_url, timeout=120)
        resp.raise_for_status()
        return resp.content

    def run_image_variants(
        self,
        model: str,
        reference_image: Path,
        prompt: str,
        count: int = 3,
    ) -> List[bytes]:
        """Generate additional same-product variants from a reference image."""
        if self.mock:
            return [reference_image.read_bytes() for _ in range(max(1, count))]

        image_data_url = path_to_data_url(reference_image)
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "image": image_data_url,
            "input_image": image_data_url,
            "image_url": image_data_url,
            "num_images": count,
            "max_images": count,
            "output_format": "png",
            "watermark": False,
        }
        outcome = self.run_model(model=model, payload=payload, timeout_s=float(os.getenv("GMI_IMAGE_TIMEOUT", "300")))
        urls: List[str] = []
        media_urls = outcome.get("media_urls")
        if isinstance(media_urls, list):
            for item in media_urls:
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    urls.append(item["url"])
                elif isinstance(item, str):
                    urls.append(item)
        if not urls:
            one = extract_media_url(outcome)
            if one:
                urls.append(one)
        binaries: List[bytes] = []
        for u in urls[: max(1, count)]:
            r = requests.get(u, timeout=120)
            r.raise_for_status()
            binaries.append(r.content)
        return binaries


class ChatClient:
    """Chat completion client for vision understanding and text generation."""

    def __init__(self, api_key: str, mock: bool = False, max_attempts: int = 1):
        self.api_key = api_key
        self.mock = mock
        self.max_attempts = max(1, int(max_attempts))
        self.base_url = os.getenv("GMI_LLM_BASE_URL", "https://api.gmi-serving.com/v1")

    def chat_json(self, model: str, messages: List[Dict[str, Any]], max_tokens: int = 1200, temperature: float = 0.2) -> Dict[str, Any]:
        if self.mock:
            return {}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=90)
                resp.raise_for_status()
                break
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    raise
                time.sleep(min(2 * attempt, 8))
        if last_exc and "resp" not in locals():
            raise last_exc
        content = resp.json()["choices"][0]["message"]["content"]
        return parse_json_content(content)


def parse_annotation_file(path: Path) -> List[OCRTextSpan]:
    spans: List[OCRTextSpan] = []
    with path.open("r", encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 9:
                continue
            try:
                coords = [float(x) for x in parts[:8]]
            except ValueError:
                continue
            text = ",".join(parts[8:]).strip()
            quad = (
                (coords[0], coords[1]),
                (coords[2], coords[3]),
                (coords[4], coords[5]),
                (coords[6], coords[7]),
            )
            spans.append(OCRTextSpan(text=text, quad=quad))
    return spans


def resolve_image_path(image_dir: Path, annotation_file: Path) -> Optional[Path]:
    base = annotation_file.name[:-4] if annotation_file.name.endswith(".txt") else annotation_file.name
    for candidate in [image_dir / base, image_dir / f"{base}.jpg", image_dir / f"{base}.png", image_dir / f"{base}.jpeg"]:
        if candidate.exists():
            return candidate
    return None


def clean_extracted_text(texts: List[str]) -> List[str]:
    cleaned: List[str] = []
    seen: set[str] = set()
    for raw in texts:
        t = (raw or "").strip()
        if not t or t == "###":
            continue
        low = t.lower()
        if low.startswith("http://") or low.startswith("https://"):
            continue
        if "taobao.com" in low or "tmall" in low:
            continue
        if "盗图" in t or "水印" in t:
            continue
        if len(t) <= 2 and all(ch in "★☆*" for ch in t):
            continue
        if t not in seen:
            cleaned.append(t)
            seen.add(t)
    return cleaned[:30]


def build_mask_from_quads(source_image: Path, spans: List[OCRTextSpan], out_mask_path: Path, dilation_px: int = 4) -> Optional[Path]:
    if Image is None or ImageDraw is None:
        print("Pillow not installed; skip mask generation and use prompt-only erase.")
        return None
    img = Image.open(source_image)
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    for span in spans:
        pts = [(x, y) for (x, y) in span.quad]
        if dilation_px > 0:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            min_x = max(0, min(xs) - dilation_px)
            min_y = max(0, min(ys) - dilation_px)
            max_x = min(w - 1, max(xs) + dilation_px)
            max_y = min(h - 1, max(ys) + dilation_px)
            draw.rectangle([(min_x, min_y), (max_x, max_y)], fill=255)
        else:
            draw.polygon(pts, fill=255)
    out_mask_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(out_mask_path)
    return out_mask_path


def span_bbox(span: OCRTextSpan) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in span.quad]
    ys = [p[1] for p in span.quad]
    return (min(xs), min(ys), max(xs), max(ys))


def guess_overlay_span_by_text(text: str) -> bool:
    t = (text or "").strip()
    if not t or t == "###":
        return False
    low = t.lower()
    # common overlay / watermark / seller promo cues
    if low.startswith("http://") or low.startswith("https://"):
        return True
    if "taobao.com" in low or "tmall" in low:
        return True
    if any(k in t for k in ["盗图", "水印", "专用", "实拍", "正品保证", "包邮", "促销", "特价", "联系"]):
        return True
    if any(k in low for k in ["wechat", "vx", "qq", "tel", "phone"]):
        return True
    return False


def select_spans_to_erase(
    chat: ChatClient,
    vision_model: str,
    source_image: Path,
    spans: List[OCRTextSpan],
    warnings: List[str],
    mode: str,
) -> List[int]:
    """Return indices of spans to erase.

    mode:
    - "all": erase all annotated spans
    - "overlay": erase only overlay/watermark/promo text; keep product-native printed text
    """
    if mode == "all":
        return list(range(len(spans)))

    # mode == "overlay"
    # First try model-based classification (best effort).
    if not chat.mock:
        try:
            span_lines = []
            for i, s in enumerate(spans):
                x1, y1, x2, y2 = span_bbox(s)
                span_lines.append(f'{i}: "{s.text}" bbox=[{x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f}]')
            prompt = (
                "You are cleaning ecommerce images.\n"
                "Goal: remove ONLY overlaid text/watermarks/promotional captions added on top of the photo, "
                "and KEEP any text that is printed on the product itself (labels, packaging print, engravings).\n\n"
                "Given an image and OCR text boxes, decide which indices are overlays to erase.\n"
                "Return JSON only: {\"erase_indices\": number[], \"keep_indices\": number[], \"notes\": string}\n"
                "Rules:\n"
                "- If unsure, KEEP (do not erase).\n"
                "- Treat URLs, shop names, 'no stealing photos' messages, watermarks, big banners as overlays.\n"
                "- Treat ingredient lists, brand labels on packaging, model numbers on product as product-native.\n\n"
                "OCR boxes:\n"
                + "\n".join(span_lines)
            )
            data = chat.chat_json(
                model=vision_model,
                messages=[
                    {"role": "system", "content": "You classify overlay text vs product-native text."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": path_to_data_url(source_image)}},
                        ],
                    },
                ],
                max_tokens=800,
                temperature=0.1,
            )
            erase_indices = data.get("erase_indices", [])
            if isinstance(erase_indices, list):
                cleaned: List[int] = []
                for v in erase_indices:
                    try:
                        iv = int(v)
                    except Exception:
                        continue
                    if 0 <= iv < len(spans):
                        cleaned.append(iv)
                # be conservative: if model returns nothing, fall back to heuristics
                if cleaned:
                    return sorted(set(cleaned))
        except Exception as exc:
            warnings.append(f"overlay_classifier_failed: {exc}")

    # Heuristic fallback: erase obvious overlays by text patterns.
    erase = [i for i, s in enumerate(spans) if guess_overlay_span_by_text(s.text)]
    return erase


def run_step1_text_erase(
    rq: RequestQueueClient,
    source_image: Path,
    spans: List[OCRTextSpan],
    product_id: str,
    out_dir: Path,
    eraser_model: str,
    use_mask: bool,
) -> Optional[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    mask_path = out_dir / f"{product_id}_mask.png"
    maybe_mask = build_mask_from_quads(source_image, spans, mask_path) if use_mask else None
    edit_prompt = (
        "TASK: Remove text overlays only.\n"
        "Edits allowed: remove all overlaid text, watermarks, banners, stickers, and borders.\n"
        "Edits NOT allowed: do not change the product, do not add/remove items, do not change shape, "
        "color, count, packaging, branding, or material.\n"
        "Preserve camera view, lighting, and background perspective. Photorealistic result.\n"
        "Output must contain no visible text and no watermark."
    )
    result_bytes = rq.run_image_edit(
        model=eraser_model,
        source_image=source_image,
        prompt=edit_prompt,
        mask_image=maybe_mask,
    )
    if not result_bytes:
        return None
    erased_path = out_dir / f"{product_id}_erased.png"
    erased_path.write_bytes(result_bytes)
    return erased_path


def run_step1_text_erase_local(
    source_image: Path,
    spans: List[OCRTextSpan],
    product_id: str,
    out_dir: Path,
    use_mask: bool,
) -> Optional[Path]:
    """Deterministic local erase that removes text regions completely.

    Strategy:
    - Build a text mask from annotation boxes.
    - Replace each box with surrounding background estimate (not mosaic text).
    - Blend edges softly to avoid visible seams.
    """
    if Image is None or ImageDraw is None:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(source_image).convert("RGB")
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    for span in spans:
        x1, y1, x2, y2 = span_bbox(span)
        # Slight dilation to fully cover glyph strokes
        pad = 3
        draw.rectangle(
            [
                (max(0, x1 - pad), max(0, y1 - pad)),
                (min(img.size[0] - 1, x2 + pad), min(img.size[1] - 1, y2 + pad)),
            ],
            fill=255,
        )

    if not use_mask:
        erased_path = out_dir / f"{product_id}_erased.png"
        img.save(erased_path)
        return erased_path

    work = img.copy()
    w, h = img.size
    for span in spans:
        x1f, y1f, x2f, y2f = span_bbox(span)
        x1 = max(0, int(x1f) - 3)
        y1 = max(0, int(y1f) - 3)
        x2 = min(w - 1, int(x2f) + 3)
        y2 = min(h - 1, int(y2f) + 3)
        if x2 <= x1 or y2 <= y1:
            continue

        bw = x2 - x1 + 1
        bh = y2 - y1 + 1

        # Prefer copying nearby texture strips (top/bottom), fallback left/right.
        patch = None
        top_y2 = y1 - 1
        top_y1 = top_y2 - bh + 1
        if top_y1 >= 0:
            patch = img.crop((x1, top_y1, x2 + 1, top_y2 + 1))
        else:
            bottom_y1 = y2 + 1
            bottom_y2 = bottom_y1 + bh - 1
            if bottom_y2 < h:
                patch = img.crop((x1, bottom_y1, x2 + 1, bottom_y2 + 1))
            else:
                left_x2 = x1 - 1
                left_x1 = left_x2 - bw + 1
                if left_x1 >= 0:
                    patch = img.crop((left_x1, y1, left_x2 + 1, y2 + 1))
                else:
                    right_x1 = x2 + 1
                    right_x2 = right_x1 + bw - 1
                    if right_x2 < w:
                        patch = img.crop((right_x1, y1, right_x2 + 1, y2 + 1))

        if patch is None or patch.size != (bw, bh):
            # Last fallback: local blurred region, but never flat color blocks.
            local = img.crop((max(0, x1 - 8), max(0, y1 - 8), min(w, x2 + 9), min(h, y2 + 9)))
            patch = local.resize((bw, bh), Image.BILINEAR).filter(ImageFilter.GaussianBlur(radius=2))

        work.paste(patch, (x1, y1))

    # Feather only masked boundaries for a cleaner transition.
    soft_mask = mask.filter(ImageFilter.GaussianBlur(radius=2))
    blended = Image.composite(work, img, soft_mask)
    blended = blended.filter(ImageFilter.SMOOTH_MORE)
    blended = blended.filter(ImageFilter.GaussianBlur(radius=0.6))

    erased_path = out_dir / f"{product_id}_erased.png"
    blended.save(erased_path)
    return erased_path


def run_step2_restore(
    rq: RequestQueueClient,
    erased_image: Path,
    product_id: str,
    out_dir: Path,
    restore_model: str,
) -> Optional[Path]:
    restore_prompt = (
        "TASK: Quality enhancement only for ecommerce.\n"
        "Improve sharpness, denoise, correct color/white balance, recover detail.\n"
        "Do NOT change product geometry, material, color, count, branding, or packaging.\n"
        "Do NOT add any new objects or text. Photorealistic only."
    )
    result_bytes = rq.run_image_edit(
        model=restore_model,
        source_image=erased_image,
        prompt=restore_prompt,
        mask_image=None,
    )
    if not result_bytes:
        return None
    final_path = out_dir / f"{product_id}_final.png"
    final_path.write_bytes(result_bytes)
    return final_path


def run_step2_harmonize_model(
    rq: RequestQueueClient,
    erased_image: Path,
    product_id: str,
    out_dir: Path,
    harmonize_model: str,
) -> Optional[Path]:
    """Model-based naturalization pass after deterministic text removal."""
    prompt = (
        "Naturalize the image after text removal. "
        "Fix abrupt patches, color seams, and texture inconsistencies in erased regions. "
        "Keep the exact same product, geometry, material, color, and composition. "
        "Do not add text, logos, new objects, or redesign."
    )
    result_bytes = rq.run_image_edit(
        model=harmonize_model,
        source_image=erased_image,
        prompt=prompt,
        mask_image=None,
    )
    if not result_bytes:
        return None
    harmonized_path = out_dir / f"{product_id}_harmonized.png"
    harmonized_path.write_bytes(result_bytes)
    return harmonized_path


def generate_additional_product_images(
    rq: RequestQueueClient,
    source_for_generation: Path,
    product_id: str,
    out_dir: Path,
    model: str,
    count: int,
    structured_attributes: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Generate additional images of the same product from repaired image.

    Default plan (first 3 images):
    1) alternate angle
    2) matched accessory/styling context
    3) lifestyle human-in-use
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    product_type = ""
    if structured_attributes and isinstance(structured_attributes.get("product_type"), str):
        product_type = structured_attributes.get("product_type", "")
    features: List[str] = []
    if structured_attributes and isinstance(structured_attributes.get("key_features"), list):
        features = [str(x) for x in structured_attributes.get("key_features", [])[:4]]
    feature_text = ", ".join(features)

    base = (
        "Generate an ecommerce image of the SAME product shown in the reference image. "
        "Preserve identity: same shape, color family, material, and branding details. "
        "No text, no watermark, no logo overlays."
    )
    if product_type:
        base += f" Product type: {product_type}."
    if feature_text:
        base += f" Product cues: {feature_text}."

    scenario_prompts = [
        base + " Shot type: alternate angle (3/4 view), clean studio background.",
        base + " Shot type: paired with a suitable accessory/context prop for this product category, still product-focused.",
        base + " Shot type: realistic lifestyle image with a person naturally using the product.",
    ]

    binaries: List[bytes] = []
    for p in scenario_prompts[: max(1, count)]:
        extra = rq.run_image_variants(
            model=model,
            reference_image=source_for_generation,
            prompt=p,
            count=1,
        )
        if extra:
            binaries.extend(extra[:1])

    # Backfill if model returns fewer images than requested.
    backfill_round = 0
    while len(binaries) < count and backfill_round < 6:
        extra = rq.run_image_variants(
            model=model,
            reference_image=source_for_generation,
            prompt=base + " Shot type: clean alternate ecommerce angle.",
            count=1,
        )
        if extra:
            binaries.extend(extra[:1])
        backfill_round += 1
    paths: List[str] = []
    for i, b in enumerate(binaries[:count], start=1):
        p = out_dir / f"{product_id}_extra_{i}.png"
        p.write_bytes(b)
        paths.append(str(p))
    return paths


def run_step2_enhance_local(
    erased_image: Path,
    product_id: str,
    out_dir: Path,
) -> Optional[Path]:
    """Deterministic local quality enhancement (non-generative)."""
    if Image is None or ImageEnhance is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(erased_image).convert("RGB")
    # Mild denoise + contrast + sharpness
    img = img.filter(ImageFilter.MedianFilter(size=3))
    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Sharpness(img).enhance(1.18)
    img = ImageEnhance.Color(img).enhance(1.03)

    final_path = out_dir / f"{product_id}_final.png"
    img.save(final_path)
    return final_path


def run_step3_understand_product(
    chat: ChatClient,
    model: str,
    product_id: str,
    source_image: Path,
    extracted_text: List[str],
) -> Dict[str, Any]:
    if chat.mock:
        return {
            "product_type": "Unknown",
            "category_hint": "General Merchandise",
            "material": "",
            "key_features": extracted_text[:5],
            "size_or_specs": [],
            "brand_or_series": "",
            "confidence": "low",
        }
    text_list = "\n".join(f"- {t}" for t in extracted_text)
    prompt = f"""
You are a product analyst for cross-border ecommerce.
Source language: Chinese (with possible letters/numbers).

Given:
- product_id: {product_id}
- image (original image with text)
- OCR text list:
{text_list}

Task:
Infer structured product attributes with conservative assumptions.
If a brand/series is Chinese-only, output it in Latin letters (pinyin or a reasonable romanization).
Return JSON only with keys:
- product_type (string)
- category_hint (string)
- material (string)
- key_features (string[])
- size_or_specs (string[])
- brand_or_series (string)
- confidence (one of: low, medium, high)
""".strip()
    messages = [
        {"role": "system", "content": "You extract structured product attributes from image + OCR cues."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": path_to_data_url(source_image)}},
            ],
        },
    ]
    return chat.chat_json(model=model, messages=messages, max_tokens=900, temperature=0.1)


def run_step4_generate_copy(
    chat: ChatClient,
    model: str,
    structured_attributes: Dict[str, Any],
    extracted_text: List[str],
) -> Tuple[LocalizedListing, LocalizedListing]:
    if chat.mock:
        en = LocalizedListing(
            title="Mock: Product title in Canadian English",
            description="Mock: Product description in Canadian English based on structured attributes.",
            category="Mock: Category > Subcategory",
            key_attributes={"feature_1": "mock"},
        )
        fr = LocalizedListing(
            title="Maquette : Titre produit en francais canadien",
            description="Maquette : Description produit en francais canadien selon les attributs structures.",
            category="Maquette : Categorie > Sous-categorie",
            key_attributes={"caracteristique_1": "maquette"},
        )
        return en, fr

    attrs_text = json.dumps(structured_attributes, ensure_ascii=False, indent=2)
    ocr_text = "\n".join(f"- {t}" for t in extracted_text[:20])
    prompt = f"""
You are an ecommerce localization copywriter for Canada.
Source language: Chinese. Output languages: Canadian English and Canadian French.

Inputs:
Structured attributes:
{attrs_text}

OCR text clues:
{ocr_text}

Output JSON only:
{{
  "canadian_english": {{
    "title": "string, English only",
    "description": "string, English only",
    "category": "string, English only category path",
    "key_attributes": {{"key":"value"}}
  }},
  "canadian_french": {{
    "title": "string, French only",
    "description": "string, French only",
    "category": "string, French only category path",
    "key_attributes": {{"key":"value"}}
  }}
}}

Rules:
- English block must be English-only.
- French block must be French-only.
- Do not include ANY Chinese characters in output text fields (including brand names).
  If the input contains Chinese brand/series, romanize it (pinyin) or omit it.
- Do not include URLs, seller claims, or watermark-related phrases.
- Be factual and conservative; no invented claims.
""".strip()
    data = chat.chat_json(
        model=model,
        messages=[
            {"role": "system", "content": "You generate factual bilingual ecommerce copy as strict JSON."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=1200,
        temperature=0.3,
    )
    en = build_listing(data, "canadian_english", "English")
    fr = build_listing(data, "canadian_french", "French")
    return en, fr


def parse_json_content(content: Any) -> Dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}
    return {}


def build_listing(payload: Dict[str, Any], key: str, language_name: str) -> LocalizedListing:
    block = payload.get(key, {})
    if not isinstance(block, dict):
        block = {}
    title = block.get("title", f"Localized {language_name} Title")
    description = block.get("description", "")
    category = block.get("category", "")
    attrs = block.get("key_attributes", {})
    if not isinstance(attrs, dict):
        attrs = {}
    return LocalizedListing(
        title=strip_chinese(title) if isinstance(title, str) else f"Localized {language_name} Title",
        description=strip_chinese(description) if isinstance(description, str) else "",
        category=strip_chinese(category) if isinstance(category, str) else "",
        key_attributes={str(k): str(v) for k, v in attrs.items()},
    )


def strip_chinese(text: str) -> str:
    """Remove CJK characters to enforce English/French-only user fields."""
    return "".join(ch for ch in text if not ("\u4e00" <= ch <= "\u9fff"))


def path_to_data_url(path: Path, force_png: bool = False) -> str:
    data = path.read_bytes()
    if force_png:
        mime = "image/png"
    else:
        ext = path.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in {"jpg", "jpeg"} else f"image/{ext or 'png'}"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def extract_media_url(outcome: Dict[str, Any]) -> Optional[str]:
    media_urls = outcome.get("media_urls")
    if isinstance(media_urls, list) and media_urls:
        first = media_urls[0]
        if isinstance(first, dict) and isinstance(first.get("url"), str):
            return first["url"]
        if isinstance(first, str):
            return first
    for key in ["image_url", "preview_image_url", "thumbnail_image_url", "url"]:
        val = outcome.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return None


def write_output(artifacts: Sequence[EcommerceArtifact], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore

            with output_path.open("w", encoding="utf-8") as fp:
                yaml.safe_dump([asdict(a) for a in artifacts], fp, allow_unicode=True, sort_keys=False)
            return output_path
        except ImportError:
            output_path = output_path.with_suffix(".json")
            suffix = ".json"
    if suffix == ".json":
        with output_path.open("w", encoding="utf-8") as fp:
            json.dump([asdict(a) for a in artifacts], fp, ensure_ascii=False, indent=2)
        return output_path
    raise ValueError("Unsupported output format. Use .yaml/.yml/.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_stability_snapshot(
    stats: Dict[str, Any],
    total_target: int,
    run_started_at: str,
) -> Dict[str, Any]:
    processed = int(stats.get("processed", 0))
    snapshot = {
        "run_started_at_utc": run_started_at,
        "updated_at_utc": _utc_now_iso(),
        "total_target": total_target,
        "processed": processed,
        "progress_ratio": round((processed / total_target) if total_target else 0.0, 6),
        "step1_erase_failed": int(stats.get("step1_erase_failed", 0)),
        "step2_restore_failed": int(stats.get("step2_restore_failed", 0)),
        "step3_vision_failed": int(stats.get("step3_vision_failed", 0)),
        "step4_copy_failed": int(stats.get("step4_copy_failed", 0)),
        "step4_copy_fallback_used": int(stats.get("step4_copy_fallback_used", 0)),
        "step4_copy_fallback_failed": int(stats.get("step4_copy_fallback_failed", 0)),
        "overlay_classifier_failed": int(stats.get("overlay_classifier_failed", 0)),
        "annotation_missing": int(stats.get("annotation_missing", 0)),
        "total_warning_events": int(stats.get("total_warning_events", 0)),
        "avg_warning_events_per_item": round(
            (stats.get("total_warning_events", 0) / processed) if processed else 0.0,
            6,
        ),
    }
    clean_items = processed - snapshot["step4_copy_fallback_failed"] - snapshot["step4_copy_failed"]
    snapshot["estimated_clean_item_ratio"] = round((clean_items / processed) if processed else 0.0, 6)
    return snapshot


def _write_stability_reports(snapshot: Dict[str, Any], json_path: Optional[Path], md_path: Optional[Path]) -> None:
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if md_path is not None:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Stability Baseline",
            "",
            f"- Updated (UTC): `{snapshot['updated_at_utc']}`",
            f"- Progress: `{snapshot['processed']}/{snapshot['total_target']}` ({snapshot['progress_ratio']:.2%})",
            f"- Step1 erase failed: `{snapshot['step1_erase_failed']}`",
            f"- Step2 restore failed: `{snapshot['step2_restore_failed']}`",
            f"- Step3 vision failed: `{snapshot['step3_vision_failed']}`",
            f"- Step4 copy failed: `{snapshot['step4_copy_failed']}`",
            f"- Step4 fallback used: `{snapshot['step4_copy_fallback_used']}`",
            f"- Step4 fallback failed: `{snapshot['step4_copy_fallback_failed']}`",
            f"- Overlay classifier failed: `{snapshot['overlay_classifier_failed']}`",
            f"- Annotation missing: `{snapshot['annotation_missing']}`",
            f"- Avg warning events per item: `{snapshot['avg_warning_events_per_item']}`",
            f"- Estimated clean item ratio: `{snapshot['estimated_clean_item_ratio']:.2%}`",
        ]
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text.strip())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "item"


def export_deliverables(artifacts: Sequence[EcommerceArtifact], deliverable_dir: Path) -> Path:
    """Export per-product package: final image + EN/FR descriptions + JSON manifest."""
    deliverable_dir.mkdir(parents=True, exist_ok=True)
    index_rows: List[Dict[str, str]] = []

    for artifact in artifacts:
        product_dir = deliverable_dir / _safe_slug(artifact.product_id)
        product_dir.mkdir(parents=True, exist_ok=True)

        final_image = Path(artifact.final_image_path or artifact.erased_image_path or artifact.source_image_path)
        image_target = product_dir / "product_image.png"
        if final_image.exists():
            image_target.write_bytes(final_image.read_bytes())
        additional_paths: List[str] = []
        for idx, p in enumerate(artifact.additional_generated_images or [], start=1):
            src = Path(p)
            if src.exists():
                dst = product_dir / f"product_image_extra_{idx}.png"
                dst.write_bytes(src.read_bytes())
                additional_paths.append(str(dst))

        en_path = product_dir / "description_en.md"
        fr_path = product_dir / "description_fr.md"
        manifest_path = product_dir / "manifest.json"

        en_text = "\n".join(
            [
                f"# {artifact.canadian_english.title}",
                "",
                f"**Category**: {artifact.canadian_english.category}",
                "",
                artifact.canadian_english.description,
                "",
                "## Key Attributes",
                *[f"- {k}: {v}" for k, v in artifact.canadian_english.key_attributes.items()],
            ]
        )
        fr_text = "\n".join(
            [
                f"# {artifact.canadian_french.title}",
                "",
                f"**Catégorie**: {artifact.canadian_french.category}",
                "",
                artifact.canadian_french.description,
                "",
                "## Attributs clés",
                *[f"- {k}: {v}" for k, v in artifact.canadian_french.key_attributes.items()],
            ]
        )

        en_path.write_text(en_text + "\n", encoding="utf-8")
        fr_path.write_text(fr_text + "\n", encoding="utf-8")
        manifest_path.write_text(json.dumps(asdict(artifact), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        index_rows.append(
            {
                "product_id": artifact.product_id,
                "product_dir": str(product_dir),
                "image_path": str(image_target),
                "english_md": str(en_path),
                "french_md": str(fr_path),
                "manifest_json": str(manifest_path),
                "additional_images": " | ".join(additional_paths),
                "warnings": " | ".join(artifact.warnings),
            }
        )

    index_csv = deliverable_dir / "deliverables_index.csv"
    with index_csv.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "product_id",
                "product_dir",
                "image_path",
                "english_md",
                "french_md",
                "manifest_json",
                "additional_images",
                "warnings",
            ],
        )
        writer.writeheader()
        writer.writerows(index_rows)
    return index_csv


def collect_input_items(args: argparse.Namespace) -> List[Tuple[str, Path, Optional[Path]]]:
    """Return tuples of (product_id, image_path, annotation_path)."""
    txt_dir = Path(args.txt_dir)
    image_dir = Path(args.image_dir)

    items: List[Tuple[str, Path, Optional[Path]]] = []
    if args.input_image:
        img = Path(args.input_image)
        if not img.exists():
            raise FileNotFoundError(f"Input image not found: {img}")
        ann_candidates = [txt_dir / f"{img.name}.txt"]
        # Common MTWI quirk: images may be named like *.jpg.jpg while labels are *.jpg.txt
        if img.name.lower().endswith(".jpg.jpg"):
            ann_candidates.append(txt_dir / f"{img.name[:-4]}.txt")  # strip the last ".jpg"
        if img.name.lower().endswith(".png.png"):
            ann_candidates.append(txt_dir / f"{img.name[:-4]}.txt")
        ann = next((p for p in ann_candidates if p.exists()), None)
        items.append((img.stem, img, ann))
    elif args.input_images_glob:
        candidates = sorted(image_dir.glob(args.input_images_glob))
        for img in candidates:
            if not img.is_file():
                continue
            ann_candidates = [txt_dir / f"{img.name}.txt"]
            if img.name.lower().endswith(".jpg.jpg"):
                ann_candidates.append(txt_dir / f"{img.name[:-4]}.txt")
            if img.name.lower().endswith(".png.png"):
                ann_candidates.append(txt_dir / f"{img.name[:-4]}.txt")
            ann = next((p for p in ann_candidates if p.exists()), None)
            items.append((img.stem, img, ann))
    else:
        annotation_files = sorted(txt_dir.glob("*.txt"))
        for ann in annotation_files:
            source_image = resolve_image_path(image_dir, ann)
            if source_image:
                items.append((ann.stem, source_image, ann))

    if args.limit:
        items = items[: args.limit]
    if not items:
        raise RuntimeError("No valid input items found. Check --txt-dir/--image-dir or input-image flags.")
    return items


def run_pipeline(args: argparse.Namespace) -> Path:
    api_key = os.getenv("GMI_API_KEY")
    if not args.mock and not api_key:
        raise RuntimeError("GMI_API_KEY is required in live mode.")

    rq = RequestQueueClient(api_key=api_key or "mock", mock=args.mock, max_attempts=args.max_attempts)
    chat = ChatClient(api_key=api_key or "mock", mock=args.mock, max_attempts=args.max_attempts)
    input_items = collect_input_items(args)
    run_started_at = _utc_now_iso()
    stability_stats: Dict[str, Any] = {
        "processed": 0,
        "step1_erase_failed": 0,
        "step2_restore_failed": 0,
        "step3_vision_failed": 0,
        "step4_copy_failed": 0,
        "step4_copy_fallback_used": 0,
        "step4_copy_fallback_failed": 0,
        "overlay_classifier_failed": 0,
        "annotation_missing": 0,
        "total_warning_events": 0,
    }
    stability_json_path = Path(args.stability_report_path) if args.stability_report_path else None
    stability_md_path = Path(args.stability_markdown_path) if args.stability_markdown_path else None
    stability_update_every = max(1, int(args.stability_update_every))

    work_img_dir = Path(args.image_output_dir)
    work_img_dir.mkdir(parents=True, exist_ok=True)

    artifacts: List[EcommerceArtifact] = []
    for product_id, source_image, ann in input_items:
        warnings: List[str] = []
        spans = parse_annotation_file(ann) if ann is not None else []
        extracted_raw = [s.text for s in spans if s.text]
        extracted = clean_extracted_text(extracted_raw)
        if ann is None:
            warnings.append("annotation_missing: ran image-only mode without quad annotations")
            stability_stats["annotation_missing"] += 1

        erased_path: Optional[Path] = None
        final_path: Optional[Path] = None

        erased_span_indices: Optional[List[int]] = None
        spans_for_mask = spans
        if not args.disable_erase and (not args.no_mask) and args.mask_mode in {"overlay", "all"}:
            erased_span_indices = select_spans_to_erase(
                chat=chat,
                vision_model=args.vision_model,
                source_image=source_image,
                spans=spans,
                warnings=warnings,
                mode=args.mask_mode,
            )
            spans_for_mask = [spans[i] for i in erased_span_indices] if erased_span_indices is not None else spans

        if not args.disable_erase:
            try:
                if args.erase_strategy == "local":
                    erased_path = run_step1_text_erase_local(
                        source_image=source_image,
                        spans=spans_for_mask,
                        product_id=product_id,
                        out_dir=work_img_dir,
                        use_mask=not bool(args.no_mask),
                    )
                else:
                    erased_path = run_step1_text_erase(
                        rq=rq,
                        source_image=source_image,
                        spans=spans_for_mask,
                        product_id=product_id,
                        out_dir=work_img_dir,
                        eraser_model=args.eraser_model,
                        use_mask=not bool(args.no_mask),
                    )
            except Exception as exc:
                warnings.append(f"step1_erase_failed: {exc}")
                stability_stats["step1_erase_failed"] += 1
        if not erased_path:
            erased_path = source_image

        # Optional naturalization pass to fix local erase artifacts.
        if args.harmonize_after_erase:
            try:
                harmonized = run_step2_harmonize_model(
                    rq=rq,
                    erased_image=erased_path,
                    product_id=product_id,
                    out_dir=work_img_dir,
                    harmonize_model=args.harmonize_model,
                )
                if harmonized is not None:
                    erased_path = harmonized
            except Exception as exc:
                warnings.append(f"step2_harmonize_failed: {exc}")

        if not args.disable_restore:
            try:
                if args.quality_strategy == "local":
                    final_path = run_step2_enhance_local(
                        erased_image=erased_path,
                        product_id=product_id,
                        out_dir=work_img_dir,
                    )
                else:
                    final_path = run_step2_restore(
                        rq=rq,
                        erased_image=erased_path,
                        product_id=product_id,
                        out_dir=work_img_dir,
                        restore_model=args.restore_model,
                    )
            except Exception as exc:
                warnings.append(f"step2_restore_failed: {exc}")
                stability_stats["step2_restore_failed"] += 1
        if not final_path:
            final_path = erased_path

        try:
            structured = run_step3_understand_product(
                chat=chat,
                model=args.vision_model,
                product_id=product_id,
                source_image=source_image,
                extracted_text=extracted,
            )
        except Exception as exc:
            warnings.append(f"step3_vision_failed: {exc}")
            stability_stats["step3_vision_failed"] += 1
            structured = {
                "product_type": "Unknown",
                "category_hint": "General Merchandise",
                "material": "",
                "key_features": extracted[:5],
                "size_or_specs": [],
                "brand_or_series": "",
                "confidence": "low",
            }

        try:
            en, fr = run_step4_generate_copy(
                chat=chat,
                model=args.qwen_model,
                structured_attributes=structured,
                extracted_text=extracted,
            )
        except Exception as exc:
            warnings.append(f"step4_copy_failed: {exc}")
            stability_stats["step4_copy_failed"] += 1
            try:
                en, fr = run_step4_generate_copy(
                    chat=chat,
                    model=args.fallback_text_model,
                    structured_attributes=structured,
                    extracted_text=extracted,
                )
                warnings.append(f"step4_copy_fallback_model_used: {args.fallback_text_model}")
                stability_stats["step4_copy_fallback_used"] += 1
            except Exception as fallback_exc:
                warnings.append(f"step4_copy_fallback_failed: {fallback_exc}")
                stability_stats["step4_copy_fallback_failed"] += 1
                en = LocalizedListing(
                    title="Fallback: Product title in Canadian English",
                    description="Fallback: Could not generate final English copy from model response.",
                    category="Fallback: Category > Subcategory",
                    key_attributes={"status": "fallback"},
                )
                fr = LocalizedListing(
                    title="Repli : Titre produit en francais canadien",
                    description="Repli : Impossible de generer la copie francaise finale depuis le modele.",
                    category="Repli : Categorie > Sous-categorie",
                    key_attributes={"etat": "repli"},
                )

        additional_images: List[str] = []
        if args.generate_additional_images:
            try:
                additional_images = generate_additional_product_images(
                    rq=rq,
                    source_for_generation=final_path,
                    product_id=product_id,
                    out_dir=work_img_dir,
                    model=args.additional_image_model,
                    count=args.additional_image_count,
                    structured_attributes=structured,
                )
            except Exception as exc:
                warnings.append(f"extra_images_failed: {exc}")

        artifacts.append(
            EcommerceArtifact(
                product_id=product_id,
                source_image_path=str(source_image),
                erased_image_path=str(erased_path) if erased_path and erased_path != source_image else None,
                final_image_path=str(final_path) if final_path else None,
                extracted_text=extracted,
                structured_attributes=structured,
                canadian_english=en,
                canadian_french=fr,
                warnings=warnings,
                erased_spans=(
                    [
                        {"index": i, "text": spans[i].text, "bbox": list(span_bbox(spans[i]))}
                        for i in (erased_span_indices or [])
                    ]
                    if erased_span_indices is not None
                    else None
                ),
                additional_generated_images=additional_images or None,
            )
        )
        # Stability baseline aggregation
        stability_stats["processed"] += 1
        stability_stats["total_warning_events"] += len(warnings)
        for w in warnings:
            if w.startswith("overlay_classifier_failed:"):
                stability_stats["overlay_classifier_failed"] += 1
        if (stability_stats["processed"] % stability_update_every) == 0:
            snap = _build_stability_snapshot(stability_stats, len(input_items), run_started_at)
            _write_stability_reports(snap, stability_json_path, stability_md_path)
            print(
                f"[stability] processed={snap['processed']}/{snap['total_target']} "
                f"step4_failed={snap['step4_copy_failed']} fallback_used={snap['step4_copy_fallback_used']}"
            )

    out_path = write_output(artifacts, Path(args.output))
    final_snap = _build_stability_snapshot(stability_stats, len(input_items), run_started_at)
    _write_stability_reports(final_snap, stability_json_path, stability_md_path)
    if args.export_deliverables:
        index_csv = export_deliverables(artifacts, Path(args.deliverable_dir))
        print(f"Saved deliverable packages index to {index_csv}")
    return out_path


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MTWI agent.md-aligned workflow pipeline")
    parser.add_argument("--txt-dir", default="data/mtwi_train/txt_train", help="MTWI annotation directory")
    parser.add_argument("--image-dir", default="data/mtwi_train/image_train", help="MTWI image directory")
    parser.add_argument("--input-image", default=None, help="Single input image path. If set, overrides directory scan.")
    parser.add_argument(
        "--input-images-glob",
        default=None,
        help="Glob pattern under --image-dir (e.g., '*.jpg*'). If set, overrides txt-dir scan.",
    )
    parser.add_argument("--output", default="outputs/mtwi_ecommerce_samples.yaml", help="Output .yaml/.json path")
    parser.add_argument("--image-output-dir", default="outputs/mtwi_images", help="Output directory for edited images")
    parser.add_argument(
        "--export-deliverables",
        action="store_true",
        help="Export per-product package folder with image + EN/FR descriptions + manifest.",
    )
    parser.add_argument(
        "--deliverable-dir",
        default="outputs/deliverables",
        help="Output directory for packaged deliverables.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of products")
    parser.add_argument("--mock", action="store_true", help="Run in mock mode")
    parser.add_argument("--max-attempts", type=int, default=2, help="Max retry attempts per external model/API call.")
    parser.add_argument(
        "--stability-update-every",
        type=int,
        default=100,
        help="Update stability baseline report every N processed items.",
    )
    parser.add_argument(
        "--stability-report-path",
        default=None,
        help="Optional path to write live stability JSON report.",
    )
    parser.add_argument(
        "--stability-markdown-path",
        default=None,
        help="Optional path to write live stability Markdown report.",
    )

    parser.add_argument("--disable-erase", action="store_true", help="Disable step1 text erase")
    parser.add_argument("--disable-restore", action="store_true", help="Disable step2 quality restore")
    parser.add_argument("--no-mask", action="store_true", help="Do not use quad mask in erase step")
    parser.add_argument(
        "--erase-strategy",
        choices=["local", "model"],
        default=os.getenv("GMI_ERASE_STRATEGY", "local"),
        help="Text erase strategy: local deterministic (recommended) or model-based.",
    )
    parser.add_argument(
        "--quality-strategy",
        choices=["local", "model"],
        default=os.getenv("GMI_QUALITY_STRATEGY", "local"),
        help="Quality enhancement strategy: local deterministic or model-based restore.",
    )
    parser.add_argument(
        "--harmonize-after-erase",
        action="store_true",
        default=True,
        help="Run model-based naturalization pass after text removal.",
    )
    parser.add_argument(
        "--no-harmonize-after-erase",
        action="store_true",
        help="Disable model-based naturalization after text removal.",
    )
    parser.add_argument(
        "--harmonize-model",
        default=os.getenv("GMI_HARMONIZE_MODEL", "bria-fibo-edit"),
        help="Model used to harmonize artifact regions after text removal.",
    )
    parser.add_argument(
        "--generate-additional-images",
        action="store_true",
        help="Generate additional same-product images from repaired image.",
    )
    parser.add_argument(
        "--additional-image-model",
        default=os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
        help="Extra model for additional product image generation.",
    )
    parser.add_argument(
        "--additional-image-count",
        type=int,
        default=3,
        help="How many additional product images to generate.",
    )
    parser.add_argument(
        "--mask-mode",
        default=os.getenv("GMI_MASK_MODE", "overlay"),
        choices=["overlay", "all"],
        help="When using mask, choose which text boxes to erase: overlay-only (keep product-printed text) or all.",
    )

    parser.add_argument("--eraser-model", default=os.getenv("GMI_ERASER_MODEL", "bria-eraser"), help="Step1 model")
    parser.add_argument("--restore-model", default=os.getenv("GMI_RESTORE_MODEL", "bria-fibo-restore"), help="Step2 model")
    parser.add_argument(
        "--vision-model",
        default=os.getenv("GMI_VISION_MODEL", "openai/gpt-4o"),
        help="Step3 model (vision-capable model in your account, e.g. openai/gpt-4o)",
    )
    parser.add_argument("--qwen-model", default=os.getenv("GMI_QWEN_MODEL", "Qwen/Qwen3.5-27B"), help="Step4 Qwen text model")
    parser.add_argument(
        "--fallback-text-model",
        default=os.getenv("GMI_FALLBACK_TEXT_MODEL", "openai/gpt-4o-mini"),
        help="Fallback text model if Qwen call fails.",
    )
    args = parser.parse_args(argv)
    if args.no_harmonize_after_erase:
        args.harmonize_after_erase = False
    return args


if __name__ == "__main__":
    args = parse_args()
    output_path = run_pipeline(args)
    print(f"Saved ecommerce artifacts to {output_path}")

