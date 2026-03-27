# MTWI 训练与批处理链路

本目录为 **命令行 pipeline**、**批量可复现运行**与**质量评估**；与根目录 **Streamlit 试用页**（见仓库 [README.md](../README.md)）共用同一套 `mtwi_ecommerce_pipeline` 逻辑。

## 仓库地图

| 路径 | 作用 |
|------|------|
| `src/mtwi_ecommerce_pipeline.py` | 主链路 CLI |
| `src/run_bulk_pipeline.py` | 批量：pipeline → deliverables → 双评估 |
| `src/eval_image_quality.py` / `eval_copy_quality.py` | 评估（可单独运行） |
| `scripts/run_one_deliverable_example.sh` | 单商品示例（`data/demo_one/` → `outputs/deliverables_demo_one/`） |
| `configs/bulk_run.yaml` / `bulk_run_smoke.yaml` | 批量配置（生产 / mock 冒烟） |
| 根目录 `streamlit_app.py` | Web UI（说明见根 README） |

其它文档：[agent.md](../agent.md)（架构示意）、[PROMPT_TUNING_NOTES.md](../PROMPT_TUNING_NOTES.md)（Prompt）、[DEV_LOG.md](../DEV_LOG.md)（变更记录）。

## 数据格式

`txt_train` 每行：`X1,Y1,X2,Y2,X3,Y3,X4,Y4,文本`（四边形顶点 + 框内文本）。

## 环境（命令行）

在**仓库根目录**（非 `src/` 内）：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GMI_API_KEY="<your-api-key>"   # 非 --mock 必需
```

`scripts/run_one_deliverable_example.sh` 在未设置环境变量时会尝试读取仓库根目录 `credentials.json` 中的 `api_key`（文件须 gitignore，勿提交）。

## 单商品交付物示例

```bash
bash scripts/run_one_deliverable_example.sh
```

## 主脚本（调参 / 自定义数据路径）

```bash
python src/mtwi_ecommerce_pipeline.py --help

python src/mtwi_ecommerce_pipeline.py --limit 3 \
  --erase-strategy local --quality-strategy local \
  --no-harmonize-after-erase --disable-restore \
  --mask-mode overlay --generate-additional-images --additional-image-count 3 \
  --vision-model "openai/gpt-4o" --qwen-model "Qwen/Qwen3.5-27B" \
  --fallback-text-model "openai/gpt-4o-mini" \
  --image-output-dir outputs/mtwi_images \
  --export-deliverables --deliverable-dir outputs/deliverables
```

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
- **交付包**：每商品子目录含 `product_image.png`、`description_en.md`、`description_fr.md`、`manifest.json`，可选 `product_image_extra_*.png`；根目录 `deliverables_index.csv`。

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
| 局域网 | 同 WiFi | `streamlit run streamlit_app.py --server.address 0.0.0.0` |
| 临时公网 | 远程演示 | ngrok / Cloudflare Tunnel 等，用完即关 |
| Streamlit Cloud | 长期托管 | 平台 Secrets 配置 `GMI_API_KEY`，仓库不含密钥 |
