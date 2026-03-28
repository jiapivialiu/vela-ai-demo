# 配置与进阶说明

根目录 [README.md](README.md) 为 Party Nights 参赛说明与 Streamlit + `demo_one` 试用；本文档补充 **GMI Cloud Inference Engine** 下的密钥、局域网、命令行与批量、**模型环境变量**。

## 推理栈（与 agent.md 一致）

- **对话推理**：步骤 3 / 4 / 4b / 4c 经 GMI **Chat**（多模态或文本 JSON）。**4b、4c 默认运行**；可用 **`--skip-listing-review`** 或 **`GMI_SKIP_LISTING_REVIEW=1`** 跳过（交付包不写质检 markdown）。
- **图像任务**：擦除 / harmonize / restore / **扩展营销图（默认 3 张）**经 GMI **Request Queue**（非 chat）。CLI **默认开启** 三种场景（不同角度 → 真人使用 → 不同背景/使用场景）；关闭：`--no-generate-additional-images` 或 `GMI_GENERATE_ADDITIONAL_IMAGES=0`。
- **产品级 LLM 规格与 `model_id` 对照表**（Qwen3-VL-235B、GPT-5.4-pro、Claude Sonnet 4.6、GPT-5.4、gpt-5.4-nano 等）：见 **[agent.md](agent.md)** 文首 **「LLM 与 Agent 规格」**；含脚本/YAML 一致性说明与每 SKU 约 **6**（unified 默认）或 **7**（split）次 chat 说明。

## 文案生成（步骤4）vs 质检（4b / 4c）

| 阶段 | 作用 | 默认链路 | 失败时常见现象 |
|------|------|----------|----------------|
| **步骤4 生成** | **原上架图 + 去字后主图（或仅擦除图）** + Step3 JSON + **完整 MTWI OCR** → 英法 `title` / `description` / `category` + **固定 `parameters`**（写入 `param_*`；缺证据时 EN=`Not specified`、FR=`Non précisé`） | **unified**：`--unified-copy-model` 一次双语 chat。**split**：同一套 **双图双语** 调用，主模型为 `--english-copy-model`，回退 `--fallback-english-copy-model`；可选 simple 抢救 + 启发式 | **正文为空、只有占位/启发式**：多半是 **生成**或网关 JSON 解析问题，**不是** 4b/4c |
| **步骤4b** | **质检**：listing 与 **图片**、结构化信息是否一致（夸张、幻觉） | 英、法 **各 1 次** 多模态 chat | 写入 `copy_review`、warnings；**不会自动重写正文**（只给修订建议） |
| **步骤4c** | **质检**：加拿大英语/法语语法与在地习惯 | **2 次** 纯文本 JSON | 写入 `locale_grammar_review`；**不改 listing 正文** |

对比 **unified** 与 **split**：

- **unified**（**默认**）：`--unified-copy-model`（默认 **`openai/gpt-5.4-pro`**），**一次** 双图双语 structured chat；失败再用 `--fallback-english-copy-model` 重试。
- **split**：主模型为 **`--english-copy-model`**（同一双图双语调用；**不再**对英法各走单独 `english-copy-model` / `french-copy-model` 并行生成）。回退与 **split** 专用 **`--french-copy-model` / `--fallback-french-copy-model`** 仍保留在 CLI 供将来扩展，当前双图路径未使用法语主模型。可再走 `--simple-copy-model` 抢救。

环境变量：`GMI_COPY_GENERATION_MODE=unified|split`（默认 **unified**），`GMI_UNIFIED_COPY_MODEL`。Bulk：`pipeline.copy_generation_mode`、`pipeline.unified_copy_model`。

## GMI API Key

- **不要把 API Key 写入仓库**。`GMI_API_KEY` 环境变量，或本机 `credentials.json`（须 gitignore）。Streamlit 侧栏粘贴的 Key 仅当前浏览器会话有效。

### API Key 方式对照

| 方式 | 做法 |
|------|------|
| **环境变量（推荐）** | `export GMI_API_KEY="你的密钥"`（Windows 可用 `set` 或系统环境变量） |
| **Streamlit 侧栏** | 运行后在左侧 **GMI API Key** 粘贴 |
| **credentials.json** | Streamlit 默认不读文件；可启动前执行：  
  `export GMI_API_KEY="$(python -c "import json;print(json.load(open('credentials.json'))['api_key'])")"` |

## 模型相关环境变量（覆盖 CLI 默认值）

与 `parse_args` 中 `default=os.getenv(...)` 对应，便于不换命令行只改环境：

