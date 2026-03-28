> **说明**：下表 **「落实默认」** 与 `src/mtwi_ecommerce_pipeline.py` → `parse_args` **完全一致**。账户内 catalog 若使用不同 ID，请用 CLI 或 **`GMI_*`** 环境变量覆盖。试用与密钥：[README.md](README.md)、[CONFIGURATION.md](CONFIGURATION.md)。

---

## LLM 与 Agent 规格（当前仓库落实）

以下对应参赛/产品侧表述与 **脚本默认** 的映射；**4b 文案质检** 与 **4c 语法质检** 在流水线中 **无开关、必选执行**（`run_pipeline` 始终调用；Mock 下返回占位 JSON）。

| 环节 | 产品要求（表述） | GMI 默认 `model_id`（`parse_args`） | CLI | 环境变量 |
|------|------------------|-------------------------------------|-----|----------|
| **步骤3 多模态理解** | **Qwen3-VL-235B** | `Qwen/Qwen3-VL-235B` | `--vision-model` | `GMI_VISION_MODEL` |
| **步骤4 英语 listing** | **GPT-5.4-pro** | `openai/gpt-5.4-pro` | `--english-copy-model` | `GMI_ENGLISH_COPY_MODEL` |
| **步骤4 法语 listing** | **Claude Sonnet 4.6** | `anthropic/claude-sonnet-4.6` | `--french-copy-model` | `GMI_FRENCH_COPY_MODEL` |
| **步骤4 英语回退** | （工程回退，非产品主路径） | `openai/gpt-5.4-mini` | `--fallback-english-copy-model` | `GMI_FALLBACK_ENGLISH_COPY_MODEL` |
| **步骤4 法语回退** | （工程回退，非产品主路径） | `openai/gpt-5.4-mini` | `--fallback-french-copy-model` | `GMI_FALLBACK_FRENCH_COPY_MODEL` |
| **4b 英文文案质检 Agent**（必选，视觉+JSON） | **GPT-5.4** | `openai/gpt-5.4` | `--copy-review-english-model` | `GMI_COPY_REVIEW_ENGLISH_MODEL` |
| **4b 法文文案质检 Agent**（必选，视觉+JSON） | **Claude Sonnet 4.6** | `anthropic/claude-sonnet-4.6` | `--copy-review-french-model` | `GMI_COPY_REVIEW_FRENCH_MODEL` |
| **4c 英文语法质检 Agent**（必选，文本 JSON） | **gpt-5.4-nano** | `openai/gpt-5.4-nano` | `--locale-grammar-english-model` | `GMI_LOCALE_GRAMMAR_ENGLISH_MODEL` |
| **4c 法文语法质检 Agent**（必选，文本 JSON） | **gpt-5.4-nano** | `openai/gpt-5.4-nano` | `--locale-grammar-french-model` | `GMI_LOCALE_GRAMMAR_FRENCH_MODEL` |

**脚本一致性（已核对）**：上述默认值同步于 **`configs/bulk_run.yaml`**、**`configs/bulk_run_smoke.yaml`**、**`src/run_bulk_pipeline.py`** 的 `build_pipeline_cmd` 内置默认、**`scripts/run_one_deliverable_example.sh`** 显式参数、**`streamlit_app.py`** 侧栏 `os.getenv(..., 默认)`。

**ID 说明**：多模态模型在 GMI 上常以 **`厂商/模型名`** 形式注册（如 `Qwen/Qwen3-VL-235B`），与口语「Qwen3-VL-235B」指同一档能力；若控制台列表不同，以控制台为准并改 env。

---

## 项目与推理框架

- **场景**：**Party Nights「AI 出海」赛道** — 电商商品图 → 去字/修图 → 结构化理解 → **加拿大英语 + 加拿大法语** listing → **必选** 事实向文案质检（4b）+ **必选** 加英/加法语法质检（4c）。
- **底座**：**GMI Cloud Inference Engine** — 统一 API Key 与模型路由；本仓库不自建推理集群。
- **代码侧分工**：
  - **对话式推理**（多模态 / 文本 JSON）：`ChatClient`（`chat_json`）— 步骤 **3 / 4 / 4b / 4c**。
  - **图像任务队列**（编辑 / 变体）：`RequestQueueClient` — 步骤 **1 model 擦除、2 harmonize/restore、扩展营销图**。
  - **本地**：`erase-strategy=local`、`quality-strategy=local` 时为 OpenCV/PIL 等，无 LLM。

