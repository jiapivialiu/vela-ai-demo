"""Minimal Streamlit UI for the MTWI ecommerce pipeline.

Run from repository root (see README.md for demo_one Streamlit steps; CONFIGURATION.md for API key and advanced options).

Batch / training CLI is documented in src/README.md.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import streamlit as st

# Repo root = parent of this file
REPO_ROOT = Path(__file__).resolve().parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mtwi_ecommerce_pipeline import parse_annotation_file, parse_args, run_pipeline  # noqa: E402

INPUT_BASENAME = "item"
INPUT_EXT = ".png"


def validate_annotation_text(raw: str) -> tuple[bool, str]:
    """Check non-empty lines match MTWI txt format (8 numbers + comma-joined text)."""
    if not raw.strip():
        return False, "请至少填写一行标注，或一行占位测试框（格式见下方说明）。"
    errors: list[str] = []
    for i, line in enumerate(raw.strip().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 9:
            errors.append(f"第 {i} 行：至少需要 9 个逗号分段（8 个坐标 + 文本）。")
            continue
        try:
            for p in parts[:8]:
                float(p.strip())
        except ValueError:
            errors.append(f"第 {i} 行：前 8 个字段必须是数字坐标。")
    if errors:
        return False, "\n".join(errors)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as tf:
        tf.write(raw.strip() + "\n")
        tmp_path = Path(tf.name)
    try:
        spans = parse_annotation_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    if not spans:
        return False, "没有解析出任何有效文本框，请检查格式。"
    return True, f"已解析 {len(spans)} 个文本框。"


def build_zip(job_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(job_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(job_dir))
    buf.seek(0)
    return buf.read()


def find_deliverable_package(deliverables: Path) -> Path | None:
    if not deliverables.is_dir():
        return None
    for sub in sorted(deliverables.iterdir()):
        if sub.is_dir() and (sub / "product_image.png").exists():
            return sub
    return None


def render_result_preview(job_dir: Path) -> None:
    """In-page preview: hero + extra images, EN/FR markdown, manifest."""
    deliverables = job_dir / "deliverables"
    pkg = find_deliverable_package(deliverables)
    if pkg is None:
        st.warning("未找到交付子目录或缺少 `product_image.png`，请检查是否勾选导出交付物或查看运行日志。")
        return

    st.markdown("### 图片预览")
    hero = pkg / "product_image.png"
    extras = sorted(pkg.glob("product_image_extra_*.png"))
    thumbs = [("主图 `product_image.png`", hero)] + [
        (p.name, p) for p in extras
    ]
    n = len(thumbs)
    cols = st.columns(min(n, 4) or 1)
    for i, (label, path) in enumerate(thumbs):
        if not path.is_file():
            continue
        with cols[i % len(cols)]:
            st.caption(label)
            st.image(str(path), use_container_width=True)

    en_path = pkg / "description_en.md"
    fr_path = pkg / "description_fr.md"
    st.markdown("### 文案预览（渲染 Markdown）")
    c_en, c_fr = st.columns(2)
    with c_en:
        st.markdown("#### English")
        if en_path.is_file():
            st.markdown(en_path.read_text(encoding="utf-8"))
        else:
            st.caption("无 `description_en.md`")
    with c_fr:
        st.markdown("#### Français")
        if fr_path.is_file():
            st.markdown(fr_path.read_text(encoding="utf-8"))
        else:
            st.caption("无 `description_fr.md`")

    review_path = pkg / "copy_review.md"
    if review_path.is_file():
        st.markdown("### 文案质检（事实 / 夸张 / 与图一致性）")
        st.markdown(review_path.read_text(encoding="utf-8"))

    loc_path = pkg / "locale_grammar_review.md"
    if loc_path.is_file():
        st.markdown("### 加拿大英语 + 加拿大法语语法质检")
        st.markdown(loc_path.read_text(encoding="utf-8"))

    manifest = pkg / "manifest.json"
    if manifest.is_file():
        with st.expander("manifest.json（原始 JSON）", expanded=False):
            st.code(manifest.read_text(encoding="utf-8"), language="json")

    samples = job_dir / "mtwi_ecommerce_samples.yaml"
    if samples.is_file():
        with st.expander("mtwi_ecommerce_samples.yaml（节选）", expanded=False):
            text = samples.read_text(encoding="utf-8")
            st.code(text[:12000] + ("\n…\n" if len(text) > 12000 else ""), language="yaml")


def main() -> None:
    st.set_page_config(page_title="MTWI Agent", page_icon="🖼️", layout="wide")
    st.title("MTWI 商品图 Agent（基础版）")
    st.caption(
        "上传商品图 + MTWI 坐标文本：去字 / 多模态理解 / 英法分模型文案；4b·4c 质检固定执行；可选扩展图。推理经 GMI Cloud。"
    )

    st.markdown(
        """
