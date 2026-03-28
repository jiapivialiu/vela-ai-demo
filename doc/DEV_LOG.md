# Development Log

Brief record of what changed and what was verified to work. _(Previously under `misc/`; consolidated here in `doc/`.)_

## 2026-03-29

### Request Queue HTTP 超时（扩展图 / 去字）

- **`RequestQueueClient.run_model`**：`POST /requests` 不再固定 **`timeout=60`**；读超时默认随当次 **`timeout_s`**（与 **`GMI_IMAGE_TIMEOUT`** 一致的量级），避免大图提交时 **`ReadTimeout: read timeout=60`**。可选 **`GMI_RQ_HTTP_SUBMIT_TIMEOUT`**、**`GMI_RQ_POLL_READ_TIMEOUT`** 等（见 **CONFIGURATION.md**）。

### 文档 / 小一致性与启发式

- **`CONFIGURATION.md`**：删除 **`GMI_EXTRA_IMAGES_PLACEHOLDER`** 下错误表述（`run_one_deliverable_example.sh` 曾默认 export `=1`）；与当前「主链路默认不跑扩展图」一致。
- **启发式 `param_brand`**：去掉仅针对 **kangfu→KANGFU** 的写死大写，统一用结构化字段原值。
- **Streamlit**：侧栏「每步最大重试」**上限改为 2**，与 `parse_args` clamp 一致。

### Step4b 英文 copy review 默认模型

- **`--copy-review-english-model`** 默认由 **`openai/gpt-5.4`** 改为与法侧一致 **`anthropic/claude-sonnet-4.6`**（同一 `_run_step4b_review_copy_one_language` 路径；仅 `model_id` 对齐）。`run_one_deliverable_example.sh` standard 档：`GMI_COPY_REVIEW_ENGLISH_MODEL` 未设时取 **`$GMI_COPY_REVIEW_FRENCH_MODEL`**。Bulk YAML / Streamlit / `run_bulk_pipeline` 回退默认已同步。

### Step4c 语法审查（EN/FR）错误隔离

- **`run_step4c_locale_grammar_review`**：与 Step4b 一致，**英 / 法各一次** `chat_json` 在子任务内 **try/except**；一侧 **HTTP 400** 等不再让整个 4c 抛错，也不会再把**同一条**异常复制进 `locale_grammar_review.md` 的两段。返回 **`(review_dict, any_side_failed)`**；`any_side_failed` 时写 **`step4c_locale_grammar_partial_failure`** 并计 **`step4c_locale_grammar_failed`**。

### 测试目录与阶段产物

- 新增 **`tests/`**：**`pytest.ini`**（`pythonpath=src tests`）、**`requirements-dev.txt`**（含 **pytest**）、**`test_pipeline_mock_smoke.py`**（临时目录内 mock 全链路 + `data/demo_one`）、**`test_extract_media_outcome.py`**、**`test_parse_args_embedded.py`**、**`test_stage_chain.py`**（依赖 **`python tests/generate_stage_artifacts.py`** 写入的 **`tests/stage_artifacts/generated/latest_manifest.json`**）。
- **`tests/generate_stage_artifacts.py`**：mock 跑通并落盘 **`generated/<run_id>/`**，记录相对仓库根的 **`latest_manifest.json`**（`final_png`、`deliverable_product_dir` 等）供下一阶段测试复用。**`tests/stage_artifacts/generated/`** gitignore。
- **`write_output`**：若 **`yaml` 模块无 `safe_dump`**（例如被本地 **`yaml.py`** 遮蔽），回退 **`.json`** 并依赖 **PyYAML** 说明。

### 全自动去字预处理（无 MTWI）

