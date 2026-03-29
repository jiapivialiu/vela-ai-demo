> **说明**：下表 **「落实默认」** 与 `src/mtwi_ecommerce_pipeline.py` → `parse_args` **完全一致**。账户内 catalog 若使用不同 ID，请用 CLI 或 **`GMI_*`** 环境变量覆盖。试用与密钥：[README.md](README.md)、[CONFIGURATION.md](CONFIGURATION.md)。

---

## LLM 与 Agent 规格（当前仓库落实）

以下对应参赛/产品侧表述与 **脚本默认** 的映射；**4b 文案质检** 与 **4c 语法质检** **默认执行**，可用 **`--skip-listing-review`** 或 **`GMI_SKIP_LISTING_REVIEW=1`** 跳过（本地迭代/省调用；参赛提交前建议关闭跳过）。

| 环节 | 产品要求（表述） | GMI 默认 `model_id`（`parse_args`） | CLI | 环境变量 |
|------|------------------|-------------------------------------|-----|----------|
| **步骤0 标注审核 Agent**（擦除前，可选默认开） | 与步骤3 同档 **VLM** 审 MTWI 每框 | 默认同左 | `--annotation-audit` / `--no-annotation-audit` | `GMI_ANNOTATION_AUDIT` |
| | 可单独指定审核模型 | 默认 = `--vision-model` | `--annotation-audit-model` | `GMI_ANNOTATION_AUDIT_MODEL` |
| **步骤3 多模态理解** | **Qwen3-VL-235B** | `Qwen/Qwen3-VL-235B` | `--vision-model` | `GMI_VISION_MODEL` |
| **Step3 输入图** | 默认 **修复后主图**（避开水印原图） | `final` | `--copy-understand-image`（`final` / `source` / `extra1`） | `GMI_COPY_UNDERSTAND_IMAGE` |
| **原图 vs extra_1 一致性** | 同档 VLM JSON（仅 `extra1` 模式） | 默认同 `--vision-model` | （随 `extra1` 自动调用） | — |
| **步骤4 英法 listing（默认 unified）** | **双图**（原上架图 + 去字后图）+ OCR + Step3 JSON → 双语 JSON（含固定 `parameters` → `param_*`） | `openai/gpt-5.4-pro` | `--unified-copy-model` | `GMI_UNIFIED_COPY_MODEL` |
| **步骤4 文案模式** | 默认 **unified**；**split** = 同一套双图双语调用，主模型为 **英语模型**（见下行） | `unified` | `--copy-generation-mode` | `GMI_COPY_GENERATION_MODE` |
| **步骤4 英语模型（split 主路径）** | **GPT-5.4-pro**（双图双语生成主调） | `openai/gpt-5.4-pro` | `--english-copy-model` | `GMI_ENGLISH_COPY_MODEL` |
| **步骤4 法语模型（split）** | 当前双图路径 **未单独调用**；保留 CLI/env 供扩展 | `anthropic/claude-sonnet-4.6` | `--french-copy-model` | `GMI_FRENCH_COPY_MODEL` |
| **跳过 4b+4c** | 可选 | — | `--skip-listing-review` | `GMI_SKIP_LISTING_REVIEW` |
| **步骤4 英语回退** | unified 双语重试 / split 英回退 | `openai/gpt-5.4-mini` | `--fallback-english-copy-model` | `GMI_FALLBACK_ENGLISH_COPY_MODEL` |
| **步骤4 法语回退** | 仅 split 法回退 | `openai/gpt-5.4-mini` | `--fallback-french-copy-model` | `GMI_FALLBACK_FRENCH_COPY_MODEL` |
| **4b 英文文案质检 Agent**（默认开；可跳过） | **Claude Sonnet 4.6**（与法侧同档、同调用形态） | `anthropic/claude-sonnet-4.6` | `--copy-review-english-model` | `GMI_COPY_REVIEW_ENGLISH_MODEL` |
| **4b 法文文案质检 Agent**（默认开；可跳过） | **Claude Sonnet 4.6** | `anthropic/claude-sonnet-4.6` | `--copy-review-french-model` | `GMI_COPY_REVIEW_FRENCH_MODEL` |
| **4c 英文语法质检 Agent**（默认开；可跳过） | **gpt-5.4-nano** | `openai/gpt-5.4-nano` | `--locale-grammar-english-model` | `GMI_LOCALE_GRAMMAR_ENGLISH_MODEL` |
| **4c 法文语法质检 Agent**（默认开；可跳过） | **gpt-5.4-nano** | `openai/gpt-5.4-nano` | `--locale-grammar-french-model` | `GMI_LOCALE_GRAMMAR_FRENCH_MODEL` |

