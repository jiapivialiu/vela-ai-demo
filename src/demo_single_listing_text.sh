#!/usr/bin/env bash
# Demo ① — 单图：图像处理 + 英法文案 + 交付包（不含营销扩展图 product_image_extra_*）
#
# 输入：`data/demo_one/`（与 `run_one_deliverable_example.sh` 相同）
# 输出：`${DEMO_OUTPUT_ROOT:-demo_outputs}/` 下 mtwi 过程图、yaml/json、`deliverables_demo_one/<slug>/`
#
# 演示顺序建议：先跑本脚本，再跑 `demo_single_marketing_extras.sh` 补扩展图。
#
# 环境：`GMI_API_KEY` 或根目录 `credentials.json`；可选 `DEMO_OUTPUT_ROOT`、`GMI_DEMO_MODEL_PROFILE=openai_alt` 等（见 `run_one_deliverable_example.sh` 注释）。
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/run_one_deliverable_example.sh"
