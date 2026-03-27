# Development Log

Brief record of what changed and what was verified to work.

## 2026-03-27 (verified live)

- **Credentials safety**: added `credentials.json` to `.gitignore` (avoid accidental commits).
- **LLM smoke test**: `curl` to `POST https://api.gmi-serving.com/v1/chat/completions` works with streaming.
- **Image smoke test**: Request Queue `POST https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey/requests` works and returns a `media_urls[].url`.
- **Pipeline (text, live)**: ran `src/press_on_nails_pipeline.py` on `data/press_on_nails.csv` with `GMI_LLM_MODEL=deepseek-ai/DeepSeek-V3.2`, produced English-only user-facing fields in YAML.
- **Pipeline (text+image, live)**: generated one image and saved a local file path in `generated_image_path`.

## 2026-03-26 (initial implementation)

- Added `src/press_on_nails_pipeline.py` CLI for CSV → localized English output.
- Added mock mode (`--mock`) for offline demos.
- Added optional image generation via Request Queue (`--generate-image`, `--image-output-dir`).
