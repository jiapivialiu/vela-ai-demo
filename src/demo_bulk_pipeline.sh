#!/usr/bin/env bash
# Demo ③ — 批量：多 SKU 图像 + 英法文案 + 交付物 +（按 YAML）扩展图 + 评估汇总
#
# 使用 `src/run_bulk_pipeline.py` 与配置文件。默认 `configs/bulk_run.yaml`（真实 API、limit 等见 YAML）。
# 冒烟 / 省钱：`DEMO_BULK_CONFIG=configs/bulk_run_smoke.yaml bash src/demo_bulk_pipeline.sh`
#
# 参数：可选传入配置文件路径，覆盖默认与 DEMO_BULK_CONFIG：
#   bash src/demo_bulk_pipeline.sh configs/bulk_run_smoke.yaml
#
# 环境：`GMI_API_KEY` 或 `credentials.json`；数据路径在 YAML 的 `pipeline.txt_dir` / `pipeline.image_dir`。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONFIG="${DEMO_BULK_CONFIG:-${1:-configs/bulk_run.yaml}}"
if [[ ! -f "$CONFIG" ]]; then
  echo >&2 "Config not found: $CONFIG"
  exit 1
fi

# bulk_run_smoke.yaml 等 mock: true 时可不调真实 GMI；否则必须提供 Key
MOCK_RUN="$(
  .venv/bin/python -c "
import sys
from pathlib import Path
try:
    import yaml  # type: ignore
except Exception:
    sys.exit(2)
p = Path(sys.argv[1])
if not p.is_file():
    sys.exit(2)
c = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
sys.exit(0 if (c.get('pipeline') or {}).get('mock') else 1)
" "$CONFIG" 2>/dev/null && echo 1 || echo 0
)"
if [[ "$MOCK_RUN" != "1" ]]; then
  if [[ -z "${GMI_API_KEY:-}" ]] && [[ -f credentials.json ]]; then
    GMI_API_KEY="$(.venv/bin/python -c "import json; print(json.load(open('credentials.json'))['api_key'])")"
    export GMI_API_KEY
  fi
  : "${GMI_API_KEY:?Set GMI_API_KEY or add credentials.json (not required when YAML pipeline.mock is true)}"
fi

echo >&2 "demo_bulk_pipeline: config=$CONFIG"
exec .venv/bin/python src/run_bulk_pipeline.py --config "$CONFIG"
