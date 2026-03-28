## MTWI 电商 Agent — Prompt 收敛经验

> **与文档的分工**：安装、批处理命令、输出目录、评估方式见 [src/README.md](src/README.md)；Streamlit 试用见根 [README.md](README.md)；**GMI Cloud Inference Engine** 密钥与 `GMI_*` 模型环境变量见 [CONFIGURATION.md](CONFIGURATION.md)；**默认模型 ID、每步调用次数、Chat vs Request Queue** 见 [agent.md](agent.md)。**本文件只写 prompt 约束与句式**。

目标：**只做“去字/去水印 + 画质提升 + 保守理解 + 加拿大英语/法语分模型文案 + 必选事实/语法审稿”**，避免模型自由发挥导致商品不真实。推理均经 **GMI** 路由，无自建模型服务。

### 1) 为什么“文生图”很容易不真实

- **无法强制同款**：纯 text-to-image 无论 prompt 多严格，都可能在材质、结构、数量、细节上漂移。
- **更稳定的策略**：用 **image editing / inpainting**（基于原图）做“去字/修复”，把生成自由度降到最低。

### 2) 步骤1（文字擦除）prompt 要点

- **只允许删除**：只删叠加文字/水印/贴纸/边框；禁止新增或改造任何商品元素。
- **明确保留项**：形状、颜色、数量、包装、品牌标识（若属于商品本体）必须保持。
- **Mask 优先**：能用标注四边形生成 mask 就用 mask（比纯指令更稳定）。
- **关键难点**：标注往往包含“商品本体印刷字 + 叠加水印字”，需要先区分再做 mask。

解决方案（推荐）：
- 先用视觉模型对每个文本框做 **overlay vs product-native** 分类
- 只对 overlay 框生成 mask（`--mask-mode overlay`）
- 分类不确定时一律 **KEEP**（宁可少擦，不要误擦商品本体字）

推荐表达（英文）：
- “Edits allowed: remove overlaid text/watermarks/banners…”
- “Edits NOT allowed: do not change product shape/color/count/packaging…”
- “Output must contain no visible text and no watermark.”

### 3) 步骤2（画质升级）prompt 要点

- 只做：锐化、降噪、纠正色彩、恢复细节
- 禁止：改造商品几何、改变颜色、补加配件、加文字、加 logo

如果该步骤的模型/服务不稳定（500/超时），建议在命令行先 `--disable-restore`，保证主链路可跑。

### 4) 步骤3（多模态理解）prompt 要点

- **当前默认 VLM**：`Qwen/Qwen3-VL-235B`（`--vision-model` / `GMI_VISION_MODEL`），经 GMI 多模态 chat 输出结构化 JSON。
- 强制 **保守推断**：看不到就不要编。
- 输出字段要能支撑下游文案，但不要过细（防止幻觉）。
- **品牌/系列中文处理**：若输出面向海外站点，建议 **罗马化**（拼音）或干脆不输出，避免中英文混杂。

### 5) 步骤4（英/法文案）prompt 要点

- **分模型生成**：英文 listing 由 `--english-copy-model`（默认 `openai/gpt-5.4-pro`）单独一次 JSON 调用产出；法文由 `--french-copy-model`（默认 `anthropic/claude-sonnet-4.6`）单独一次调用产出（与旧版「单次双语 JSON」不同）。
- **严格单语**：
  - English block：只能英文
  - French block：只能法文
- **禁止中文字符**：包括品牌名、属性值；遇到中文品牌应拼音化或省略。
- **禁用卖家噪音**：URL、盗图提示、水印宣称不要进入文案。
- **事实优先**：不写“最好的/100%保证”等无法验证的强营销断言。

### 6) 稳定产出建议（经验）

- **对每一步做容错降级**：某一步失败不应阻塞整体（脚本里用 `warnings` 记录失败原因）。
- **文案模型设置 fallback**：英文 / 法文主模型偶发失败时，分别用 `--fallback-english-copy-model` / `--fallback-french-copy-model`（默认 `openai/gpt-5.4-mini`）重试该语言。
- **先跑小样本**：`--limit 1` 或 `--limit 3`，先确认图片编辑链路是否真的改变了图片（hash 不同、视觉可见）。
- **按标注去字**：默认 `--mask-mode all`（txt 里每个四边形都进蒙版擦除）。**保留商品本体印刷字**时改用 `--mask-mode overlay`（在线 VLM + 启发式；Mock 下无匹配则回退为全部框并打 warning）。

### 7) 步骤 4b（必选 LLM 文案质检，英法分模型）

- **调用**：英 / 法 listing 各一次：`--copy-review-english-model` 与 `--copy-review-french-model`（**默认均为** `anthropic/claude-sonnet-4.6`）；须支持**图像 + JSON**。**不引入** LangChain/Crew 等多智能体框架。
- **角色**：各审稿员只审**对应语言**的 listing，对照**原图** + **结构化属性**，查夸张营销、与属性矛盾、与画面不符的断言。
- **合并输出**：`overall_status` 取两侧较严；`summary` 拼接 EN/FR；`en_revision_suggestions` / `fr_revision_suggestions` 分别来自两侧；`scores` 取 grounding / factual_tone 的较小值。
- **与规则评估的关系**：`eval_copy_quality.py` 仍做格式/语言启发式；本步是**语义与事实**补充，二者互补。
- **成本**：每件 **2 次** vision JSON 调用（外加步骤3、步骤4 等）。

### 8) 操作者定制（文案 / 图像）

- **接口**：`--user-copy-instructions`（+ 可选 file）、`--user-image-instructions`（+ 可选 file）；可与 `GMI_USER_COPY_INSTRUCTIONS` / `GMI_USER_IMAGE_INSTRUCTIONS` 叠加（见 `src/README.md`）。
- **原则**：文案侧仍禁止编造商品事实、禁止中文输出块；图像侧仍禁止换 SKU。定制内容仅作为**偏好层**拼进现有系统 prompt。

### 9) 步骤 4c（加拿大英语 + 加拿大法语语法质检，必选）

- **模型**：`--locale-grammar-english-model` 与 `--locale-grammar-french-model`（默认均为 `openai/gpt-5.4-nano`）；每件 **2 次**文本 `chat_json`。
- **分工**：与 4b 不同——4b 侧重**事实/图像对齐**；4c 侧重 **en-CA** 与 **français canadien** 的语法、拼写、标点及加拿大市场常用写法（不自动改稿，只出 `issues` + `suggested_edits`）。
- **输出**：`locale_grammar_review.canadian_english` / `canadian_french`，各含 `status`（pass/revise/fail）、`issues[]`、`suggested_edits`、`notes`。

