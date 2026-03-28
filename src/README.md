# MTWI 训练与批处理链路

本目录为 **命令行 pipeline**、**批量可复现运行**与**质量评估**；与根目录 **Streamlit 试用页**（见 [README.md](../README.md)，配置细节见 [CONFIGURATION.md](../CONFIGURATION.md)）共用同一套 `mtwi_ecommerce_pipeline` 逻辑。推理底座为 **GMI Cloud Inference Engine**；**默认模型、Agent 步骤与 Chat/图像队列分工** 见 [agent.md](../agent.md)。

## 仓库地图

| 路径 | 作用 |
|------|------|
| `src/mtwi_ecommerce_pipeline.py` | 主链路 CLI |
| `src/run_bulk_pipeline.py` | 批量：pipeline → deliverables → 双评估 |
| `src/eval_image_quality.py` / `eval_copy_quality.py` | 评估（可单独运行） |
| `src/run_one_deliverable_example.sh` | 单商品示例（`data/demo_one/` → **`demo_outputs/deliverables_demo_one/`**；可用 **`DEMO_OUTPUT_ROOT`** 改根目录；**不含**主链路扩展图；可选 `GMI_DEMO_MODEL_PROFILE=openai_alt`） |
| `src/run_marketing_extras_step.py` | **第二步**：仅 RQ 营销扩展图，写入已有交付目录（`--reference-image` = `*_final.png`）；与主链路输入格式隔离 |
| `src/try_additional_images_only.py` | **仅 RQ 扩展图**试跑（不调 Chat、不写交付包）；粗测模型时用 |
| `src/auto_text_erase_preprocess.py` | **无 MTWI** 批量去字：PaddleOCR + RQ（`seedream-5.0-lite`）+ 可选 harmonize（`bria-fibo-edit`）；`--mock` / `--quads-json` / `--resume` |
| `configs/bulk_run.yaml` / `bulk_run_smoke.yaml` | 批量配置（生产 / mock 冒烟） |
| 根目录 `streamlit_app.py` | Web UI（说明见根 README） |

其它文档：[agent.md](../agent.md)（架构示意）、[PROMPT_TUNING_NOTES.md](../PROMPT_TUNING_NOTES.md)（Prompt）、[doc/DEV_LOG.md](../doc/DEV_LOG.md)（变更记录）。

## 数据格式

`txt_train` 每行：`X1,Y1,X2,Y2,X3,Y3,X4,Y4,文本`（四边形顶点 + 框内文本）。

## 环境（命令行）

在**仓库根目录**（非 `src/` 内）：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GMI_API_KEY="<your-api-key>"   # 非 --mock 必需
```

`src/run_one_deliverable_example.sh` 在未设置环境变量时会尝试读取仓库根目录 `credentials.json` 中的 `api_key`（文件须 gitignore，勿提交）。

## 单商品交付物示例

```bash
bash src/run_one_deliverable_example.sh
# Chat 404/400 时试另一套 model_id 预设：
# GMI_DEMO_MODEL_PROFILE=openai_alt bash src/run_one_deliverable_example.sh
```

主链路**不含**营销扩展图。需要 `product_image_extra_*.png` 时在交付目录生成后执行（示例路径按你仓库产物调整）：

```bash
python src/run_marketing_extras_step.py \
  --reference-image demo_outputs/mtwi_images_demo_one/demo_item_final.png \
  --deliverable-dir demo_outputs/deliverables_demo_one \
  --product-id demo_item \
  --count 3
```

## 主脚本（调参 / 自定义数据路径）

```bash
python src/mtwi_ecommerce_pipeline.py --help

python src/mtwi_ecommerce_pipeline.py --limit 3 \
  --erase-strategy model --quality-strategy local \
  --no-harmonize-after-erase --disable-restore \
  --mask-mode all --generate-additional-images --additional-image-count 3 \
  --vision-model "Qwen/Qwen3-VL-235B" \
  --english-copy-model "openai/gpt-5.4-pro" \
  --french-copy-model "anthropic/claude-sonnet-4.6" \
  --fallback-english-copy-model "openai/gpt-5.4-mini" \
  --fallback-french-copy-model "openai/gpt-5.4-mini" \
  --copy-review-english-model "anthropic/claude-sonnet-4.6" \
  --copy-review-french-model "anthropic/claude-sonnet-4.6" \
  --locale-grammar-english-model "openai/gpt-5.4-nano" \
  --locale-grammar-french-model "openai/gpt-5.4-nano" \
  --image-output-dir outputs/mtwi_images \
  --export-deliverables --deliverable-dir outputs/deliverables
