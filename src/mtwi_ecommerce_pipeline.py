"""MTWI → ecommerce pipeline (GMI Cloud Inference Engine).

Flow: optional **annotation audit** (VLM JSON per MTWI box: usable vs misaligned, needs processing) →
text removal (local and/or Request Queue) → optional harmonize (seam blend) → quality (local and/or model) →
vision (default Qwen/Qwen3-VL-235B; input image selectable: cleaned **final**, raw **source**, or audited **extra1**) → EN/FR copy (**dual image**: original listing + cleaned frame, plus Step3 JSON + full OCR; **unified** or **split** both use one bilingual structured call; fixed `param_*` keys) →
optional simple bilingual recovery (split only) → Step4b/4c (optional **`--skip-listing-review`**; else EN/FR **parallel** each) → **marketing extras**: by default **one RQ call per image** (`num_images=1`); optional **`GMI_EXTRA_IMAGES_BATCH=1`** tries a single multi-image request first, then fills gaps.

See agent.md for model table and per-SKU chat call counts (~6 unified default, ~7 split); src/README.md for CLI/bulk; CONFIGURATION.md for GMI_* env vars.
`--max-attempts` is capped at **2** (enqueue/chat retries). Run: `python src/mtwi_ecommerce_pipeline.py --help` or `src/run_one_deliverable_example.sh`.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - local inpaint fallback
    cv2 = None  # type: ignore
    np = None  # type: ignore


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
    listing_reference_audit: Optional[Dict[str, Any]] = None
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
        out = extract_media_bytes_from_outcome(outcome)
        if not out:
            _log_rq_outcome_debug(f"run_image_edit({model})", outcome)
        return out

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
        binaries: List[bytes] = []
        media_urls = outcome.get("media_urls")
        if isinstance(media_urls, list):
            for item in media_urls:
                if isinstance(item, dict):
                    u = None
                    for kk in ("url", "uri", "href", "signed_url", "output_url", "image_url"):
                        x = item.get(kk)
                        if isinstance(x, str) and x.startswith("http"):
                            u = x
                            break
                    if u:
                        urls.append(u)
                        continue
                    for k in ("image", "image_base64", "base64", "data", "b64_json"):
                        v = item.get(k)
                        if isinstance(v, str):
                            dec = _decode_inline_image_string(v)
                            if dec:
                                binaries.append(dec)
                                break
                elif isinstance(item, str):
                    if item.startswith("http"):
                        urls.append(item)
                    else:
                        dec = _decode_inline_image_string(item)
                        if dec:
                            binaries.append(dec)
        for u in urls:
            if len(binaries) >= count:
                break
            try:
                r = requests.get(u, timeout=120)
                r.raise_for_status()
                binaries.append(r.content)
            except Exception:
                pass
        if len(binaries) < count:
            one = extract_media_url(outcome)
            if one and len(binaries) < count:
                try:
                    r = requests.get(one, timeout=120)
                    r.raise_for_status()
                    binaries.append(r.content)
                except Exception:
                    pass
        if not binaries:
            b = extract_media_bytes_from_outcome(outcome)
            if b:
                binaries.append(b)
        if not binaries:
            _log_rq_outcome_debug(f"run_image_variants({model})", outcome)
        return binaries[: max(1, count)]


def _strip_thinking_wrappers(s: str) -> str:
    """Keep assistant text after the last closing `think` fence (reasoning models often prepend analysis)."""
    t = s.strip()
    _bq = chr(96)
    _think_close = f"{_bq}think{_bq}"
    if _think_close in t:
        t = t.rsplit(_think_close, 1)[-1].strip()
    return t


def _message_content_to_text(content: Any) -> str:
    """Normalize chat completion message.content (string or multimodal parts list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                picked = False
                for key in ("text", "content", "value"):
                    v = block.get(key)
                    if isinstance(v, str) and v.strip():
                        parts.append(v)
                        picked = True
                        break
                if not picked and block.get("type") in ("text", "output_text", "input_text"):
                    tv = block.get("text")
                    if isinstance(tv, str):
                        parts.append(tv)
        return "".join(parts)
    return str(content)


def _extract_choice_assistant_text(body: Any) -> Tuple[str, str]:
    """Return (assistant_text, finish_reason) from a /chat/completions JSON body."""
    finish = ""
    if not isinstance(body, dict):
        return "", ""
    try:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return "", ""
        ch0 = choices[0]
        if not isinstance(ch0, dict):
            return "", ""
        finish = str(ch0.get("finish_reason") or "")
        msg = ch0.get("message")
        if isinstance(msg, dict):
            primary = _message_content_to_text(msg.get("content")).strip()
            if primary:
                return primary, finish
            for alt in ("reasoning_content", "reasoning", "thinking"):
                v = msg.get(alt)
                if isinstance(v, str) and v.strip():
                    return v.strip(), finish
            return "", finish
        if msg is not None:
            return _message_content_to_text(msg).strip(), finish
        tx = ch0.get("text")
        if isinstance(tx, str):
            return tx.strip(), finish
    except (KeyError, IndexError, TypeError):
        return "", finish
    return "", finish


class ChatClient:
    """Chat completion client for vision understanding and text generation."""

    def __init__(self, api_key: str, mock: bool = False, max_attempts: int = 1):
        self.api_key = api_key
        self.mock = mock
        self.max_attempts = max(1, int(max_attempts))
        self.base_url = os.getenv("GMI_LLM_BASE_URL", "https://api.gmi-serving.com/v1")

    def _chat_completion_once(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        response_json_object: Optional[bool],
    ) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        want_json_mode = os.getenv("GMI_CHAT_JSON_RESPONSE_FORMAT", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if response_json_object is False:
            tiers: List[bool] = [False]
        elif response_json_object is True:
            tiers = [True, False] if want_json_mode else [False]
        else:
            tiers = [True, False] if want_json_mode else [False]
        payload_base: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        last_exc: Optional[Exception] = None
        resp: Optional[requests.Response] = None
        for attempt in range(1, self.max_attempts + 1):
            for use_json_object in tiers:
                payload = {**payload_base}
                if use_json_object:
                    payload["response_format"] = {"type": "json_object"}
                try:
                    r = requests.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=120,
                    )
                except Exception as exc:
                    last_exc = exc
                    break
                if r.status_code == 400 and use_json_object and want_json_mode:
                    continue
                try:
                    r.raise_for_status()
                except Exception as exc:
                    last_exc = exc
                    break
                resp = r
                break
            if resp is not None:
                break
            if attempt >= self.max_attempts:
                if last_exc:
                    raise last_exc
                raise RuntimeError("chat completion failed with no response")
            time.sleep(min(2 * attempt, 8))
        if resp is None:
            if last_exc:
                raise last_exc
            raise RuntimeError("chat completion failed with no response")
        return resp

    def chat_plain(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 1200,
        temperature: float = 0.2,
        response_json_object: Optional[bool] = None,
    ) -> str:
        """Same as chat_json but returns raw assistant text (no JSON parse)."""
        if self.mock:
            return ""
        resp = self._chat_completion_once(model, messages, max_tokens, temperature, response_json_object)
        body = resp.json()
        text, _finish = _extract_choice_assistant_text(body)
        return text

    def chat_json(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 1200,
        temperature: float = 0.2,
        response_json_object: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if self.mock:
            return {}
        resp = self._chat_completion_once(model, messages, max_tokens, temperature, response_json_object)
        body = resp.json()
        text, finish = _extract_choice_assistant_text(body)
        parsed = parse_json_content(text)
        if not parsed and text.strip() and finish == "length" and max_tokens < 8192:
            bumped = min(max_tokens * 2, 8192)
            resp2 = self._chat_completion_once(model, messages, bumped, temperature, response_json_object)
            text2, _f2 = _extract_choice_assistant_text(resp2.json())
            parsed = parse_json_content(text2)
        return parsed if isinstance(parsed, dict) else {}


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
    if not erase and spans and chat.mock:
        warnings.append(
            "overlay_mode_mock_no_span_match: erasing all annotation boxes "
            "(mock skips VLM; English/non-Chinese labels often miss heuristics; use --mask-mode all explicitly)"
        )
        return list(range(len(spans)))
    return erase


def audit_mtwi_annotation_spans(
    chat: ChatClient,
    model: str,
    source_image: Path,
    spans: List[OCRTextSpan],
    warnings: List[str],
) -> Optional[List[int]]:
    """VLM pass: validate each MTWI line (quad + transcript) and choose which boxes go into the erase mask.

    Returns:
        - list of indices to erase (may be empty if nothing should be processed)
        - None if audit failed or should fall back to ``select_spans_to_erase`` (mask_mode)

    Source language of transcripts may be Chinese or English. Output reasoning is internal; JSON field values are English.
    """
    if chat.mock or not spans:
        return None

    span_lines: List[str] = []
    for i, s in enumerate(spans):
        x1, y1, x2, y2 = span_bbox(s)
        t = (s.text or "").replace("\n", " ").strip()
        if len(t) > 240:
            t = t[:237] + "..."
        span_lines.append(f'{i}: transcript="{t}" axis_aligned_bbox_xyxy=[{x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f}]')

    prompt = (
        "You audit MTWI-style text annotations for an ecommerce product photo cleanup pipeline.\n"
        "The image is attached. Each line is one annotation: index, transcript string from the label file, "
        "and axis-aligned bounding box (min/max of the four quad corners in image pixel coordinates).\n\n"
        "For EVERY index from 0 through "
        + str(len(spans) - 1)
        + ", output one decision object with:\n"
        "- index: same integer\n"
        "- bbox_contains_target_text: true if readable text matching the transcript (allow minor OCR drift) "
        "appears inside or touching that bbox; false if the box is empty, misplaced, or covers unrelated pixels\n"
        "- annotation_usable: false if the box is clearly wrong (text visibly outside the box, or box on wrong object); "
        "true if the box is at least roughly aligned with some text/sticker/watermark\n"
        "- needs_processing: true if this region should be inpainted to remove overlaid text, stickers, watermarks, "
        "URLs, or promo banners; false to leave untouched (e.g. product-native printed specs to keep for listing accuracy)\n"
        "- notes: short English note if any issue\n\n"
        "Rules:\n"
        "- If a box is unusable or misplaced, set annotation_usable=false and needs_processing=false (do not mask it).\n"
        "- If transcript does not match what is visible in the box, set bbox_contains_target_text=false; "
        "still set needs_processing=true only when the box clearly contains removable overlay text anyway.\n"
        "- When unsure about product-print vs overlay, prefer needs_processing=true for obvious seller/watermark content; "
        "prefer false for model numbers/ingredients printed on the product body.\n"
        "- You MUST include every index exactly once.\n\n"
        "Return JSON only:\n"
        '{"decisions":[{"index":0,"bbox_contains_target_text":true,"annotation_usable":true,"needs_processing":true,"notes":""}],'
        '"summary":""}\n\n'
        "Annotations:\n" + "\n".join(span_lines)
    )

    try:
        data = chat.chat_json(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You validate MTWI bounding boxes and decide erase mask membership. Reply with JSON only.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": path_to_data_url(source_image)}},
                    ],
                },
            ],
            max_tokens=1200,
            temperature=0.1,
        )
    except Exception as exc:
        warnings.append(f"annotation_audit_failed: {exc}")
        return None

    decisions = data.get("decisions") if isinstance(data, dict) else None
    if not isinstance(decisions, list) or not decisions:
        warnings.append("annotation_audit_failed: missing_decisions")
        return None

    by_index: Dict[int, Dict[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("index", -1))
        except Exception:
            continue
        if 0 <= idx < len(spans):
            by_index[idx] = raw

    if len(by_index) != len(spans):
        warnings.append(
            f"annotation_audit_incomplete: got {len(by_index)} decisions for {len(spans)} spans; falling back to mask_mode"
        )
        return None

    erase_indices: List[int] = []
    for i in range(len(spans)):
        d = by_index[i]
        usable = bool(d.get("annotation_usable", True))
        needs = bool(d.get("needs_processing", False))
        bbox_ok = bool(d.get("bbox_contains_target_text", True))
        note = (d.get("notes") or "").strip()
        if not usable:
            warnings.append(f"annotation_audit_idx_{i}_unusable_skipped: {note or 'misaligned or invalid box'}")
            continue
        if not needs:
            if note:
                warnings.append(f"annotation_audit_idx_{i}_preserve: {note}")
            continue
        if not bbox_ok:
            warnings.append(f"annotation_audit_idx_{i}_transcript_mismatch: {note or 'check label file'}")
        erase_indices.append(i)

    summary = (data.get("summary") or "").strip() if isinstance(data, dict) else ""
    if summary:
        warnings.append(f"annotation_audit_summary: {summary[:500]}")

    return sorted(set(erase_indices))


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


def _local_erase_strip_paste_fallback(
    img: "Image.Image",
    spans: List[OCRTextSpan],
    mask: "Image.Image",
) -> "Image.Image":
    """Legacy fill: paste adjacent strips (can look copy-pasted; used only without OpenCV)."""
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
            local = img.crop((max(0, x1 - 8), max(0, y1 - 8), min(w, x2 + 9), min(h, y2 + 9)))
            patch = local.resize((bw, bh), Image.BILINEAR).filter(ImageFilter.GaussianBlur(radius=2))

        work.paste(patch, (x1, y1))

    soft_mask = mask.filter(ImageFilter.GaussianBlur(radius=2))
    blended = Image.composite(work, img, soft_mask)
    blended = blended.filter(ImageFilter.SMOOTH_MORE)
    return blended.filter(ImageFilter.GaussianBlur(radius=0.6))


def run_step1_text_erase_local(
    source_image: Path,
    spans: List[OCRTextSpan],
    product_id: str,
    out_dir: Path,
    use_mask: bool,
) -> Optional[Path]:
    """Deterministic local erase: white underlay inside mask, then OpenCV inpaint (diffusion from edges).

    Avoids strip copy-paste artifacts. Requires ``opencv-python-headless`` + ``numpy``; otherwise falls back
    to the old adjacent-strip method. Radius: env ``GMI_LOCAL_INPAINT_RADIUS`` (default ``6``).
    """
    if Image is None or ImageDraw is None:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(source_image).convert("RGB")
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    for span in spans:
        x1, y1, x2, y2 = span_bbox(span)
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

    if cv2 is not None and np is not None:
        mask_u8 = np.array(mask, dtype=np.uint8)
        if not np.any(mask_u8):
            erased_path = out_dir / f"{product_id}_erased.png"
            img.save(erased_path)
            return erased_path
        # Slight dilation so glyph anti-aliases sit inside the inpaint region.
        k = np.ones((3, 3), np.uint8)
        mask_u8 = cv2.dilate(mask_u8, k, iterations=1)
        rgb = np.array(img, dtype=np.uint8)
        filled = rgb.copy()
        filled[mask_u8 > 0] = (255, 255, 255)
        try:
            rad = max(1, min(24, int(os.getenv("GMI_LOCAL_INPAINT_RADIUS", "6"))))
        except ValueError:
            rad = 6
        bgr = cv2.cvtColor(filled, cv2.COLOR_RGB2BGR)
        out_bgr = cv2.inpaint(bgr, mask_u8, inpaintRadius=rad, flags=cv2.INPAINT_NS)
        out_rgb = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)
        blended = Image.fromarray(out_rgb, mode="RGB")
    else:
        print("OpenCV/numpy not available; local erase uses legacy strip paste (install opencv-python-headless).")
        blended = _local_erase_strip_paste_fallback(img, spans, mask)

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
        "Naturalize the image after text removal / inpainting. "
        "Blend erased regions seamlessly with surrounding texture, lighting, and color so the result looks untouched. "
        "Fix abrupt patches, seams, and repetitive cloning artifacts. "
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
    scenario_offset: int = 0,
    first_file_index: int = 1,
    warnings: Optional[List[str]] = None,
) -> List[str]:
    """Generate additional images of the same product from repaired image.

    **Default**: one Request Queue call **per** extra image with ``num_images=1`` and a single shot line
    (alternate angle → lifestyle → scene, then generic backfill). This matches the pre-refactor pipeline and
    works reliably with Seedream-style models that often return empty ``media_urls`` when ``num_images>1``.

    Optional: set ``GMI_EXTRA_IMAGES_BATCH=1`` to try **one** batched call (``num_images=N`` + numbered briefs)
    first, then fill any shortfall with per-shot ``count=1`` calls.

    Backfill: up to **6** extra single-image attempts with a generic angle prompt if still short.

    If variants still return no bytes, **edit fallback** (default on): same model via ``run_image_edit``
    (Step1-style payload) once per missing slot — set ``GMI_EXTRA_IMAGES_USE_EDIT_FALLBACK=0`` to disable.

    ``scenario_offset`` skips the first N scenario lines (e.g. after extra_1 was already generated).
    ``first_file_index`` sets the starting suffix for ``*_extra_{n}.png`` filenames.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    product_type = ""
    if structured_attributes and isinstance(structured_attributes.get("product_type"), str):
        product_type = structured_attributes.get("product_type", "")
    features: List[str] = []
    if structured_attributes and isinstance(structured_attributes.get("key_features"), list):
        features = [str(x) for x in structured_attributes.get("key_features", [])[:4]]
    feature_text = ", ".join(features)

    base_identity = (
        "TASK: Output ONE new photorealistic ecommerce-style frame. Use the reference image as the **product identity "
        "anchor** (same SKU: shape, colors, materials, visible branding zones, bundled items). You **may** change "
        "camera, lighting, background, and staging strongly so the result is **visibly different** from the reference "
        "composition — the goal is marketing variety, not a near-duplicate crop.\n"
        "Rules: no readable text, watermarks, price tags, or UI overlays in the output. Do not swap in a different "
        "product category or unrelated object. Follow this request’s VARIANT role: (1) new angle / studio, "
        "(2) new real-world usage context without a visible person, (3) person clearly using the product.\n"
    )
    context_bits: List[str] = []
    if product_type:
        context_bits.append(f"Product category (for believable props and setting): {product_type}.")
    if feature_text:
        context_bits.append(f"On-pack / listing cues (do not render as overlaid text): {feature_text}.")
    if user_image_instructions.strip():
        context_bits.append(
            "Operator preferences (lighting mood, palette; must not change which product this is): "
            + user_image_instructions.strip()
        )
    context_block = ("\n" + " ".join(context_bits) + "\n") if context_bits else ""

    # Three fixed roles (per product): (1) different angle — studio packshot, (2) different application scenario —
    # contextual “where it’s used” without a person, (3) person-in-use lifestyle scene.
    scenario_suffixes = [
        (
            "VARIANT 1 — DIFFERENT ANGLE (STUDIO PRODUCT SHOT, NO PEOPLE): "
            "Focus on a **clearly new camera angle** vs the reference (e.g. opposite three-quarter, higher/lower eye line, "
            "slight top-down, or ~90° on-axis rotation). Studio or catalog look: white / pale-gray / soft-gradient sweep OK; "
            "optional minimal prop is fine (clear acrylic riser, subtle linen, soft shadow card) as long as it stays "
            "neutral and does not become a lifestyle room scene. No people, no hands. Lighting may differ from the reference "
            "(harder key, softer wrap, or rim) to reinforce that this is a new shot, not a copy."
        ),
        (
            "VARIANT 2 — DIFFERENT APPLICATION SCENARIO (IN-USE CONTEXT, NO VISIBLE PERSON): "
            "This image is about switching the usage context: place the SAME product in a believable real-world application "
            "environment for its category (bathroom counter, desk, gym bag nearby, kitchen shelf, travel surface — pick one "
            "that fits the product type). Natural ambient light; background and props support “where you’d use it” but stay "
            "softer than the hero product. Do not show a recognizable person, face, or full hands; at most an extremely "
            "blurred partial wrist edge if unavoidable. Product must remain the sharp focal subject."
        ),
        (
            "VARIANT 3 — PERSON IN USE (LIFESTYLE USAGE SCENE): "
            "This image must show someone actually using or interacting with the product in a plausible everyday scenario "
            "for this item type (holding, applying, operating, wearing as appropriate). One adult; natural pose. "
            "Face may be turned away, cropped, or softly out of focus — emphasis on product + hands/body interaction. "
            "Warm believable lighting; background softly blurred. Hands and fingers anatomically correct; product identity "
            "must match the reference (same item, not a substitute)."
        ),
    ]
    backfill_suffix = (
        "VARIANT — EXTRA CATALOG ANGLE: New three-quarter or slight top-down angle vs the reference; "
        "clean neutral studio or minimal set; no people; product-centered."
    )

    def _shot_prompt(variant_body: str) -> str:
        return base_identity + context_block + "\n" + variant_body + "\n"

    want = max(0, int(count))
    if want == 0:
        return []

    off = max(0, int(scenario_offset))
    start_idx = max(1, int(first_file_index))
    shot_lines: List[str] = []
    si = off
    while len(shot_lines) < want and si < len(scenario_suffixes):
        shot_lines.append(scenario_suffixes[si])
        si += 1
    while len(shot_lines) < want:
        shot_lines.append(backfill_suffix)

    extra_dbg = (os.getenv("GMI_EXTRA_IMAGES_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")

    def _log_extra(stage: str, slot_idx: int, prompt: str, returned_bytes: bool) -> None:
        if not extra_dbg:
            return
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        head = " ".join(prompt.split())[:200]
        msg = (
            f"extra_images_debug: {stage} slot={slot_idx} model={model!r} "
            f"prompt_len={len(prompt)} sha256_12={digest} returned_bytes={returned_bytes} head={head!r}"
        )
        if warnings is not None:
            warnings.append(msg)
        print(msg, file=sys.stderr)

    if extra_dbg:
        for i, body in enumerate(shot_lines[:want]):
            p = _shot_prompt(body)
            _log_extra("planned_prompt_digest", i, p, False)

    batch_first = (os.getenv("GMI_EXTRA_IMAGES_BATCH") or "0").strip().lower() in ("1", "true", "yes", "on")
    binaries: List[bytes] = []

    if batch_first and want > 0:
        brief_blocks = "\n\n---\n\n".join(
            f"IMAGE {i + 1} OF {want} (must differ from other images and from reference framing):\n{_shot_prompt(shot_lines[i])}"
            for i in range(want)
        )
        multi_prompt = (
            base_identity
            + context_block
            + f"\n\nGenerate exactly {want} full-frame product images in this single request. "
            "Each IMAGE block is one output and must follow that block’s VARIANT role: "
            "block 1 = different angle (studio only), block 2 = different application/usage scenario (no visible person), "
            "block 3 = person-in-use lifestyle. Do not reuse the same creative role across blocks. "
            "Same product identity across all; no text or watermarks in outputs.\n\n"
            + brief_blocks
        )
        try:
            binaries = list(
                rq.run_image_variants(
                    model=model,
                    reference_image=source_for_generation,
                    prompt=multi_prompt,
                    count=want,
                )
            )[:want]
        except Exception:
            binaries = []
        _log_extra("batch_variants_rq", 0, multi_prompt, bool(binaries))

    # Per-shot num_images=1 (legacy path; reliable for many RQ image models when num_images>1 is flaky).
    for si in range(len(binaries), want):
        prompt_one = _shot_prompt(shot_lines[si])
        got = False
        try:
            one = rq.run_image_variants(
                model=model,
                reference_image=source_for_generation,
                prompt=prompt_one,
                count=1,
            )
            got = bool(one)
            if one:
                binaries.append(one[0])
        except Exception:
            pass
        _log_extra("per_shot_variants_rq", si, prompt_one, got)

    backfill_full = _shot_prompt(backfill_suffix)
    backfill_round = 0
    while len(binaries) < want and backfill_round < 6:
        slot_bf = len(binaries)
        got_bf = False
        try:
            extra = rq.run_image_variants(
                model=model,
                reference_image=source_for_generation,
                prompt=backfill_full,
                count=1,
            )
            got_bf = bool(extra)
            if extra:
                binaries.append(extra[0])
        except Exception:
            pass
        _log_extra("backfill_variants_rq", slot_bf, backfill_full, got_bf)
        backfill_round += 1

    # Many RQ models return empty ``media_urls`` for the variants payload but succeed on the same
    # ``run_image_edit`` path used for Step1 erase. Fill any remaining slots with per-shot edit calls.
    use_edit_fb = (os.getenv("GMI_EXTRA_IMAGES_USE_EDIT_FALLBACK") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if use_edit_fb and len(binaries) < want and not rq.mock:
        edit_tries = 0
        edit_cap = max(want * 3, 6)
        while len(binaries) < want and edit_tries < edit_cap:
            idx = len(binaries)
            line = shot_lines[idx] if idx < len(shot_lines) else backfill_suffix
            prompt_edit = _shot_prompt(line)
            edit_tries += 1
            got_ed = False
            try:
                blob = rq.run_image_edit(
                    model=model,
                    source_image=source_for_generation,
                    prompt=prompt_edit,
                    mask_image=None,
                )
                got_ed = bool(blob)
                if blob:
                    binaries.append(blob)
            except Exception:
                pass
            _log_extra("edit_fallback_rq", idx, prompt_edit, got_ed)

    if len(binaries) < want and (os.getenv("GMI_EXTRA_IMAGES_PLACEHOLDER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        shortfall = want - len(binaries)
        ref_bytes = source_for_generation.read_bytes()
        while len(binaries) < want:
            binaries.append(ref_bytes)
        if warnings is not None:
            warnings.append(
                f"extra_images_placeholder: RQ model {model!r} returned no images; copied reference image "
                f"for {shortfall} slot(s) (not real marketing variants)."
            )
            warnings.append(
                "extra_images_placeholder_note: Distinct prompts were still sent per slot (different angle studio / "
                "different application scenario no person / person-in-use lifestyle). Identical files mean the API "
                "yielded no decodable image bytes, not that one prompt was reused. Set GMI_EXTRA_IMAGES_DEBUG=1 to append "
                "per-request prompt digests (sha + length + head) to these warnings. Set GMI_RQ_OUTCOME_DEBUG=1 to print "
                "RQ outcome field shapes on stderr when decode fails (compare with console docs / support)."
            )

    paths: List[str] = []
    for j, b in enumerate(binaries[:want]):
        p = out_dir / f"{product_id}_extra_{start_idx + j}.png"
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


def run_listing_reference_consistency_audit(
    chat: ChatClient,
    model: str,
    original_listing_image: Path,
    marketing_variant_image: Path,
    warnings: List[str],
) -> Dict[str, Any]:
    """Compare original listing photo vs first marketing extra; gate using variant for copy/vision."""
    if chat.mock:
        return {
            "same_core_product": True,
            "confidence": "high",
            "drift_notes": "",
            "safe_to_use_variant_for_copy": True,
        }
    prompt = """
