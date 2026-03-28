"""Fully automated product-image text removal (no MTWI annotation required).

Pipeline: optional manual quads → PaddleOCR detection → binary mask → hybrid erase
(small text areas: local OpenCV inpaint; large areas: GMI Request Queue ``seedream-5.0-lite``)
→ optional color harmonize with ``bria-fibo-edit`` (erased image + original reference).

Uses the same GMI Request Queue client as ``mtwi_ecommerce_pipeline`` (Bearer token, 2s poll).

CLI::

    python src/auto_text_erase_preprocess.py --input-dir ./raw --output-dir ./out_auto \\
        --mock

Env: ``GMI_API_KEY``, ``GMI_MEDIA_BASE_URL`` (default RQ base), ``GMI_AUTO_ERASE_RQ_TIMEOUT`` (default 30),
``GMI_AUTO_ERASE_MAX_ATTEMPTS`` (enqueue retries, default 2 = one retry after failure).

Optional ``--quads-json`` schema (manual boxes, skips OCR for matched files)::

    {
      "files": {
        "SKU001.jpg": {"quads": [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ...]},
        "SKU002.png": {"quads": [...]}
      }
    }

Or a single global ``"quads": [...]`` array (same regions applied when resolving for an image — prefer ``files`` for batches).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from mtwi_ecommerce_pipeline import (  # noqa: E402
    RequestQueueClient,
    extract_media_bytes_from_outcome,
    extract_media_url,
    path_to_data_url,
    rq_image_generation_temperature,
)

try:
    from PIL import Image, ImageDraw  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore
    np = None  # type: ignore

FAILURE_HINT_MANUAL_QUADS = (
    "Automatic text detection or cloud erase did not complete successfully. "
    "Provide precise text regions as optional input: use --quads-json (or per-file entries under "
    "'files' in that JSON), or run the MTWI pipeline with annotated quads. "
    "Quad format: list of 4 points [[x1,y1],...,[x4,y4]] per text region."
)

DEFAULT_ERASER_MODEL = "seedream-5.0-lite"
DEFAULT_HARMONIZE_MODEL = "bria-fibo-edit"
DEFAULT_CONF = 0.5
DEFAULT_SMALL_AREA_PX = 500
DEFAULT_WORKERS = 4
DEFAULT_RQ_TIMEOUT = 30.0

_ocr_singleton = None
_ocr_lock = threading.Lock()
_progress_lock = threading.Lock()


def _rq_timeout_s() -> float:
    try:
        return float(os.getenv("GMI_AUTO_ERASE_RQ_TIMEOUT", str(DEFAULT_RQ_TIMEOUT)))
    except ValueError:
        return DEFAULT_RQ_TIMEOUT


def _rq_max_attempts() -> int:
    try:
        return max(1, int(os.getenv("GMI_AUTO_ERASE_MAX_ATTEMPTS", "2")))
    except ValueError:
        return 2


def quad_polygon_area(quad: List[List[int]]) -> float:
    if len(quad) < 3:
        return 0.0
    s = 0.0
    n = len(quad)
    for i in range(n):
        j = (i + 1) % n
        s += quad[i][0] * quad[j][1] - quad[j][0] * quad[i][1]
    return abs(s) / 2.0


def normalize_quad_points(raw: Any) -> Optional[List[List[int]]]:
    if not isinstance(raw, list) or len(raw) < 4:
        return None
    out: List[List[int]] = []
    for p in raw[:4]:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return None
        out.append([int(round(float(p[0]))), int(round(float(p[1])))])
    return out


def load_quads_json_file(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("quads JSON root must be an object")
    return data


def resolve_quads_for_image(
    quads_config: Optional[Dict[str, Any]],
    image_path: Path,
) -> Optional[List[List[List[int]]]]:
    if not quads_config:
        return None
    name = image_path.name
    stem = image_path.stem
    files = quads_config.get("files")
    if isinstance(files, dict):
        for key in (name, stem, f"{stem}.jpg", f"{stem}.png", f"{stem}.jpeg"):
            entry = files.get(key)
            if isinstance(entry, dict) and "quads" in entry:
                return _parse_quads_list(entry.get("quads"))
            if isinstance(entry, list):
                return _parse_quads_list(entry)
    if "quads" in quads_config:
        return _parse_quads_list(quads_config.get("quads"))
    return None


def _parse_quads_list(raw: Any) -> Optional[List[List[List[int]]]]:
    if not isinstance(raw, list):
        return None
    quads: List[List[List[int]]] = []
    for item in raw:
        nq = normalize_quad_points(item)
        if nq:
            quads.append(nq)
    return quads if quads else None


def get_paddle_ocr_engine():
    global _ocr_singleton
    if _ocr_singleton is None:
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PaddleOCR is required for automatic detection. Install: pip install paddleocr "
                "(and paddlepaddle for your platform). Or pass --quads-json to supply boxes."
            ) from exc
        _ocr_singleton = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    return _ocr_singleton


def detect_text_quads_paddle(
    image_path: Path,
    conf_threshold: float = DEFAULT_CONF,
) -> List[List[List[int]]]:
    """Run PaddleOCR detection+recognition; keep boxes with score >= conf_threshold.

    Returns quads as ``List[List[List[int]]]`` (four integer corners per box).
    """
    if Image is None:
        raise RuntimeError("Pillow is required for OCR input handling.")
    engine = get_paddle_ocr_engine()
    with _ocr_lock:
        result = engine.ocr(str(image_path), cls=True)
    quads: List[List[List[int]]] = []
    if not result:
        return quads
    lines = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else []
    for line in lines:
        if not line or len(line) < 2:
            continue
        box = line[0]
        meta = line[1]
        conf = 1.0
        if isinstance(meta, (list, tuple)) and len(meta) >= 2:
            try:
                conf = float(meta[1])
            except (TypeError, ValueError):
                conf = 1.0
        if conf < conf_threshold:
            continue
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            continue
        nq = normalize_quad_points(list(box))
        if nq:
            quads.append(nq)
    return quads


def build_binary_mask(
    width: int,
    height: int,
    quads: List[List[List[int]]],
    dilate_px: int = 0,
) -> "Image.Image":
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow is required for mask generation.")
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for quad in quads:
        pts = [(max(0, min(width - 1, p[0])), max(0, min(height - 1, p[1]))) for p in quad[:4]]
        draw.polygon(pts, fill=255)
    if dilate_px > 0 and cv2 is not None and np is not None:
        m = np.array(mask, dtype=np.uint8)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1, dilate_px * 2 + 1))
        m = cv2.dilate(m, k, iterations=1)
        mask = Image.fromarray(m, mode="L")
    return mask


def save_mask(mask: "Image.Image", path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(path)
    return path


def local_inpaint_with_mask(source_image: Path, mask: "Image.Image", out_path: Path) -> Path:
    if Image is None:
        raise RuntimeError("Pillow is required.")
    img = Image.open(source_image).convert("RGB")
    if img.size != mask.size:
        mask = mask.resize(img.size, Image.Resampling.NEAREST)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if cv2 is None or np is None:
        img.save(out_path)
        return out_path
    mask_u8 = np.array(mask, dtype=np.uint8)
    if not np.any(mask_u8):
        img.save(out_path)
        return out_path
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
    Image.fromarray(out_rgb, mode="RGB").save(out_path)
    return out_path


def all_quads_small(quads: List[List[List[int]]], area_threshold: float) -> bool:
    if not quads:
        return True
    return all(quad_polygon_area(q) < area_threshold for q in quads)


def run_rq_erase_with_mask(
    rq: RequestQueueClient,
    source_image: Path,
    mask_path: Path,
    eraser_model: str,
    erased_out: Path,
    user_image_instructions: str = "",
) -> Tuple[bool, str, Optional[str]]:
    """Returns (ok, message, result_image_url_if_any). Uses same payload shape as ``run_image_edit``."""
    edit_prompt = (
        "TASK: Remove text overlays only.\n"
        "Edits allowed: remove all overlaid text, watermarks, banners, stickers in the white mask regions.\n"
        "Edits NOT allowed: do not change the product, do not add/remove items, do not change shape, "
        "color, count, packaging, branding, or material outside masked areas.\n"
        "Preserve camera view, lighting, and background perspective. Photorealistic result.\n"
        "Output must contain no visible text in masked areas and no watermark."
    )
    if user_image_instructions.strip():
        edit_prompt += "\n\nOperator notes: " + user_image_instructions.strip()
    if rq.mock:
        data = source_image.read_bytes()
        erased_out.parent.mkdir(parents=True, exist_ok=True)
        erased_out.write_bytes(data)
        return True, "mock_ok", None
    image_data_url = path_to_data_url(source_image)
    payload: Dict[str, Any] = {
        "prompt": edit_prompt,
        "image": image_data_url,
        "input_image": image_data_url,
        "image_url": image_data_url,
        "output_format": "png",
        "watermark": False,
        "temperature": rq_image_generation_temperature(),
    }
    if mask_path.exists():
        mask_data_url = path_to_data_url(mask_path, force_png=True)
        payload["mask"] = mask_data_url
        payload["mask_image"] = mask_data_url
    try:
        outcome = rq.run_model(model=eraser_model, payload=payload, timeout_s=_rq_timeout_s())
    except Exception as exc:
        return False, f"rq_erase_exception: {exc}", None
    url = extract_media_url(outcome) if isinstance(outcome, dict) else None
    result_bytes = extract_media_bytes_from_outcome(outcome)
    if not result_bytes:
        return False, "rq_erase_empty_result", url
    erased_out.parent.mkdir(parents=True, exist_ok=True)
    erased_out.write_bytes(result_bytes)
    return True, "ok", url


def run_harmonize_dual_image(
    rq: RequestQueueClient,
    original_image: Path,
    erased_image: Path,
    harmonize_model: str,
    user_image_instructions: str = "",
) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Harmonize erased image toward original color/tone. Returns (bytes, url_or_none, error)."""
    if rq.mock:
        return erased_image.read_bytes(), None, None
    timeout_s = _rq_timeout_s()
    orig_url = path_to_data_url(original_image)
    erased_url = path_to_data_url(erased_image)
    prompt = (
        "Color harmonization for ecommerce product photo. "
        "The second input (edited image) is the same product scene after text was removed from masked regions. "
        "The first input is the original photo. Match overall white balance, saturation, and brightness of the "
        "edited image to the original where appropriate; keep inpainted regions seamless and natural. "
        "Do not restore removed text or watermarks. Do not add new text or logos. Photorealistic output."
    )
    if user_image_instructions.strip():
        prompt += " Operator notes: " + user_image_instructions.strip()
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "image": erased_url,
        "input_image": erased_url,
        "image_url": erased_url,
        "original_image": orig_url,
        "reference_image": orig_url,
        "source_reference": orig_url,
        "output_format": "png",
        "watermark": False,
        "temperature": rq_image_generation_temperature(),
    }
    try:
        outcome = rq.run_model(model=harmonize_model, payload=payload, timeout_s=timeout_s)
    except Exception as exc:
        return None, None, str(exc)
    url = extract_media_url(outcome) if isinstance(outcome, dict) else None
    data = extract_media_bytes_from_outcome(outcome)
    return data, url, None if data else "harmonize_no_bytes"