- 新增 **`src/auto_text_erase_preprocess.py`**：PaddleOCR 检测（默认置信度 ≥0.5）→ 多边形二值 mask（可选膨胀）→ 混合策略（单框面积均 &lt;500px 则 OpenCV inpaint，否则 **RQ** `seedream-5.0-lite` + mask）→ 可选 **`bria-fibo-edit`** 双图 harmonize（原图 + 去字图）。**ThreadPoolExecutor** 默认 4 线程、进度打印、**`--resume`** 跳过已有 `<stem>_final.png`、汇总 **`auto_erase_summary.csv`**。可选 **`--quads-json`**（`files` 按文件名映射）绕过 OCR。RQ：**2s** 轮询、超时默认 **30s**（**`GMI_AUTO_ERASE_RQ_TIMEOUT`**），入队重试 **`GMI_AUTO_ERASE_MAX_ATTEMPTS`**（默认 2，即失败后再试 1 次）。失败/降级在 meta CSV 中附带 **手动四角坐标** 备选说明。

### 流水线实时进度（CLI stderr + JSONL + Streamlit）

- 新增 **`src/pipeline_progress.py`**：`pipeline_progress_emit` / **`pipeline_progress_span`**；**stderr** 打印带**本地时间**的人类可读行；可选 **`GMI_PIPELINE_PROGRESS_FILE`** 或 **`--pipeline-progress-file`** 追加 **JSONL**（供 UI 轮询）。
- **`run_pipeline`** 对 **annotation_audit、step1_erase、harmonize、restore、extra1_pregen、listing_reference_audit、step3_vision、step4_listing、step4b/4c、step5_extras、write_output、export** 等打点；**`eval_image_quality` / `eval_copy_quality`** 增加每样本一行与汇总 **mean**。
- **`streamlit_app.py`**：流水线在**后台线程**运行，主界面 **`~0.35s` 轮询** `pipeline_progress.jsonl` 刷新 **text_area**；完成后写入结果并展示交付物。
- **Streamlit 失败可见性**：后台线程 **`except SystemExit`**（`argparse` 非法参数）与 **`except BaseException`**，避免仅 **`Exception`** 时 `ok`/`error` 为空导致界面只显示泛化「流水线执行失败」；失败时写入运行目录 **`streamlit_pipeline_error.txt`**；界面 **`st.expander` 展示完整 traceback**。
- **Streamlit 结果区位置**：`_PIPELINE_RESULT_KEY` 的交付预览从页顶移到 **「开始处理」+ 进度块之后**，用 **分隔线 +「运行结果」** 标题，避免完成后 rerun 时预览出现在最上方。
- **Streamlit `parse_args` 根因修复**：嵌入调用时传给 **`ArgumentParser.parse_args(list)`** 的列表**不会**像 shell 一样去掉 `argv[0]`；原先 `_build_argv` 首项为 **`mtwi_ecommerce_pipeline`** 会触发 **`unrecognized arguments` / SystemExit(2)**。现 **`_build_streamlit_pipeline_argv`** 仅含 **`--...` 选项**；**`parse_args`** 内 **`_normalize_embedded_argv`** 会剥掉**一个**不以 `-` 开头的首 token（兼容旧调用）。

### RQ outcome 解析（扩展图 / 图像变体）

- **`extract_media_bytes_from_outcome` / `extract_all_media_bytes_from_outcome`**：支持顶层 **`data` / `content` 字符串**（data-URL 或裸 base64）；**`result`/`data`/… 包裹键**下 **`str`**；列表键增加 **`files` / `media` / `items`**；**`media_urls` 项**中 **`data` 为字符串**时解码；**`extract_all`** 对每个 **`media_urls` dict 项**再调 **`extract_media_bytes_from_outcome`** 兜底。
- **`run_image_variants`**：`media_urls` 首遍对每个 **dict 项**直接 **`extract_media_bytes_from_outcome(item)`**，与全 outcome 解析对齐。
- **`GMI_RQ_OUTCOME_DEBUG_DEEP`**：嵌套约 **3 层**的 key 树（截断长串）；**`GMI_RQ_OUTCOME_DEBUG`** 文档改为明确「仅顶层摘要，非完整 JSON」。

### 营销扩展图（RQ）降级与解析

