# Stage artifacts (for chained tests)

- **`generated/`** is **gitignored**. Populate it with:

  ```bash
  python tests/generate_stage_artifacts.py
  ```

- Each run creates `generated/<run_id>/` (inputs copy + mock pipeline outputs) and updates **`generated/latest_manifest.json`** with paths **relative to repo root** so the next test stage can load `demo_item_final.png`, `artifacts.yaml`, and the deliverable folder without re-running the full pipeline.

- Use **`data/demo_one`** as the canonical source for inputs when generating fixtures (same as the main demo).