@dataclass
class ProcessResult:
    input_path: str
    output_dir: str
    status: str
    message: str
    num_quads: int
    used_ocr: bool
    used_manual_quads: bool
    hybrid_local_only: bool
    erased_path: Optional[str] = None
    final_path: Optional[str] = None
    result_image_url: Optional[str] = None
    suggestion: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


def process_one_image(
    source_image: Path,
    output_subdir: Path,
    rq: RequestQueueClient,
    *,
    quads_config: Optional[Dict[str, Any]] = None,
    conf_threshold: float = DEFAULT_CONF,
    small_area_px: float = DEFAULT_SMALL_AREA_PX,
    hybrid: bool = True,
    dilate_px: int = 0,
    eraser_model: str = DEFAULT_ERASER_MODEL,
    harmonize_model: str = DEFAULT_HARMONIZE_MODEL,
    skip_harmonize: bool = False,
    user_image_instructions: str = "",
) -> ProcessResult:
    output_subdir.mkdir(parents=True, exist_ok=True)
    product_id = source_image.stem
    meta_path = output_subdir / "meta.json"
    manual = resolve_quads_for_image(quads_config, source_image)
    used_manual = manual is not None
    quads: List[List[List[int]]] = list(manual) if manual else []
    used_ocr = False

    if not quads:
        try:
            quads = detect_text_quads_paddle(source_image, conf_threshold=conf_threshold)
            used_ocr = True
        except Exception as exc:
            msg = f"ocr_failed: {exc}"
            print(f"[auto-erase] {source_image.name}: {msg}", file=sys.stderr)
            final_p = output_subdir / f"{product_id}_final.png"
            if Image:
                Image.open(source_image).convert("RGB").save(final_p)
            else:
                final_p.write_bytes(source_image.read_bytes())
            res = ProcessResult(
                str(source_image),
                str(output_subdir),
                "ocr_failed",
                msg,
                0,
                False,
                False,
                False,
                erased_path=str(final_p),
                final_path=str(final_p),
                suggestion=FAILURE_HINT_MANUAL_QUADS,
            )
            meta_path.write_text(json.dumps({**asdict(res), "quads": []}, indent=2), encoding="utf-8")
            return res

    if not quads:
        final_p = output_subdir / f"{product_id}_final.png"
        if Image:
            Image.open(source_image).convert("RGB").save(final_p)
        else:
            final_p.write_bytes(source_image.read_bytes())
        res = ProcessResult(
            str(source_image),
            str(output_subdir),
            "no_text_detected",
            "No text regions above confidence threshold; copied source.",
            0,
            used_ocr,
            used_manual,
            False,
            erased_path=str(final_p),
            final_path=str(final_p),
        )
        meta_path.write_text(json.dumps({**asdict(res), "quads": []}, indent=2), encoding="utf-8")
        return res

    img0 = Image.open(source_image)
    w, h = img0.size
    mask = build_binary_mask(w, h, quads, dilate_px=dilate_px)
    mask_path = output_subdir / f"{product_id}_mask.png"
    save_mask(mask, mask_path)

    use_local = hybrid and all_quads_small(quads, small_area_px)
    erased_path = output_subdir / f"{product_id}_erased.png"
    hybrid_local_only = False
    erase_msg = ""
    result_url: Optional[str] = None

    if use_local:
        hybrid_local_only = True
        local_inpaint_with_mask(source_image, mask, erased_path)
        erase_msg = "local_inpaint_small_regions"
    else:
        ok, erase_msg, result_url = run_rq_erase_with_mask(
            rq,
            source_image,
            mask_path,
            eraser_model,
            erased_path,
            user_image_instructions=user_image_instructions,
        )
        if not ok:
            print(f"[auto-erase] {source_image.name}: erase failed ({erase_msg}), falling back to local inpaint.", file=sys.stderr)
            local_inpaint_with_mask(source_image, mask, erased_path)
            erase_msg = f"rq_failed_fallback_local: {erase_msg}"
        elif result_url:
            print(f"[auto-erase] {source_image.name}: erase result url {result_url}", flush=True)

    final_out = output_subdir / f"{product_id}_final.png"
    if skip_harmonize:
        if Image:
            Image.open(erased_path).convert("RGB").save(final_out)
        else:
            final_out.write_bytes(Path(erased_path).read_bytes())
        final_path = final_out
    else:
        h_bytes, h_url, h_err = run_harmonize_dual_image(
            rq,
            source_image,
            erased_path,
            harmonize_model,
            user_image_instructions=user_image_instructions,
        )
        if h_bytes:
            final_out.write_bytes(h_bytes)
            final_path = final_out
            if h_url:
                result_url = h_url
                print(f"[auto-erase] {source_image.name}: harmonize result url {h_url}", flush=True)
        else:
            print(
                f"[auto-erase] {source_image.name}: harmonize skipped or failed ({h_err}); using erased image.",
                file=sys.stderr,
            )
            if Image:
                Image.open(erased_path).convert("RGB").save(final_out)
            else:
                final_out.write_bytes(Path(erased_path).read_bytes())
            final_path = final_out

    status = "ok"
    message = erase_msg or "ok"
    suggestion = ""
    if "rq_failed" in erase_msg or "exception" in erase_msg.lower():
        status = "degraded"
        suggestion = FAILURE_HINT_MANUAL_QUADS

    res = ProcessResult(
        str(source_image),
        str(output_subdir),
        status,
        message,
        len(quads),
        used_ocr,
        used_manual,
        hybrid_local_only,
        erased_path=str(erased_path),
        final_path=str(final_path),
        result_image_url=result_url,
        suggestion=suggestion,
        meta={
            "quads": quads,
            "mask_path": str(mask_path),
        },
    )
    meta_payload = asdict(res)
    nested = meta_payload.pop("meta", None) or {}
    meta_payload.update(nested)
    meta_path.write_text(json.dumps(meta_payload, indent=2, default=str), encoding="utf-8")
    return res