- **`extract_all_media_bytes_from_outcome`**：从 RQ outcome 收集多图字节（含 **`media_urls[].data.url`**、常见 list 键、递归 `result`/`data`），去重后补全 **`run_image_variants`** 短读。
- **`_extra_marketing_fetch_bytes`**：默认 **仅路径 1**（**`run_image_variants`** 主模型 → 可选 fallback）；**`run_image_edit`（路径 2）** 需 **`GMI_EXTRA_IMAGES_USE_EDIT_FALLBACK=1`**，Seedream 另需 **`GMI_EXTRA_IMAGES_SEEDREAM_USE_EDIT_FALLBACK=1`**。异常写入 **`extra_images_rq_*_failed`**。**Seedream generic backfill** 默认 **2** 轮（**`GMI_EXTRA_IMAGES_SEEDREAM_BACKFILL_CAP`**）。**丢弃与参考文件字节完全相同的输出**。**`GMI_EXTRA_IMAGES_MAX_PARALLEL`**（默认 1，上限 8）可并行 per-shot。
- **占位复制**：仍为 **显式 `GMI_EXTRA_IMAGES_PLACEHOLDER=1`** 才用参考图填洞；未设置则 **少文件 + warnings**。Bulk YAML：**`pipeline.fallback_additional_image_model`** → CLI。`try_additional_images_only.py`：**`--fallback-model`**。

## 2026-03-28

### RQ 图像 outcome 解析与调试

- **`extract_media_bytes_from_outcome`**：补充 **`media_urls[].uri` / `href`**、**`images` / `outputs` / `results`** 等列表、顶层 **`data:`** URL、**`result`/`data` 为 list** 等路径；递归深度上限避免环。
- **`GMI_RQ_OUTCOME_DEBUG=1`**：`run_image_variants` / `run_image_edit` 在 **零字节** 时 **stderr** 打印 outcome 字段摘要（无整段 base64）。

### `run_one_deliverable_example.sh` RQ 图像模型

- 默认 **`export GMI_ADDITIONAL_IMAGE_MODEL` / `GMI_ERASER_MODEL`** 为 **`seedream-5.0-lite`**（可由 **`GMI_DEMO_RQ_IMAGE_MODEL`** 改），**覆盖** 父 shell 中已设的 `GMI_ADDITIONAL_IMAGE_MODEL`，避免误用 `gemini-*` 却仍以为在跑 Seedream。沿用环境：`GMI_DEMO_USE_ENV_IMAGE_MODEL=1`。启动打印一行 **`run_one_deliverable_example: RQ image models — …`** 到 stderr。

### Extra-image prompts (less “match reference framing” pressure)

- **`generate_additional_product_images` `base_identity`**：强调参考图为 **identity anchor**，鼓励 **机位/光/景** 明显变化以做营销多样性；去掉易被判读成「不要偏离参考构图」的 duplicate-packshot 句式。**VARIANT 1** 允许极简棚内置景（亚克力台、轻微布面等）与不同光型，避免模型过度「安全」导致与 final 过于接近。
- **营销扩展图 prompt（物理合理性）**：`base_identity` 增加 **Physical realism** 段（正确用法、稳定摆放、避免竖立平衡奇迹、线材重力等）；**VARIANT 1/2/3** 与 **backfill** 补充台面/手持/吹风机吹发等示例约束；batch 多图提示同步一句。
- **营销扩展图 VARIANT 3 / `product_type` 上下文**：强化 **人–货互动**（作用部位、朝向、握法）；**不写具体品类举例**以保持通用性，仅原则性描述；`product_type` 行只强调与标签一致的典型真实用法。
- **营销扩展图 — 朝向**：`base_identity` + **VARIANT 3** 增加 **Directional handheld tools / Orientation check**：**输出端朝向使用目标**，禁止 **进风/尾部抵住头发或皮肤而喷口朝外** 的反向持握；拇指与按键布局与参考图 SKU 一致。
- **营销扩展图 — 松弛感**：在保留朝向/反向禁止的前提下，强调 **relaxed、candid、mid-motion OK**，弱化 **mandatory / 多条 Do not** 与僵硬的 orientation 分段，避免人货互动过于板正。
- **营销扩展图 — 身體向商品**：`base_identity` 增加 **Body-directed products**（作用于使用者身体/佩戴时：目标部位、接近角、距离、使用方式一致）；**VARIANT 3** 与 **product_type**、batch 总述同步强调 **angle / mode of use**，与松弛语气并存。

