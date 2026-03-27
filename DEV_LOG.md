# Development Log

Brief record of what changed and what was verified to work.

## 2026-03-27 (verified live)

- **Bilingual localization upgrade**: updated `src/press_on_nails_pipeline.py` to generate both `canadian_english` and `canadian_french` copy blocks per product (title, bullet points, description, CTA).
- **Prompt/output schema update**: revised LLM JSON contract to explicitly request and parse dual-language Canadian-market outputs.
- **Docs refresh**: updated `README.md` examples and wording from English-only to English+French output.
- **Credentials safety**: added `credentials.json` to `.gitignore` (avoid accidental commits).
- **LLM smoke test**: `curl` to `POST https://api.gmi-serving.com/v1/chat/completions` works with streaming.
- **Image smoke test**: Request Queue `POST https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey/requests` works and returns a `media_urls[].url`.
- **Pipeline (text, live)**: ran `src/press_on_nails_pipeline.py` on `data/press_on_nails.csv` with `GMI_LLM_MODEL=deepseek-ai/DeepSeek-V3.2`, produced English-only user-facing fields in YAML.
- **Pipeline (text+image, live)**: generated one image and saved a local file path in `generated_image_path`.

## 2026-03-26 (initial implementation)

- Added `src/press_on_nails_pipeline.py` CLI for CSV → localized English output.
- Added mock mode (`--mock`) for offline demos.
- Added optional image generation via Request Queue (`--generate-image`, `--image-output-dir`).
