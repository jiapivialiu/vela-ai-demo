# Vela AI Demo

本项目用于参加 **Party Nights「AI 出海」赛道**。电商商品图工作流（去字 / 理解 / 加拿大英语与法语 listing / 质检）搭建在 **GMI Cloud Inference Engine** 之上，通过统一推理接口调用多模态与文本模型。

仓库内提供 **Streamlit 试用页**：上传商品图 + MTWI 格式文本框标注，一键跑通链路并预览、下载结果（支持 Mock 离线试流程）。链路中 **4b 文案质检** 与 **4c 加英/加法语法质检** 默认始终执行（真实模式会多次调用 GMI；侧栏「回退与审稿模型」可改模型 ID，见 [CONFIGURATION.md](CONFIGURATION.md)）。

**更多配置**（API Key、局域网、`GMI_*` 模型环境变量、批量）见 **[CONFIGURATION.md](CONFIGURATION.md)**。**Agent 步骤、默认 LLM、每 SKU 调用量与流程图** 见 [agent.md](agent.md)；命令行与批处理见 [src/README.md](src/README.md)。

---

## 用 `demo_one` 开 Streamlit 试用

1. **环境**（仓库根目录）

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2. **GMI API Key**（真实调模型时必选其一）

- 终端：`export GMI_API_KEY="你的密钥"`
- 或启动网页后在侧栏密码框粘贴（仅本会话）

不调 API、只试界面与占位结果：侧栏勾选 **Mock 模式**。

3. **启动**

```bash
streamlit run streamlit_app.py
```

浏览器打开 **http://localhost:8501**。

4. **对齐 `demo_one` 示例**

- 标注：打开 **[data/demo_one/txt_train/demo_item.txt](data/demo_one/txt_train/demo_item.txt)**，将其中一行（`X1,Y1,…,X4,Y4,文本`）复制到页面 **「2. 文本框标注」** 文本框，或 **上传该文件**。
- 图片：上传一张 **商品主图**（PNG / JPG / WebP 等）。示例坐标对应画面左上角附近一块区域；你可使用任意商品图，将标注行中的坐标改成与你图上水印/文字区域大致一致即可。
- 可选填写 **「3. 用户要求」**；侧栏可改 Mask、扩展图等（详见 [CONFIGURATION.md](CONFIGURATION.md)）。
- 点击 **开始处理**，在 **结果预览 / 下载** 查看输出。产物在 `outputs/streamlit_runs/`（已 gitignore）。