You compare two ecommerce images for the SAME SKU pipeline.

Image A: original seller listing photo (may have watermarks/overlays).
Image B: AI marketing render (different angle/staging; should show the same product).

Return JSON only:
{
  "same_core_product": boolean,
  "confidence": "low" | "medium" | "high",
  "drift_notes": "English: what changed between A and B (pose, accessories, color drift, wrong object, etc.)",
  "safe_to_use_variant_for_copy": boolean
}

Rules:
- safe_to_use_variant_for_copy = true only if B clearly depicts the same product category and identity as A (allow angle/lighting/staging changes).
- If B shows a different product, different colorway not in A, or invented accessories, set same_core_product false and safe_to_use_variant_for_copy false.
- If unsure, prefer safe_to_use_variant_for_copy false.
""".strip()
    try:
        data = chat.chat_json(
            model=model,
            messages=[
                {"role": "system", "content": "You audit listing vs marketing image consistency. JSON only."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Image A (original listing):\n" + prompt},
                        {"type": "image_url", "image_url": {"url": path_to_data_url(original_listing_image)}},
                        {"type": "text", "text": "Image B (marketing variant extra_1):"},
                        {"type": "image_url", "image_url": {"url": path_to_data_url(marketing_variant_image)}},
                    ],
                },
            ],
            max_tokens=700,
            temperature=0.1,
        )
    except Exception as exc:
        warnings.append(f"listing_reference_audit_failed: {exc}")
        return {
            "same_core_product": False,
            "confidence": "low",
            "drift_notes": str(exc),
            "safe_to_use_variant_for_copy": False,
        }
    if not isinstance(data, dict):
        warnings.append("listing_reference_audit_malformed")
        return {
            "same_core_product": False,
            "confidence": "low",
            "drift_notes": "non-dict response",
            "safe_to_use_variant_for_copy": False,
        }
    safe = bool(data.get("safe_to_use_variant_for_copy", False)) and bool(data.get("same_core_product", False))
    out = {
        "same_core_product": bool(data.get("same_core_product", False)),
        "confidence": str(data.get("confidence", "low")).strip().lower(),
        "drift_notes": str(data.get("drift_notes", "")).strip(),
        "safe_to_use_variant_for_copy": safe,
    }
    if out["confidence"] not in {"low", "medium", "high"}:
        out["confidence"] = "low"
    return out


def run_step3_understand_product(
    chat: ChatClient,
    model: str,
    product_id: str,
    vision_image: Path,
    extracted_text: List[str],
    image_context: str = "clean_main",
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
    if image_context == "original_raw":
        image_desc = (
            "Attached image: **original seller listing** (may still show overlays, watermarks, promos). "
            "Read both pixels and on-image text; OCR list below is the MTWI transcript of those regions."
        )
    elif image_context == "marketing_variant":
        image_desc = (
            "Attached image: **marketing variant** of the SAME SKU (angle/staging may differ from the raw listing). "
            "Infer attributes from visible product pixels; ignore pure background staging. "
            "Cross-check with OCR from the source listing; do not invent specs not supported by image + OCR."
        )
    else:
        image_context = "clean_main"
        image_desc = (
            "Attached image: **cleaned main product photo** (promotional text from the original listing was removed or inpainted). "
            "The OCR list is the **verbatim transcript** of text that appeared on the **original** image (Chinese + Latin/numbers); use it to recover brand, model, power, bundle claims, and product naming. "
            "Reconcile: printed specs visible on the product/box in this image should align with OCR when they refer to the same SKU."
        )

    prompt = f"""