def collect_images(input_dir: Path, patterns: Tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")) -> List[Path]:
    out: List[Path] = []
    for p in sorted(input_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in patterns:
            out.append(p)
    return out


def write_summary_csv(rows: List[ProcessResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "input_path",
        "output_dir",
        "status",
        "message",
        "num_quads",
        "used_ocr",
        "used_manual_quads",
        "hybrid_local_only",
        "erased_path",
        "final_path",
        "result_image_url",
        "suggestion",
    ]
    import csv

    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: getattr(r, k, "") for k in fieldnames})


def run_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    mock: bool = False,
    quads_json: Optional[Path] = None,
    resume: bool = False,
    max_workers: int = DEFAULT_WORKERS,
    conf_threshold: float = DEFAULT_CONF,
    small_area_px: float = DEFAULT_SMALL_AREA_PX,
    hybrid: bool = True,
    dilate_px: int = 0,
    skip_harmonize: bool = False,
    eraser_model: str = DEFAULT_ERASER_MODEL,
    harmonize_model: str = DEFAULT_HARMONIZE_MODEL,
    user_image_instructions: str = "",
) -> List[ProcessResult]:
    api_key = os.getenv("GMI_API_KEY", "")
    if not mock and not api_key.strip():
        raise RuntimeError("GMI_API_KEY is required unless --mock is set.")

    quads_config: Optional[Dict[str, Any]] = None
    if quads_json and quads_json.is_file():
        quads_config = load_quads_json_file(quads_json)

    rq = RequestQueueClient(api_key=api_key or "mock", mock=mock, max_attempts=_rq_max_attempts())
    images = collect_images(input_dir)
    results: List[ProcessResult] = []
    total = len(images)
    done = 0

    def job(img_path: Path) -> ProcessResult:
        sub = output_dir / img_path.stem
        if resume:
            final_p = sub / f"{img_path.stem}_final.png"
            if final_p.exists():
                return ProcessResult(
                    str(img_path),
                    str(sub),
                    "skipped_resume",
                    "Existing final output; skipped.",
                    0,
                    False,
                    False,
                    False,
                    final_path=str(final_p),
                )
        return process_one_image(
            img_path,
            sub,
            rq,
            quads_config=quads_config,
            conf_threshold=conf_threshold,
            small_area_px=small_area_px,
            hybrid=hybrid,
            dilate_px=dilate_px,
            eraser_model=eraser_model,
            harmonize_model=harmonize_model,
            skip_harmonize=skip_harmonize,
            user_image_instructions=user_image_instructions,
        )

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as ex:
        futures = {ex.submit(job, p): p for p in images}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = ProcessResult(
                    str(p),
                    str(output_dir / p.stem),
                    "error",
                    str(exc),
                    0,
                    False,
                    False,
                    False,
                    suggestion=FAILURE_HINT_MANUAL_QUADS,
                )
                (output_dir / p.stem).mkdir(parents=True, exist_ok=True)
                (output_dir / p.stem / "meta.json").write_text(
                    json.dumps(asdict(r), indent=2),
                    encoding="utf-8",
                )
            results.append(r)
            with _progress_lock:
                done += 1
                print(f"[auto-erase] progress {done}/{total} {p.name} -> {r.status}", flush=True)

    results.sort(key=lambda x: x.input_path)
    return results


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automated OCR + RQ text removal + harmonize (no MTWI required).")
    p.add_argument("--input-dir", type=Path, required=True, help="Directory of product images.")
    p.add_argument("--output-dir", type=Path, required=True, help="Output root (one subfolder per image).")
    p.add_argument("--quads-json", type=Path, default=None, help="Optional manual quads JSON (see module docstring).")
    p.add_argument("--mock", action="store_true", help="Do not call GMI; copy-through / local only.")
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip images that already have <stem>_final.png in their output folder.",
    )
    p.add_argument("--max-workers", type=int, default=DEFAULT_WORKERS, help="ThreadPoolExecutor size (default 4).")
    p.add_argument("--conf", type=float, default=DEFAULT_CONF, help="PaddleOCR score threshold (default 0.5).")
    p.add_argument("--small-area-px", type=float, default=DEFAULT_SMALL_AREA_PX, help="Hybrid: all boxes below this area use local inpaint.")
    p.add_argument("--no-hybrid", action="store_true", help="Always use RQ for erase when quads exist (when not mock).")
    p.add_argument("--mask-dilate", type=int, default=0, help="Optional mask dilation radius in pixels.")
    p.add_argument("--skip-harmonize", action="store_true", help="Skip bria-fibo-edit harmonize step.")
    p.add_argument("--eraser-model", default=os.getenv("GMI_ERASER_MODEL", DEFAULT_ERASER_MODEL))
    p.add_argument("--harmonize-model", default=os.getenv("GMI_HARMONIZE_MODEL", DEFAULT_HARMONIZE_MODEL))
    p.add_argument(
        "--user-image-instructions",
        default="",
        help="Optional short operator notes passed to erase/harmonize prompts.",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    rows = run_batch(
        args.input_dir,
        args.output_dir,
        mock=args.mock,
        quads_json=args.quads_json,
        resume=args.resume,
        max_workers=args.max_workers,
        conf_threshold=args.conf,
        small_area_px=args.small_area_px,
        hybrid=not args.no_hybrid,
        dilate_px=args.mask_dilate,
        skip_harmonize=args.skip_harmonize,
        eraser_model=args.eraser_model,
        harmonize_model=args.harmonize_model,
        user_image_instructions=args.user_image_instructions,
    )
    csv_path = args.output_dir / "auto_erase_summary.csv"
    write_summary_csv(rows, csv_path)
    print(f"[auto-erase] wrote {csv_path}", flush=True)
    failed = sum(1 for r in rows if r.status in {"error", "ocr_failed"})
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
