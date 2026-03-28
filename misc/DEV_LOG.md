# Development Log

Brief record of what changed and what was verified to work.

## 2026-03-28

- **`agent.md`**: Lead with **LLM 与 Agent 规格** table mapping product names (Qwen3-VL-235B, GPT-5.4-pro, Claude Sonnet 4.6, GPT-5.4, gpt-5.4-nano×2) to GMI `model_id` + CLI/env; states 4b/4c mandatory and script parity (`parse_args`, bulk YAML, `run_bulk_pipeline`, shell demo, Streamlit). `CONFIGURATION.md` cross-link updated.
- **Doc audit**: Removed contradictory DEV_LOG bullets (optional 4b/4c); updated 2026-03-27 pipeline summary to split EN/FR + mandatory reviews; root `README` + Streamlit caption note 4b/4c always-on.
- **Docs (LLM + framework sync)**: `agent.md` — Party Nights + GMI stack, ChatClient vs Request Queue, per-SKU chat call count (~7), strategy defaults, updated flowchart; `CONFIGURATION.md` — full `GMI_*` env list; `PROMPT_TUNING_NOTES.md` / `src/README.md` / root `README.md` / `mtwi_ecommerce_pipeline` module docstring / `run_bulk_pipeline` docstring aligned.
- **Docs**: Root `README.md` slimmed to Party Nights AI 出海赛道 + GMI Cloud Inference Engine one-liner + Streamlit `demo_one` only; new `CONFIGURATION.md` for API Key, LAN, CLI/bulk pointers, doc index; `src/README.md` / `PROMPT_TUNING_NOTES.md` / `streamlit_app.py` cross-links updated.
- **Model routing + mandatory reviews**: Step3 default VLM `Qwen/Qwen3-VL-235B`. Step4 split `--english-copy-model` (`openai/gpt-5.4-pro`) / `--french-copy-model` (`anthropic/claude-sonnet-4.6`) with per-language fallbacks (`openai/gpt-5.4-mini`). Step4b always runs two vision audits: `--copy-review-english-model` (`openai/gpt-5.4`) + `--copy-review-french-model` (`anthropic/claude-sonnet-4.6`), merged. Step4c always runs `--locale-grammar-*-model` (default `openai/gpt-5.4-nano` each). Removed `--qwen-model`, `--fallback-text-model`, `--enable-copy-review`, `--enable-locale-grammar-review`, old single review flags. Updated `run_bulk_pipeline`, YAMLs, `streamlit_app.py`, `scripts/run_one_deliverable_example.sh`, `agent.md`, `src/README.md`, `PROMPT_TUNING_NOTES.md`.
- **Operator style hooks**: `--user-copy-instructions` (+ optional `*_file`), `--user-image-instructions` (+ optional `*_file`), env `GMI_USER_COPY_INSTRUCTIONS` / `GMI_USER_IMAGE_INSTRUCTIONS`; wired into step4, copy review, model erase/harmonize/restore, extra images; stored on `EcommerceArtifact`; bulk YAML + Streamlit text areas; docs in `src/README.md` / `PROMPT_TUNING_NOTES.md`.
- _(Superseded)_ Earlier same-day bullets describing **optional** 4b/4c flags (`--enable-copy-review`, `--locale-grammar-model`, etc.) — replaced by mandatory bilingual 4b/4c + split models (see row above).
- **Docs split**: root `README.md` = Streamlit only (venv, API key, `streamlit run`); training/bulk/eval/sharing table → `src/README.md`. Cross-links updated (`agent.md`, `PROMPT_TUNING_NOTES.md`, configs, `run_bulk_pipeline` docstring, shell script comment).
- **README**: added “分享给别人试用” table (repo vs Mock vs LAN vs tunnel vs Streamlit Cloud).
- **Streamlit UI**: added root `streamlit_app.py` — image + MTWI txt upload, sidebar options (mock/API key, mask mode, harmonize, extra images), run pipeline, **in-page preview** (images + EN/FR markdown + manifest/samples expanders), ZIP download; `outputs/streamlit_runs/` gitignored; `requirements.txt` includes `streamlit`.
- **README simplified**: single table of existing scripts/configs; removed long duplicate sections; `bash scripts/...` for one-item demo; fixed narrative (YAML keys described in prose, not CLI-style `erase-strategy`).
- **Repo hygiene (gen + eval path only)**:
  - Moved single-item shell demo to `scripts/run_one_deliverable_example.sh` (removed misplaced copy under `src/`).
  - Removed `test_requests.sh` (ad-hoc curl checks; not part of pipeline or bulk runner).
  - Trimmed `configs/bulk_run.yaml` / `bulk_run_smoke.yaml`: dropped redundant `eraser_model` / `restore_model` / unused extra-image keys in smoke; empty `env: {}` block (optional `env` still supported in code).
- **Documentation cleanup**: Deduplicated `README.md` (single index + run paths); trimmed repeated bullets in this log; pointed `agent.md` / configs at README for parameters; `PROMPT_TUNING_NOTES.md` remains the only place for prompt prose.

## 2026-03-27 (verified live)

- **MTWI ecommerce pipeline** (`src/mtwi_ecommerce_pipeline.py`): text removal (local and/or model), optional harmonize, quality (local or restore), VLM structured understanding, **split EN/FR copy** (+ per-language fallback), **mandatory 4b + 4c** (bilingual vision copy review + locale grammar), optional extra same-product images with backfill if fewer images returned than requested. _(As of 2026-03-28, defaults and flags match `agent.md`.)_
- **Automation**: per-product deliverables (`product_image.png`, EN/FR markdown, `manifest.json`, `deliverables_index.csv`); `--input-image` / `--input-images-glob`; overlay vs all mask (`--mask-mode`), `erased_spans` / warnings for traceability.
- **Bulk runner** (`src/run_bulk_pipeline.py`, `configs/bulk_run.yaml`, `configs/bulk_run_smoke.yaml`): one command for pipeline + image metrics + copy metrics + `run_manifest.json` / `run.log` / `stability_baseline.*`; `--max-attempts` on external calls.
- **Eval**: `src/eval_image_quality.py`, `src/eval_copy_quality.py`.
- **Docs / safety**: README MTWI-first; `credentials.json` gitignored; earlier `press_on_nails_pipeline.py` bilingual + GMI smoke tests noted below for history.

## 2026-03-26 (initial implementation)

- `src/press_on_nails_pipeline.py`: CSV → localized output; `--mock`; optional image via Request Queue.