## Your role
You are a **multimodal product analyst** for cross-border ecommerce. Your job is to fuse **what you see in the image** with **the OCR transcript** (text that was on the seller listing, including Chinese marketing lines) into one strict JSON object for downstream Canadian English/French copywriters.

## Inputs
- **product_id**: `{product_id}`
- **Image context**: {image_desc}
- **OCR transcript** (from MTWI boxes on the original listing; may contain duplicates or noise):
{text_list}

## Reasoning rules
1. **Vision first**: Identify the main sellable item, accessories in frame, packaging, and any legible print on the product or box.
2. **OCR as structured hints**: Map Chinese trade terms to English concepts (e.g. 吹风机 / 电吹风 / 专业吹风机 → hair dryer). Use Latin/numbers for model IDs and wattage.
3. **No lazy defaults**: Do not emit `product_type: "Unknown"` when the category is clear from shape + OCR. Reserve `confidence: "low"` only when the image is ambiguous *and* OCR is unhelpful.
4. **Conservative facts**: Do not invent certifications, awards, or medical claims not visible or stated in OCR.

## Output format (JSON only, no markdown fences)
Return exactly these keys (all required):
| Key | Type | Requirements |
|-----|------|--------------|
| product_type | string | **English** short noun phrase for the main item (e.g. `hair dryer`). From pixels + OCR. |
| category_hint | string | **English** retail path with ` > ` (e.g. `Beauty & Personal Care > Hair Care > Hair Dryers`). |
| material | string | **English**; empty string if not inferable. |
| key_features | string[] | 3–12 **English** bullets derived from image + OCR (brand, model, power, in-box items, key promos translated to factual English). |
| size_or_specs | string[] | **English** measurable specs (wattage, dimensions if visible, etc.); empty array if none. |
| brand_or_series | string | **Latin**; pinyin/romanization if Chinese-only in OCR. |
| confidence | string | One of: `low`, `medium`, `high` — your certainty after fusing image + OCR.
""".strip()
    messages = [
        {
            "role": "system",
            "content": (
                "You are a multimodal ecommerce product analyst. You MUST output a single JSON object with the exact keys "
                "requested in the user message. product_type, category_hint, material, key_features, size_or_specs, and "
                "brand_or_series are always grounded in the provided image plus OCR; use English for all string/array "
                "values except confidence."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": path_to_data_url(vision_image)}},
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


_STEP4_PRODUCT_ID_RULES = """
Product identification (critical):
- Infer the physical product type from **the image**, structured JSON, **and** OCR together. OCR may be Chinese: interpret trade terms (e.g. 吹风机 / 电吹风 / 专业吹风机 → hair dryer).
- If product_type is blank, "Unknown", or too generic, infer the specific category from pixels + OCR + key_features — do not write vague "general merchandise" copy.
- Title and the opening of the description must name the item type clearly in the target language (e.g. English: "hair dryer"; French: "séchoir à cheveux").
"""

_STEP4_INPUTS_CONTRACT = """
## Inputs you receive
1. **IMAGE** — Product photo used for this listing (typically after overlay text removal). Use it to confirm shape, colour, accessories, packaging, and printed specs on the product.
2. **STRUCTURED JSON** — Output of an earlier vision analyst step; treat as machine hints. If it conflicts with the image or OCR, **prefer image + OCR**.
3. **OCR TRANSCRIPT** — Verbatim strings from the **original** listing image regions (often Chinese + numbers/Latin). These are the **removed overlay texts**; use them for model numbers, wattage, bundle contents, brand names (romanize in output), and product naming.
"""

_STEP4_OUTPUT_FIELD_SPECS = """
## Per-field output requirements (must satisfy all that apply)
- **title**: One line; **must** include the product type; add brand or model when supported by image/OCR; aim ~50–120 characters.
- **description**: Multi-sentence factual prose (or short implied bullets); weave visible features and OCR-backed specs; no Chinese characters; no internal labels like "OCR:" or "confidence".
- **category**: Retail path using ` > `; labels entirely in the **target locale** (Canadian English or Canadian French).
- **key_attributes**: Flat object, **4–12** entries where possible (e.g. brand, model, power, colour_family, in_the_box); values only in the target locale; romanize Chinese brands.
"""


def _step4_use_listing_image(listing_image: Optional[Path]) -> Optional[Path]:
    """Attach product image to Step4 unless GMI_STEP4_COPY_USE_IMAGE disables it."""
    if listing_image is None or not listing_image.is_file():
        return None
    v = (os.getenv("GMI_STEP4_COPY_USE_IMAGE") or "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return None
    return listing_image


def _step4_user_message_content(prompt_text: str, listing_image: Optional[Path]) -> Any:
    img = _step4_use_listing_image(listing_image)
    if img is None:
        return prompt_text
    return [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": path_to_data_url(img)}},
    ]


# Fixed bilingual listing parameters (JSON keys); missing values → locale sentinel after parse.
LISTING_PARAMETER_KEYS: Tuple[str, ...] = (
    "brand",
    "model",
    "product_type",
    "power",
    "colour",
    "material",
    "dimensions",
    "included_accessories",
    "certifications_visible",
    "country_of_origin",
    "warranty",
    "weight",
)
STEP4_PARAM_MISSING_EN = "Not specified"
STEP4_PARAM_MISSING_FR = "Non précisé"


def _step4_dual_image_paths_ok(
    source_image: Optional[Path],
    cleaned_image: Optional[Path],
) -> Tuple[Optional[Path], Optional[Path]]:
    """Return paths for multimodal listing when GMI_STEP4_COPY_USE_IMAGE allows."""
    if (os.getenv("GMI_STEP4_COPY_USE_IMAGE") or "1").strip().lower() in ("0", "false", "no", "off"):
        return None, None
    src = source_image if source_image and source_image.is_file() else None
    cln = cleaned_image if cleaned_image and cleaned_image.is_file() else None
    return src, cln


def _step4_user_message_content_dual(
    prompt_text: str,
    source_image: Optional[Path],
    cleaned_image: Optional[Path],
    warnings: Optional[List[str]] = None,
) -> Any:
    """Multimodal user body: text then unique image URL(s): original listing, then cleaned (order matches prompt)."""
    src, cln = _step4_dual_image_paths_ok(source_image, cleaned_image)
    if src is None and cln is None:
        return prompt_text
    parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]
    seen: set[str] = set()
    for p in (src, cln):
        if p is None:
            continue
        url = path_to_data_url(p)
        if url in seen:
            continue
        seen.add(url)
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if warnings is not None:
        if src is None:
            warnings.append("step4_dual_image_missing_original: cleaned-only or no listing image for multimodal")
        if cln is None:
            warnings.append("step4_dual_image_missing_cleaned: original-only multimodal")
    return parts


def _step4_max_tokens() -> int:
    try:
        return max(600, int(os.getenv("GMI_STEP4_MAX_TOKENS", "2048")))
    except ValueError:
        return 2048


def _parse_delimited_step4_text(text: str) -> Dict[str, Any]:
    """Parse TITLE:/CATEGORY:/DESCRIPTION:…/END_DESCRIPTION/KEY_ATTR_* lines."""
    raw = text.replace("\r\n", "\n")
    title = ""
    category = ""
    desc_lines: List[str] = []
    attrs: Dict[str, str] = {}
    mode: Optional[str] = None
    for line in raw.split("\n"):
        stripped = line.strip()
        u = stripped
        if re.match(r"(?i)^TITLE:\s*", u):
            title = re.split(r":\s*", u, 1)[1].strip() if ":" in u else ""
            mode = None
        elif re.match(r"(?i)^CATEGORY:\s*", u):
            category = re.split(r":\s*", u, 1)[1].strip() if ":" in u else ""
            mode = None
        elif re.match(r"(?i)^DESCRIPTION:\s*", u):
            rest = re.split(r":\s*", u, 1)[1].strip() if ":" in u else ""
            desc_lines = [rest] if rest else []
            mode = "desc"
        elif re.match(r"(?i)^END_DESCRIPTION\s*$", u):
            mode = None
        elif mode == "desc":
            desc_lines.append(line.rstrip())
        elif re.match(r"(?i)^KEY_ATTR_", u):
            m = re.match(r"(?i)^KEY_ATTR_([^:]+):\s*(.*)$", u)
            if m:
                attrs[m.group(1).strip()] = m.group(2).strip()
    description = "\n".join(desc_lines).strip()
    return {"title": title, "category": category, "description": description, "key_attributes": attrs}


def _coerce_step4_locale_block(payload: Dict[str, Any], listing_key: str) -> Dict[str, Any]:
    """Accept common JSON shapes when models ignore the exact top-level key name."""
    if not isinstance(payload, dict):
        return {}
    direct = payload.get(listing_key)
    if isinstance(direct, dict):
        return direct
    norm_target = listing_key.lower().replace("-", "_")
    for k, v in payload.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        if k.lower().replace("-", "_") == norm_target:
            return v
    for wrap in ("result", "data", "output", "response", "listing", "copy"):
        inner = payload.get(wrap)
        if isinstance(inner, dict):
            nested = _coerce_step4_locale_block(inner, listing_key)
            if nested:
                return nested
    if len(payload) == 1:
        _, sole_v = next(iter(payload.items()))
        if isinstance(sole_v, dict):
            if any(x in sole_v for x in ("title", "description", "category", "key_attributes")):
                return sole_v
            nk = sole_v.get(listing_key)
            if isinstance(nk, dict):
                return nk
    for v in payload.values():
        if isinstance(v, dict) and (v.get("title") or v.get("description")):
            return v
    return {}


def run_step4_generate_copy_language(
    chat: ChatClient,
    model: str,
    structured_attributes: Dict[str, Any],
    extracted_text: List[str],
    user_copy_instructions: str,
    target: Literal["canadian_english", "canadian_french"],
    listing_image: Optional[Path] = None,
) -> LocalizedListing:
    """Generate one locale listing (Canadian English or Canadian French) with a dedicated model.

    When ``listing_image`` is set (default: same frame as Step3), the model receives **image + JSON + OCR**
    so copy is grounded in pixels and removed overlay text.
    """
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
## Your role
You are a **senior ecommerce copywriter** preparing a listing for **{lang_name}** shoppers in Canada (marketplace-style).

## Task
Produce **one** JSON object with a single top-level key `{listing_key}`. Every factual claim must be grounded in **the product image**, the structured JSON, and the OCR transcript (removed overlay text from the source listing). Source signals include Chinese; **output strings must contain no Chinese characters** (romanize or omit brands).

{_STEP4_INPUTS_CONTRACT}

## Structured attributes (from prior vision step)
```json
{attrs_text}
```

## OCR transcript (verbatim from original listing regions)
{ocr_text}

{_STEP4_PRODUCT_ID_RULES}
{_STEP4_OUTPUT_FIELD_SPECS}
{operator_block}

## Output format (JSON only — no markdown fences)
Return exactly:
{{
  "{listing_key}": {{
    "title": "string",
    "description": "string",
    "category": "string",
    "key_attributes": {{"key": "value"}}
  }}
}}

## Hard rules
- All string values inside `{listing_key}` must be **{lang_name}** only.
- No URLs, no "free shipping/authentic" seller hype unless OCR explicitly supports a neutral factual restatement.
- No invented certifications, awards, or medical claims.
""".strip()
    user_body = _step4_user_message_content(prompt, listing_image)
    data = chat.chat_json(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a senior ecommerce copywriter for {lang_name} (Canada). "
                    "You may receive a product image plus JSON and OCR; ground the listing in those sources. "
                    "Reply with strict JSON only — one top-level key as specified by the user."
                ),
            },
            {"role": "user", "content": user_body},
        ],
        max_tokens=_step4_max_tokens(),
        temperature=0.3,
    )
    label = "English" if target == "canadian_english" else "French"
    data_dict = data if isinstance(data, dict) else {}
    block = _coerce_step4_locale_block(data_dict, listing_key)
    listing = build_listing({listing_key: block} if block else data_dict, listing_key, label)
    if not chat.mock and _step4_copy_listing_degenerate(listing):
        plain_listing = _step4_try_plaintext_listing(
            chat=chat,
            model=model,
            structured_attributes=structured_attributes,
            extracted_text=extracted_text,
            user_copy_instructions=user_copy_instructions,
            target=target,
            listing_image=listing_image,
        )
        if plain_listing is not None:
            listing = plain_listing
    if not chat.mock and _step4_copy_listing_degenerate(listing):
        raise RuntimeError(
            f"step4_degenerate_listing: locale={target} title={listing.title!r} description_empty=True"
        )
    return listing


