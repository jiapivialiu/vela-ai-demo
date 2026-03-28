"""Unit tests: RQ outcome → image bytes (no network)."""

from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

# src on path via pytest.ini
from mtwi_ecommerce_pipeline import extract_all_media_bytes_from_outcome, extract_media_bytes_from_outcome

# 1×1 PNG
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _png_bytes_over_200b() -> bytes:
    buf = BytesIO()
    import random

    rng = random.Random(42)
    w, h = 40, 40
    pixels = [(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)) for _ in range(w * h)]
    img = Image.new("RGB", (w, h))
    img.putdata(pixels)
    img.save(buf, format="PNG")
    data = buf.getvalue()
    assert len(data) > 200, f"PNG len={len(data)}; bump dimensions if Pillow changes"
    return data


def test_extract_top_level_data_base64_string() -> None:
    # Bare base64 path requires decoded size >200 bytes; tiny PNG uses data-URL branch.
    b64 = base64.b64encode(_TINY_PNG).decode("ascii")
    assert extract_media_bytes_from_outcome({"data": f"data:image/png;base64,{b64}"}) == _TINY_PNG


def test_extract_content_data_url() -> None:
    b64 = base64.b64encode(_TINY_PNG).decode("ascii")
    assert extract_media_bytes_from_outcome({"content": f"data:image/png;base64,{b64}"}) == _TINY_PNG


def test_extract_wrap_result_string() -> None:
    b64 = base64.b64encode(_TINY_PNG).decode("ascii")
    assert extract_media_bytes_from_outcome({"result": f"data:image/png;base64,{b64}"}) == _TINY_PNG


def test_extract_media_urls_item_data_string() -> None:
    # extract_all_media_bytes_from_outcome drops blobs shorter than 200 bytes.
    raw = _png_bytes_over_200b()
    b64 = base64.b64encode(raw).decode("ascii")
    out = extract_all_media_bytes_from_outcome(
        {"media_urls": [{"data": f"data:image/png;base64,{b64}"}]},
        max_n=3,
    )
    assert len(out) == 1
    assert out[0] == raw


def test_extract_list_key_files() -> None:
    b64 = base64.b64encode(_TINY_PNG).decode("ascii")
    got = extract_media_bytes_from_outcome({"files": [f"data:image/png;base64,{b64}"]})
    assert got == _TINY_PNG
