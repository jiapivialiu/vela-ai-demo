#!/usr/bin/env bash
# Single-product deliverable demo (data/demo_one/). Context: src/README.md, agent.md.
# Flow: GMI pipeline — model erase + VLM + unified bilingual copy + 4b/4c + 3 extra images → outputs/deliverables_demo_one/.
#
# Chat 404/400 多半是账户里 **model_id 与 agent.md 默认不一致**，不是「调 temperature」能解决的。
# - 默认：`GMI_DEMO_MODEL_PROFILE=standard`（与 agent.md / parse_args 一致）
# - 试另一套常见 OpenAI 前缀 + 较小 VLM：`GMI_DEMO_MODEL_PROFILE=openai_alt bash src/run_one_deliverable_example.sh`
# - 仍不对：在 GMI 控制台复制可用 ID，逐项 export GMI_VISION_MODEL、GMI_ENGLISH_COPY_MODEL 等覆盖。
#
# Request Queue image models (Step1 erase + marketing extras):
# - Default: **seedream-5.0-lite** for both, exported here so a parent-shell
#   `export GMI_ADDITIONAL_IMAGE_MODEL=gemini-…` does NOT override this demo.
# - Use another id: `GMI_DEMO_RQ_IMAGE_MODEL=your-id bash src/run_one_deliverable_example.sh`
# - Different erase vs extras: `GMI_DEMO_ERASER_MODEL=…` (extras stay `GMI_DEMO_RQ_IMAGE_MODEL`)
# - Respect inherited GMI_ADDITIONAL_IMAGE_MODEL / GMI_ERASER_MODEL: `GMI_DEMO_USE_ENV_IMAGE_MODEL=1`
#
# Requires: .venv, pip install -r requirements.txt, GMI_API_KEY (or credentials.json for this script only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -z "${GMI_API_KEY:-}" ]] && [[ -f credentials.json ]]; then
  GMI_API_KEY="$(.venv/bin/python -c "import json; print(json.load(open('credentials.json'))['api_key'])")"
  export GMI_API_KEY
fi
: "${GMI_API_KEY:?Set GMI_API_KEY or add credentials.json}"

# If image RQ returns no marketing variants, copy the reference (final) image so deliverables still have 3 files.
# Set GMI_EXTRA_IMAGES_PLACEHOLDER=0 to disable (strict: no fake extras).
: "${GMI_EXTRA_IMAGES_PLACEHOLDER:=1}"
export GMI_EXTRA_IMAGES_PLACEHOLDER

PROFILE="${GMI_DEMO_MODEL_PROFILE:-standard}"
case "$PROFILE" in
  openai_alt)
    export GMI_VISION_MODEL="${GMI_VISION_MODEL:-Qwen/Qwen2.5-VL-7B-Instruct}"
    export GMI_ENGLISH_COPY_MODEL="${GMI_ENGLISH_COPY_MODEL:-openai/gpt-4o}"
    export GMI_FRENCH_COPY_MODEL="${GMI_FRENCH_COPY_MODEL:-openai/gpt-4o}"
    export GMI_FALLBACK_ENGLISH_COPY_MODEL="${GMI_FALLBACK_ENGLISH_COPY_MODEL:-openai/gpt-4o-mini}"
    export GMI_FALLBACK_FRENCH_COPY_MODEL="${GMI_FALLBACK_FRENCH_COPY_MODEL:-openai/gpt-4o-mini}"
    export GMI_COPY_REVIEW_ENGLISH_MODEL="${GMI_COPY_REVIEW_ENGLISH_MODEL:-openai/gpt-4o-mini}"
    export GMI_COPY_REVIEW_FRENCH_MODEL="${GMI_COPY_REVIEW_FRENCH_MODEL:-openai/gpt-4o-mini}"
    export GMI_LOCALE_GRAMMAR_ENGLISH_MODEL="${GMI_LOCALE_GRAMMAR_ENGLISH_MODEL:-openai/gpt-4o-mini}"
    export GMI_LOCALE_GRAMMAR_FRENCH_MODEL="${GMI_LOCALE_GRAMMAR_FRENCH_MODEL:-openai/gpt-4o-mini}"
    ;;
  standard | *)
    export GMI_VISION_MODEL="${GMI_VISION_MODEL:-Qwen/Qwen3-VL-235B}"
    export GMI_ENGLISH_COPY_MODEL="${GMI_ENGLISH_COPY_MODEL:-openai/gpt-5.4-pro}"
    export GMI_FRENCH_COPY_MODEL="${GMI_FRENCH_COPY_MODEL:-anthropic/claude-sonnet-4.6}"
    export GMI_FALLBACK_ENGLISH_COPY_MODEL="${GMI_FALLBACK_ENGLISH_COPY_MODEL:-openai/gpt-5.4-mini}"
    export GMI_FALLBACK_FRENCH_COPY_MODEL="${GMI_FALLBACK_FRENCH_COPY_MODEL:-openai/gpt-5.4-mini}"
    export GMI_COPY_REVIEW_ENGLISH_MODEL="${GMI_COPY_REVIEW_ENGLISH_MODEL:-openai/gpt-5.4}"
    export GMI_COPY_REVIEW_FRENCH_MODEL="${GMI_COPY_REVIEW_FRENCH_MODEL:-anthropic/claude-sonnet-4.6}"
    export GMI_LOCALE_GRAMMAR_ENGLISH_MODEL="${GMI_LOCALE_GRAMMAR_ENGLISH_MODEL:-openai/gpt-5.4-nano}"
    export GMI_LOCALE_GRAMMAR_FRENCH_MODEL="${GMI_LOCALE_GRAMMAR_FRENCH_MODEL:-openai/gpt-5.4-nano}"
    ;;
