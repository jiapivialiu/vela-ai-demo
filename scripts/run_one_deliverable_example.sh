#!/usr/bin/env bash
# Single-product deliverable demo (data/demo_one/). Context: src/README.md, agent.md.
# Flow: GMI pipeline — local erase/quality + VLM + split EN/FR copy + mandatory 4b/4c reviews + 3 extra images → outputs/deliverables_demo_one/.
# Requires: .venv, pip install -r requirements.txt, GMI_API_KEY (or credentials.json for this script only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -z "${GMI_API_KEY:-}" ]] && [[ -f credentials.json ]]; then
  GMI_API_KEY="$(.venv/bin/python -c "import json; print(json.load(open('credentials.json'))['api_key'])")"
  export GMI_API_KEY
fi
: "${GMI_API_KEY:?Set GMI_API_KEY or add credentials.json}"

exec .venv/bin/python src/mtwi_ecommerce_pipeline.py \
  --txt-dir data/demo_one/txt_train \
  --image-dir data/demo_one/image_train \
  --limit 1 \
  --erase-strategy local \
  --quality-strategy local \
  --no-harmonize-after-erase \
  --mask-mode all \
  --vision-model "Qwen/Qwen3-VL-235B" \
  --english-copy-model "openai/gpt-5.4-pro" \
  --french-copy-model "anthropic/claude-sonnet-4.6" \
  --fallback-english-copy-model "openai/gpt-5.4-mini" \
  --fallback-french-copy-model "openai/gpt-5.4-mini" \
  --copy-review-english-model "openai/gpt-5.4" \
  --copy-review-french-model "anthropic/claude-sonnet-4.6" \
  --locale-grammar-english-model "openai/gpt-5.4-nano" \
  --locale-grammar-french-model "openai/gpt-5.4-nano" \
  --generate-additional-images \
  --additional-image-model "${GMI_ADDITIONAL_IMAGE_MODEL:-seedream-5.0-lite}" \
  --additional-image-count 3 \
  --image-output-dir outputs/mtwi_images_demo_one \
  --output outputs/mtwi_ecommerce_demo_one.yaml \
  --export-deliverables \
  --deliverable-dir outputs/deliverables_demo_one \
  --max-attempts 2