### 仅 RQ 扩展图试跑脚本

- **`src/try_additional_images_only.py`**：只调 **`generate_additional_product_images`**，不调 Chat；默认参考图 **`demo_outputs/mtwi_images_demo_one/demo_item_final.png`**（与 **`run_one_deliverable_example.sh`** 的 **`DEMO_OUTPUT_ROOT`** 默认一致），默认模型 env 或 **`seedream-5.0-lite`**。全链路若只换扩展图模型：同时设 **`GMI_ERASER_MODEL`**（保持去字）与 **`GMI_ADDITIONAL_IMAGE_MODEL`**（扩展图）。

### Marketing extras RQ 调用方式

- **`generate_additional_product_images`**：默认 **每张 `num_images=1` 变体（路径 1）**；可选 **`GMI_EXTRA_IMAGES_BATCH=1`** 先批量再补齐；**`run_image_edit`（路径 2）** 默认 **关**，需 **`GMI_EXTRA_IMAGES_USE_EDIT_FALLBACK=1`**（Seedream 另需 **`GMI_EXTRA_IMAGES_SEEDREAM_USE_EDIT_FALLBACK=1`**）。「要了 N 张、实得不足」时写 **`extra_images_shortfall`**。
- **`extra_images_placeholder_note`**：占位复制 final 时追加说明 — 每张仍发过 **不同** VARIANT（**不同角度棚拍 / 换应用场景无人 / 有人在使用**）。**`GMI_EXTRA_IMAGES_DEBUG=1`**：warnings + stderr 记录各次 RQ 的 prompt 摘要与 **`returned_bytes`**。

### Dual-image Step4 + optional skip reviews

- **Listing generation**（unified 与 split）：`run_step4_generate_listing_dual_image_bilingual` — **原图 + `final`/`erased` 去字后图**、全量 OCR、Step3 JSON；固定 `LISTING_PARAMETER_KEYS` → `param_*`；缺证据 EN=`Not specified`、FR=`Non précisé`。
- **`--skip-listing-review`** / **`GMI_SKIP_LISTING_REVIEW`**：跳过 Step4b/4c；交付不写 `copy_review.md` / `locale_grammar_review.md`。
- **Deliverables**：`description_en.md` / `description_fr.md` 含 **Parameters** + **Other attributes**；启发式 fallback 补齐 `param_*`。
- **Split**：不再并行 `run_step4_generate_copy_language`；与 unified 同为单次双图双语（split 主模型 `english_copy_model`）。已删未使用的 `_step4_split_generate_locale_with_fallback`。
- **Bulk / Streamlit**：`pipeline.skip_listing_review`；侧栏勾选「跳过 Step4b/4c」。

### Prompts (product type / category)

- **Step3** (`run_step3_understand_product`): `product_type` / `category_hint` must be **English**, vision-first; map Chinese OCR trade terms (e.g. 吹风机 → hair dryer); avoid lazy `"Unknown"` when the item is visually identifiable.
- **Step4** (split JSON, unified bilingual, plaintext fallback): shared `_STEP4_INPUTS_CONTRACT`, `_STEP4_OUTPUT_FIELD_SPECS`, `_STEP4_PRODUCT_ID_RULES`; explicit **role / task / format** sections; **multimodal** user message with **same `step3_img`** as Step3 (override with `GMI_STEP4_COPY_USE_IMAGE=0` if gateway rejects vision+json).
- **Step3** user prompt rework: role table + per-key JSON contract + OCR described as transcript of original listing text aligned with cleaned image.

### Performance (wall-clock)