**页面结构**：侧栏 **运行配置**（Mock、Key、链路开关、模型名）→ 主区 **1 上传图片** → **2 文本框标注**
→ **3 用户要求（可选）** → **开始处理** → **4 结果预览** → **5 下载**。
"""
    )

    with st.sidebar:
        st.header("运行配置")
        mock = st.checkbox("Mock 模式（不调外部 API，离线验链路）", value=False)
        api_key = st.text_input("GMI API Key", type="password", help="可与环境变量 GMI_API_KEY 二选一；Mock 可不填")
        if api_key.strip():
            os.environ["GMI_API_KEY"] = api_key.strip()

        st.subheader("链路选项")
        mask_mode = st.selectbox("Mask 策略", ["overlay", "all"], index=0, help="overlay：尽量只擦水印；all：擦全部标注框")
        harmonize = st.checkbox("去字后模型自然修复（harmonize）", value=False)
        gen_extra = st.checkbox("生成扩展营销图（3 张）", value=True)
        disable_restore = st.checkbox("关闭模型 restore（推荐，减少 500）", value=True)
        st.caption("步骤 4b（英法文案质检）与 4c（语法质检）固定执行，每件含多次 LLM 调用。")

        st.subheader("模型（可改）")
        vision_model = st.text_input("多模态理解（步骤3）", value=os.getenv("GMI_VISION_MODEL", "Qwen/Qwen3-VL-235B"))
        english_copy_model = st.text_input(
            "英文文案生成（步骤4）",
            value=os.getenv("GMI_ENGLISH_COPY_MODEL", "openai/gpt-5.4-pro"),
        )
        french_copy_model = st.text_input(
            "法文文案生成（步骤4）",
            value=os.getenv("GMI_FRENCH_COPY_MODEL", "anthropic/claude-sonnet-4.6"),
        )
        with st.expander("回退与审稿模型（高级）", expanded=False):
            fb_en = st.text_input(
                "英文生成回退",
                value=os.getenv("GMI_FALLBACK_ENGLISH_COPY_MODEL", "openai/gpt-5.4-mini"),
            )
            fb_fr = st.text_input(
                "法文生成回退",
                value=os.getenv("GMI_FALLBACK_FRENCH_COPY_MODEL", "openai/gpt-5.4-mini"),
            )
            cr_en = st.text_input(
                "英文文案质检（步骤4b·视觉）",
                value=os.getenv("GMI_COPY_REVIEW_ENGLISH_MODEL", "openai/gpt-5.4"),
            )
            cr_fr = st.text_input(
                "法文文案质检（步骤4b·视觉）",
                value=os.getenv("GMI_COPY_REVIEW_FRENCH_MODEL", "anthropic/claude-sonnet-4.6"),
            )
            lg_en = st.text_input(
                "英文语法质检（步骤4c）",
                value=os.getenv("GMI_LOCALE_GRAMMAR_ENGLISH_MODEL", "openai/gpt-5.4-nano"),
            )
            lg_fr = st.text_input(
                "法文语法质检（步骤4c）",
                value=os.getenv("GMI_LOCALE_GRAMMAR_FRENCH_MODEL", "openai/gpt-5.4-nano"),
            )

    st.subheader("1. 上传图片")
    image_file = st.file_uploader("商品图（PNG / JPG）", type=["png", "jpg", "jpeg", "webp"])

    st.subheader("2. 文本框标注（MTWI txt 格式）")
    st.markdown(
        """
每行一条：**8 个顶点坐标 + 逗号 + 框内文本**（与 `txt_train` 一致）：

`X1,Y1,X2,Y2,X3,Y3,X4,Y4,文本`

