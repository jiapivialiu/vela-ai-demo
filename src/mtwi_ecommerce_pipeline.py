"""MTWI → ecommerce pipeline (GMI Cloud Inference Engine).

Flow: text removal (local and/or Request Queue) → optional harmonize → quality (local and/or model) →
vision (default Qwen/Qwen3-VL-235B) → EN/FR copy (default gpt-5.4-pro / claude-sonnet-4.6) →
mandatory bilingual vision copy review + mandatory locale grammar review → optional extra images.

See agent.md for model table and per-SKU chat call counts; src/README.md for CLI/bulk; CONFIGURATION.md for GMI_* env vars.
Run: `python src/mtwi_ecommerce_pipeline.py --help` or `scripts/run_one_deliverable_example.sh`.
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
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

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
    copy_review: Optional[Dict[str, Any]] = None
    locale_grammar_review: Optional[Dict[str, Any]] = None
    user_copy_instructions: str = ""
    user_image_instructions: str = ""


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


def resolve_operator_instructions(
    inline: str,
    file_path: Optional[str],
    max_chars: int,
    warnings: Optional[List[str]] = None,
) -> str:
    """Resolve user-facing instructions: optional file overrides inline when file exists."""
    inline = (inline or "").strip()
    chosen = inline
    if file_path and str(file_path).strip():
        p = Path(file_path).expanduser()
        if p.is_file():
            chosen = p.read_text(encoding="utf-8").strip()
        elif warnings is not None:
            warnings.append(f"user_instructions_file_missing: {p}")
    if len(chosen) > max_chars:
        chosen = chosen[:max_chars] + "\n...[truncated by pipeline]"
    return chosen


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
    user_image_instructions: str = "",
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
    if user_image_instructions.strip():
        edit_prompt += (
            "\n\nOperator image preferences (must not override identity-preservation rules above):\n"
            + user_image_instructions.strip()
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
    user_image_instructions: str = "",
) -> Optional[Path]:
    restore_prompt = (
        "TASK: Quality enhancement only for ecommerce.\n"
        "Improve sharpness, denoise, correct color/white balance, recover detail.\n"
        "Do NOT change product geometry, material, color, count, branding, or packaging.\n"
        "Do NOT add any new objects or text. Photorealistic only."
    )
    if user_image_instructions.strip():
        restore_prompt += (
            "\n\nOperator preferences for overall look (lighting, color mood, background feel; no new objects):\n"
            + user_image_instructions.strip()
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
    user_image_instructions: str = "",
) -> Optional[Path]:
    """Model-based naturalization pass after deterministic text removal."""
    prompt = (
        "Naturalize the image after text removal. "
        "Fix abrupt patches, color seams, and texture inconsistencies in erased regions. "
        "Keep the exact same product, geometry, material, color, and composition. "
        "Do not add text, logos, new objects, or redesign."
    )
    if user_image_instructions.strip():
        prompt += " Operator preferences (subtle adjustments only, same product): " + user_image_instructions.strip()
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
    user_image_instructions: str = "",
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
    if user_image_instructions.strip():
        base += (
            " Operator style requirements for shots (background, lighting, mood; keep same product identity): "
            + user_image_instructions.strip()
        )

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


def _step4_operator_block(user_copy_instructions: str) -> str:
    if not user_copy_instructions.strip():
        return ""
    return f"""
Operator requirements for tone, audience, bullets, length, SEO keywords, or brand voice (apply only when compatible with facts; do not invent product claims; output text must match the requested language only, with no Chinese):
{user_copy_instructions.strip()}
"""


def run_step4_generate_copy_language(
    chat: ChatClient,
    model: str,
    structured_attributes: Dict[str, Any],
    extracted_text: List[str],
    user_copy_instructions: str,
    target: Literal["canadian_english", "canadian_french"],
) -> LocalizedListing:
    """Generate one locale listing (Canadian English or Canadian French) with a dedicated model."""
    lang_name = "Canadian English" if target == "canadian_english" else "Canadian French (français canadien)"
    listing_key = target
    if chat.mock:
        if target == "canadian_english":
            return LocalizedListing(
                title="Mock: Product title in Canadian English",
                description="Mock: Product description in Canadian English based on structured attributes.",
                category="Mock: Category > Subcategory",
                key_attributes={"feature_1": "mock"},
            )
        return LocalizedListing(
            title="Maquette : Titre produit en francais canadien",
            description="Maquette : Description produit en francais canadien selon les attributs structures.",
            category="Maquette : Categorie > Sous-categorie",
            key_attributes={"caracteristique_1": "maquette"},
        )

    attrs_text = json.dumps(structured_attributes, ensure_ascii=False, indent=2)
    ocr_text = "\n".join(f"- {t}" for t in extracted_text[:20])
    operator_block = _step4_operator_block(user_copy_instructions)
    prompt = f"""