```text
GMI_VISION_MODEL
GMI_COPY_UNDERSTAND_IMAGE
GMI_COPY_GENERATION_MODE
GMI_UNIFIED_COPY_MODEL
GMI_ENGLISH_COPY_MODEL
GMI_FRENCH_COPY_MODEL
GMI_FALLBACK_ENGLISH_COPY_MODEL
GMI_FALLBACK_FRENCH_COPY_MODEL
GMI_SIMPLE_COPY_MODEL
GMI_COPY_REVIEW_ENGLISH_MODEL
GMI_COPY_REVIEW_FRENCH_MODEL
GMI_LOCALE_GRAMMAR_ENGLISH_MODEL
GMI_LOCALE_GRAMMAR_FRENCH_MODEL
GMI_ERASER_MODEL
GMI_RESTORE_MODEL
GMI_HARMONIZE_MODEL
GMI_ADDITIONAL_IMAGE_MODEL
GMI_GENERATE_ADDITIONAL_IMAGES
GMI_EXTRA_IMAGES_BATCH
GMI_EXTRA_IMAGES_USE_EDIT_FALLBACK
GMI_EXTRA_IMAGES_PLACEHOLDER
GMI_EXTRA_IMAGES_DEBUG
GMI_RQ_OUTCOME_DEBUG
GMI_ERASE_STRATEGY
GMI_LOCAL_INPAINT_RADIUS
GMI_QUALITY_STRATEGY
GMI_MASK_MODE
GMI_ANNOTATION_AUDIT
GMI_ANNOTATION_AUDIT_MODEL
GMI_USER_COPY_INSTRUCTIONS
GMI_USER_IMAGE_INSTRUCTIONS
GMI_CHAT_JSON_RESPONSE_FORMAT
GMI_STEP4_MAX_TOKENS
GMI_STEP4_COPY_USE_IMAGE
GMI_SKIP_LISTING_REVIEW
GMI_DEMO_RQ_IMAGE_MODEL
GMI_DEMO_ERASER_MODEL
GMI_DEMO_USE_ENV_IMAGE_MODEL
```

- **`GMI_DEMO_MODEL_PROFILE`**：仅 **`src/run_one_deliverable_example.sh`** 使用。`standard`（默认，与 agent.md 表一致）或 **`openai_alt`**（较小 VLM + `openai/gpt-4o` / `gpt-4o-mini` 全家桶，便于账户未开通 GPT‑5.4 / Claude 4.6 时试跑）。
- **`GMI_DEMO_RQ_IMAGE_MODEL`**：仅 **`run_one_deliverable_example.sh`**。默认 **`seedream-5.0-lite`**；脚本会 **`export GMI_ADDITIONAL_IMAGE_MODEL`**（及默认同值的 **`GMI_ERASER_MODEL`**），**覆盖** 父 shell 里误设的 `GMI_ADDITIONAL_IMAGE_MODEL`（例如 `gemini-*`），保证 demo 的 RQ 去字与扩展图与仓库默认一致。换模型：`GMI_DEMO_RQ_IMAGE_MODEL=other-id bash …`。
- **`GMI_DEMO_ERASER_MODEL`**：可选；与 **`GMI_DEMO_RQ_IMAGE_MODEL`** 同时使用时，**仅**覆盖去字模型，扩展图仍为 **`GMI_DEMO_RQ_IMAGE_MODEL`**（需 **`GMI_DEMO_USE_ENV_IMAGE_MODEL` 未开启**）。
- **`GMI_DEMO_USE_ENV_IMAGE_MODEL`**：设为 `1` / `true` / `on` 时，脚本 **不再** 强制覆盖，改为沿用（或 `:=` 补默认）当前 shell 的 **`GMI_ADDITIONAL_IMAGE_MODEL`** / **`GMI_ERASER_MODEL`**，便于刻意用 Gemini 等试跑。启动时脚本会向 **stderr** 打印一行当前 RQ 图像模型。

- **`GMI_STEP4_MAX_TOKENS`**：步骤 4 文案（JSON + 定界纯文本回退）与简单双语抢救的 `max_tokens` 上限，默认 **`2048`**（旧默认 900 易截断 JSON）。至少 **600**。
- **`GMI_STEP4_COPY_USE_IMAGE`**：默认 **`1`** / `true` — Step4 **listing** 请求附带 **原图 + 去字后图**（多模态，顺序与 prompt 中 IMAGE A / B 一致），与 Step3 JSON、OCR 一起约束文案。若网关/模型不支持 vision+json，可设为 **`0`** / `false` / `off` 退回纯文本 user 消息（仅 JSON+OCR）。
- **`GMI_SKIP_LISTING_REVIEW`**：设为 `1` / `true` / `yes` / `on` 等价于 **`--skip-listing-review`**：不跑 Step4b / Step4c；`export-deliverables` 不写 `copy_review.md` / `locale_grammar_review.md`。

