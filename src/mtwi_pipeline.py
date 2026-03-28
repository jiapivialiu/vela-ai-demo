"""MTWI image-to-copy pipeline.

Reads product images from the MTWI dataset, uses a vision model (Qwen3-VL)
to extract structured product information, then generates bilingual
(English/French) marketing copy via the existing DeepSeek pipeline.

Example:
    # Mock mode (no API calls)
    python src/mtwi_pipeline.py --limit 3 --mock

    # Live mode (requires GMI_API_KEY)
    python src/mtwi_pipeline.py --limit 1
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import requests

from press_on_nails_pipeline import (
    GMICloudLLMClient,
    LocalizedMaterial,
    StructuredProduct,
    split_style_tags,
    write_structured_output,
)

VISION_PROMPT = """\
You are a product image analyst. Examine this e-commerce product image and
extract structured information about the product shown.

Return a single JSON object with these fields:
- "category": string — product category in Chinese (e.g. "连衣裙", "手机壳"). If the image is not a product, use "unknown".
- "title": string — a short Chinese product title.
- "colors": string[] — main colours visible, in Chinese.
- "material": string — material if identifiable, otherwise "未知".
- "style_tags": string[] — 2-4 style descriptors in Chinese (e.g. ["甜美", "休闲"]).
- "target_audience": string — target audience in Chinese.
- "key_features": string[] — 2-3 key selling points in Chinese.
- "brief_description": string — one-sentence Chinese description.

Rules:
- Only output the JSON object. No markdown, no explanation.
- If the image does not show a product, set category to "unknown" and fill
  other fields with reasonable defaults.
"""

MOCK_VISION_RESULT: Dict[str, Any] = {
    "category": "连衣裙",
    "title": "夏季碎花连衣裙",
    "colors": ["粉色", "白色"],
    "material": "棉麻",
    "style_tags": ["甜美", "休闲"],
    "target_audience": "年轻女性",
    "key_features": ["透气面料", "A字版型显瘦"],
    "brief_description": "一款适合夏季穿着的碎花连衣裙，清新甜美风格。",
}


class GMICloudVisionClient:
    """Client for interpreting product images via Qwen3-VL on GMI Cloud."""

    def __init__(self, api_key: str, mock: bool = False):
        self.mock = mock
        self.api_key = api_key
        self.base_url = os.getenv("GMI_VL_BASE_URL", "https://api.gmi-serving.com/v1")
        self.model = os.getenv(
            "GMI_VL_MODEL", "Qwen/Qwen3-VL-235B-A22B-Instruct-FP8"
        )

        if self.mock:
            print("GMICloudVisionClient is running in mock mode (no external calls).")
        else:
            print(f"GMICloudVisionClient initialized with model: {self.model}")

    def interpret_image(self, image_path: Path) -> Dict[str, Any]:
        """Interpret a product image and return structured JSON."""
        if self.mock:
            return self._mock_interpret(image_path)

        image_b64 = self._encode_image(image_path)
        suffix = image_path.suffix.lower().lstrip(".")
        media_type = "png" if suffix == "png" else "jpeg"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{media_type};base64,{image_b64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": VISION_PROMPT,
                        },
                    ],
                }
            ],
            "max_tokens": 512,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return self._parse_response(content, image_path)

    def _mock_interpret(self, image_path: Path) -> Dict[str, Any]:
        """Return deterministic mock data for offline testing."""
        print(f"  [mock-vision] {image_path.name}")
        return dict(MOCK_VISION_RESULT)

    @staticmethod
    def _encode_image(image_path: Path) -> str:
        with image_path.open("rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @staticmethod
    def _parse_response(content: Any, image_path: Path) -> Dict[str, Any]:
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            print(f"Warning: Unexpected vision response type for {image_path.name}: {type(content)}")
            return {"category": "unknown", "title": image_path.stem, "brief_description": ""}
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            print(f"Warning: Failed to parse vision JSON for {image_path.name}: {content[:200]}")
            return {"category": "unknown", "title": image_path.stem, "brief_description": content[:140]}


def load_images(input_dir: Path, limit: Optional[int] = None) -> List[Path]:
    """Scan a directory for image files and return sorted paths."""
    extensions = {".jpg", ".jpeg", ".png"}
    paths = sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in extensions
    )
    if limit is not None:
        paths = paths[:limit]
    return paths


def vision_output_to_product(vision_result: Dict[str, Any], image_path: Path) -> StructuredProduct:
    """Convert Qwen3-VL JSON output into a StructuredProduct."""
    product_id = image_path.stem
    title = vision_result.get("title", product_id)
    brief = vision_result.get("brief_description", title)

    raw_tags = vision_result.get("style_tags", [])
    if isinstance(raw_tags, list):
        style_tags = [str(t) for t in raw_tags]
    else:
        style_tags = split_style_tags(str(raw_tags))

    attributes: Dict[str, str] = {}
    if vision_result.get("category"):
        attributes["category"] = str(vision_result["category"])
    if vision_result.get("colors"):
        colors = vision_result["colors"]
        attributes["colorways"] = ", ".join(colors) if isinstance(colors, list) else str(colors)
    if vision_result.get("material"):
        attributes["material"] = str(vision_result["material"])
    if vision_result.get("target_audience"):
        attributes["audience"] = str(vision_result["target_audience"])

    features = vision_result.get("key_features", [])
    if isinstance(features, list) and features:
        attributes["key_features"] = "; ".join(str(f) for f in features)

    return StructuredProduct(
        product_id=product_id,
        source_title=title,
        brief_description=brief,
        attributes=attributes,
        style_tags=style_tags,
    )


def run_pipeline(args: argparse.Namespace) -> Path:
    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")

    images = load_images(input_dir, limit=args.limit)
    if not images:
        raise RuntimeError(f"No images found in {input_dir}")

    print(f"Found {len(images)} image(s) to process.")

    api_key = os.getenv("GMI_API_KEY", "")
    if not args.mock and not api_key:
        raise RuntimeError("GMI_API_KEY environment variable is required in live mode.")

    vision_client = GMICloudVisionClient(api_key=api_key, mock=args.mock)
    llm_client = GMICloudLLMClient(api_key=api_key, mock=args.mock)

    materials: List[LocalizedMaterial] = []
    for i, image_path in enumerate(images, 1):
        print(f"\n[{i}/{len(images)}] Processing {image_path.name} ...")

        vision_result = vision_client.interpret_image(image_path)

        if vision_result.get("category") == "unknown":
            print(f"  Skipping non-product image: {image_path.name}")
            continue

        product = vision_output_to_product(vision_result, image_path)
        material = llm_client.generate_marketing_copy(product)
        materials.append(material)

    output_path = Path(args.output)
    write_structured_output(materials, output_path)
    return output_path


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MTWI image-to-copy pipeline: extract product info from images and generate bilingual marketing copy."
    )
    parser.add_argument(
        "--input-dir", default="data/mtwi",
        help="Directory containing product images (default: data/mtwi)",
    )
    parser.add_argument(
        "--output", default="outputs/mtwi_samples.yaml",
        help="Output file path (default: outputs/mtwi_samples.yaml)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max number of images to process (default: all)",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Run in mock mode for offline testing (no API calls)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    cli_args = parse_args()
    artifact_path = run_pipeline(cli_args)
    print(f"\nSaved localized materials to {artifact_path}")