You are an ecommerce localization copywriter for Canada.
Source language: Chinese. Target output language: {lang_name} only.

Inputs:
Structured attributes:
{attrs_text}

OCR text clues:
{ocr_text}
{operator_block}
Output JSON only with a single top-level key "{listing_key}":
{{
  "{listing_key}": {{
    "title": "string",
    "description": "string",
    "category": "string category path",
    "key_attributes": {{"key":"value"}}
  }}
}}

Rules:
- All string values in that object must be in the target language only ({lang_name}).
- Do not include ANY Chinese characters in output text fields (including brand names).
  If the input contains Chinese brand/series, romanize it (pinyin) or omit it.
- Do not include URLs, seller claims, or watermark-related phrases.
- Be factual and conservative; no invented claims.
""".strip()
    data = chat.chat_json(
        model=model,
        messages=[
            {
                "role": "system",
                "content": f"You generate factual {lang_name} ecommerce copy as strict JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=900,
        temperature=0.3,
    )
    label = "English" if target == "canadian_english" else "French"
    return build_listing(data if isinstance(data, dict) else {}, listing_key, label)


def _normalize_copy_review(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure expected keys exist for downstream YAML / UI."""
    status = str(raw.get("overall_status", "")).strip().lower()
    if status not in {"pass", "revise", "fail"}:
        status = "revise" if raw else "pass"
    out: Dict[str, Any] = {
        "overall_status": status,
        "summary": str(raw.get("summary", "")).strip() or "No summary returned.",
        "exaggeration_findings": _as_str_list(raw.get("exaggeration_findings")),
        "attribute_conflicts": _as_str_list(raw.get("attribute_conflicts")),
        "image_visual_mismatches": _as_str_list(raw.get("image_visual_mismatches")),
        "en_revision_suggestions": str(raw.get("en_revision_suggestions", "")).strip(),
        "fr_revision_suggestions": str(raw.get("fr_revision_suggestions", "")).strip(),
        "scores": {},
    }
    scores = raw.get("scores")
    if isinstance(scores, dict):
        for k in ("grounding", "factual_tone"):
            v = scores.get(k)
            if isinstance(v, (int, float)):
                out["scores"][k] = float(max(0.0, min(1.0, float(v))))
    return out