def _step4_copy_listing_degenerate(listing: LocalizedListing) -> bool:
    """True if the model output did not yield usable body copy (triggers fallback model)."""
    return not (listing.description or "").strip()


def _step4_try_plaintext_listing(
    chat: ChatClient,
    model: str,
    structured_attributes: Dict[str, Any],
    extracted_text: List[str],
    user_copy_instructions: str,
    target: Literal["canadian_english", "canadian_french"],
    listing_image: Optional[Path] = None,
) -> Optional[LocalizedListing]:
    """If JSON copy fails, ask for line-delimited fields (no response_format)."""
    if chat.mock:
        return None
    listing_key = target
    lang_name = "Canadian English" if target == "canadian_english" else "Canadian French (français canadien)"
    attrs_text = json.dumps(structured_attributes, ensure_ascii=False, indent=2)
    ocr_text = "\n".join(f"- {t}" for t in extracted_text[:20])
    operator_block = _step4_operator_block(user_copy_instructions)
    prompt = f"""## Your role
You are a **senior ecommerce copywriter** for **{lang_name}** (Canada). You see the **product image**, structured context, and **OCR transcript** (text removed from the original listing). Ground the listing in those sources.

{_STEP4_INPUTS_CONTRACT}

## Structured context
{attrs_text}

## OCR transcript
{ocr_text}
{_STEP4_PRODUCT_ID_RULES}
{_STEP4_OUTPUT_FIELD_SPECS}
{operator_block}

## Output shape (plain text only — no JSON, no markdown fences)
TITLE: <single line>
CATEGORY: <single line>
DESCRIPTION: <one or more lines of body copy>
END_DESCRIPTION
(Optional) KEY_ATTR_name: value

Use END_DESCRIPTION on its own line after the description."""
    user_body = _step4_user_message_content(prompt, listing_image)
    plain = chat.chat_plain(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You write factual {lang_name} ecommerce copy in the exact line format requested. "
                    "Use the image plus OCR when provided. No JSON."
                ),
            },
            {"role": "user", "content": user_body},
        ],
        max_tokens=_step4_max_tokens(),
        temperature=0.35,
        response_json_object=False,
    )
    parsed = _parse_delimited_step4_text(plain)
    desc = (parsed.get("description") or "").strip()
    if not desc:
        return None
    block = {
        "title": parsed.get("title") or "",
        "description": desc,
        "category": parsed.get("category") or "",
        "key_attributes": parsed.get("key_attributes") or {},
    }
    label = "English" if target == "canadian_english" else "French"
    listing = build_listing({listing_key: block}, listing_key, label)
    if _step4_copy_listing_degenerate(listing):
        return None
    return listing


def _listing_is_heuristic_fallback(listing: LocalizedListing, locale: Literal["en", "fr"]) -> bool:
    attrs = listing.key_attributes or {}
    if locale == "en":
        return str(attrs.get("draft_source", "")).lower() == "heuristic_en"
    return str(attrs.get("source_brouillon", "")).lower() == "heuristique_fr"


def _listings_need_simple_copy_recovery(en: LocalizedListing, fr: LocalizedListing) -> bool:
    """Split-model path produced empty body or heuristic drafts — try one-shot bilingual JSON."""
    if not (en.description or "").strip() or not (fr.description or "").strip():
        return True
    return _listing_is_heuristic_fallback(en, "en") or _listing_is_heuristic_fallback(fr, "fr")


