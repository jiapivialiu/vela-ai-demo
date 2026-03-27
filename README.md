# Vela AI Agent — 本地 Streamlit 网页

在浏览器里上传**商品图**和 **MTWI 格式**的文本框坐标（每行 `X1,Y1,…,X4,Y4,文本`），一键跑通修图与英法文案，并可在页面预览、打包下载。

> 命令行批处理、数据集目录、评估脚本等见 **[src/README.md](src/README.md)**。

## 1. 环境

在**仓库根目录**执行：

```bash
python -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## 2. 配置 API Key（真实模型）

调用 GMI 接口需要 **API Key**（与用量/计费相关）。任选其一：

| 方式 | 做法 |
|------|------|
| **环境变量（推荐）** | 启动前执行：`export GMI_API_KEY="你的密钥"`（Windows 可用 `set` / 系统环境变量） |
| **网页侧栏** | 运行应用后，在左侧 **GMI API Key** 密码框粘贴；仅当前浏览器会话内生效 |

**注意**：不要把真实 Key 写进代码或提交到 Git。可选在本机使用根目录 `credentials.json`（需已在 `.gitignore`），但 **Streamlit 页面默认只认环境变量与侧栏**；若要用文件，请先 `export GMI_API_KEY="$(python -c "import json;print(json.load(open('credentials.json'))['api_key'])")"` 再启动。

不需要 Key、只想试界面与流程时：侧栏勾选 **Mock 模式**（不调外部 API，结果为占位逻辑）。

## 3. 启动本地网页

```bash
streamlit run streamlit_app.py
```

默认浏览器打开 **http://localhost:8501**。

- 同一 WiFi 给他人临时访问：`streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501`，对方访问 `http://<你的电脑局域网 IP>:8501`（仍使用你本机的 Key）。

运行产物目录：`outputs/streamlit_runs/`（已 `.gitignore`，无需手删）。