示例：`20,20,320,20,320,72,20,72,促销水印`
"""
    )
    default_txt = "20,20,320,20,320,72,20,72,DEMO_OVERLAY_TEXT\n"
    ann_text = st.text_area("标注内容（可直接粘贴 .txt 全文）", value=default_txt, height=160)
    ann_upload = st.file_uploader("或上传 .txt", type=["txt"])
    if ann_upload is not None:
        ann_text = ann_upload.read().decode("utf-8", errors="replace")

    st.subheader("3. 用户要求（可选）")
    st.markdown(
        "用自然语言写你对**整单任务**的期望；会**同时**传给文案侧与图像侧（与下方专项叠加）。"
        "勿粘贴 API Key；可与环境变量 `GMI_USER_*` 叠加。"
    )
    user_requirements = st.text_area(
        "用户要求（总述）",
        value="",
        height=120,
        key="user_requirements_general",
        help="合并进 --user-copy-instructions 与 --user-image-instructions；适合写整体语气、渠道、禁忌等。",
        placeholder="例如：面向加拿大电商；标题简短；图像偏干净白底、少装饰。",
    )
    with st.expander("文案 / 图像 专项（可选，与总述叠加）", expanded=False):
        user_copy_instr = st.text_area(
            "文案：风格与约束",
            value="",
            height=100,
            help="仅文案：英法生成与文案质检。在总述之后追加。",
            placeholder="例如：要点式描述、标题不超过 80 字符；避免无法核实的绝对化用语。",
        )
        user_image_instr = st.text_area(
            "图像：整体风格",
            value="",
            height=100,
            help="仅图像：去字、harmonize、restore、扩展图。在总述之后追加。",
            placeholder="例如：纯白电商底、柔和顶光、少阴影。",
        )

    run_clicked = st.button("开始处理", type="primary", use_container_width=True)

    if run_clicked:
        if image_file is None:
            st.error("请先上传图片。")
            return
        ok, msg = validate_annotation_text(ann_text)
        if not ok:
            st.error(msg)
            return
        if not mock and not (os.environ.get("GMI_API_KEY") or "").strip():
            st.error("非 Mock 模式需要配置 GMI_API_KEY（环境变量或侧边栏）。")
            return
        st.info(msg)

        job_dir = REPO_ROOT / "outputs" / "streamlit_runs" / f"job_{uuid.uuid4().hex[:16]}"
        inp = job_dir / "inputs"
        ann_dir = job_dir / "annotations"
        for d in (inp, ann_dir, job_dir / "images", job_dir / "deliverables"):
            d.mkdir(parents=True, exist_ok=True)

        suffix = Path(image_file.name).suffix.lower() or INPUT_EXT
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = INPUT_EXT
        image_path = inp / f"{INPUT_BASENAME}{suffix}"
        image_path.write_bytes(image_file.getvalue())

        ann_path = ann_dir / f"{image_path.name}.txt"
        ann_path.write_text(ann_text.strip() + "\n", encoding="utf-8")

        argv = [
            "--input-image",
            str(image_path),
            "--txt-dir",
            str(ann_dir),
            "--output",
            str(job_dir / "mtwi_ecommerce_samples.yaml"),
            "--image-output-dir",
            str(job_dir / "images"),
            "--deliverable-dir",
            str(job_dir / "deliverables"),
            "--export-deliverables",
            "--limit",
            "1",
            "--erase-strategy",
            "local",
            "--quality-strategy",
            "local",
            "--mask-mode",
            mask_mode,
            "--vision-model",
            vision_model.strip(),
            "--english-copy-model",
            english_copy_model.strip(),
            "--french-copy-model",
            french_copy_model.strip(),
            "--fallback-english-copy-model",
            fb_en.strip(),
            "--fallback-french-copy-model",
            fb_fr.strip(),
            "--copy-review-english-model",
            cr_en.strip(),
            "--copy-review-french-model",
            cr_fr.strip(),
            "--locale-grammar-english-model",
            lg_en.strip(),
            "--locale-grammar-french-model",
            lg_fr.strip(),
            "--max-attempts",
            "2",
            "--stability-update-every",
            "1",
            "--stability-report-path",
            str(job_dir / "stability_baseline.json"),
            "--stability-markdown-path",
            str(job_dir / "stability_baseline.md"),
        ]
        if disable_restore:
            argv.append("--disable-restore")
        if harmonize:
            argv.append("--harmonize-after-erase")
        else:
            argv.append("--no-harmonize-after-erase")
        if gen_extra:
            argv.extend(
                [
                    "--generate-additional-images",
                    "--additional-image-model",
                    os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
                    "--additional-image-count",
                    "3",
                ]
            )
        if mock:
            argv.append("--mock")

        def _merge_instructions(general: str, specific: str) -> str:
            parts = [p.strip() for p in (general, specific) if p and p.strip()]
            return "\n\n".join(parts)

        copy_merged = _merge_instructions(user_requirements, user_copy_instr)
        image_merged = _merge_instructions(user_requirements, user_image_instr)
        if copy_merged:
            argv.extend(["--user-copy-instructions", copy_merged])
        if image_merged:
            argv.extend(["--user-image-instructions", image_merged])

        with st.spinner("处理中，请稍候…"):
            try:
                args = parse_args(argv)
                run_pipeline(args)
            except Exception as e:
                st.exception(e)
                return

        st.success("处理完成。")
        st.session_state["last_job_dir"] = str(job_dir)

    job_key = st.session_state.get("last_job_dir")
    if job_key:
        jd = Path(job_key)
        if jd.is_dir():
            st.divider()
            st.subheader("4. 结果预览")
            render_result_preview(jd)

            st.subheader("5. 下载")
            zbytes = build_zip(jd)
            st.download_button(
                label="下载本次运行目录（ZIP：deliverables、YAML、过程图等）",
                data=zbytes,
                file_name=f"{jd.name}.zip",
                mime="application/zip",
                use_container_width=True,
            )
            with st.expander("本 job 输出文件列表"):
                for path in sorted(jd.rglob("*")):
                    if path.is_file():
                        st.caption(str(path.relative_to(jd)))


if __name__ == "__main__":
    main()