def run_step4_generate_copy_bilingual_simple(
    chat: ChatClient,
    model: str,
    structured_attributes: Dict[str, Any],
    extracted_text: List[str],
    user_copy_instructions: str,
    listing_image: Optional[Path] = None,
) -> Tuple[LocalizedListing, LocalizedListing]:
    """Single-chat EN+FR listing (simpler for gateways); never requests response_format json_object.

    Pass ``listing_image`` (same frame as Step3 by default) so the model grounds bilingual copy in pixels + OCR.
    """
    if chat.mock:
        return (
            LocalizedListing(
                title="Mock: Product title in Canadian English",
                description="Mock: Product description in Canadian English (simple bilingual call).",
                category="Mock: Category > Subcategory",
                key_attributes={"feature_1": "mock", "draft_source": "simple_bilingual"},
            ),
            LocalizedListing(
                title="Maquette : Titre produit en francais canadien",
                description="Maquette : Description (appel bilingue simple).",
                category="Maquette : Categorie > Sous-categorie",
                key_attributes={"caracteristique_1": "maquette", "source_brouillon": "simple_bilingue"},
            ),
        )

    attrs_text = json.dumps(structured_attributes, ensure_ascii=False, indent=2)
    ocr_text = "\n".join(f"- {t}" for t in extracted_text[:20])
    operator_block = _step4_operator_block(user_copy_instructions)
    prompt = f"""
## Your role
You are a **bilingual ecommerce localization lead** for **Canada**. You produce **Canadian English** and **Canadian French** listing copy in **one** JSON object.

## Task
Ground both locales in **the product image**, structured JSON, and OCR (removed overlay text). Chinese appears only in inputs — **no Chinese in output strings**.

{_STEP4_INPUTS_CONTRACT}

## Structured attributes (prior vision step)
```json
{attrs_text}
```

## OCR transcript
{ocr_text}

{_STEP4_PRODUCT_ID_RULES}
{_STEP4_OUTPUT_FIELD_SPECS}
{operator_block}

## Output format (JSON only — no markdown fences)
Return **one** JSON object with **exactly** these top-level keys:
- `"canadian_english"`: object with `title`, `description`, `category`, `key_attributes` (all **English** strings).
- `"canadian_french"`: same keys, all **Canadian French** strings.

## Hard rules
- Same factual content in both languages (no extra claims in one locale).
- Romanize Chinese brand names; do not copy Chinese characters into outputs.
- No URLs; no invented certifications.
""".strip()
    user_body = _step4_user_message_content(prompt, listing_image)
    data = chat.chat_json(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You reply with a single JSON object only. Top-level keys: canadian_english, canadian_french. "
                    "You may receive a product image plus JSON and OCR — ground copy in those sources. No markdown fences."
                ),
            },
            {"role": "user", "content": user_body},
        ],
        max_tokens=min(4096, _step4_max_tokens() + 1024),
        temperature=0.25,
        response_json_object=False,
    )
    data_dict = data if isinstance(data, dict) else {}
    en_block = _coerce_step4_locale_block(data_dict, "canadian_english")
    fr_block = _coerce_step4_locale_block(data_dict, "canadian_french")
    en = build_listing({"canadian_english": en_block}, "canadian_english", "English")
    fr = build_listing({"canadian_french": fr_block}, "canadian_french", "French")
    attrs_en = dict(en.key_attributes)
    attrs_en.setdefault("draft_source", "simple_bilingual")
    attrs_fr = dict(fr.key_attributes)
    attrs_fr.setdefault("source_brouillon", "simple_bilingue")
    return (
        LocalizedListing(
            title=en.title,
            description=en.description,
            category=en.category,
            key_attributes=attrs_en,
        ),
        LocalizedListing(
            title=fr.title,
            description=fr.description,
            category=fr.category,
            key_attributes=attrs_fr,
        ),
    )


def _apply_simple_copy_recovery_if_needed(
    chat: ChatClient,
    args: argparse.Namespace,
    structured: Dict[str, Any],
    extracted: List[str],
    user_copy_resolved: str,
    en: LocalizedListing,
    fr: LocalizedListing,
    warnings: List[str],
    listing_image: Optional[Path] = None,
) -> Tuple[LocalizedListing, LocalizedListing]:
    if getattr(args, "no_simple_copy_recovery", False) or chat.mock:
        return en, fr
    if not _listings_need_simple_copy_recovery(en, fr):
        return en, fr
    try:
        en2, fr2 = run_step4_generate_copy_bilingual_simple(
            chat=chat,
            model=args.simple_copy_model,
            structured_attributes=structured,
            extracted_text=extracted,
            user_copy_instructions=user_copy_resolved,
            listing_image=listing_image,
        )
    except Exception as exc:
        warnings.append(f"step4_simple_bilingual_recovery_failed: {exc}")
        return en, fr

    ok_en = not _step4_copy_listing_degenerate(en2)
    ok_fr = not _step4_copy_listing_degenerate(fr2)
    en_bad = _listing_is_heuristic_fallback(en, "en") or not (en.description or "").strip()
    fr_bad = _listing_is_heuristic_fallback(fr, "fr") or not (fr.description or "").strip()

    replaced: List[str] = []
    if ok_en and en_bad:
        en = en2
        replaced.append("en")
    if ok_fr and fr_bad:
        fr = fr2
        replaced.append("fr")
    if replaced:
        warnings.append(
            f"step4_simple_bilingual_recovery_ok: model={args.simple_copy_model} replaced={'+'.join(replaced)}"
        )
    else:
        warnings.append("step4_simple_bilingual_recovery_no_improvement: kept prior listings")
    return en, fr


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

    def _en_side() -> Tuple[Dict[str, Any], bool]:
        try:
            return (
                _run_step4b_review_copy_one_language(
                    chat,
                    model_english,
                    product_id,
                    source_image,
                    structured_attributes,
                    en,
                    "Canadian English",
                    audit_english=True,
                    user_copy_instructions=user_copy_instructions,
                ),
                False,
            )
        except Exception:
            return (
                _normalize_copy_review(
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
                ),
                True,
            )

    def _fr_side() -> Tuple[Dict[str, Any], bool]:
        try:
            return (
                _run_step4b_review_copy_one_language(
                    chat,
                    model_french,
                    product_id,
                    source_image,
                    structured_attributes,
                    fr,
                    "Canadian French",
                    audit_english=False,
                    user_copy_instructions=user_copy_instructions,
                ),
                False,
            )
        except Exception:
            return (
                _normalize_copy_review(
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
                ),
                True,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_en = pool.submit(_en_side)
        f_fr = pool.submit(_fr_side)
        en_raw, en_failed = f_en.result()
        fr_raw, fr_failed = f_fr.result()
    failed = en_failed or fr_failed
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

    def _en_block() -> Dict[str, Any]:
        en_raw = chat.chat_json(
            model=model_english,
            messages=[
                {"role": "system", "content": "You are a Canadian English editor. Output strict JSON only."},
                {"role": "user", "content": en_prompt},
            ],
            max_tokens=900,
            temperature=0.1,
        )
        return _normalize_locale_grammar_block(en_raw if isinstance(en_raw, dict) else {})

    def _fr_block() -> Dict[str, Any]:
        fr_raw = chat.chat_json(
            model=model_french,
            messages=[
                {"role": "system", "content": "You are a Canadian French editor. Output strict JSON only."},
                {"role": "user", "content": fr_prompt},
            ],
            max_tokens=900,
            temperature=0.1,
        )
        return _normalize_locale_grammar_block(fr_raw if isinstance(fr_raw, dict) else {})

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_en = pool.submit(_en_block)
        f_fr = pool.submit(_fr_block)
        en_block = f_en.result()
        fr_block = f_fr.result()

    return {"canadian_english": en_block, "canadian_french": fr_block}


def parse_json_content(content: Any) -> Dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {}
    s = _strip_thinking_wrappers(content.strip())
    if not s:
        return {}
    # Strip ```json ... ``` fences (common when response_format is ignored or unsupported)
    if "```" in s:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE)
        if m:
            s = m.group(1).strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    return item
        return {}
    except json.JSONDecodeError:
        pass
    # First JSON value in string (object or array)
    idx_obj = s.find("{")
    idx_arr = s.find("[")
    idx = -1
    if idx_obj >= 0 and (idx_arr < 0 or idx_obj < idx_arr):
        idx = idx_obj
    elif idx_arr >= 0:
        idx = idx_arr
    if idx < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(s[idx:])
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    return item
        return {}
    except json.JSONDecodeError:
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


def _normalize_listing_parameters_block(params: Any, missing: str) -> Dict[str, str]:
    raw = params if isinstance(params, dict) else {}
    out: Dict[str, str] = {}
    for k in LISTING_PARAMETER_KEYS:
        v = raw.get(k)
        s = str(v).strip() if v is not None else ""
        out[k] = s if s else missing
    return out


def _listing_from_dual_structured_block(
    block: Dict[str, Any],
    language_name: str,
    missing_param: str,
) -> LocalizedListing:
    """Build LocalizedListing from dual-image JSON locale block (title, description, category, parameters)."""
    title = block.get("title", "")
    description = block.get("description", "")
    category = block.get("category", "")
    params_norm = _normalize_listing_parameters_block(block.get("parameters"), missing_param)
    merged_attrs: Dict[str, str] = {f"param_{k}": v for k, v in params_norm.items()}
    extra = block.get("key_attributes")
    if isinstance(extra, dict):
        for k, v in extra.items():
            sk = str(k).strip()
            if sk and sk not in merged_attrs:
                merged_attrs[sk] = str(v).strip()
    return LocalizedListing(
        title=strip_chinese(str(title)) if isinstance(title, str) else "",
        description=strip_chinese(str(description)) if isinstance(description, str) else "",
        category=strip_chinese(str(category)) if isinstance(category, str) else "",
        key_attributes=merged_attrs,
    )