## 「Agent」在本文档中的含义

**【Agent】步骤**：通过 GMI **对话式 API**（`chat` + 结构化 JSON）完成 **理解、生成或审核** → 使用 **VLM** 或 **文本 LLM**。

**其余步骤**：编排与 I/O、本地图像处理，或 **Request Queue 图像接口**（非 `chat_json`）。

---

## 图像与其它默认模型（非上述 LLM 规格核心）

| 类型 | 环节 | 默认模型 ID | CLI | 环境变量 |
|------|------|-------------|-----|----------|
| 图像模型 | 步骤1 model 擦除 | `bria-eraser` | `--eraser-model` | `GMI_ERASER_MODEL` |
| 图像模型 | 步骤2 harmonize | `bria-fibo-edit` | `--harmonize-model` | `GMI_HARMONIZE_MODEL` |
| 图像模型 | 步骤2 restore | `bria-fibo-restore` | `--restore-model` | `GMI_RESTORE_MODEL` |
| 图像模型 | 扩展营销图 | `seedream-5.0-lite` | `--additional-image-model` | `GMI_ADDITIONAL_IMAGE_MODEL` |

**策略默认**：`--erase-strategy` 默认 `local`，`--quality-strategy` 默认 `local`，`--mask-mode` 默认 `overlay`；CLI 中 harmonize 默认开启，可用 `--no-harmonize-after-erase` 关闭（Streamlit 侧常关 harmonize 以降低调用）。

---

## 每 SKU 对话类调用量（无生成 fallback 时）

| 步骤 | 次数 | 说明 |
|------|------|------|
| 3 | 1 | Qwen3-VL-235B（`Qwen/Qwen3-VL-235B`）→ 结构化属性 JSON |
| 4 | 2 | 英语 GPT-5.4-pro + 法语 Claude Sonnet 4.6，各 1 次 JSON |
| 4b | 2 | 英语 GPT-5.4 + 法语 Claude Sonnet 4.6，各 1 次视觉 JSON，合并为 `copy_review` |
| 4c | 2 | 英语 + 法语语法各 1 次，**均为** `openai/gpt-5.4-nano` |
| **合计** | **7** | 不含步骤4主模型失败后的 fallback、不含图像队列 |

---

## 入口与输入

| 入口 | 用途 |
|------|------|
| **Streamlit** | 根目录 `streamlit_app.py`；[README.md](README.md) |
| **CLI** | `src/mtwi_ecommerce_pipeline.py` |
| **批量** | `src/run_bulk_pipeline.py` + `configs/bulk_run*.yaml` |

**共同输入**：商品主图；MTWI 每行 `X1,Y1,X2,Y2,X3,Y3,X4,Y4,文本`；可选 `--user-copy-instructions` / `--user-image-instructions`（及 file、`GMI_USER_*`）。

---

## 主链路流程图（括号内为默认 `model_id`）

```
┌─────────────────────────────────────────────────────────────┐
│  输入：商品图 + MTWI 标注 + 可选用户偏好                         │
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
│【Agent·必选】4b     │       │【Agent·必选】4c               │
│ 英文文案质检         │       │ 两语言语法质检 **均为**        │
│ GPT-5.4 →           │       │ `openai/gpt-5.4-nano`        │
│ `openai/gpt-5.4`    │       │ （英、法各 1 次文本 JSON）      │
│ 法文文案质检         │       │                              │
│ Claude Sonnet 4.6 → │       │                              │
│ `anthropic/claude-  │       │                              │
│  sonnet-4.6`        │       │                              │
└──────────┬──────────┘       └──────────────┬──────────────┘
           └───────────────┬───────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  可选：扩展营销图 → `seedream-5.0-lite`                        │
└─────────────────────────────────────────────────────────────┘
```

**输出物**：过程图、`mtwi_ecommerce_samples.yaml`；`--export-deliverables` 时含 **`description_en.md` / `description_fr.md` / `product_image.png` / `manifest.json`**、**`copy_review.md`**、**`locale_grammar_review.md`**。命令行见 [src/README.md](src/README.md)；Prompt 见 [PROMPT_TUNING_NOTES.md](PROMPT_TUNING_NOTES.md)。
