# Vela AI MTWI Ecommerce Demo

MTWI 图 + 四边形文本框 → 去字/提质 → 视觉理解 → 加拿大英语 + 法语文案 → 可选同商品扩展图。**批量跑**会再接图像评估、文案评估和稳定性基线输出。

## 脚本与文档（当前仓库）

| 路径 | 作用 |
|------|------|
| `src/mtwi_ecommerce_pipeline.py` | 主链路 CLI |
| `src/run_bulk_pipeline.py` | 批量：pipeline → deliverables → 双评估 |
| `src/eval_image_quality.py` / `src/eval_copy_quality.py` | 评估（也可单独跑） |
| `scripts/run_one_deliverable_example.sh` | 单商品示例（`data/demo_one/` → `outputs/deliverables_demo_one/`） |
| `configs/bulk_run.yaml` / `configs/bulk_run_smoke.yaml` | 批量配置（生产 / mock 冒烟） |

架构示意：[agent.md](agent.md)。Prompt 约束：[PROMPT_TUNING_NOTES.md](PROMPT_TUNING_NOTES.md)。变更记录：[DEV_LOG.md](DEV_LOG.md)。

## 数据

`txt_train` 每行：`X1,Y1,X2,Y2,X3,Y3,X4,Y4,文本`（四顶点 + 框内文本）。

## 环境

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GMI_API_KEY="<your-api-key>"   # 非 --mock 必需
```

可选：仓库根目录 `credentials.json`（已在 `.gitignore`，勿提交）。`run_one_deliverable_example.sh` 在未导出变量时会读取其中的 `api_key`。

## 怎么跑

### 单商品交付物示例

```bash
bash scripts/run_one_deliverable_example.sh
```

### 主脚本（调参 / 自定义数据路径）

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

### 批量可复现

```bash
python src/run_bulk_pipeline.py --config configs/bulk_run.yaml
# 冒烟：configs/bulk_run_smoke.yaml
```

配置里**没写**的字段由 `run_bulk_pipeline.py` 使用内置默认值。若改用 **model** 去字或启用模型 restore，在 YAML 的 `pipeline` 下自行增加 `eraser_model`、`restore_model` 等键即可覆盖。

每次批量运行目录：`outputs/runs/<run_id>/`（含 `run.log`、`mtwi_ecommerce_samples.yaml`、`deliverables/`、`*_metrics.*`、`stability_baseline.*`）。

### 固定 N 条（例如 100）

复制 `configs/bulk_run.yaml` 为新文件，把其中 `pipeline.limit` 改成 `100`，再 `--config` 指向该文件即可。

## 输出提要

- **聚合 YAML**：单次默认 `outputs/mtwi_ecommerce_samples.yaml`；批量在对应 `run_id` 目录下。
- **交付包**：每商品子目录：`product_image.png`、`description_en.md`、`description_fr.md`、`manifest.json`，可选 `product_image_extra_*.png`；根目录 `deliverables_index.csv`。

## 单独跑评估

将 `--samples-yaml` 换成你的产物路径（批量 run 下为 `outputs/runs/<run_id>/mtwi_ecommerce_samples.yaml`）：

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
- 滚动基线：同目录 `stability_baseline.md`（刷新间隔由配置里 `stability_update_every` 控制）