**脚本一致性（已核对）**：上述默认值同步于 **`configs/bulk_run.yaml`**、**`configs/bulk_run_smoke.yaml`**、**`src/run_bulk_pipeline.py`** 的 `build_pipeline_cmd`、**`src/run_one_deliverable_example.sh`**（该脚本默认 `GMI_DEMO_MODEL_PROFILE=standard`；若设 **`openai_alt`** 则改用另一套常见 `model_id`，见脚本注释 / CONFIGURATION.md）。

**ID 说明**：多模态模型在 GMI 上常以 **`厂商/模型名`** 形式注册（如 `Qwen/Qwen3-VL-235B`），与口语「Qwen3-VL-235B」指同一档能力；若控制台列表不同，以控制台为准并改 env。

---

## 项目与推理框架

- **场景**：**Party Nights「AI 出海」赛道** — 电商商品图 → 去字/修图 → 结构化理解 → **加拿大英语 + 加拿大法语** listing（**双图 + 单次 bilingual JSON**，unified/split 均走结构化 parameters）→ **默认** 事实向文案质检（4b）+ 加英/加法语法质检（4c），**可 `--skip-listing-review` 跳过**。
- **底座**：**GMI Cloud Inference Engine** — 统一 API Key 与模型路由；本仓库不自建推理集群。
- **代码侧分工**：
  - **对话式推理**（多模态 / 文本 JSON）：`ChatClient`（`chat_json`）— 步骤 **3 / 4 / 4b / 4c**。
  - **图像任务队列**（编辑 / 变体）：`RequestQueueClient` — 步骤 **1 model 擦除、2 harmonize/restore、扩展营销图**。
  - **本地**：`erase-strategy=local` 时去字为 **白底 + OpenCV inpaint**（需 `opencv-python-headless`）；`quality-strategy=local` 等为 PIL 等，无 LLM。

## 「Agent」在本文档中的含义

**【Agent】步骤**：通过 GMI **对话式 API**（`chat` + 结构化 JSON）完成 **理解、生成或审核** → 使用 **VLM** 或 **文本 LLM**。

**其余步骤**：编排与 I/O、本地图像处理，或 **Request Queue 图像接口**（非 `chat_json`）。

---

## 图像与其它默认模型（非上述 LLM 规格核心）

| 类型 | 环节 | 默认模型 ID | CLI | 环境变量 |
|------|------|-------------|-----|----------|
| 图像模型 | 步骤1 model 擦除 | 默认同扩展图 **`seedream-5.0-lite`** | `--eraser-model` | `GMI_ERASER_MODEL`（未设则与 `GMI_ADDITIONAL_IMAGE_MODEL` / `--additional-image-model` 一致） |
| 图像模型 | 步骤2 harmonize | `bria-fibo-edit` | `--harmonize-model` | `GMI_HARMONIZE_MODEL` |
| 图像模型 | 步骤2 restore | `bria-fibo-restore` | `--restore-model` | `GMI_RESTORE_MODEL` |
| 图像模型 | 扩展营销图（**主链路默认关**；独立步骤见 `run_marketing_extras_step.py`） | `seedream-5.0-lite` | `--generate-additional-images` / `--additional-image-count` / `--additional-image-model` | `GMI_ADDITIONAL_IMAGE_MODEL` / `GMI_GENERATE_ADDITIONAL_IMAGES` |

**策略默认**：`--annotation-audit` **默认开启**（仅 **真实模式**）；`--erase-strategy` 默认 **`model`**（与 `--additional-image-model` 同档 RQ 去字）；`--quality-strategy` 默认 `local`；`--mask-mode` 默认 **`all`**；**扩展营销图默认关闭**（`--generate-additional-images` 或 `GMI_GENERATE_ADDITIONAL_IMAGES=1` 开启）；harmonize 默认开启，可用 `--no-harmonize-after-erase` 关闭。离线/省钱可用 `--erase-strategy local`。

---

## 每 SKU 对话类调用量（无生成 fallback 时）