- **`GMI_CHAT_JSON_RESPONSE_FORMAT`**：设为 `1` / `true` / `yes` / `on`（默认）时，先请求 `response_format: json_object`；若网关返回 400，会自动再试不带该字段。设为 `0` / `false` / `off` 则始终不传 `response_format`（依赖提示词 + 解析器从正文里抽 JSON）。
- **`GMI_SIMPLE_COPY_MODEL`**：仅 **`split`** 文案链路。当英法分调仍落到启发式或描述为空时，再发 **一次** 双语 JSON（不传 `response_format`）。默认同 **`GMI_FALLBACK_ENGLISH_COPY_MODEL`**。关闭：`--no-simple-copy-recovery` 或 bulk `no_simple_copy_recovery: true`。
- **`GMI_UNIFIED_COPY_MODEL`**：仅 **`unified`** 主路径。未设置时默认与 **`GMI_ENGLISH_COPY_MODEL`** 相同（当前仓库默认 **`openai/gpt-5.4-pro`**）。
- **`GMI_GENERATE_ADDITIONAL_IMAGES`**：`1` / `true` / `on` 与 `0` / `false` / `off` 覆盖是否生成 **Request Queue** 扩展图（默认生成，与 CLI 一致）。
- **`GMI_EXTRA_IMAGES_BATCH`**：默认 **不设置** = 扩展图 **每张单独** 调 RQ（`num_images=1`，与早期版本一致，多数 RQ 图像模型对单张更稳）。设为 `1` / `true` / `on` 时先尝试 **单次** `num_images=N` 的多图 prompt，不足再按张补全。
- **`GMI_EXTRA_IMAGES_USE_EDIT_FALLBACK`**：默认 **`1` / on** — 当 **变体** 接口仍无 `media_urls` 时，对缺额用 **`run_image_edit`**（与去字相同的 RQ payload 形态）逐张生成扩展图。设为 `0` / `false` / `off` 关闭。
- **`GMI_EXTRA_IMAGES_PLACEHOLDER`**：设为 `1` / `true` / `on` 时，若 RQ 仍无图，用 **参考图（final）字节复制** 填满缺额，保证 `product_image_extra_*` 数量；`manifest.warnings` 会写 **`extra_images_placeholder`**。**`src/run_one_deliverable_example.sh` 默认 `GMI_EXTRA_IMAGES_PLACEHOLDER=1`**（可用 `=0` 关闭）。
- **`GMI_EXTRA_IMAGES_DEBUG`**：设为 `1` / `true` / `on` 时，在 **`manifest.warnings`** 与 **stderr** 中记录扩展图相关 RQ 调用的 **prompt 长度、sha256 前 12 位、开头约 200 字符**，以及 **`returned_bytes`**（是否拿到图像字节）。用于确认每张扩展图是否使用了 **不同的完整 prompt**（若三张图仍相同，多半是 placeholder 复制而非单一 prompt）。
- **`GMI_RQ_OUTCOME_DEBUG`**：设为 `1` / `true` / `on` 时，当 **`run_image_variants` / `run_image_edit`** 未解析出任何图像字节，在 **stderr** 打印一行 **`GMI RQ outcome debug […]`**：`outcome` 顶层字段名、类型、列表长度、字符串头片段（**不**打印完整 base64）。用于对照 GMI 控制台返回 JSON，扩展 **`extract_media_bytes_from_outcome`** 或核对 **`GMI_MEDIA_BASE_URL`** / 模型是否真产出图像。

**RQ 扩展图拿不到图、只能复制 final（`extra_images_placeholder`）时怎么排查**

