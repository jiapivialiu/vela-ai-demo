"""Minimal Streamlit UI for the MTWI ecommerce pipeline.

Run from repository root (see root README.md for venv, API key, and `streamlit run streamlit_app.py`).

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
    st.caption("上传商品图 + 按 MTWI 格式的坐标文本，一键跑通去字 / 理解与英法文案 / 可选扩展图。")

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

        st.subheader("模型（可改）")
        vision_model = st.text_input("视觉模型", value=os.getenv("GMI_VISION_MODEL", "openai/gpt-4o"))
        qwen_model = st.text_input("文案主模型", value=os.getenv("GMI_QWEN_MODEL", "Qwen/Qwen3.5-27B"))
        fallback_model = st.text_input("文案回退模型", value=os.getenv("GMI_FALLBACK_TEXT_MODEL", "openai/gpt-4o-mini"))

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
            "--qwen-model",
            qwen_model.strip(),
            "--fallback-text-model",
            fallback_model.strip(),
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
            st.subheader("3. 结果预览")
            render_result_preview(jd)

            st.subheader("4. 下载")
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