| 步骤 | 次数 | 说明 |
|------|------|------|
| 0 | **0–1**（默认 1，live） | 标注审核：`--annotation-audit` 开启且非 Mock 时 +1 次 VLM JSON（默认同 `--vision-model`）；Mock 跳过 |
| 3 | 1 | Qwen3-VL-235B → 结构化属性 JSON（输入图见 `--copy-understand-image`，默认 **final** 主图） |
| 3b | **0–2** | `extra1` 模式：+1 次 RQ 预生成 `extra_1`；+1 次 VLM 审核原图 vs `extra_1`（不通过则仍用 final 做 Step3） |
| 4 | **1**（默认 unified） | 双图 bilingual structured JSON（`--unified-copy-model`）；失败可 +1 次同结构重试（`fallback_english_copy_model`） |
| 4 | **1**（split） | 同上结构，主模型为 `--english-copy-model`（非英法各 1 次） |
| 4b | **0–2** | 默认各 1 次视觉 JSON；**`--skip-listing-review` 时为 0** |
| 4c | **0–2** | 默认各 1 次文本 JSON；**跳过审阅时为 0** |
| **合计** | **7**（unified，默认含 4b/4c） / **7**（split，同上） | 与旧版 split=8 不同：split 不再 +1 次法语文案生成。**跳过 4b/4c 时减 4 次 chat**。 |

---

## 入口与输入

| 入口 | 用途 |
|------|------|
| **Streamlit** | 若仓库含 `streamlit_app.py`；[README.md](README.md) |
| **CLI** | `src/mtwi_ecommerce_pipeline.py` |
| **批量** | `src/run_bulk_pipeline.py` + `configs/bulk_run*.yaml` |

**共同输入**：**必选**商品主图；**可选** MTWI 每行 `X1,Y1,X2,Y2,X3,Y3,X4,Y4,文本`（`--input-image` 且无同名配套 `.txt` 时为 image-only，见 `collect_input_items`）。**有标注时**：四边形参与蒙版去字（若开启擦除），且摘录文本进入 Step3/4 上下文，**英法 listing 文本生成通常更好**。**无标注时**：去字以整图语义 / 提示为主（蒙版为空），图像仍走同一 RQ 或 local 链路；**不将「是否带 MTWI」理解为对成图画质的单一决定因素**（策略不同，并非缺标注就不能出可用主图）。另可选 `--user-copy-instructions` / `--user-image-instructions`（及 file、`GMI_USER_*`）。

---

## 主链路流程图（括号内为默认 `model_id`）

```
┌─────────────────────────────────────────────────────────────┐
│  输入：商品主图 + 〔可选〕 MTWI 标注 + 可选用户偏好              │
│  （有标注 → OCR/摘录利文案；蒙版去字 vs 无底稿语义去字，图像链照常）  │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  步骤1～2：擦除 / 画质（local 或 Request Queue，见上表）          │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  【Agent·必选】步骤3：多模态理解 → JSON                          │
│  Qwen3-VL-235B → `Qwen/Qwen3-VL-235B`（`--vision-model`）       │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  【Agent·必选】步骤4：英法 listing（各 1 次）                   │
│  EN：GPT-5.4-pro → `openai/gpt-5.4-pro`                        │
│  FR：Claude Sonnet 4.6 → `anthropic/claude-sonnet-4.6`         │
│  回退（工程）：各 `openai/gpt-5.4-mini`                         │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌─────────────────────┐       ┌─────────────────────────────┐
│【Agent·默认】4b      │       │【Agent·默认】4c               │
│ 英 / 法 文案质检     │       │ 两语言语法质检（均为）         │
│ 均为 Claude          │       │ `openai/gpt-5.4-nano`        │
│ Sonnet 4.6 →        │       │ （英、法各 1 次文本 JSON）      │
│ `anthropic/claude-  │       │                              │
│  sonnet-4.6`        │       │ 与 4b 同一开关                 │
│ （各 1 次视觉 JSON）  │       │                              │
│ --skip-listing-     │       │                              │
│ review 时 4b+4c     │       │                              │
│ 俱不跑（非必选）     │       │                              │
└──────────┬──────────┘       └──────────────┬──────────────┘
           └───────────────┬───────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  可选：扩展营销图（主链路默认关；或 `run_marketing_extras_step.py`）→ `seedream-5.0-lite` │
└─────────────────────────────────────────────────────────────┘
```

**输出物**：过程图、`mtwi_ecommerce_samples.yaml`；`--export-deliverables` 时含 **`description_en.md` / `description_fr.md` / `product_image.png` / `manifest.json`**；**`copy_review.md`**、**`locale_grammar_review.md`** 仅在 **未** `--skip-listing-review`（且未 `GMI_SKIP_LISTING_REVIEW=1`）时写入。扩展图 **`product_image_extra_*`** 仅在开启主链路扩展图或单独跑营销步骤时写入。命令行见 [src/README.md](src/README.md)；Prompt 见 [PROMPT_TUNING_NOTES.md](PROMPT_TUNING_NOTES.md)。
