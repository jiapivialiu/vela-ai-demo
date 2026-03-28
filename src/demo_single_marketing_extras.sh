#!/usr/bin/env bash
# Demo ② — 单图：仅 Request Queue 营销扩展图（写入已有交付目录）
#
# 前提：已跑过 Demo ①（或同等流水线），存在：
#   - `${DEMO_OUTPUT_ROOT}/mtwi_images_demo_one/${DEMO_SKU}_final.png`
#   - `${DEMO_OUTPUT_ROOT}/deliverables_demo_one/${DEMO_SKU}/`（含 manifest.json）
#
# 环境变量（可选）：
#   DEMO_OUTPUT_ROOT   默认 demo_outputs（须与 Demo ① 一致）
#   DEMO_SKU           默认 demo_item（与 data/demo_one  stem 一致）
#   DEMO_EXTRAS_COUNT  默认 3
#   GMI_API_KEY / credentials.json
#   GMI_ADDITIONAL_IMAGE_MODEL、GMI_FALLBACK_IMAGE_MODEL 等见 CONFIGURATION.md
# 离线试跑：首个参数 `--mock`（不调 GMI，占位字节写入交付包）。
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEMO_OUTPUT_ROOT="${DEMO_OUTPUT_ROOT:-demo_outputs}"
DEMO_SKU="${DEMO_SKU:-demo_item}"
DEMO_EXTRAS_COUNT="${DEMO_EXTRAS_COUNT:-3}"

MOCK_FLAG=()
if [[ "${1:-}" == "--mock" ]]; then
  MOCK_FLAG=(--mock)
  shift
fi

if [[ -z "${GMI_API_KEY:-}" ]] && [[ -f credentials.json ]]; then
  GMI_API_KEY="$(.venv/bin/python -c "import json; print(json.load(open('credentials.json'))['api_key'])")"
  export GMI_API_KEY
fi
if [[ ${#MOCK_FLAG[@]} -eq 0 ]]; then
  : "${GMI_API_KEY:?Set GMI_API_KEY or add credentials.json (or pass --mock)}"
fi

# 与 Demo ① 默认 RQ 图模对齐（避免父 shell 里错误的 ADDITIONAL_IMAGE_MODEL 污染演示）
DEMO_RQ_IMAGE_MODEL="${GMI_DEMO_RQ_IMAGE_MODEL:-seedream-5.0-lite}"
if [[ "${GMI_DEMO_USE_ENV_IMAGE_MODEL:-0}" =~ ^(1|true|yes|on)$ ]]; then
  export GMI_ADDITIONAL_IMAGE_MODEL="${GMI_ADDITIONAL_IMAGE_MODEL:-$DEMO_RQ_IMAGE_MODEL}"
else
  export GMI_ADDITIONAL_IMAGE_MODEL="$DEMO_RQ_IMAGE_MODEL"
fi

REF="${DEMO_OUTPUT_ROOT}/mtwi_images_demo_one/${DEMO_SKU}_final.png"
DELIV="${DEMO_OUTPUT_ROOT}/deliverables_demo_one"

echo >&2 "demo_single_marketing_extras: REF=${REF} DELIV=${DELIV} SKU=${DEMO_SKU} count=${DEMO_EXTRAS_COUNT} model=${GMI_ADDITIONAL_IMAGE_MODEL}"

if [[ ! -f "$REF" ]]; then
  echo >&2 "Missing final image (run Demo ① first): $REF"
  exit 1
fi
if [[ ! -d "$DELIV/${DEMO_SKU}" ]]; then
  echo >&2 "Missing deliverable SKU folder (run Demo ① first): $DELIV/${DEMO_SKU}"
  exit 1
fi

exec .venv/bin/python src/run_marketing_extras_step.py \
  --reference-image "$REF" \
  --deliverable-dir "$DELIV" \
  --product-id "$DEMO_SKU" \
  --count "$DEMO_EXTRAS_COUNT" \
  "${MOCK_FLAG[@]}"