- **Step4 split**: EN and FR copy generation run in **parallel** (`ThreadPoolExecutor`, same primary→fallback→heuristic semantics per locale).
- **Step4b / Step4c**: English and French reviewer calls run **in parallel** each.
- **Marketing extras**: `generate_additional_product_images` uses **one** Request Queue call with `num_images=count` and a numbered multi-shot prompt; at most **2** backfill rounds if the batch returns too few images (was up to 6 single-image rounds).
- **`--max-attempts`**: parsed value is **clamped to 1–2** (default remains 2).

### Pipeline 更新摘要（本轮，按链路顺序）

1. **蒙版与标注**：`--mask-mode` 默认 **`all`**（`GMI_MASK_MODE`）；`overlay` + Mock 启发式 0 框时回退全框并 warning。**标注审核** `audit_mtwi_annotation_spans`（`--annotation-audit` 默认开，仅 live）：逐框 `annotation_usable` / `needs_processing` / `bbox_contains_target_text`；失败回退 `select_spans_to_erase`，stability 记 `annotation_audit_fallback_used`。`--annotation-audit-model`、`GMI_ANNOTATION_AUDIT` / `GMI_ANNOTATION_AUDIT_MODEL`。
2. **去字**：`erase-strategy=local` → 蒙版内 **白底 + OpenCV `inpaint`（NS）**（`GMI_LOCAL_INPAINT_RADIUS`，依赖 `numpy` + `opencv-python-headless`；缺依赖则旧版邻条粘贴 + 提示）。**默认** `erase-strategy=model`（RQ）；`--eraser-model` 未设时与 **`--additional-image-model`**（默认 **`seedream-5.0-lite`**）一致，`GMI_ERASER_MODEL` 可覆盖。Harmonize 文案略加强调接缝融合。
3. **理解与文案**：`--copy-understand-image` 默认 **`final`**（修复后主图，不再默认脏原图）；可选 **`source`** / **`extra1`**。`extra1`：先 RQ 预生成 `extra_1` → **`run_listing_reference_consistency_audit`**（原图 vs extra_1，JSON：`safe_to_use_variant_for_copy` 等）→ 通过才用 extra_1 做 Step3；否则回退 final。后续扩展图用 **`generate_additional_product_images`** 的 `scenario_offset` / `first_file_index` 续接 extra_2+。交付 **`listing_reference_audit`** 写入 `EcommerceArtifact` / manifest。**4b** 仍只对 **原上架图** `source_image` 做视觉质检。`GMI_COPY_UNDERSTAND_IMAGE`；`configs/bulk_run.yaml` 含 `copy_understand_image: final`。
4. **入口**：根目录 **`streamlit_app.py`**（上传图 + MTWI txt、侧栏：Mock/API、mask、**标注审核**、**去字 model/local** 与扩展图 **同一 RQ 模型**、**文案理解用图** `final|extra1|source`、额外图数量 0–6、预览与 ZIP）。**`run_bulk_pipeline`** 传上述新参数；**`src/run_one_deliverable_example.sh`** 使用 RQ 去字 + 与扩展图对齐的 eraser。根 **`requirements.txt`**、**`.gitignore`**（`outputs/streamlit_runs/`、`credentials.json`）。文档 **`agent.md`**、**`CONFIGURATION.md`** 已对齐。

### 明细（与上表对应，便于检索）

