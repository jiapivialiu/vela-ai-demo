"""CLI argv embedding: synthetic prog name must not break parse_args."""

from __future__ import annotations

from pathlib import Path

from mtwi_ecommerce_pipeline import parse_args


def test_parse_args_strips_leading_non_option_token(tmp_path: Path) -> None:
    argv = [
        "fake_prog_name",
        "--mock",
        "--txt-dir",
        str(tmp_path),
        "--image-dir",
        str(tmp_path),
        "--limit",
        "1",
        "--output",
        str(tmp_path / "o.yaml"),
        "--image-output-dir",
        str(tmp_path / "img"),
    ]
    args = parse_args(argv)
    assert args.mock is True
    assert args.txt_dir == str(tmp_path)
