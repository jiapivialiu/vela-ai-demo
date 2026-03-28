# 配置与进阶说明

根目录 [README.md](README.md) 为 Party Nights 参赛说明与 Streamlit + `demo_one` 试用；本文档补充 **GMI Cloud Inference Engine** 下的密钥、局域网、命令行与批量、**模型环境变量**。

## 推理栈（与 agent.md 一致）

- **对话推理**：步骤 3 / 4 / 4b / 4c 经 GMI **Chat**（多模态或文本 JSON）。**4b、4c 无关闭开关**，每件必跑。
- **图像任务**：擦除 / harmonize / restore / 扩展图经 GMI **Request Queue**（非 chat）。
- **产品级 LLM 规格与 `model_id` 对照表**（Qwen3-VL-235B、GPT-5.4-pro、Claude Sonnet 4.6、GPT-5.4、gpt-5.4-nano 等）：见 **[agent.md](agent.md)** 文首 **「LLM 与 Agent 规格」**；含脚本/YAML 一致性说明与每 SKU 约 7 次 chat 说明。

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
GMI_ENGLISH_COPY_MODEL
GMI_FRENCH_COPY_MODEL
GMI_FALLBACK_ENGLISH_COPY_MODEL
GMI_FALLBACK_FRENCH_COPY_MODEL
GMI_COPY_REVIEW_ENGLISH_MODEL
GMI_COPY_REVIEW_FRENCH_MODEL
GMI_LOCALE_GRAMMAR_ENGLISH_MODEL
GMI_LOCALE_GRAMMAR_FRENCH_MODEL
GMI_ERASER_MODEL
GMI_RESTORE_MODEL
GMI_HARMONIZE_MODEL
GMI_ADDITIONAL_IMAGE_MODEL
GMI_ERASE_STRATEGY
GMI_QUALITY_STRATEGY
GMI_MASK_MODE
GMI_USER_COPY_INSTRUCTIONS
GMI_USER_IMAGE_INSTRUCTIONS
```

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
- 单商品脚本：`bash scripts/run_one_deliverable_example.sh`。
- 批量：`configs/bulk_run.yaml`、`configs/bulk_run_smoke.yaml`，`python src/run_bulk_pipeline.py --config ...`。

## 其它文档

| 文档 | 内容 |
|------|------|
| [agent.md](agent.md) | Agent 步骤、默认模型、调用次数、流程图 |
| [PROMPT_TUNING_NOTES.md](PROMPT_TUNING_NOTES.md) | Prompt 约束与调参 |
| [DEV_LOG.md](DEV_LOG.md) | 变更记录 |