def run_step4_generate_listing_dual_image_bilingual(
    chat: ChatClient,
    model: str,
    source_image: Path,
    cleaned_image: Optional[Path],
    structured_attributes: Dict[str, Any],
    extracted_text: List[str],
    user_copy_instructions: str,
    warnings: Optional[List[str]] = None,
) -> Tuple[LocalizedListing, LocalizedListing]:
    """One bilingual chat: original + cleaned images, OCR, optional Step3 JSON → strict EN/FR blocks with fixed parameters."""
    w = warnings if warnings is not None else []
    if chat.mock:
        pm_en = {f"param_{k}": STEP4_PARAM_MISSING_EN for k in LISTING_PARAMETER_KEYS}
        pm_fr = {f"param_{k}": STEP4_PARAM_MISSING_FR for k in LISTING_PARAMETER_KEYS}
        return (
            LocalizedListing(
                title="Mock EN: Dual-image structured listing",
                description="Mock English description (dual-image path).",
                category="Mock > Category",
                key_attributes={**pm_en, "draft_source": "mock_dual_image"},
            ),
            LocalizedListing(
                title="Maquette FR : Liste structurée deux images",
                description="Maquette description française (chemin deux images).",
                category="Maquette > Catégorie",
                key_attributes={**pm_fr, "source_brouillon": "mock_deux_images"},
            ),
        )

    attrs_text = json.dumps(structured_attributes, ensure_ascii=False, indent=2)
    ocr_text = "\n".join(f"- {t}" for t in extracted_text)
    operator_block = _step4_operator_block(user_copy_instructions)
    key_lines = ", ".join(f'"{k}"' for k in LISTING_PARAMETER_KEYS)
    prompt = f"""
## Your role
You are a **bilingual ecommerce listing author** for **Canada**. You write **Canadian English** and **Canadian French** in one JSON response.

## Inputs (use all together)
1. **IMAGE A** (first image after this text): **Original seller listing** — may show Chinese/Latin promo text, watermarks, price hooks. Read product naming and claims here.
2. **IMAGE B** (second image, if present): **Cleaned product photo** — text overlays removed or repaired; use for true product shape, colour, accessories, and printed specs on the product/box.
3. **Structured JSON** (hints from an automated vision step; may be wrong — prefer images + OCR if conflict):
```json
{attrs_text}
```
4. **OCR transcript** (verbatim strings from labeled regions on the **original** listing; includes Chinese):
{ocr_text}

## Fusion rules
- Identify the **physical product type** from images + OCR (e.g. Chinese 吹风机 → hair dryer in EN, séchoir à cheveux in FR).
- Do **not** invent certifications, medical claims, or deals not supported by images/OCR.
- **Title** and **description** are mandatory and must be substantive.
- **parameters**: every key listed below **must** appear. If you cannot verify a value from images or OCR, set it **exactly** to:
  - `Not specified` inside `canadian_english.parameters`
  - `Non précisé` inside `canadian_french.parameters`

## Parameter keys (exact spelling, all required in each locale block)
{key_lines}

{operator_block}

## Output format (JSON only — no markdown)
Return one object with top-level keys **only** `canadian_english` and `canadian_french`. Each value must be an object with:
- `title` (string, non-empty)
- `description` (string, non-empty)
- `category` (string, retail path with ` > ` in that locale)
- `parameters` (object): exactly the keys above; string values only; use the missing-value rules.

## Language rules
- `canadian_english` strings: English only (no Chinese characters).
- `canadian_french` strings: French only (Canadian French; no Chinese characters). Romanize Chinese brand names.
""".strip()

    user_body = _step4_user_message_content_dual(prompt, source_image, cleaned_image, w)
    data = chat.chat_json(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You output a single JSON object with keys canadian_english and canadian_french only. "
                    "Each locale includes title, description, category, and parameters with all required keys. "
                    "Ground content in the provided images and OCR. No markdown fences."
                ),
            },
            {"role": "user", "content": user_body},
        ],
        max_tokens=min(8192, _step4_max_tokens() + 1536),
        temperature=0.25,
        response_json_object=False,
    )
    data_dict = data if isinstance(data, dict) else {}
    en_block = _coerce_step4_locale_block(data_dict, "canadian_english")
    fr_block = _coerce_step4_locale_block(data_dict, "canadian_french")
    en_listing = _listing_from_dual_structured_block(en_block, "English", STEP4_PARAM_MISSING_EN)
    fr_listing = _listing_from_dual_structured_block(fr_block, "French", STEP4_PARAM_MISSING_FR)
    return en_listing, fr_listing


def strip_chinese(text: str) -> str:
    """Remove CJK characters to enforce English/French-only user fields."""
    return "".join(ch for ch in text if not ("\u4e00" <= ch <= "\u9fff"))


def _structured_field_weak(val: Any) -> bool:
    s = str(val or "").strip()
    if not s:
        return True
    low = s.lower()
    if low in ("unknown", "n/a", "none", "unspecified"):
        return True
    if low in ("general merchandise", "marchandise générale"):
        return True
    return False