esac
export GMI_UNIFIED_COPY_MODEL="${GMI_UNIFIED_COPY_MODEL:-$GMI_ENGLISH_COPY_MODEL}"

DEMO_RQ_IMAGE_MODEL="${GMI_DEMO_RQ_IMAGE_MODEL:-seedream-5.0-lite}"
if [[ "${GMI_DEMO_USE_ENV_IMAGE_MODEL:-0}" =~ ^(1|true|yes|on)$ ]]; then
  export GMI_ADDITIONAL_IMAGE_MODEL="${GMI_ADDITIONAL_IMAGE_MODEL:-$DEMO_RQ_IMAGE_MODEL}"
  export GMI_ERASER_MODEL="${GMI_ERASER_MODEL:-$GMI_ADDITIONAL_IMAGE_MODEL}"
else
  export GMI_ADDITIONAL_IMAGE_MODEL="$DEMO_RQ_IMAGE_MODEL"
  export GMI_ERASER_MODEL="${GMI_DEMO_ERASER_MODEL:-$DEMO_RQ_IMAGE_MODEL}"
fi
echo >&2 "run_one_deliverable_example: RQ image models — eraser=${GMI_ERASER_MODEL} additional=${GMI_ADDITIONAL_IMAGE_MODEL}"

exec .venv/bin/python src/mtwi_ecommerce_pipeline.py \
  --txt-dir data/demo_one/txt_train \
  --image-dir data/demo_one/image_train \
  --limit 1 \
  --erase-strategy model \
  --eraser-model "$GMI_ERASER_MODEL" \
  --quality-strategy local \
  --no-harmonize-after-erase \
  --mask-mode all \
  --vision-model "$GMI_VISION_MODEL" \
  --copy-generation-mode unified \
  --unified-copy-model "$GMI_UNIFIED_COPY_MODEL" \
  --english-copy-model "$GMI_ENGLISH_COPY_MODEL" \
  --french-copy-model "$GMI_FRENCH_COPY_MODEL" \
  --fallback-english-copy-model "$GMI_FALLBACK_ENGLISH_COPY_MODEL" \
  --fallback-french-copy-model "$GMI_FALLBACK_FRENCH_COPY_MODEL" \
  --copy-review-english-model "$GMI_COPY_REVIEW_ENGLISH_MODEL" \
  --copy-review-french-model "$GMI_COPY_REVIEW_FRENCH_MODEL" \
  --locale-grammar-english-model "$GMI_LOCALE_GRAMMAR_ENGLISH_MODEL" \
  --locale-grammar-french-model "$GMI_LOCALE_GRAMMAR_FRENCH_MODEL" \
  --additional-image-model "$GMI_ADDITIONAL_IMAGE_MODEL" \
  --additional-image-count 3 \
  --image-output-dir outputs/mtwi_images_demo_one \
  --output outputs/mtwi_ecommerce_demo_one.yaml \
  --export-deliverables \
  --deliverable-dir outputs/deliverables_demo_one \
  --max-attempts 2
