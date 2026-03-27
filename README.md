# 🌟 Vela AI 出海商品本地化 & 多模态素材生成 Agent Demo

Vela AI starts here.

## 项目概述

本项目旨在探索 **中文商品 → 加拿大市场本地化 + 多模态展示** 的自动化解决方案。
通过结合 **多模态理解、语言本地化、内容生成和图像生成** 技术，我们实现了一个最小可落地 demo，使中文商品在海外市场能够快速生成高质量、适应本地用户习惯的商品展示内容。

* **示例品类**：穿戴甲（Press-on Nails）
* **输入**：中文商品描述 + 商品图片
* **输出**：英文商品标题、文案、导购文本 + 模特佩戴图

---

## 🧩 核心流程

```text
中文商品描述 + 商品图片
         ↓
[Step 1] 商品多模态理解（提取风格、卖点、场景）
         ↓
[Step 2] 本地化策略生成（Canadian English, tone 控制）
         ↓
[Step 3] 文案生成（Title / Bullet points / Description / 导购语）
         ↓
[Step 4] 图像生成（商品佩戴效果图）
         ↓
[Step 5] 导购展示组合（可视化 UI / 简单网页）
```

---

## 🚀 技术架构

* **模型层**：

  * GMI Cloud 多模态理解模型
  * GMI Cloud 文本生成模型（生成加拿大市场文案）
  * GMI Cloud 图像生成模型（佩戴效果图）
* **Agent层**：

  * LangChain / 简单 Python orchestration
  * 多模态输入输出统一调度
* **前端展示**：

  * Streamlit / Gradio demo 页面
* **数据准备**：

  * 自有商品图片 + 中文描述（5–10个示例商品即可）
  * 可选增强：RecSysDatasets / Amazon 开源商品数据用于 few-shot prompt

---

## 💡 贡献点与亮点

1. **多模态闭环**

   * 将商品图片 + 中文描述 → 结构化信息 → 英文文案 → 图像生成
   * 完整覆盖理解 → 本地化 → 展示链路

2. **本地化策略控制**

   * 针对加拿大市场的语言风格、拼写习惯、营销语气进行自动化调整
   * 区别于传统直接翻译的方法

3. **端到端自动化 demo**

   * 最小可落地系统（MVP），从商品输入到生成多模态输出全自动
   * 可直接作为出海商品展示或导购原型使用

4. **易扩展 & 可复用**

   * Agent框架可接入更多品类或市场
   * 支持多模态生成，可扩展至视频或多角度展示

---

## 🔍 解决的痛点

| 传统方法                | 本项目优势                       |
| ------------------- | --------------------------- |
| 海外商品上架需要人工翻译 & 调整文案 | 自动生成加拿大英语本地化文案，控制 tone 和关键词 |
| 图片展示缺乏场景化和模特佩戴      | 自动生成佩戴效果图，结合商品风格与使用场景       |
| 上架过程耗时、重复劳动多        | Agent 自动化执行多模态生成流程，显著降低人力成本 |
| 无法快速验证海外市场风格        | 通过 demo 可快速迭代不同文案和展示风格      |

---

## 📦 Demo 使用说明

1. 准备示例商品：

   * 中文描述（标题 + 卖点）
   * 商品图片（单张或多张）
2. 安装依赖：

```bash
pip install requests streamlit gmi-sdk
```

3. 配置 GMI Cloud API Key
4. 启动 demo：

```bash
streamlit run app.py
```

5. 输入中文商品信息，即可生成英文文案 + 多模态展示图片

### 🧪 本地 Press-on Nails Pipeline（CLI）

本仓库还提供了一个最小可运行的 CLI Pipeline，用于直接从 `data/press_on_nails.csv`
读取中文非结构化商品信息，并生成结构化英文输出：

**依赖安装（建议在虚拟环境中）**

```bash
python -m venv .venv
source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt  # 可选，如不存在则安装 requests, pyyaml
```

核心依赖：

- `requests`：调用 GMI Cloud Inference Engine LLM REST API
- `PyYAML`：将结构化输出保存为可读性强的 `.yaml`

**运行（Mock 模式，离线演示）**

```bash
source .venv/bin/activate
python src/press_on_nails_pipeline.py --limit 3 --mock
```

这会读取 `data/press_on_nails.csv`，解析中文描述，生成示例英文文案，并写入：

- `outputs/press_on_nails_samples.yaml`

**运行（真实 LLM 模式，走 GMI Cloud GPU）**

```bash
export GMI_API_KEY="<your-api-key>"  # 来自 GMI Cloud 控制台
export GMI_LLM_MODEL="deepseek-ai/DeepSeek-R1"  # 可选，默认同此值

source .venv/bin/activate
python src/press_on_nails_pipeline.py --limit 3
```

脚本会调用：

- Endpoint: `POST https://api.gmi-serving.com/v1/chat/completions`
- Request: `response_format={"type": "json_object"}` 强制模型返回 JSON 结构

输出示例结构（YAML 视图）：

```yaml
- product_id: "1001"
   localized_title: "..."          # LLM 生成的英文标题
   bullet_points: ["...", ...]     # 3–4 条英文卖点
   description: "..."             # 面向加拿大用户的英文描述
   call_to_action: "..."          # 引导下单的 CTA 文案
   image_prompt: "..."            # 可直接喂给图像模型的英文 prompt
   source_summary:                  # 从中文源数据解析出的结构化属性
      brand: "海鲤"
      style: "可爱,甜美"
      pattern: "猫爪"
      ...

如需在本地端到端生成 **文案 + 图片**，可以在以上命令基础上加上：

```bash
python src/press_on_nails_pipeline.py --limit 3 --generate-image --image-output-dir outputs/images
```

其中：

- `GMI_IMAGE_MODEL`（可选）控制所用的图像模型，默认使用模型库中的 `seedream-5.0-lite` 文本转图像模型。
- 生成的图片会保存在 `outputs/images/` 目录下，对应路径会写入 YAML 的 `generated_image_path` 字段。
```

---

## 🎯 下一步计划

* 扩展到更多品类（美妆、配饰、家居等）
* 支持多国家、多语言本地化
* 集成商品推荐系统，自动挑选热销商品
* 探索视频生成 / 模型强化学习优化导购效果

---

这个 README 的结构可以直接用于你的项目展示，**突出你的创新点**，尤其是“多模态闭环 + 本地化控制 + Agent自动化”，相比现有 Shopify AI 或国内现有电商 AI 系统的差异就是：

1. 不依赖人工翻译 → 端到端本地化
2. 自动生成图文组合 → 真实展示场景
3. 可快速迭代多商品 → demo 可扩展