def enrich_structured_attributes_from_ocr(
    extracted_text: List[str],
    structured: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill gaps when Step3 vision/chat failed: infer product class, brand, and specs from OCR lines (no LLM)."""
    out: Dict[str, Any] = {**structured}
    blob = " ".join(str(x) for x in extracted_text)

    changed = False
    if _structured_field_weak(out.get("product_type")):
        if any(k in blob for k in ("吹风机", "电吹风", "風筒", "电风吹")):
            out["product_type"] = "hair dryer"
            changed = True

    if _structured_field_weak(out.get("category_hint")):
        if str(out.get("product_type") or "").strip().lower() == "hair dryer":
            out["category_hint"] = "Personal care appliances"
            changed = True

    brand_cur = str(out.get("brand_or_series") or "").strip()
    if not brand_cur:
        if "康夫" in blob or "KANGFU" in blob.upper():
            out["brand_or_series"] = "Kangfu"
            changed = True

    specs: List[str] = []
    raw_specs = out.get("size_or_specs")
    if isinstance(raw_specs, list):
        specs = [str(x).strip() for x in raw_specs if str(x).strip()]
    spec_join = " ".join(specs).lower()

    m_w = re.search(r"(\d{3,5})\s*W(?:atts)?", blob, re.I)
    if m_w:
        pw = m_w.group(0).strip().replace(" ", "")
        if "watts" in pw.lower():
            pw = re.sub(r"watts", "W", pw, flags=re.I)
        if pw.lower() not in spec_join:
            specs.append(pw if pw.upper().endswith("W") else f"{pw}W")

    for line in extracted_text:
        t = str(line).strip()
        if re.fullmatch(r"\d{4}", t) and t not in spec_join and f"model {t}" not in spec_join:
            specs.append(f"Model {t}")
            break

    if "送风嘴" in blob or "风嘴" in blob:
        mm = re.search(r"(\d+)\s*个", blob)
        nnoz = mm.group(1) if mm else "2"
        hint = f"{nnoz} styling nozzles included"
        if hint.lower() not in spec_join:
            specs.append(hint)

    out["size_or_specs"] = specs

    if str(out.get("confidence") or "").lower() == "low" and changed:
        out["confidence"] = "medium_ocr_fallback"

    return out


def _listing_param_placeholders(target: Literal["canadian_english", "canadian_french"]) -> Dict[str, str]:
    miss = STEP4_PARAM_MISSING_EN if target == "canadian_english" else STEP4_PARAM_MISSING_FR
    return {f"param_{k}": miss for k in LISTING_PARAMETER_KEYS}


# Minimal EN→FR labels for heuristic listing titles/params when vision API is down.
_HEURISTIC_PRODUCT_TYPE_EN_TO_FR: Dict[str, str] = {
    "hair dryer": "Sèche-cheveux",
}
_HEURISTIC_CATEGORY_EN_TO_FR: Dict[str, str] = {
    "personal care appliances": "Électroménager de soins personnels",
    "general merchandise": "Marchandise générale",
}


def _heuristic_listing_param_fills(
    structured: Dict[str, Any],
    extracted: List[str],
    target: Literal["canadian_english", "canadian_french"],
) -> Dict[str, str]:
    """Fill param_* from enriched structured + OCR when models did not return JSON."""
    blob = " ".join(str(x) for x in extracted)
    is_fr = target == "canadian_french"
    fills: Dict[str, str] = {}

    brand = str(structured.get("brand_or_series") or "").strip()
    if brand:
        fills["param_brand"] = "KANGFU" if brand.lower() == "kangfu" else brand

    pt = str(structured.get("product_type") or "").strip()
    if pt and not _structured_field_weak(pt):
        fills["param_product_type"] = (
            _HEURISTIC_PRODUCT_TYPE_EN_TO_FR.get(pt.lower(), pt) if is_fr else pt
        )

    for line in extracted:
        t = str(line).strip()
        if re.fullmatch(r"\d{4}", t):
            fills["param_model"] = t
            break

    m_w = re.search(r"(\d{3,5})\s*W(?:atts)?", blob, re.I)
    if m_w:
        raw = m_w.group(0).strip().replace(" ", "")
        if not re.search(r"W", raw, re.I):
            raw = f"{raw}W"
        fills["param_power"] = raw

    if "送风嘴" in blob or "风嘴" in blob:
        mm = re.search(r"(\d+)\s*个", blob)
        nnoz = mm.group(1) if mm else "2"
        fills["param_included_accessories"] = (
            f"{nnoz} buses de concentration" if is_fr else f"{nnoz} styling nozzles"
        )

    if "CCC" in blob.upper() or re.search(r"\b3C\b", blob):
        fills["param_certifications_visible"] = (
            "Certification CCC visible sur le produit"
            if is_fr
            else "CCC certification visible on product"
        )

    return {k: v for k, v in fills.items() if v}


def build_step4_heuristic_listing(
    structured: Dict[str, Any],
    extracted: List[str],
    target: Literal["canadian_english", "canadian_french"],
) -> LocalizedListing:
    """Deterministic draft from Step3 + OCR when copy models return nothing usable (always non-empty body)."""
    ptype_raw = str(structured.get("product_type") or "").strip()
    cat_raw = str(structured.get("category_hint") or "").strip()
    if target == "canadian_french":
        ptype = _HEURISTIC_PRODUCT_TYPE_EN_TO_FR.get(ptype_raw.lower(), strip_chinese(ptype_raw)) or (
            strip_chinese(ptype_raw) or "Produit"
        )
        cat = _HEURISTIC_CATEGORY_EN_TO_FR.get(cat_raw.lower(), strip_chinese(cat_raw)) or (
            strip_chinese(cat_raw) or "Marchandise générale"
        )
    else:
        ptype = strip_chinese(ptype_raw) or "Product"
        cat = strip_chinese(cat_raw) or "General Merchandise"
    material = strip_chinese(str(structured.get("material") or "").strip())
    brand = strip_chinese(str(structured.get("brand_or_series") or "").strip())
    feats = structured.get("key_features")
    if not isinstance(feats, list):
        feats = []
    feat_lines = [strip_chinese(str(x).strip()) for x in feats[:10]]
    feat_lines = [x for x in feat_lines if x]
    specs = structured.get("size_or_specs")
    if not isinstance(specs, list):
        specs = []
    spec_lines = [strip_chinese(str(x).strip()) for x in specs[:8]]
    spec_lines = [x for x in spec_lines if x]
    ocr_lines: List[str] = []
    for line in extracted[:15]:
        s = strip_chinese(str(line).strip())
        if len(s) > 1:
            ocr_lines.append(s[:240])
    conf = strip_chinese(str(structured.get("confidence") or "").strip())

    if target == "canadian_english":
        title = f"{ptype} — {cat}"[:220]
        parts: List[str] = [
            "Draft listing assembled from vision + OCR because the copy models did not return usable JSON.",
            "Review and replace with verified marketing copy before publishing.",
            "",
            f"**Product type:** {ptype}",
            f"**Category:** {cat}",
        ]
        if brand:
            parts.append(f"**Brand / series:** {brand}")
        if material:
            parts.append(f"**Material:** {material}")
        if feat_lines:
            parts.extend(["", "**Highlights:**", *[f"- {x}" for x in feat_lines]])
        if spec_lines:
            parts.extend(["", "**Size / specs:**", *[f"- {x}" for x in spec_lines]])
        if ocr_lines:
            parts.extend(["", "**On-image text (Latin / numbers only, Chinese stripped):**", *[f"- {x}" for x in ocr_lines]])
        if conf:
            parts.append(f"\n*(Vision confidence: {conf})*")
        base_attrs = _listing_param_placeholders("canadian_english")
        for pk, pv in _heuristic_listing_param_fills(structured, extracted, "canadian_english").items():
            base_attrs[pk] = pv
        base_attrs["draft_source"] = "heuristic_en"
        base_attrs["needs_human_review"] = "true"
        return LocalizedListing(
            title=title,
            description="\n".join(parts),
            category=cat[:200],
            key_attributes=base_attrs,
        )

    title_fr = f"{ptype} — {cat}"[:220]
    parts_fr: List[str] = [
        "Ébauche construite à partir de la vision et de l'OCR : les modèles n'ont pas renvoyé de JSON exploitable.",
        "À réviser avant publication.",
        "",
        f"**Type de produit :** {ptype}",
        f"**Catégorie :** {cat}",
    ]
    if brand:
        parts_fr.append(f"**Marque / série :** {brand}")
    if material:
        parts_fr.append(f"**Matériau :** {material}")
    if feat_lines:
        parts_fr.extend(["", "**Points saillants :**", *[f"- {x}" for x in feat_lines]])
    if spec_lines:
        parts_fr.extend(["", "**Taille / specs :**", *[f"- {x}" for x in spec_lines]])
    if ocr_lines:
        parts_fr.extend(
            [
                "",
                "**Texte sur l'image (latin/chiffres seulement, chinois retiré) :**",
                *[f"- {x}" for x in ocr_lines],
            ]
        )
    if conf:
        parts_fr.append(f"\n*(Confiance vision : {conf})*")
    base_fr = _listing_param_placeholders("canadian_french")
    for pk, pv in _heuristic_listing_param_fills(structured, extracted, "canadian_french").items():
        base_fr[pk] = pv
    base_fr["source_brouillon"] = "heuristique_fr"
    base_fr["revision_requise"] = "true"
    return LocalizedListing(
        title=title_fr,
        description="\n".join(parts_fr),
        category=cat[:200],
        key_attributes=base_fr,
    )


def path_to_data_url(path: Path, force_png: bool = False) -> str:
    data = path.read_bytes()
    if force_png:
        mime = "image/png"
    else:
        ext = path.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in {"jpg", "jpeg"} else f"image/{ext or 'png'}"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _rq_outcome_debug_enabled() -> bool:
    return (os.getenv("GMI_RQ_OUTCOME_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def _rq_outcome_debug_summary(outcome: Any, max_keys: int = 36) -> str:
    if outcome is None:
        return "outcome=None"
    if not isinstance(outcome, dict):
        return f"type={type(outcome).__name__} repr={repr(outcome)[:400]}"
    parts: List[str] = []
    for i, (k, v) in enumerate(sorted(outcome.keys())):
        if i >= max_keys:
            parts.append("…")
            break
        if isinstance(v, dict):
            parts.append(f"{k}=dict(keys={list(v.keys())[:10]})")
        elif isinstance(v, list):
            el = type(v[0]).__name__ if v else "empty"
            parts.append(f"{k}=list(n={len(v)},{el})")
        elif isinstance(v, str):
            parts.append(f"{k}=str(n={len(v)},head={v[:72]!r})")
        elif isinstance(v, (bytes, bytearray)):
            parts.append(f"{k}=bytes(n={len(v)})")
        else:
            parts.append(f"{k}={type(v).__name__}")
    return "; ".join(parts) if parts else "{}"


def _log_rq_outcome_debug(component: str, outcome: Any) -> None:
    if not _rq_outcome_debug_enabled():
        return
    print(f"GMI RQ outcome debug [{component}]: {_rq_outcome_debug_summary(outcome)}", file=sys.stderr)


def extract_media_url(outcome: Dict[str, Any]) -> Optional[str]:
    media_urls = outcome.get("media_urls")
    if isinstance(media_urls, list) and media_urls:
        first = media_urls[0]
        if isinstance(first, dict):
            for kk in ("url", "uri", "href", "signed_url", "output_url", "image_url"):
                u = first.get(kk)
                if isinstance(u, str) and u.startswith("http"):
                    return u
        elif isinstance(first, str) and first.startswith("http"):
            return first
    for key in (
        "image_url",
        "preview_image_url",
        "thumbnail_image_url",
        "url",
        "output_url",
        "result_url",
        "uri",
        "href",
        "signed_url",
    ):
        val = outcome.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return None


def _decode_inline_image_string(s: str) -> Optional[bytes]:
    """Decode data-URL or raw base64 image string from RQ outcome (Gemini / some gateways)."""
    s = (s or "").strip()
    if not s:
        return None
    if s.startswith("data:") and "base64," in s:
        try:
            raw = base64.b64decode(s.split("base64,", 1)[1].strip())
            return raw if raw else None
        except Exception:
            return None
    if len(s) > 80:
        try:
            raw = base64.b64decode(s, validate=False)
            if len(raw) > 200 and (raw[:4] == b"\x89PNG" or raw[:2] == b"\xff\xd8" or raw[:4] == b"RIFF"):
                return raw
        except Exception:
            pass
    return None


def extract_media_bytes_from_outcome(outcome: Any, _depth: int = 0) -> Optional[bytes]:
    """HTTP URL fetch first (via extract_media_url); then nested dict / list / base64 fields.

    Some gateways put images in ``images`` / ``outputs`` lists or use ``uri`` instead of ``url``.
    Recursion is depth-capped to avoid cycles.
    """
    if _depth > 8 or not isinstance(outcome, dict):
        return None
    for dk in ("url", "image_url", "output_url", "result_url", "preview_image_url", "uri", "href"):
        v = outcome.get(dk)
        if isinstance(v, str) and v.startswith("data:"):
            got = _decode_inline_image_string(v)
            if got:
                return got
    url = extract_media_url(outcome)
    if url:
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            return r.content
        except Exception:
            pass
    for key in (
        "image",
        "output_image",
        "result_image",
        "image_base64",
        "b64_json",
        "base64_image",
        "base64",
        "b64",
        "output_b64",
        "bytes",
    ):
        v = outcome.get(key)
        if isinstance(v, str):
            got = _decode_inline_image_string(v)
            if got:
                return got
        elif isinstance(v, (bytes, bytearray)) and len(v) > 200:
            return bytes(v)
    for key in (
        "images",
        "image_list",
        "output_images",
        "generated_images",
        "results",
        "outputs",
        "artifacts",
        "candidates",
    ):
        v = outcome.get(key)
        if not isinstance(v, list):
            continue
        for item in v:
            if isinstance(item, str):
                if item.startswith("http"):
                    try:
                        r = requests.get(item, timeout=120)
                        r.raise_for_status()
                        return r.content
                    except Exception:
                        continue
                got = _decode_inline_image_string(item)
                if got:
                    return got
            elif isinstance(item, dict):
                got = extract_media_bytes_from_outcome(item, _depth + 1)
                if got:
                    return got
            elif isinstance(item, (bytes, bytearray)) and len(item) > 200:
                return bytes(item)
    for wrap in ("result", "data", "output", "response", "outcome", "image_result"):
        inner = outcome.get(wrap)
        if isinstance(inner, dict):
            got = extract_media_bytes_from_outcome(inner, _depth + 1)
            if got:
                return got
        elif isinstance(inner, list):
            got = extract_media_bytes_from_outcome({"images": inner}, _depth + 1)
            if got:
                return got
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
        "annotation_audit_fallback_used": int(stats.get("annotation_audit_fallback_used", 0)),
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
            f"- Annotation audit fallback (mask_mode): `{snapshot['annotation_audit_fallback_used']}`",
            f"- Avg warning events per item: `{snapshot['avg_warning_events_per_item']}`",
            f"- Estimated clean item ratio: `{snapshot['estimated_clean_item_ratio']:.2%}`",
        ]
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text.strip())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "item"


def _listing_param_label(param_key: str) -> str:
    """param_brand -> Brand; param_product_type -> Product Type"""
    base = param_key[6:] if param_key.startswith("param_") else param_key
    return base.replace("_", " ").strip().title()


def _partition_listing_key_attributes(attrs: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    param: Dict[str, str] = {}
    other: Dict[str, str] = {}
    for k, v in attrs.items():
        sk = str(k)
        if sk.startswith("param_"):
            param[sk] = str(v)
        else:
            other[sk] = str(v)
    return param, other


def _format_parameters_markdown_section(param_attrs: Dict[str, str], title: str) -> List[str]:
    lines = [title, ""]
    ordered_keys = [f"param_{k}" for k in LISTING_PARAMETER_KEYS]
    for pk in ordered_keys:
        if pk in param_attrs:
            lines.append(f"- **{_listing_param_label(pk)}**: {param_attrs[pk]}")
    for pk in sorted(k for k in param_attrs if k not in ordered_keys):
        lines.append(f"- **{_listing_param_label(pk)}**: {param_attrs[pk]}")
    if len(lines) == 2:
        lines.append("- _(none)_")
    lines.append("")
    return lines


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

        en_param, en_other = _partition_listing_key_attributes(dict(artifact.canadian_english.key_attributes))
        fr_param, fr_other = _partition_listing_key_attributes(dict(artifact.canadian_french.key_attributes))
        en_other_lines = [f"- {k}: {v}" for k, v in sorted(en_other.items())] or ["- _(none)_"]
        fr_other_lines = [f"- {k}: {v}" for k, v in sorted(fr_other.items())] or ["- _(aucun)_"]
        en_text = "\n".join(
            [
                f"# {artifact.canadian_english.title}",
                "",
                f"**Category**: {artifact.canadian_english.category}",
                "",
                artifact.canadian_english.description,
                "",
                *_format_parameters_markdown_section(en_param, "## Parameters"),
                "## Other attributes",
                *en_other_lines,
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
                *_format_parameters_markdown_section(fr_param, "## Paramètres"),
                "## Autres attributs",
                *fr_other_lines,
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
        "annotation_audit_fallback_used": 0,
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
            audit_model = (getattr(args, "annotation_audit_model", "") or "").strip() or args.vision_model
            if getattr(args, "annotation_audit", True) and not chat.mock and spans:
                audited = audit_mtwi_annotation_spans(
                    chat=chat,
                    model=audit_model,
                    source_image=source_image,
                    spans=spans,
                    warnings=warnings,
                )
                if audited is not None:
                    erased_span_indices = audited
                    if not audited and spans:
                        warnings.append(
                            "annotation_audit_empty_mask: VLM selected no inpaint targets; "
                            "fix txt/quads or pass --no-annotation-audit to use --mask-mode only"
                        )
                else:
                    stability_stats["annotation_audit_fallback_used"] += 1
            if erased_span_indices is None:
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

        listing_mode = getattr(args, "copy_understand_image", "final")
        pre_additional_paths: List[str] = []
        listing_ref_audit: Optional[Dict[str, Any]] = None
        step3_img: Path = final_path or erased_path or source_image
        image_context = "clean_main"

        if listing_mode == "source":
            step3_img = source_image
            image_context = "original_raw"
        elif listing_mode == "extra1":
            if not args.generate_additional_images or int(getattr(args, "additional_image_count", 0) or 0) < 1:
                warnings.append(
                    "copy_understand_extra1_unavailable: need generate-additional-images and count>=1; using final for step3"
                )
            elif not final_path:
                warnings.append("copy_understand_extra1_unavailable: no final_path; using source for step3")
                step3_img = source_image
                image_context = "original_raw"
            else:
                mini_struct: Dict[str, Any] = {"product_type": "", "key_features": extracted[:6] if extracted else []}
                early: List[str] = []
                try:
                    early = generate_additional_product_images(
                        rq=rq,
                        source_for_generation=final_path,
                        product_id=product_id,
                        out_dir=work_img_dir,
                        model=args.additional_image_model,
                        count=1,
                        structured_attributes=mini_struct,
                        user_image_instructions=user_image_resolved,
                        scenario_offset=0,
                        first_file_index=1,
                        warnings=warnings,
                    )
                except Exception as exc:
                    warnings.append(f"listing_extra1_pregen_failed: {exc}")
                if early:
                    ex_path = Path(early[0])
                    pre_additional_paths = [str(ex_path)]
                    listing_ref_audit = run_listing_reference_consistency_audit(
                        chat=chat,
                        model=args.vision_model,
                        original_listing_image=source_image,
                        marketing_variant_image=ex_path,
                        warnings=warnings,
                    )
                    if listing_ref_audit.get("safe_to_use_variant_for_copy"):
                        step3_img = ex_path
                        image_context = "marketing_variant"
                    else:
                        dn = (listing_ref_audit.get("drift_notes") or "")[:220]
                        warnings.append(f"listing_reference_audit_reject_extra1: {dn}")
                else:
                    warnings.append("listing_extra1_pregen_empty: using final for step3")

        try:
            structured = run_step3_understand_product(
                chat=chat,
                model=args.vision_model,
                product_id=product_id,
                vision_image=step3_img,
                extracted_text=extracted,
                image_context=image_context,
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

        structured = enrich_structured_attributes_from_ocr(extracted, structured)
        if structured.get("confidence") == "medium_ocr_fallback":
            warnings.append("structured_attributes_enriched_from_ocr: vision step unavailable or weak; filled gaps from on-image text")

        step4_primary_failed = False
        cleaned_listing_image: Optional[Path] = final_path or erased_path
        if args.copy_generation_mode == "unified":
            warnings.append("step4_copy_generation_mode_unified")
            try:
                en, fr = run_step4_generate_listing_dual_image_bilingual(
                    chat=chat,
                    model=args.unified_copy_model,
                    source_image=source_image,
                    cleaned_image=cleaned_listing_image,
                    structured_attributes=structured,
                    extracted_text=extracted,
                    user_copy_instructions=user_copy_resolved,
                    warnings=warnings,
                )
                if _step4_copy_listing_degenerate(en) or _step4_copy_listing_degenerate(fr):
                    raise RuntimeError("step4_unified_degenerate_listing")
            except Exception as exc:
                step4_primary_failed = True
                warnings.append(f"step4_unified_copy_primary_failed: {exc}")
                try:
                    en, fr = run_step4_generate_listing_dual_image_bilingual(
                        chat=chat,
                        model=args.fallback_english_copy_model,
                        source_image=source_image,
                        cleaned_image=cleaned_listing_image,
                        structured_attributes=structured,
                        extracted_text=extracted,
                        user_copy_instructions=user_copy_resolved,
                        warnings=warnings,
                    )
                    if _step4_copy_listing_degenerate(en) or _step4_copy_listing_degenerate(fr):
                        raise RuntimeError("step4_unified_fallback_degenerate_listing")
                    warnings.append(f"step4_unified_copy_fallback_used: {args.fallback_english_copy_model}")
                    stability_stats["step4_copy_fallback_used"] += 1
                except Exception as fb_exc:
                    warnings.append(f"step4_unified_copy_fallback_failed: {fb_exc}")
                    stability_stats["step4_copy_fallback_failed"] += 1
                    en = build_step4_heuristic_listing(structured, extracted, "canadian_english")
                    fr = build_step4_heuristic_listing(structured, extracted, "canadian_french")
                    warnings.append("step4_unified_heuristic_used_after_model_failure")
        else:
            warnings.append("step4_copy_generation_mode_split_dual_image")
            try:
                en, fr = run_step4_generate_listing_dual_image_bilingual(
                    chat=chat,
                    model=args.english_copy_model,
                    source_image=source_image,
                    cleaned_image=cleaned_listing_image,
                    structured_attributes=structured,
                    extracted_text=extracted,
                    user_copy_instructions=user_copy_resolved,
                    warnings=warnings,
                )
                if _step4_copy_listing_degenerate(en) or _step4_copy_listing_degenerate(fr):
                    raise RuntimeError("step4_split_dual_degenerate_listing")
            except Exception as exc:
                step4_primary_failed = True
                warnings.append(f"step4_split_dual_copy_primary_failed: {exc}")
                try:
                    en, fr = run_step4_generate_listing_dual_image_bilingual(
                        chat=chat,
                        model=args.fallback_english_copy_model,
                        source_image=source_image,
                        cleaned_image=cleaned_listing_image,
                        structured_attributes=structured,
                        extracted_text=extracted,
                        user_copy_instructions=user_copy_resolved,
                        warnings=warnings,
                    )
                    if _step4_copy_listing_degenerate(en) or _step4_copy_listing_degenerate(fr):
                        raise RuntimeError("step4_split_dual_fallback_degenerate_listing")
                    warnings.append(f"step4_split_dual_copy_fallback_used: {args.fallback_english_copy_model}")
                    stability_stats["step4_copy_fallback_used"] += 1
                except Exception as fb_exc:
                    warnings.append(f"step4_split_dual_copy_fallback_failed: {fb_exc}")
                    stability_stats["step4_copy_fallback_failed"] += 1
                    en = build_step4_heuristic_listing(structured, extracted, "canadian_english")
                    fr = build_step4_heuristic_listing(structured, extracted, "canadian_french")
                    warnings.append("step4_split_dual_heuristic_used_after_model_failure")

            en, fr = _apply_simple_copy_recovery_if_needed(
                chat=chat,
                args=args,
                structured=structured,
                extracted=extracted,
                user_copy_resolved=user_copy_resolved,
                en=en,
                fr=fr,
                warnings=warnings,
                listing_image=step3_img,
            )

        if step4_primary_failed:
            stability_stats["step4_copy_failed"] += 1

        copy_review: Optional[Dict[str, Any]] = None
        locale_grammar_review: Optional[Dict[str, Any]] = None
        if getattr(args, "skip_listing_review", False):
            warnings.append("listing_review_skipped: Step4b and Step4c not run (--skip-listing-review or GMI_SKIP_LISTING_REVIEW)")
        else:
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

        additional_images: List[str] = list(pre_additional_paths)
        if args.generate_additional_images and final_path:
            n_extra = max(0, int(args.additional_image_count))
            need = max(0, n_extra - len(additional_images))
            if need > 0:
                try:
                    more = generate_additional_product_images(
                        rq=rq,
                        source_for_generation=final_path,
                        product_id=product_id,
                        out_dir=work_img_dir,
                        model=args.additional_image_model,
                        count=need,
                        structured_attributes=structured,
                        user_image_instructions=user_image_resolved,
                        scenario_offset=1 if pre_additional_paths else 0,
                        first_file_index=len(additional_images) + 1,
                        warnings=warnings,
                    )
                    additional_images.extend(more)
                    if len(more) < need:
                        warnings.append(
                            f"extra_images_shortfall: requested {need} from RQ model {args.additional_image_model!r}, "
                            f"got {len(more)} (see GMI_EXTRA_IMAGES_BATCH / per-shot defaults in generate_additional_product_images)."
                        )
                except Exception as exc:
                    warnings.append(f"extra_images_failed: {exc}")
        elif args.generate_additional_images and not final_path:
            warnings.append("extra_images_skipped_no_final_path")

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
                listing_reference_audit=listing_ref_audit,
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


def _default_copy_generation_mode() -> str:
    v = (os.getenv("GMI_COPY_GENERATION_MODE") or "unified").strip().lower()
    return v if v in ("split", "unified") else "unified"


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
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="Max retry attempts per external model/API call (capped at 2).",
    )
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
        default=os.getenv("GMI_ERASE_STRATEGY", "model"),
        help="Text erase: model = Request Queue (default; same default ID as --additional-image-model for quality). "
        "local = white+inpaint/OpenCV (no RQ erase call).",
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
    parser.set_defaults(generate_additional_images=True)
    _gen_img = parser.add_mutually_exclusive_group()
    _gen_img.add_argument(
        "--generate-additional-images",
        dest="generate_additional_images",
        action="store_true",
        help="Generate marketing variants via Request Queue (default: on; count from --additional-image-count).",
    )
    _gen_img.add_argument(
        "--no-generate-additional-images",
        dest="generate_additional_images",
        action="store_false",
        help="Skip extra same-product shots (alternate angle, in-use, scene).",
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
        default=os.getenv("GMI_MASK_MODE", "all"),
        choices=["overlay", "all"],
        help="With quad mask: 'all' = erase every annotated box (MTWI default). "
        "'overlay' = VLM + heuristics pick overlay/watermark boxes only (live); mock overlay falls back to all if heuristics match nothing.",
    )
    parser.set_defaults(annotation_audit=True)
    _ann_audit = parser.add_mutually_exclusive_group()
    _ann_audit.add_argument(
        "--annotation-audit",
        dest="annotation_audit",
        action="store_true",
        help="Before erase (live): VLM validates each MTWI quad+transcript and selects which boxes to inpaint; "
        "skips misaligned boxes. Mock mode always skips this step.",
    )
    _ann_audit.add_argument(
        "--no-annotation-audit",
        dest="annotation_audit",
        action="store_false",
        help="Skip pre-erase VLM audit; use --mask-mode only (all vs overlay) to choose boxes.",
    )
    parser.add_argument(
        "--annotation-audit-model",
        default=os.getenv("GMI_ANNOTATION_AUDIT_MODEL") or "",
        help="VLM for MTWI annotation audit (default: same as --vision-model).",
    )

    parser.add_argument(
        "--eraser-model",
        default=None,
        help="Step1 model erase (default: GMI_ERASER_MODEL if set, else same as --additional-image-model).",
    )
    parser.add_argument("--restore-model", default=os.getenv("GMI_RESTORE_MODEL", "bria-fibo-restore"), help="Step2 model")
    parser.add_argument(
        "--vision-model",
        default=os.getenv("GMI_VISION_MODEL", "Qwen/Qwen3-VL-235B"),
        help="Step3 VLM (multimodal structured attributes).",
    )
    parser.add_argument(
        "--copy-understand-image",
        choices=["final", "source", "extra1"],
        default=os.getenv("GMI_COPY_UNDERSTAND_IMAGE", "final"),
        help="Image for Step3 before copy: cleaned main (final), raw listing (source), or first marketing extra "
        "(extra1: pre-generates extra_1, runs VLM audit vs original, then Step3 on extra_1 if safe).",
    )
    parser.add_argument(
        "--copy-generation-mode",
        choices=["split", "unified"],
        default=_default_copy_generation_mode(),
        help="Step4: unified (default) = dual-image bilingual structured listing. split = same dual-image call but primary model "
        "is --english-copy-model (not per-locale parallel); then simple recovery.",
    )
    parser.add_argument(
        "--unified-copy-model",
        default=os.getenv("GMI_UNIFIED_COPY_MODEL")
        or os.getenv("GMI_ENGLISH_COPY_MODEL", "openai/gpt-5.4-pro"),
        help="Primary model when --copy-generation-mode unified (default: same as --english-copy-model / GPT-5.4-pro).",
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
        "--simple-copy-model",
        default=os.getenv("GMI_SIMPLE_COPY_MODEL")
        or os.getenv("GMI_FALLBACK_ENGLISH_COPY_MODEL", "openai/gpt-5.4-mini"),
        help="One-shot bilingual EN+FR JSON recovery when split copy falls back to heuristics (no response_format).",
    )
    parser.add_argument(
        "--no-simple-copy-recovery",
        action="store_true",
        help="Disable simple bilingual recovery (default: recovery is on when heuristics or empty descriptions are used).",
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
        "--skip-listing-review",
        action="store_true",
        help="Skip Step4b copy review and Step4c locale grammar (no extra chat calls; no review markdown in deliverables).",
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
    args.max_attempts = max(1, min(2, int(args.max_attempts)))
    skip_rev = (os.getenv("GMI_SKIP_LISTING_REVIEW") or "").strip().lower()
    if skip_rev in ("1", "true", "yes", "on"):
        args.skip_listing_review = True
    if getattr(args, "eraser_model", None) is None:
        args.eraser_model = (os.getenv("GMI_ERASER_MODEL") or "").strip() or args.additional_image_model
    if args.no_harmonize_after_erase:
        args.harmonize_after_erase = False
    gai_env = (os.getenv("GMI_GENERATE_ADDITIONAL_IMAGES") or "").strip().lower()
    if gai_env in ("0", "false", "no", "off"):
        args.generate_additional_images = False
    elif gai_env in ("1", "true", "yes", "on"):
        args.generate_additional_images = True
    aa_env = (os.getenv("GMI_ANNOTATION_AUDIT") or "").strip().lower()
    if aa_env in ("0", "false", "no", "off"):
        args.annotation_audit = False
    elif aa_env in ("1", "true", "yes", "on"):
        args.annotation_audit = True
    return args


if __name__ == "__main__":
    args = parse_args()
    output_path = run_pipeline(args)
    print(f"Saved ecommerce artifacts to {output_path}")

