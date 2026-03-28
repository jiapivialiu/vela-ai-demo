# Tests

## Setup

```bash
pip install -r requirements-dev.txt
```

Run from **repo root**:

```bash
pytest tests/
```

## Layout

| Path | Role |
|------|------|
| `tests/conftest.py` | Shared paths, `load_stage_manifest()` helper |
| `tests/generate_stage_artifacts.py` | **Mock** full pipeline → `stage_artifacts/generated/<run_id>/` + `latest_manifest.json` |
| `tests/stage_artifacts/generated/` | **Ignored by git** — produced by the generator |
| `test_pipeline_mock_smoke.py` | End-to-end **mock** run in `tmp_path` (no pre-generated fixtures) |
| `test_extract_media_outcome.py` | RQ outcome parsing helpers |
| `test_stage_chain.py` | Uses **`latest_manifest.json`** → extras mock step (skipped until you run the generator) |

## Chained stages

1. **Generate checkpoints** (optional, for tests that read intermediate files):

   ```bash
   python tests/generate_stage_artifacts.py
   ```

2. **Run pytest** — `test_stage_chain.py` will run only if `tests/stage_artifacts/generated/latest_manifest.json` exists.

3. **CI / quick loop** — `test_pipeline_mock_smoke.py` does not depend on `generated/`; it copies `data/demo_one` into a temp dir and asserts outputs.

## PyYAML / `yaml.safe_dump`

If you see `AttributeError: module 'yaml' has no attribute 'safe_dump'`, install **`PyYAML`** (`pip install PyYAML`) and ensure no local file named **`yaml.py`** shadows the package. The pipeline falls back to **`.json`** artifacts if YAML is unavailable.