def _as_str_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def format_copy_review_markdown(review: Dict[str, Any]) -> str:
    lines = [
        "# Copy quality review",
        "",
        f"**Status**: `{review.get('overall_status', '')}`",
        f"**Summary**: {review.get('summary', '')}",
        "",
        "## Scores",
    ]
    scores = review.get("scores") or {}
    if isinstance(scores, dict) and scores:
        for k, v in scores.items():
            lines.append(f"- **{k}**: {v}")
    else:
        lines.append("- _(none)_")
    lines.extend(["", "## Exaggeration / hype findings", ""])
    for item in review.get("exaggeration_findings") or []:
        lines.append(f"- {item}")
    if not review.get("exaggeration_findings"):
        lines.append("- _(none)_")
    lines.extend(["", "## Conflicts with structured attributes", ""])
    for item in review.get("attribute_conflicts") or []:
        lines.append(f"- {item}")
    if not review.get("attribute_conflicts"):
        lines.append("- _(none)_")
    lines.extend(["", "## Image / visual mismatches", ""])
    for item in review.get("image_visual_mismatches") or []:
        lines.append(f"- {item}")
    if not review.get("image_visual_mismatches"):
        lines.append("- _(none)_")
    lines.extend(
        [
            "",
            "## Suggested fixes (EN)",
            "",
            review.get("en_revision_suggestions") or "_(none)_",
            "",
            "## Suggested fixes (FR)",
            "",
            review.get("fr_revision_suggestions") or "_(none)_",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _merge_copy_reviews_bilingual(en_rev: Dict[str, Any], fr_rev: Dict[str, Any]) -> Dict[str, Any]:
    """Combine English-only and French-only vision audits into one manifest-friendly object."""
    a = _normalize_copy_review(en_rev)
    b = _normalize_copy_review(fr_rev)

    def _rank(st: str) -> int:
        s = str(st).lower()
        return {"fail": 2, "revise": 1, "pass": 0}.get(s, 1)

    ra, rb = _rank(a["overall_status"]), _rank(b["overall_status"])
    overall = "fail" if max(ra, rb) == 2 else ("revise" if max(ra, rb) == 1 else "pass")

    def _merge_lists(x: List[str], y: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in x + y:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    sa = str(a.get("summary", "")).strip()
    sb = str(b.get("summary", "")).strip()
    _parts: List[str] = []
    if sa:
        _parts.append(f"EN: {sa}")
    if sb:
        _parts.append(f"FR: {sb}")
    summary = " | ".join(_parts) if _parts else "Combined EN+FR copy review."

    scores_a = a.get("scores") if isinstance(a.get("scores"), dict) else {}
    scores_b = b.get("scores") if isinstance(b.get("scores"), dict) else {}
    g = min(
        float(scores_a.get("grounding", 0.5)) if scores_a else 0.5,
        float(scores_b.get("grounding", 0.5)) if scores_b else 0.5,
    )
    f = min(
        float(scores_a.get("factual_tone", 0.5)) if scores_a else 0.5,
        float(scores_b.get("factual_tone", 0.5)) if scores_b else 0.5,
    )

    return _normalize_copy_review(
        {
            "overall_status": overall,
            "summary": summary or "Combined EN+FR copy review.",
            "exaggeration_findings": _merge_lists(
                _as_str_list(a.get("exaggeration_findings")),
                _as_str_list(b.get("exaggeration_findings")),
            ),
            "attribute_conflicts": _merge_lists(
                _as_str_list(a.get("attribute_conflicts")),
                _as_str_list(b.get("attribute_conflicts")),
            ),
            "image_visual_mismatches": _merge_lists(
                _as_str_list(a.get("image_visual_mismatches")),
                _as_str_list(b.get("image_visual_mismatches")),
            ),
            "en_revision_suggestions": str(a.get("en_revision_suggestions", "")).strip(),
            "fr_revision_suggestions": str(b.get("fr_revision_suggestions", "")).strip(),
            "scores": {"grounding": g, "factual_tone": f},
        }
    )


def _run_step4b_review_copy_one_language(
    chat: ChatClient,
    model: str,
    product_id: str,
    source_image: Path,
    structured_attributes: Dict[str, Any],
    listing: LocalizedListing,
    language_name: str,
    audit_english: bool,
    user_copy_instructions: str,
) -> Dict[str, Any]:
    """Vision audit for a single locale; returns full-schema dict (other language suggestions empty)."""
    attrs_text = json.dumps(structured_attributes, ensure_ascii=False, indent=2)
    listing_text = json.dumps(
        {"canadian_english" if audit_english else "canadian_french": asdict(listing)},
        ensure_ascii=False,
        indent=2,
    )
    prompt = f"""
You are a quality reviewer for Canadian ecommerce listings.
product_id: {product_id}

Focus: audit ONLY the {language_name} listing below (not the other language).
You see the ORIGINAL listing image. Structured attributes were inferred from image + OCR (may be incomplete).

Structured attributes:
{attrs_text}

Generated listing ({language_name} only):
{listing_text}
"""
    if user_copy_instructions.strip():
        prompt += f"""

Operator copy/style requirements the listing was asked to follow (check whether the text satisfies these without inventing facts against the image):
{user_copy_instructions.strip()}
"""
    prompt += """

Return JSON only with keys:
- overall_status: one of "pass", "revise", "fail" for THIS language listing vs image + attributes
- summary: one short sentence for operators (English is OK)
- exaggeration_findings: string[]
- attribute_conflicts: string[]
- image_visual_mismatches: string[]
- en_revision_suggestions: string — bullet-style fixes for English (empty if this audit is not English)
- fr_revision_suggestions: string — bullet-style fixes for French (empty if this audit is not French)
- scores: object with "grounding" and "factual_tone" each 0.0-1.0

Rules:
- If structured confidence is "low", flag only clear inventions.
- Do not output Chinese characters.
- For the language you are NOT auditing, set that language's *_revision_suggestions to an empty string.
""".strip()
    messages = [
        {
            "role": "system",
            "content": "You audit ecommerce copy for factual grounding vs image and structured attributes. Output strict JSON only.",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": path_to_data_url(source_image)}},
            ],
        },
    ]
    raw = chat.chat_json(model=model, messages=messages, max_tokens=1000, temperature=0.1)
    if not raw:
        return _normalize_copy_review(
            {
                "overall_status": "revise",
                "summary": f"{language_name} copy review returned empty JSON.",
                "exaggeration_findings": [],
                "attribute_conflicts": [],
                "image_visual_mismatches": [],
                "en_revision_suggestions": "" if not audit_english else "",
                "fr_revision_suggestions": "" if audit_english else "",
                "scores": {},
            }
        )
    return _normalize_copy_review(raw if isinstance(raw, dict) else {})


def run_step4b_review_copy_bilingual(
    chat: ChatClient,
    model_english: str,
    model_french: str,
    product_id: str,
    source_image: Path,
    structured_attributes: Dict[str, Any],
    en: LocalizedListing,
    fr: LocalizedListing,
    user_copy_instructions: str = "",
) -> Tuple[Dict[str, Any], bool]:
    """Two vision JSON calls (EN + FR reviewers); returns (merged_review, any_side_failed)."""
    if chat.mock:
        return (
            _normalize_copy_review(
                {
                    "overall_status": "pass",
                    "summary": "Mock mode: copy review placeholder pass (EN+FR).",
                    "exaggeration_findings": [],
                    "attribute_conflicts": [],
                    "image_visual_mismatches": [],
                    "en_revision_suggestions": "",
                    "fr_revision_suggestions": "",
                    "scores": {"grounding": 1.0, "factual_tone": 1.0},
                }
            ),
            False,
        )

    failed = False
    try:
        en_raw = _run_step4b_review_copy_one_language(
            chat,
            model_english,
            product_id,
            source_image,
            structured_attributes,
            en,
            "Canadian English",
            audit_english=True,
            user_copy_instructions=user_copy_instructions,
        )
    except Exception:
        failed = True
        en_raw = _normalize_copy_review(
            {
                "overall_status": "revise",
                "summary": "English copy reviewer call failed.",
                "exaggeration_findings": [],
                "attribute_conflicts": [],
                "image_visual_mismatches": [],
                "en_revision_suggestions": "",
                "fr_revision_suggestions": "",
                "scores": {},
            }
        )
    try:
        fr_raw = _run_step4b_review_copy_one_language(
            chat,
            model_french,
            product_id,
            source_image,
            structured_attributes,
            fr,
            "Canadian French",
            audit_english=False,
            user_copy_instructions=user_copy_instructions,
        )
    except Exception:
        failed = True
        fr_raw = _normalize_copy_review(
            {
                "overall_status": "revise",
                "summary": "French copy reviewer call failed.",
                "exaggeration_findings": [],
                "attribute_conflicts": [],
                "image_visual_mismatches": [],
                "en_revision_suggestions": "",
                "fr_revision_suggestions": "",
                "scores": {},
            }
        )
    merged = _merge_copy_reviews_bilingual(en_raw, fr_raw)
    return merged, failed


def _normalize_locale_grammar_block(raw: Dict[str, Any]) -> Dict[str, Any]:
    st = str(raw.get("status", "pass")).strip().lower()
    if st not in {"pass", "revise", "fail"}:
        st = "revise" if raw else "pass"
    return {
        "status": st,
        "issues": _as_str_list(raw.get("issues")),
        "suggested_edits": str(raw.get("suggested_edits", "")).strip(),
        "notes": str(raw.get("notes", "")).strip(),
    }


def format_locale_grammar_markdown(review: Dict[str, Any]) -> str:
    lines = ["# Canadian locale grammar review (EN + FR)", ""]
    for key, title in (
        ("canadian_english", "## Canadian English (en-CA)"),
        ("canadian_french", "## Canadian French (français canadien)"),
    ):
        block = review.get(key) if isinstance(review, dict) else None
        if not isinstance(block, dict):
            block = {}
        lines.append(title)
        lines.append("")
        lines.append(f"**Status**: `{block.get('status', '')}`")
        lines.append("")
        lines.append(block.get("notes") or "_(none)_")
        lines.append("")
        lines.append("### Issues")
        issues = block.get("issues") or []
        if issues:
            for it in issues:
                lines.append(f"- {it}")
        else:
            lines.append("- _(none)_")
        lines.append("")
        lines.append("### Suggested edits")
        lines.append(block.get("suggested_edits") or "_(none)_")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_step4c_locale_grammar_review(
    chat: ChatClient,
    model_english: str,
    model_french: str,
    en: LocalizedListing,
    fr: LocalizedListing,
) -> Dict[str, Any]:
    """Two text-only reviewers: Canadian English and Canadian French grammar/conventions (separate models)."""
    if chat.mock:
        ok = _normalize_locale_grammar_block(
            {"status": "pass", "issues": [], "suggested_edits": "", "notes": "Mock: skipped."}
        )
        return {"canadian_english": ok, "canadian_french": {**ok}}

    en_json = json.dumps(asdict(en), ensure_ascii=False, indent=2)
    en_prompt = f"""
You are a Canadian English (en-CA) copy editor for ecommerce listings in Canada.

Listing fields (JSON):
{en_json}

Task:
- Fix nothing in place; only analyze.
- Check grammar, spelling, punctuation, hyphenation, and Canadian English conventions (e.g. Canadian spelling where appropriate, consistent terminology).
- Do not alter factual product claims; flag if a wording implies unproven facts.

Return JSON only:
{{
  "status": "pass" | "revise" | "fail",
  "issues": string[],
  "suggested_edits": string (concise bullet-style corrections; empty if none),
  "notes": string (one short summary for operators)
}}
Rules:
- Output JSON keys in English.
- issues entries should be short and specific (field + problem).
""".strip()

    en_raw = chat.chat_json(
        model=model_english,
        messages=[
            {"role": "system", "content": "You are a Canadian English editor. Output strict JSON only."},
            {"role": "user", "content": en_prompt},
        ],
        max_tokens=900,
        temperature=0.1,
    )
    en_block = _normalize_locale_grammar_block(en_raw if isinstance(en_raw, dict) else {})

    fr_json = json.dumps(asdict(fr), ensure_ascii=False, indent=2)
    fr_prompt = f"""
You are a Canadian French (français canadien) copy editor for ecommerce listings in Canada.

Listing fields (JSON):
{fr_json}

Task:
- Fix nothing in place; only analyze.
- Check grammar, spelling, agreement, punctuation, and Canadian French usage where it differs from France French (e.g. vocabulary and register appropriate for Canadian market; avoid European-only terms when a common Canadian form exists).
- Do not alter factual product claims.

Return JSON only:
{{
  "status": "pass" | "revise" | "fail",
  "issues": string[],
  "suggested_edits": string (concise bullet-style corrections; empty if none),
  "notes": string (one short summary for operators; French is OK in notes)
}}
Rules:
- JSON keys must remain in English as shown.
- issues can be written in French.
""".strip()

    fr_raw = chat.chat_json(
        model=model_french,
        messages=[
            {"role": "system", "content": "You are a Canadian French editor. Output strict JSON only."},
            {"role": "user", "content": fr_prompt},
        ],
        max_tokens=900,
        temperature=0.1,
    )
    fr_block = _normalize_locale_grammar_block(fr_raw if isinstance(fr_raw, dict) else {})

    return {"canadian_english": en_block, "canadian_french": fr_block}


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
        "step4b_copy_review_failed": int(stats.get("step4b_copy_review_failed", 0)),
        "copy_review_fail": int(stats.get("copy_review_fail", 0)),
        "copy_review_revise": int(stats.get("copy_review_revise", 0)),
        "step4c_locale_grammar_failed": int(stats.get("step4c_locale_grammar_failed", 0)),
        "locale_grammar_fail": int(stats.get("locale_grammar_fail", 0)),
        "locale_grammar_revise": int(stats.get("locale_grammar_revise", 0)),
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
            f"- Step4b copy review failed: `{snapshot['step4b_copy_review_failed']}`",
            f"- Copy review status fail: `{snapshot['copy_review_fail']}`",
            f"- Copy review status revise: `{snapshot['copy_review_revise']}`",
            f"- Step4c locale grammar failed: `{snapshot['step4c_locale_grammar_failed']}`",
            f"- Locale grammar fail (EN or FR): `{snapshot['locale_grammar_fail']}`",
            f"- Locale grammar revise (EN or FR): `{snapshot['locale_grammar_revise']}`",
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
        review_md_path = product_dir / "copy_review.md"
        review_md_str = ""
        if artifact.copy_review:
            review_md_path.write_text(
                format_copy_review_markdown(artifact.copy_review),
                encoding="utf-8",
            )
            review_md_str = str(review_md_path)

        locale_md_path = product_dir / "locale_grammar_review.md"
        locale_md_str = ""
        if artifact.locale_grammar_review:
            locale_md_path.write_text(
                format_locale_grammar_markdown(artifact.locale_grammar_review),
                encoding="utf-8",
            )
            locale_md_str = str(locale_md_path)

        index_rows.append(
            {
                "product_id": artifact.product_id,
                "product_dir": str(product_dir),
                "image_path": str(image_target),
                "english_md": str(en_path),
                "french_md": str(fr_path),
                "manifest_json": str(manifest_path),
                "copy_review_md": review_md_str,
                "locale_grammar_md": locale_md_str,
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
                "copy_review_md",
                "locale_grammar_md",
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
        "step4b_copy_review_failed": 0,
        "copy_review_fail": 0,
        "copy_review_revise": 0,
        "step4c_locale_grammar_failed": 0,
        "locale_grammar_fail": 0,
        "locale_grammar_revise": 0,
    }
    stability_json_path = Path(args.stability_report_path) if args.stability_report_path else None
    stability_md_path = Path(args.stability_markdown_path) if args.stability_markdown_path else None
    stability_update_every = max(1, int(args.stability_update_every))

    work_img_dir = Path(args.image_output_dir)
    work_img_dir.mkdir(parents=True, exist_ok=True)

    instr_warnings: List[str] = []
    user_copy_resolved = resolve_operator_instructions(
        getattr(args, "user_copy_instructions", "") or "",
        getattr(args, "user_copy_instructions_file", None),
        max_chars=4000,
        warnings=instr_warnings,
    )
    env_copy = (os.getenv("GMI_USER_COPY_INSTRUCTIONS") or "").strip()
    if env_copy:
        user_copy_resolved = (
            (user_copy_resolved + "\n\n" + env_copy).strip()[:4000] if user_copy_resolved else env_copy[:4000]
        )

    user_image_resolved = resolve_operator_instructions(
        getattr(args, "user_image_instructions", "") or "",
        getattr(args, "user_image_instructions_file", None),
        max_chars=2500,
        warnings=instr_warnings,
    )
    env_img = (os.getenv("GMI_USER_IMAGE_INSTRUCTIONS") or "").strip()
    if env_img:
        user_image_resolved = (
            (user_image_resolved + "\n\n" + env_img).strip()[:2500] if user_image_resolved else env_img[:2500]
        )

    for w in instr_warnings:
        print(w)

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
                        user_image_instructions=user_image_resolved,
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
                    user_image_instructions=user_image_resolved,
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
                        user_image_instructions=user_image_resolved,
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

        step4_primary_failed = False
        try:
            en = run_step4_generate_copy_language(
                chat=chat,
                model=args.english_copy_model,
                structured_attributes=structured,
                extracted_text=extracted,
                user_copy_instructions=user_copy_resolved,
                target="canadian_english",
            )
        except Exception as exc:
            step4_primary_failed = True
            warnings.append(f"step4_copy_en_primary_failed: {exc}")
            try:
                en = run_step4_generate_copy_language(
                    chat=chat,
                    model=args.fallback_english_copy_model,
                    structured_attributes=structured,
                    extracted_text=extracted,
                    user_copy_instructions=user_copy_resolved,
                    target="canadian_english",
                )
                warnings.append(f"step4_copy_en_fallback_used: {args.fallback_english_copy_model}")
                stability_stats["step4_copy_fallback_used"] += 1
            except Exception as fb_exc:
                warnings.append(f"step4_copy_en_failed: {fb_exc}")
                stability_stats["step4_copy_fallback_failed"] += 1
                en = LocalizedListing(
                    title="Fallback: Product title in Canadian English",
                    description="Fallback: Could not generate final English copy from model response.",
                    category="Fallback: Category > Subcategory",
                    key_attributes={"status": "fallback"},
                )

        try:
            fr = run_step4_generate_copy_language(
                chat=chat,
                model=args.french_copy_model,
                structured_attributes=structured,
                extracted_text=extracted,
                user_copy_instructions=user_copy_resolved,
                target="canadian_french",
            )
        except Exception as exc:
            step4_primary_failed = True
            warnings.append(f"step4_copy_fr_primary_failed: {exc}")
            try:
                fr = run_step4_generate_copy_language(
                    chat=chat,
                    model=args.fallback_french_copy_model,
                    structured_attributes=structured,
                    extracted_text=extracted,
                    user_copy_instructions=user_copy_resolved,
                    target="canadian_french",
                )
                warnings.append(f"step4_copy_fr_fallback_used: {args.fallback_french_copy_model}")
                stability_stats["step4_copy_fallback_used"] += 1
            except Exception as fb_exc:
                warnings.append(f"step4_copy_fr_failed: {fb_exc}")
                stability_stats["step4_copy_fallback_failed"] += 1
                fr = LocalizedListing(
                    title="Repli : Titre produit en francais canadien",
                    description="Repli : Impossible de generer la copie francaise finale depuis le modele.",
                    category="Repli : Categorie > Sous-categorie",
                    key_attributes={"etat": "repli"},
                )

        if step4_primary_failed:
            stability_stats["step4_copy_failed"] += 1

        copy_review: Optional[Dict[str, Any]] = None
        try:
            copy_review, cr_side_failed = run_step4b_review_copy_bilingual(
                chat=chat,
                model_english=args.copy_review_english_model,
                model_french=args.copy_review_french_model,
                product_id=product_id,
                source_image=source_image,
                structured_attributes=structured,
                en=en,
                fr=fr,
                user_copy_instructions=user_copy_resolved,
            )
            if cr_side_failed:
                warnings.append("step4b_copy_review_partial_failure: one or both reviewer calls failed")
                stability_stats["step4b_copy_review_failed"] += 1
            status = str(copy_review.get("overall_status", "")).lower()
            if status == "fail":
                warnings.append(f"copy_review_fail: {copy_review.get('summary', '')}")
                stability_stats["copy_review_fail"] += 1
            elif status == "revise":
                warnings.append(f"copy_review_revise: {copy_review.get('summary', '')}")
                stability_stats["copy_review_revise"] += 1
        except Exception as exc:
            warnings.append(f"step4b_copy_review_failed: {exc}")
            stability_stats["step4b_copy_review_failed"] += 1
            copy_review = {
                "overall_status": "revise",
                "summary": f"Reviewer call failed: {exc}",
                "exaggeration_findings": [],
                "attribute_conflicts": [],
                "image_visual_mismatches": [],
                "en_revision_suggestions": "",
                "fr_revision_suggestions": "",
                "scores": {},
            }

        locale_grammar_review: Optional[Dict[str, Any]] = None
        try:
            locale_grammar_review = run_step4c_locale_grammar_review(
                chat=chat,
                model_english=args.locale_grammar_english_model,
                model_french=args.locale_grammar_french_model,
                en=en,
                fr=fr,
            )
            en_g = locale_grammar_review.get("canadian_english", {})
            fr_g = locale_grammar_review.get("canadian_french", {})
            if not isinstance(en_g, dict):
                en_g = {}
            if not isinstance(fr_g, dict):
                fr_g = {}
            en_s = str(en_g.get("status", "pass")).lower()
            fr_s = str(fr_g.get("status", "pass")).lower()
            if en_s == "fail" or fr_s == "fail":
                warnings.append(
                    "locale_grammar_fail: "
                    f"en={en_s} fr={fr_s}; "
                    f"en: {(en_g.get('notes') or '')[:160]} | fr: {(fr_g.get('notes') or '')[:160]}"
                )
                stability_stats["locale_grammar_fail"] += 1
            elif en_s == "revise" or fr_s == "revise":
                warnings.append(f"locale_grammar_revise: en={en_s} fr={fr_s}")
                stability_stats["locale_grammar_revise"] += 1
        except Exception as exc:
            warnings.append(f"step4c_locale_grammar_failed: {exc}")
            stability_stats["step4c_locale_grammar_failed"] += 1
            fb = _normalize_locale_grammar_block(
                {
                    "status": "revise",
                    "issues": [str(exc)],
                    "suggested_edits": "",
                    "notes": "Locale grammar reviewer call failed.",
                }
            )
            locale_grammar_review = {"canadian_english": fb, "canadian_french": {**fb}}

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
                    user_image_instructions=user_image_resolved,
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
                copy_review=copy_review,
                locale_grammar_review=locale_grammar_review,
                user_copy_instructions=user_copy_resolved,
                user_image_instructions=user_image_resolved,
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
        default=os.getenv("GMI_VISION_MODEL", "Qwen/Qwen3-VL-235B"),
        help="Step3 VLM (multimodal structured attributes).",
    )
    parser.add_argument(
        "--english-copy-model",
        default=os.getenv("GMI_ENGLISH_COPY_MODEL", "openai/gpt-5.4-pro"),
        help="Step4 Canadian English listing generation (text JSON).",
    )
    parser.add_argument(
        "--french-copy-model",
        default=os.getenv("GMI_FRENCH_COPY_MODEL", "anthropic/claude-sonnet-4.6"),
        help="Step4 Canadian French listing generation (text JSON).",
    )
    parser.add_argument(
        "--fallback-english-copy-model",
        default=os.getenv("GMI_FALLBACK_ENGLISH_COPY_MODEL", "openai/gpt-5.4-mini"),
        help="If --english-copy-model fails, retry English generation with this model.",
    )
    parser.add_argument(
        "--fallback-french-copy-model",
        default=os.getenv("GMI_FALLBACK_FRENCH_COPY_MODEL", "openai/gpt-5.4-mini"),
        help="If --french-copy-model fails, retry French generation with this model.",
    )
    parser.add_argument(
        "--copy-review-english-model",
        default=os.getenv("GMI_COPY_REVIEW_ENGLISH_MODEL", "openai/gpt-5.4"),
        help="Step4b vision JSON audit for English listing vs image (required).",
    )
    parser.add_argument(
        "--copy-review-french-model",
        default=os.getenv("GMI_COPY_REVIEW_FRENCH_MODEL", "anthropic/claude-sonnet-4.6"),
        help="Step4b vision JSON audit for French listing vs image (required).",
    )
    parser.add_argument(
        "--locale-grammar-english-model",
        default=os.getenv("GMI_LOCALE_GRAMMAR_ENGLISH_MODEL", "openai/gpt-5.4-nano"),
        help="Step4c text JSON Canadian English grammar review (required).",
    )
    parser.add_argument(
        "--locale-grammar-french-model",
        default=os.getenv("GMI_LOCALE_GRAMMAR_FRENCH_MODEL", "openai/gpt-5.4-nano"),
        help="Step4c text JSON Canadian French grammar review (required).",
    )
    parser.add_argument(
        "--user-copy-instructions",
        default="",
        help="Optional operator notes for listing copy (tone, length, SEO, audience). Injected into step4; also shown to copy reviewer. "
        "Appended after GMI_USER_COPY_INSTRUCTIONS if set.",
    )
    parser.add_argument(
        "--user-copy-instructions-file",
        default=None,
        help="UTF-8 file whose contents replace inline --user-copy-instructions when the file exists.",
    )
    parser.add_argument(
        "--user-image-instructions",
        default="",
        help="Optional operator notes for image pipeline (background, lighting, mood). Used in model erase, harmonize, restore, extra shots. "
        "Appended after GMI_USER_IMAGE_INSTRUCTIONS if set.",
    )
    parser.add_argument(
        "--user-image-instructions-file",
        default=None,
        help="UTF-8 file whose contents replace inline --user-image-instructions when the file exists.",
    )
    args = parser.parse_args(argv)
    if args.no_harmonize_after_erase:
        args.harmonize_after_erase = False
    return args


if __name__ == "__main__":
    args = parse_args()
    output_path = run_pipeline(args)
    print(f"Saved ecommerce artifacts to {output_path}")