```

步骤 **4b**（英文 + 法文各一次视觉 JSON 质检）与 **4c**（英文 + 法文各一次语法 JSON 质检）**默认始终执行**；可用上列 `--*-model` 与对应 `GMI_*` 环境变量覆盖模型 ID（须与账户内可用名称一致）。

### 操作者定制（单条与批量通用）

对**整次运行**生效（批量时每个 SKU 使用同一套说明）：

| 方式 | 说明 |
|------|------|
| `--user-copy-instructions "..."` | 文案：语气、长度、SEO、受众等；写入 step4，并传给文案质检 |
| `--user-copy-instructions-file path.txt` | 若文件存在，**替换**内联文案说明（UTF-8） |
| `--user-image-instructions "..."` | 图像：背景、光线、影调；用于模型去字、harmonize、restore、扩展图 prompt |
| `--user-image-instructions-file path.txt` | 若文件存在，**替换**内联图像说明 |
| 环境变量 | `GMI_USER_COPY_INSTRUCTIONS` / `GMI_USER_IMAGE_INSTRUCTIONS` 会**追加**到上述解析结果之后（长度仍受截断保护） |

产物中 `manifest.json` / YAML 会记录 `user_copy_instructions` 与 `user_image_instructions` 字段便于审计。

批量可在 `configs/bulk_run.yaml` 的 `pipeline` 下配置 `user_copy_instructions`（多行 YAML）、`user_image_instructions` 或对应 `*_file` 键；`skip_listing_review: true` 可跳过 Step4b/4c。

## 批量可复现

```bash
python src/run_bulk_pipeline.py --config configs/bulk_run.yaml
# 冒烟：configs/bulk_run_smoke.yaml
```

配置里**未列出**的字段由 `run_bulk_pipeline.py` 使用内置默认值。若改用 **model** 去字或启用模型 restore，在 YAML 的 `pipeline` 下增加 `eraser_model`、`restore_model` 等键即可覆盖。

每次批量运行目录：`outputs/runs/<run_id>/`（含 `run.log`、`mtwi_ecommerce_samples.yaml`、`deliverables/`、`*_metrics.*`、`stability_baseline.*`）。

### 固定 N 条（例如 100）

复制 `configs/bulk_run.yaml` 为新文件，将其中 `pipeline.limit` 改为 `100`，再 `--config` 指向该文件。

## 输出提要

- **聚合 YAML**：单次默认 `outputs/mtwi_ecommerce_samples.yaml`；批量在对应 `run_id` 目录下。
- **交付包**：每商品子目录含 `product_image.png`、`description_en.md`、`description_fr.md`、`manifest.json`，可选 `product_image_extra_*.png`；根目录 `deliverables_index.csv` 含 `copy_review_md`、`locale_grammar_md`。
- **文案 LLM 质检（必选，4b）**：英 / 法 `--copy-review-english-model` / `--copy-review-french-model`（**默认均为** `anthropic/claude-sonnet-4.6`，同一套多模态 + JSON 调用）；各一次视觉 + JSON，结果合并进 `manifest.json` 的 `copy_review` 与 **`copy_review.md`**。稳定性：`step4b_copy_review_failed`、`copy_review_fail`、`copy_review_revise`。
- **加拿大英语 + 加拿大法语语法质检（必选，4c）**：`--locale-grammar-english-model` / `--locale-grammar-french-model`（默认均为 `openai/gpt-5.4-nano`）。`locale_grammar_review`、**`locale_grammar_review.md`**、索引列 `locale_grammar_md`。稳定性：`step4c_locale_grammar_failed`、`locale_grammar_fail`、`locale_grammar_revise`。

## 单独跑评估

将 `--samples-yaml` 换成实际产物路径（批量 run 下为 `outputs/runs/<run_id>/mtwi_ecommerce_samples.yaml`）：

```bash
python src/eval_image_quality.py \
  --samples-yaml outputs/mtwi_ecommerce_samples.yaml \
  --output-csv outputs/mtwi_image_metrics.csv \
  --output-md outputs/mtwi_image_metrics.md

python src/eval_copy_quality.py \
  --samples-yaml outputs/mtwi_ecommerce_samples.yaml \
  --output-csv outputs/mtwi_copy_metrics.csv \
  --output-md outputs/mtwi_copy_metrics.md
```

## 看板

- 日志：`tail -f outputs/runs/<run_id>/run.log`
- 滚动基线：同目录 `stability_baseline.md`（刷新间隔由配置 `stability_update_every` 控制）

## 分享 Streamlit 试用页（给他人）

| 方式 | 适用 | 注意 |
|------|------|------|
| 发仓库 + 根 **[README.md](../README.md)** | 对方本机跑 Streamlit | 对方自备 Key 或使用 Mock |
| 局域网 | 同 WiFi | 见 **[CONFIGURATION.md](../CONFIGURATION.md)** |
| 临时公网 | 远程演示 | ngrok / Cloudflare Tunnel 等，用完即关 |
| Streamlit Cloud | 长期托管 | 平台 Secrets 配置 `GMI_API_KEY`，仓库不含密钥 |
