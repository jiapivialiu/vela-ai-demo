"""Real-time pipeline progress for CLI (stderr) and optional JSONL file (e.g. Streamlit UI).

Set ``GMI_PIPELINE_PROGRESS_FILE`` to a path, or call ``pipeline_progress_init(path)`` before ``run_pipeline``.
Each line in the file is one JSON object: ``ts_utc``, ``ts_local``, ``phase``, ``event`` (``start``/``end``/``info``),
optional ``model``, ``product_id``, ``elapsed_s``, ``detail``.

Human-readable lines are always printed to **stderr** with a local timestamp.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_lock = threading.Lock()
_file_path: Optional[Path] = None


def pipeline_progress_reset() -> None:
    global _file_path
    _file_path = None


def pipeline_progress_init(path: Optional[Path] = None) -> None:
    """Attach log file. If ``path`` is None, use env ``GMI_PIPELINE_PROGRESS_FILE``."""
    global _file_path
    if path is not None:
        _file_path = path
    else:
        raw = (os.getenv("GMI_PIPELINE_PROGRESS_FILE") or "").strip()
        _file_path = Path(raw) if raw else None
    if _file_path:
        _file_path.parent.mkdir(parents=True, exist_ok=True)
        _file_path.write_text("", encoding="utf-8")


def _timestamps() -> tuple[str, str]:
    utc = datetime.now(timezone.utc).isoformat()
    local = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return utc, local


def pipeline_progress_emit(
    phase: str,
    event: str,
    *,
    model: str = "",
    product_id: str = "",
    elapsed_s: Optional[float] = None,
    detail: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    utc, local = _timestamps()
    rec: Dict[str, Any] = {
        "ts_utc": utc,
        "ts_local": local,
        "phase": phase,
        "event": event,
    }
    if model:
        rec["model"] = model
    if product_id:
        rec["product_id"] = product_id
    if elapsed_s is not None:
        rec["elapsed_s"] = elapsed_s
    if detail:
        rec["detail"] = detail
    if extra:
        for k, v in extra.items():
            if k not in rec:
                rec[k] = v

    parts = [f"[{local}]", f"[{phase}]", event]
    if model:
        parts.append(f"model={model}")
    if product_id:
        parts.append(f"product={product_id}")
    if elapsed_s is not None:
        parts.append(f"{elapsed_s:.2f}s")
    if detail:
        parts.append(f"| {detail}")
    print(" ".join(parts), file=sys.stderr, flush=True)

    line = json.dumps(rec, ensure_ascii=False) + "\n"
    if _file_path:
        with _lock:
            with _file_path.open("a", encoding="utf-8") as fp:
                fp.write(line)


@contextlib.contextmanager
def pipeline_progress_span(
    phase: str,
    *,
    model: str = "",
    product_id: str = "",
    detail: str = "",
) -> Iterator[None]:
    pipeline_progress_emit(phase, "start", model=model, product_id=product_id, detail=detail)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        pipeline_progress_emit(
            phase,
            "end",
            model=model,
            product_id=product_id,
            elapsed_s=round(dt, 3),
            detail=detail,
        )


def read_progress_tail(path: Path, max_lines: int = 40) -> List[str]:
    """Return up to ``max_lines`` non-empty text lines for UI (JSON pretty optional)."""
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    return lines[-max_lines:]


def format_progress_lines_for_ui(lines: List[str]) -> str:
    """Turn JSONL lines into short human lines for Streamlit."""
    out: List[str] = []
    for ln in lines:
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            out.append(ln[:200])
            continue
        ts = o.get("ts_local") or o.get("ts_utc", "")[:19]
        ph = o.get("phase", "?")
        ev = o.get("event", "?")
        model = o.get("model", "")
        pid = o.get("product_id", "")
        el = o.get("elapsed_s")
        det = o.get("detail", "")
        seg = f"{ts}  {ph}  {ev}"
        if model:
            seg += f"  [{model}]"
        if pid:
            seg += f"  ({pid})"
        if isinstance(el, (int, float)):
            seg += f"  {el}s"
        if det:
            seg += f"  — {det}"
        out.append(seg)
    return "\n".join(out)