- **Copy / Step3**：`--copy-understand-image`；`extra1` 预生成 + `run_listing_reference_consistency_audit`；`listing_reference_audit`；扩展图续接参数。
- **Step1 RQ 默认**：`--erase-strategy model`；`--eraser-model` ← `additional_image_model`；`run_one` / bulk / Streamlit。
- **Step1 local**：白 + `cv2.inpaint`；`GMI_LOCAL_INPAINT_RADIUS`；`opencv-python-headless` + `numpy`。
- **标注审核**：`audit_mtwi_annotation_spans`；fallback 与 stability 字段；Streamlit / YAML。
- **`--mask-mode`**：`all` 默认；overlay+mock 空启发式回退。
- **Streamlit**：主表单 **第 4 步「额外营销图（可选）」** — 复选框默认关；开启后选张数（1–6）→ `--generate-additional-images` + `--additional-image-count`。独立步骤：**`src/run_marketing_extras_step.py`**。
- **Extra marketing images default OFF**: `parse_args` `set_defaults(generate_additional_images=False)`；`--generate-additional-images` 开启；`GMI_GENERATE_ADDITIONAL_IMAGES=1` 可强制开启。Bulk：`configs/bulk_run.yaml` 仍显式 `generate_additional_images: true` 时才会加 CLI 标志。三场景 prompt：角度 → 使用场景 → 背景/用法。
- **Default Step4 = unified**: `GMI_COPY_GENERATION_MODE` / `--copy-generation-mode` default **`unified`**; **split** opt-in. `bulk_run*.yaml`, `run_bulk_pipeline`, `run_one_deliverable_example.sh`, `agent.md`, `CONFIGURATION.md` updated.
- **`--unified-copy-model` default**: aligns with **`GMI_ENGLISH_COPY_MODEL`** / **`openai/gpt-5.4-pro`** (was mini); bulk YAML + `build_pipeline_cmd` fallback uses `english_copy_model`.
- **`ChatClient.chat_json` / `parse_json_content`**: If the gateway returns 400 for `response_format: json_object`, retry the same call without it. Parse markdown-fenced JSON, leading prose, and `JSONDecoder.raw_decode` from the first `{`. Normalize `message.content` when it is a list of text parts (multimodal-style). Optional env `GMI_CHAT_JSON_RESPONSE_FORMAT` (`1` default vs `0` to skip json_object entirely); documented in `CONFIGURATION.md`.
- **Step4 copy**: `parse_json_content` also accepts a top-level JSON **array** (first object wins). If `build_listing` yields an empty `description`, raise `step4_degenerate_listing` so the existing primary→fallback→hardcoded path runs (fixes “EN Fallback + FR looks empty” when the API returned 200 but unparsed or wrong-shaped JSON).
- **Step4 copy**: `_coerce_step4_locale_block` maps alternate JSON shapes (`result` / `listing` / wrong key casing / single nested object with `title`). When **both** primary and fallback copy calls fail, use **`build_step4_heuristic_listing`** (vision + OCR, strips CJK for EN/FR fields) instead of one-line “could not generate” placeholders so deliverables always have substantive draft text.
- **Step4 simple recovery**: If EN/FR is heuristic or description empty, run **`run_step4_generate_copy_bilingual_simple`** (one `chat_json` with `response_json_object=False`, default model `GMI_SIMPLE_COPY_MODEL` or EN fallback). CLI `--simple-copy-model` / `--no-simple-copy-recovery`; bulk YAML `simple_copy_model`, `no_simple_copy_recovery`; Streamlit advanced expander. `ChatClient.chat_json(..., response_json_object=...)`.
- **Step4 copy mode**: `--copy-generation-mode unified|split` (env `GMI_COPY_GENERATION_MODE`, **default unified**). **unified** = single `run_step4_generate_copy_bilingual_simple` (+ EN fallback retry) then heuristic. **split** = dual-locale path + `_apply_simple_copy_recovery`. Bulk YAML `copy_generation_mode` / `unified_copy_model`.
- **Chat / Step4 robustness**: `_extract_choice_assistant_text` reads `message.content`, then `reasoning_content` / `reasoning` / `thinking`, then legacy `text`; multimodal parts accept `text`/`content`/`value`. `parse_json_content` strips text after the last closing think-fence segment when models prepend reasoning. `chat_json` retries with **2× max_tokens** (cap 8192) when `finish_reason` is `length` and parse is empty. **`_step4_try_plaintext_listing`**: if JSON listing is empty, one **`chat_plain`** line-delimited call (`TITLE:`/`DESCRIPTION:`/`END_DESCRIPTION`, no `response_format`). Default **`GMI_STEP4_MAX_TOKENS=2048`**. Request timeout 120s.
- **`agent.md`**: Lead with **LLM 与 Agent 规格** table mapping product names (Qwen3-VL-235B, GPT-5.4-pro, Claude Sonnet 4.6, GPT-5.4, gpt-5.4-nano×2) to GMI `model_id` + CLI/env; states 4b/4c mandatory and script parity (`parse_args`, bulk YAML, `run_bulk_pipeline`, shell demo, Streamlit). `CONFIGURATION.md` cross-link updated.
- **Doc audit**: Removed contradictory DEV_LOG bullets (optional 4b/4c); updated 2026-03-27 pipeline summary to split EN/FR + mandatory reviews; root `README` + Streamlit caption note 4b/4c always-on.
- **Docs (LLM + framework sync)**: `agent.md` — Party Nights + GMI stack, ChatClient vs Request Queue, per-SKU chat call count (~7), strategy defaults, updated flowchart; `CONFIGURATION.md` — full `GMI_*` env list; `PROMPT_TUNING_NOTES.md` / `src/README.md` / root `README.md` / `mtwi_ecommerce_pipeline` module docstring / `run_bulk_pipeline` docstring aligned.
- **Docs**: Root `README.md` slimmed to Party Nights AI 出海赛道 + GMI Cloud Inference Engine one-liner + Streamlit `demo_one` only; new `CONFIGURATION.md` for API Key, LAN, CLI/bulk pointers, doc index; `src/README.md` / `PROMPT_TUNING_NOTES.md` / `streamlit_app.py` cross-links updated.
- **Model routing + mandatory reviews**: Step3 default VLM `Qwen/Qwen3-VL-235B`. Step4 split `--english-copy-model` (`openai/gpt-5.4-pro`) / `--french-copy-model` (`anthropic/claude-sonnet-4.6`) with per-language fallbacks (`openai/gpt-5.4-mini`). Step4b always runs two vision audits: `--copy-review-english-model` (`openai/gpt-5.4`) + `--copy-review-french-model` (`anthropic/claude-sonnet-4.6`), merged. Step4c always runs `--locale-grammar-*-model` (default `openai/gpt-5.4-nano` each). Removed `--qwen-model`, `--fallback-text-model`, `--enable-copy-review`, `--enable-locale-grammar-review`, old single review flags. Updated `run_bulk_pipeline`, YAMLs, `streamlit_app.py`, `src/run_one_deliverable_example.sh`, `agent.md`, `src/README.md`, `PROMPT_TUNING_NOTES.md`.
- **Operator style hooks**: `--user-copy-instructions` (+ optional `*_file`), `--user-image-instructions` (+ optional `*_file`), env `GMI_USER_COPY_INSTRUCTIONS` / `GMI_USER_IMAGE_INSTRUCTIONS`; wired into step4, copy review, model erase/harmonize/restore, extra images; stored on `EcommerceArtifact`; bulk YAML + Streamlit text areas; docs in `src/README.md` / `PROMPT_TUNING_NOTES.md`.
- _(Superseded)_ Earlier same-day bullets describing **optional** 4b/4c flags (`--enable-copy-review`, `--locale-grammar-model`, etc.) — replaced by mandatory bilingual 4b/4c + split models (see row above).
- **Docs split**: root `README.md` = Streamlit only (venv, API key, `streamlit run`); training/bulk/eval/sharing table → `src/README.md`. Cross-links updated (`agent.md`, `PROMPT_TUNING_NOTES.md`, configs, `run_bulk_pipeline` docstring, shell script comment).
- **README**: added “分享给别人试用” table (repo vs Mock vs LAN vs tunnel vs Streamlit Cloud).
- **Streamlit UI（早期条目，功能已并入上方「Pipeline 更新摘要」）**：页面预览、ZIP、`outputs/streamlit_runs/`、`streamlit` 依赖等仍适用。
- **README simplified**: single table of existing scripts/configs; removed long duplicate sections; one-item demo: **`bash src/run_one_deliverable_example.sh`**; fixed narrative (YAML keys described in prose, not CLI-style `erase-strategy`).
- **Repo hygiene (gen + eval path only)**:
  - 单条交付 demo：**`src/run_one_deliverable_example.sh`**（仓库根执行；勿与已删除的 `scripts/` 路径混淆）。
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
