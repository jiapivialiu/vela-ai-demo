"""Press-on nails localization demo pipeline.

This script follows the workflow outlined in README.md and DEVELOPMENT_CHARTER.md.
It uses the GMI Cloud framework to perform localization tasks.

Models used (per task):

- Text localization & marketing copy generation
    - Endpoint: GMI Cloud ``/v1/chat/completions``
    - Model name: environment variable ``GMI_LLM_MODEL``
        (default: ``deepseek-ai/DeepSeek-V3.2``)

- Image generation (when ``--generate-image`` is set)
    - Endpoint: GMI Cloud Request Queue media API
    - Model name: environment variable ``GMI_IMAGE_MODEL``
        (default: ``seedream-5.0-lite``)

Example:
        # Run with the live GMI Cloud client
        python src/press_on_nails_pipeline.py --input data/press_on_nails.csv --limit 1

        # Run in mock mode for offline testing
        python src/press_on_nails_pipeline.py --mock
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests


KNOWN_FIELD_ORDER = [
    "款式",
    "品牌",
    "风格",
    "适用人群",
    "图案",
    "颜色",
    "产地",
    "货号",
    "品牌类型",
    "特殊用途化妆品",
]

FIELD_TRANSLATIONS = {
    "款式": "format",
    "品牌": "brand",
    "风格": "style",
    "适用人群": "audience",
    "图案": "pattern",
    "颜色": "colorways",
    "产地": "origin",
    "货号": "sku",
    "品牌类型": "brand_tier",
    "是否进口": "imported",
    "特殊用途化妆品": "special_cosmetic",
    "是否跨境出口专供货源": "cross_border_only",
}


@dataclass
class ProductRecord:
    product_id: str
    raw_block: str


@dataclass
class StructuredProduct:
    product_id: str
    source_title: str
    brief_description: str
    attributes: Dict[str, str]
    style_tags: List[str]


@dataclass
class LocalizedMaterial:
    product_id: str
    localized_title: str
    bullet_points: List[str]
    description: str
    call_to_action: str
    image_prompt: str
    source_summary: Dict[str, str]
    # Local path of the generated image (if any)
    generated_image_path: Optional[str] = None


class GMICloudLLMClient:
    """Client for interacting with GMI Cloud services."""

    def __init__(self, api_key: str, mock: bool = False):
        self.mock = mock
        self.api_key = api_key
        self.base_url = os.getenv("GMI_LLM_BASE_URL", "https://api.gmi-serving.com/v1")
        # Default base model; can be overridden via env for experimentation.
        self.model = os.getenv("GMI_LLM_MODEL", "deepseek-ai/DeepSeek-V3.2")

        if self.mock:
            print("GMICloudLLMClient is running in mock mode (no external calls).")
        else:
            print(f"GMICloudLLMClient initialized with model: {self.model}")

    def generate_marketing_copy(self, product: StructuredProduct) -> LocalizedMaterial:
        """Generates localized marketing copy using GMI Cloud or a mock response."""
        if self.mock:
            return self._generate_mock_copy(product)

        prompt = self._build_generation_prompt(product)

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a world-class e-commerce localization expert for the Canadian market.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "max_tokens": 1024,
            "temperature": 0.8,
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
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        # content is expected to be a JSON string in choices[0].message.content
        message_content: Any = data["choices"][0]["message"]["content"]
        content = self._parse_response(message_content)

        return LocalizedMaterial(
            product_id=product.product_id,
            localized_title=content.get("title", f"Localized Title for {product.product_id}"),
            bullet_points=content.get("bullet_points", []),
            description=content.get("description", ""),
            call_to_action=content.get("call_to_action", ""),
            image_prompt=build_image_prompt(product),
            source_summary=product.attributes,
        )

    def _generate_mock_copy(self, product: StructuredProduct) -> LocalizedMaterial:
        """Generates deterministic marketing copy for offline testing."""
        brand = product.attributes.get("brand", "Vela Nails")
        style_tags = product.style_tags or ["modern", "chic"]
        pattern = product.attributes.get("pattern", "minimalist art")

        localized_title = f"Mock: {brand} Press-On Nails | {' & '.join(s.title() for s in style_tags)} Style"
        bullet_points = [
            f"Mock: Embrace the {style_tags[0]} trend with these easy-to-apply nails.",
            "Mock: Includes 24 nails in 12 sizes for a perfect fit.",
            f"Mock: Features a durable, high-gloss finish with a {pattern} design.",
        ]
        description = (
            "Mock: Get a flawless, salon-quality manicure in minutes. Our press-on nails are designed "
            "for long-lasting wear and effortless style, perfect for any occasion in Canada."
        )
        call_to_action = "Mock: Add to cart and discover your new favourite look!"

        mock_content = {
            "title": localized_title,
            "bullet_points": bullet_points,
            "description": description,
            "call_to_action": call_to_action,
            "suggested_color_palette": "Pastel pinks and soft whites",
            "suggested_style_keywords": style_tags,
        }

        image_prompt = build_image_prompt(product)

        return LocalizedMaterial(
            product_id=product.product_id,
            localized_title=localized_title,
            bullet_points=bullet_points,
            description=description,
            call_to_action=call_to_action,
            image_prompt=image_prompt,
            source_summary=product.attributes,
        )

    def _build_generation_prompt(self, product: StructuredProduct) -> str:
        """Builds a detailed prompt for the text generation model.

        The model will be called in JSON mode, so this prompt focuses on
        semantics and field definitions.
        """
        style = ", ".join(product.style_tags)
        attributes = "\n".join(
            f"- {key}: {value}" for key, value in product.attributes.items() if value
        )

        return f"""
