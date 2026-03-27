#!/usr/bin/env bash
# Single-product deliverable demo (data/demo_one/). Full context: README.md § 「单商品交付物」.
# Flow: local erase + local quality + EN/FR copy + 3 extra images + outputs under outputs/deliverables_demo_one/.
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
  --vision-model "openai/gpt-4o" \
  --qwen-model "Qwen/Qwen3.5-27B" \
  --fallback-text-model "openai/gpt-4o-mini" \
  --generate-additional-images \
  --additional-image-model "${GMI_ADDITIONAL_IMAGE_MODEL:-seedream-5.0-lite}" \
  --additional-image-count 3 \
  --image-output-dir outputs/mtwi_images_demo_one \
  --output outputs/mtwi_ecommerce_demo_one.yaml \
  --export-deliverables \
  --deliverable-dir outputs/deliverables_demo_one \
  --max-attempts 2