1. **先确认不是「演示用占位」**：`GMI_EXTRA_IMAGES_PLACEHOLDER=0` — 不复制 final，缺几张就只生成几张并 **`extra_images_shortfall`**，避免误以为模型生成了三张不同的图。
2. **看网关实际回了什么**：同一 run 加 **`GMI_RQ_OUTCOME_DEBUG=1`**，看 stderr 里 `outcome` 是否有 `media_urls` / `images` / 内嵌 base64；若几乎是空 `{}`，多为 **模型未产出或 request 被拒**（控制台看该 `request_id`）。
3. **核对端点与模型**：`GMI_MEDIA_BASE_URL` 默认 Inference Engine Request Queue；`model_id` 须与账户开通的一致（如 **`seedream-5.0-lite`**）。
4. **解析已加强**：代码会从 `media_urls[].uri` / `href`、`images` / `outputs` 列表、顶层 `data:` URL 等路径取图；若仍失败，把 **`GMI RQ outcome debug`** 一行（脱敏后）交给支持或自行对照文档补字段。
5. **超时**：`GMI_IMAGE_TIMEOUT`（秒）默认擦除 240、变体 300，过短可能轮询未到 `success`。
- **`GMI_MASK_MODE`**：`all`（默认）= txt 中全部四边形参与蒙版擦除；`overlay` = 仅擦叠加水印类框（在线 VLM + 启发式；Mock 无匹配时回退为全部框）。
- **`GMI_ANNOTATION_AUDIT`**：`1` / `true` / `on` 与 `0` / `false` / `off` 覆盖是否在擦除前运行 **标注审核 VLM**（默认开启；**Mock 始终跳过**）。
- **`GMI_ANNOTATION_AUDIT_MODEL`**：审核专用 VLM；未设置时与 **`GMI_VISION_MODEL`** 相同。
- **`GMI_COPY_UNDERSTAND_IMAGE`**：`final`（默认）= Step3 用修复后主图；`source` = 原上架图；`extra1` = 先 RQ 出第 1 张扩展图，再 VLM 对比原图与 `extra_1`，通过后才用 `extra_1` 做 Step3（结果写入交付 `manifest` 的 `listing_reference_audit`）。
- **`GMI_LOCAL_INPAINT_RADIUS`**：`erase-strategy=local` 时，蒙版内先 **白底** 再 **OpenCV `inpaint`（NS）** 融合边界；半径默认 **`6`**（约 1–24）。需安装 **`opencv-python-headless`** 与 **`numpy`**；未安装时回退旧版「邻条粘贴」逻辑。
- **`GMI_ERASE_STRATEGY`**：`model`（默认）= 步骤1 用 Request Queue 去字；`local` = 仅本地 inpaint，不调去字 RQ。
- **`GMI_ERASER_MODEL`**：去字 RQ 的 `model_id`；**不设置** 时与 **`GMI_ADDITIONAL_IMAGE_MODEL`**（及 CLI `--additional-image-model`）一致，便于与扩展图同档画质。
- **仅试扩展图（不调 Chat）**：`python src/try_additional_images_only.py --reference-image outputs/.../demo_item_final.png --model <image_model_id>`。默认 RQ 去字与扩展图均为 **`seedream-5.0-lite`**；若去字与扩展图要用 **不同** `model_id`，分别设 **`GMI_ERASER_MODEL`** 与 **`GMI_ADDITIONAL_IMAGE_MODEL`**。

默认值与含义以 **`python src/mtwi_ecommerce_pipeline.py --help`** 与 [agent.md](agent.md) 表格为准。

## Streamlit

```bash
streamlit run streamlit_app.py
```

- 默认 **http://localhost:8501**。
- **局域网**：`streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501`，同网段访问 `http://<本机局域网 IP>:8501`。
- 侧栏模型字段与「回退与审稿模型」展开项，与上列 `GMI_*` 及 CLI 一致。

## 命令行与批量

- **完整 CLI、评估、交付物**：**[src/README.md](src/README.md)**。
- 单商品脚本：`bash src/run_one_deliverable_example.sh`。RQ 去字 + 扩展图默认 **强制 `seedream-5.0-lite`**（见上 **`GMI_DEMO_RQ_IMAGE_MODEL`**）；若要用 shell 里已 export 的 `GMI_ADDITIONAL_IMAGE_MODEL`，加 **`GMI_DEMO_USE_ENV_IMAGE_MODEL=1`**。若 Chat 持续 404/400，可先试 `GMI_DEMO_MODEL_PROFILE=openai_alt`（脚本内注释），或在控制台核对 `model_id` 后用 `GMI_VISION_MODEL` / `GMI_ENGLISH_COPY_MODEL` 等覆盖。
- 批量：`configs/bulk_run.yaml`、`configs/bulk_run_smoke.yaml`，`python src/run_bulk_pipeline.py --config ...`。

## 其它文档

| 文档 | 内容 |
|------|------|
| [agent.md](agent.md) | Agent 步骤、默认模型、调用次数、流程图 |
| [PROMPT_TUNING_NOTES.md](PROMPT_TUNING_NOTES.md) | Prompt 约束与调参 |
| [doc/DEV_LOG.md](doc/DEV_LOG.md) | 变更记录 |