You are an e-commerce localization expert for the Canadian market.

You receive a Chinese press-on nails product listing (unstructured text fields
and attributes). Your job is to:

1. Understand the product's style, colour story, shape/length, and target
   customer from the provided fields.
2. Write natural, on-brand Canadian English marketing copy.
3. Return a **single JSON object** with the fields described below.

---
Product Profile (raw, may contain noise like inventory or logistics info):
- Source Title: {product.source_title}
- Key Styles: {style}
- Raw Attributes:
{attributes}

---
Output JSON specification:

Return a JSON object with these keys:

- "title": string
    - 80–120 characters.
    - Must be a polished, SEO-friendly product title suitable for a Canadian
      marketplace.
- "bullet_points": string[]
    - 3–4 bullet points.
    - Each bullet should highlight a concrete benefit or feature (fit, finish,
      wearing occasions, comfort, etc.).
- "description": string
    - 200–300 characters.
    - A short, appealing paragraph explaining why this design is great for
      Canadian customers.
- "call_to_action": string
    - A concise call-to-action encouraging the user to buy now.
- "suggested_color_palette": string
    - A short phrase summarizing the main colours (e.g., "blush pink with gold
      foil accents").
- "suggested_style_keywords": string[]
    - 2–4 English style tags like ["minimalist", "glam", "office-ready"].

Rules:
- Only output the JSON object, with double-quoted keys.
- Do NOT include any explanations, comments, or markdown.
- All output text fields (title, bullet_points, description, call_to_action,
  suggested_color_palette, suggested_style_keywords) must be in **English
  only**. Do not include any Chinese characters in these fields.
- If some details are missing in the Chinese source, make tasteful,
  style-consistent assumptions.
"""

    def _parse_response(self, content: Any) -> Dict[str, Any]:
        """Parse the JSON-mode content from the LLM.

        The chat completion API returns a string in JSON mode. In case the SDK
        or API ever returns a dict directly, handle that as well.
        """
        if isinstance(content, dict):
            return content

        if not isinstance(content, str):
            print(f"Warning: Unexpected content type from LLM: {type(content)}")
            return {}

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            print(f"Warning: Failed to decode JSON content from LLM: {content}")
            return {}


def load_records(csv_path: Path, limit: Optional[int] = None) -> List[ProductRecord]:
    records: List[ProductRecord] = []
    with csv_path.open("r", encoding="utf-8") as fp:
        reader = csv.reader(fp)
        header = next(reader, None)
        for row in reader:
            if not row:
                continue
            product_id = row[0].strip()
            raw_block = row[1].strip() if len(row) > 1 else ""
            if not product_id or not raw_block:
                continue
            records.append(ProductRecord(product_id=product_id, raw_block=raw_block))
            if limit and len(records) >= limit:
                break
    return records


def interpret_product(record: ProductRecord) -> StructuredProduct:
    lines = [line.strip() for line in record.raw_block.splitlines() if line.strip()]
    source_title = lines[0] if lines else f"Press-on nails {record.product_id}"
    attributes: Dict[str, str] = {}
    pending_key: Optional[str] = None

    for line in lines[1:]:
        if line in KNOWN_FIELD_ORDER:
            pending_key = line
            continue
        if pending_key:
            attributes[pending_key] = (
                attributes.get(pending_key, "") + (", " if attributes.get(pending_key) else "") + line
            )
            pending_key = None
            continue
        if "价格" in line or line.startswith("¥"):
            attributes.setdefault("价格", line)

    normalized_attrs = {
        FIELD_TRANSLATIONS.get(key, key): value for key, value in attributes.items()
    }
    style_tags = split_style_tags(normalized_attrs.get("style", source_title))
    brief_description = " ".join(lines[:3]) if lines else record.raw_block[:140]

    return StructuredProduct(
        product_id=record.product_id,
        source_title=source_title,
        brief_description=brief_description,
        attributes=normalized_attrs,
        style_tags=style_tags,
    )


def split_style_tags(style_field: str) -> List[str]:
    separators = [",", "，", "|", "/"]
    working = style_field
    for sep in separators:
        working = working.replace(sep, ",")
    return [chunk.strip().lower() for chunk in working.split(",") if chunk.strip()]


def build_image_prompt(product: StructuredProduct) -> str:
    palette = product.attributes.get("colorways", "soft neutrals")
    style = ", ".join(product.style_tags or ["modern"])
    pattern = product.attributes.get("pattern", "minimal art")
    return (
        "Editorial macro shot of a hand model with medium almond press-on nails, "
        f"inspired by {palette} tones, styled for a {style} vibe with {pattern} accents, "
        "shot on Portra 400, soft natural window light."
    )


def write_structured_output(materials: Sequence[LocalizedMaterial], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()

    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore

            with output_path.open("w", encoding="utf-8") as fp:
                yaml.safe_dump([asdict(item) for item in materials], fp, allow_unicode=True, sort_keys=False)
            return output_path
        except ImportError:
            fallback = output_path.with_suffix(".json")
            print(
                f"PyYAML not installed; falling back to {fallback.name}. Run `pip install pyyaml` for YAML support."
            )
            output_path = fallback
            suffix = ".json"

    if suffix == ".json":
        with output_path.open("w", encoding="utf-8") as fp:
            json.dump([asdict(item) for item in materials], fp, ensure_ascii=False, indent=2)
        return output_path

    raise ValueError("Unsupported output format. Use .yaml, .yml, or .json")


class GMIImageGenerationClient:
    """Client for GMI Cloud image/video-style generation models via Request Queue API.

    This uses the generic Request Queue endpoint documented in the Video API
    reference. For text-to-image use cases, we typically provide a minimal
    payload with a `prompt` field, and let the model's default settings handle
    other parameters.
    """

    def __init__(self, api_key: str, mock: bool = False):
        self.mock = mock
        self.api_key = api_key
        self.base_url = os.getenv(
            "GMI_MEDIA_BASE_URL",
            "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey",
        )
        # Default text-to-image model; can be overridden via env.
        self.model = os.getenv("GMI_IMAGE_MODEL", "seedream-5.0-lite")

        mode = "mock" if self.mock else "live"
        print(f"GMIImageGenerationClient initialized in {mode} mode with model: {self.model}")

    def generate_image_from_prompt(self, prompt: str, product_id: str, output_dir: Path) -> Optional[Path]:
        """Generate an image for the given prompt and save it locally.

        Returns the local file path if successful, otherwise None.
        """
        if self.mock:
            # In mock mode we do not hit the external service.
            print(f"[mock-image] Skipping remote call for product {product_id}.")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        enqueue_url = f"{self.base_url}/requests"
        payload = {
            "model": self.model,
            "payload": {
                "prompt": prompt,
            },
        }

        resp = requests.post(enqueue_url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("request_id")
        if not request_id:
            print(f"Warning: No request_id returned from image generation enqueue: {data}")
            return None

        status_url = f"{self.base_url}/requests/{request_id}"

        # Poll until the job finishes or times out.
        import time

        deadline = time.time() + float(os.getenv("GMI_IMAGE_TIMEOUT", "180"))
        status = None
        final_outcome: Optional[Dict[str, Any]] = None

        while time.time() < deadline:
            poll_resp = requests.get(status_url, headers=headers, timeout=30)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            status = poll_data.get("status")
            outcome = poll_data.get("outcome")

            if status in {"success", "failed"}:
                final_outcome = outcome
                break

            time.sleep(2.0)

        if status != "success" or not final_outcome:
            print(
                f"Warning: Image generation for request {request_id} did not succeed or timed out. "
                f"Final status={status}, outcome={final_outcome}"
            )
            return None

        # Try to locate an image-like URL in the outcome.
        image_url = None
        if isinstance(final_outcome, dict):
            # Prefer keys that clearly indicate an image.
            for key in [
                "image_url",
                "thumbnail_image_url",
                "preview_image_url",
            ]:
                if key in final_outcome:
                    image_url = final_outcome[key]
                    break

            # Fallback: if the outcome is a simple dict of URLs, pick the first one.
            if not image_url:
                for value in final_outcome.values():
                    if isinstance(value, str) and value.startswith("http"):
                        image_url = value
                        break

        if not image_url:
            print(f"Warning: Could not find an image URL in outcome: {final_outcome}")
            return None

        # Download the image asset locally.
        output_dir.mkdir(parents=True, exist_ok=True)
        # Try to infer an extension from the URL; default to .png.
        ext = ".png"
        try:
            from urllib.parse import urlparse

            path = urlparse(image_url).path
            if "." in path:
                ext_candidate = path.rsplit(".", 1)[-1]
                if ext_candidate:
                    ext = f".{ext_candidate.split('?')[0]}"
        except Exception:
            pass

        local_path = output_dir / f"{product_id}_image{ext}"
        asset_resp = requests.get(image_url, timeout=120)
        asset_resp.raise_for_status()
        with local_path.open("wb") as fp:
            fp.write(asset_resp.content)

        return local_path


def run_pipeline(args: argparse.Namespace) -> Path:
    records = load_records(Path(args.input), limit=args.limit)
    if not records:
        raise RuntimeError("No records found. Check the input file path or limit value.")

    api_key = os.getenv("GMI_API_KEY")
    if not args.mock and not api_key:
        # Fallback to the key from the demo notebook if env var is not set.
        # For production usage, prefer configuring GMI_API_KEY via environment
        # or a secrets manager instead of relying on this hard-coded value.
        api_key = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjZjMmVmYWU5LThmYjMtNDY5ZS04ZWYzLWNjNjA2OWNmN2I0MSIsInNjb3BlIjoiaWVfbW9kZWwiLCJjbGllbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCJ9.eAafK2oDia2PBO6Ka66lK4mk4IVYTp4wb74KAJX59Oc"
        )
        print("Warning: GMI_API_KEY not set; using fallback API key from demo notebook. For real deployments, set GMI_API_KEY explicitly.")

    llm_client = GMICloudLLMClient(api_key=api_key, mock=args.mock)

    image_client: Optional[GMIImageGenerationClient] = None
    if getattr(args, "generate_image", False):
        image_client = GMIImageGenerationClient(api_key=api_key, mock=args.mock)

    materials: List[LocalizedMaterial] = []
    for record in records:
        structured = interpret_product(record)
        material = llm_client.generate_marketing_copy(structured)

        if image_client is not None:
            image_output_dir = Path(getattr(args, "image_output_dir", "outputs/images"))
            generated_path = image_client.generate_image_from_prompt(
                prompt=material.image_prompt,
                product_id=structured.product_id,
                output_dir=image_output_dir,
            )
            if generated_path is not None:
                material.generated_image_path = str(generated_path)

        materials.append(material)

    output_path = write_structured_output(materials, Path(args.output))
    return output_path


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Press-on nails localization demo")
    parser.add_argument("--input", default="data/press_on_nails.csv", help="Path to the raw CSV dataset.")
    parser.add_argument(
        "--output", default="outputs/press_on_nails_samples.yaml", help="Destination file for localized materials."
    )
    parser.add_argument("--limit", type=int, default=None, help="Number of samples to process (processes all by default).")
    parser.add_argument(
        "--mock", action="store_true", help="Run in mock mode for offline testing, ignoring GMI Cloud."
    )
    parser.add_argument(
        "--generate-image",
        action="store_true",
        help=(
            "If set, call a GMI Cloud image/text-to-image model via the Request Queue API "
            "to generate a hero image for each product using the computed image_prompt."
        ),
    )
    parser.add_argument(
        "--image-output-dir",
        default="outputs/images",
        help="Directory to save generated images (used when --generate-image is set).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    cli_args = parse_args()
    artifact_path = run_pipeline(cli_args)
    print(f"Saved localized materials to {artifact_path}")
