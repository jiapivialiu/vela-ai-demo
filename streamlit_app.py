"""Streamlit UI: upload MTWI image + annotations, run ecommerce pipeline, preview deliverables.

See README.md for venv / API key. Outputs go under outputs/streamlit_runs/<run_id>/.
"""

from __future__ import annotations

import io
import os
import sys
import traceback
import uuid
import zipfile
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mtwi_ecommerce_pipeline import parse_args, run_pipeline  # noqa: E402

_PIPELINE_JOB_KEY = "_streamlit_pipeline_job"
_PIPELINE_RESULT_KEY = "_streamlit_pipeline_result"


def _write_run_inputs(image_bytes: bytes, image_suffix: str, annotation_text: str, run_dir: Path) -> None:
    """Save image + MTWI txt with matching stems (directory-scan mode)."""
    img_dir = run_dir / "image_train"
    txt_dir = run_dir / "txt_train"
    img_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    stem = "streamlit_item"
    img_path = img_dir / f"{stem}{image_suffix}"
    img_path.write_bytes(image_bytes)
    (txt_dir / f"{stem}.txt").write_text(annotation_text.strip() + "\n", encoding="utf-8")


def _build_argv(
    run_dir: Path,
    mock: bool,
    extra_count: int,
    mask_mode: str,
    harmonize: bool,
    disable_restore: bool,
    copy_mode: str,
    user_copy: str,
    user_image: str,
    vision_model: str,
    unified_model: str,
    english_model: str,
    french_model: str,
    fb_en: str,
    fb_fr: str,
    simple_model: str,
    no_simple_recovery: bool,
    cr_en: str,
    cr_fr: str,
    lg_en: str,
    lg_fr: str,
    additional_model: str,
    max_attempts: int,
    annotation_audit: bool,
    annotation_audit_model: str,
    erase_strategy: str,
    copy_understand_image: str,
    skip_listing_review: bool = False,
) -> list[str]:
    img_out = run_dir / "mtwi_images"
    yaml_out = run_dir / "artifacts.yaml"
    deliv = run_dir / "deliverables"
    argv = [
        "mtwi_ecommerce_pipeline",
        "--txt-dir",
        str(run_dir / "txt_train"),
        "--image-dir",
        str(run_dir / "image_train"),
        "--limit",
        "1",
        "--image-output-dir",
        str(img_out),
        "--output",
        str(yaml_out),
        "--export-deliverables",
        "--deliverable-dir",
        str(deliv),
        "--mask-mode",
        mask_mode,
        "--erase-strategy",
        erase_strategy,
        "--copy-generation-mode",
        copy_mode,
        "--vision-model",
        vision_model,
        "--copy-understand-image",
        copy_understand_image,
        "--unified-copy-model",
        unified_model,
        "--english-copy-model",
        english_model,
        "--french-copy-model",
        french_model,
        "--fallback-english-copy-model",
        fb_en,
        "--fallback-french-copy-model",
        fb_fr,
        "--simple-copy-model",
        simple_model,
        "--copy-review-english-model",
        cr_en,
        "--copy-review-french-model",
        cr_fr,
        "--locale-grammar-english-model",
        lg_en,
        "--locale-grammar-french-model",
        lg_fr,
        "--additional-image-model",
        additional_model,
        "--max-attempts",
        str(max_attempts),
    ]
    if erase_strategy == "model":
        argv.extend(["--eraser-model", additional_model])
    if mock:
        argv.append("--mock")
    if extra_count <= 0:
        argv.append("--no-generate-additional-images")
    else:
        argv.extend(["--additional-image-count", str(int(extra_count))])
    if not harmonize:
        argv.append("--no-harmonize-after-erase")
    if disable_restore:
        argv.append("--disable-restore")
    if no_simple_recovery:
        argv.append("--no-simple-copy-recovery")
    if user_copy.strip():
        argv.extend(["--user-copy-instructions", user_copy.strip()[:4000]])
    if user_image.strip():
        argv.extend(["--user-image-instructions", user_image.strip()[:2500]])
    if not annotation_audit:
        argv.append("--no-annotation-audit")
    if annotation_audit_model.strip():
        argv.extend(["--annotation-audit-model", annotation_audit_model.strip()])
    if skip_listing_review:
        argv.append("--skip-listing-review")
    return argv


def _restore_gmi_key_after_run(prev_key: str | None, session_api_key: str) -> None:
    if prev_key is not None:
        os.environ["GMI_API_KEY"] = prev_key
    elif not session_api_key.strip():
        os.environ.pop("GMI_API_KEY", None)


def _execute_pipeline_job(job: dict) -> None:
    """Run parse_args + run_pipeline; set ``_pipeline_result`` for next frame."""
    argv = job["argv"]
    run_dir = Path(job["run_dir"])
    prev_key = job.get("prev_key")
    session_api_key = str(job.get("session_api_key") or "")
    mock = bool(job.get("mock"))
    try:
        if session_api_key.strip():
            os.environ["GMI_API_KEY"] = session_api_key.strip()
        elif mock:
            os.environ.pop("GMI_API_KEY", None)
        args = parse_args(argv)
        run_pipeline(args)
        st.session_state[_PIPELINE_RESULT_KEY] = {"ok": True, "run_dir": str(run_dir)}
    except Exception:
        st.session_state[_PIPELINE_RESULT_KEY] = {"ok": False, "error": traceback.format_exc()}
    finally:
        _restore_gmi_key_after_run(prev_key, session_api_key)


def _render_deliverables(run_dir: Path) -> None:
    deliv = run_dir / "deliverables"
    subdirs = sorted(p for p in deliv.iterdir() if p.is_dir()) if deliv.is_dir() else []
    if not subdirs:
        st.warning("未找到交付子目录。")
        return
    prod = subdirs[0]
    c1, c2 = st.columns(2)
    main_img = prod / "product_image.png"
    if main_img.is_file():
        c1.image(str(main_img), caption="主图 product_image.png", use_container_width=True)
    extras = sorted(prod.glob("product_image_extra_*.png"))
    if extras:
        c2.markdown("**额外营销图**")
        for p in extras:
            c2.image(str(p), caption=p.name, use_container_width=True)
    en_md = prod / "description_en.md"
    fr_md = prod / "description_fr.md"
    if en_md.is_file():
        with st.expander("加拿大英语 description_en.md", expanded=False):
            st.markdown(en_md.read_text(encoding="utf-8"))
    if fr_md.is_file():
        with st.expander("加拿大法语 description_fr.md", expanded=False):
            st.markdown(fr_md.read_text(encoding="utf-8"))
    man = prod / "manifest.json"
    if man.is_file():
        with st.expander("manifest.json"):
            st.code(man.read_text(encoding="utf-8")[:12000], language="json")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(prod.rglob("*")):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(prod).as_posix())
    buf.seek(0)
    st.download_button(
        "下载本 SKU 交付 ZIP",
        data=buf.getvalue(),
        file_name=f"{prod.name}_deliverable.zip",
        mime="application/zip",
    )


def main() -> None:
    st.set_page_config(page_title="Vela AI Demo — MTWI 链路", layout="wide")
    st.title("MTWI 商品图 → 去字 / 文案 / 交付包")
    st.caption("中文标注输入；用户可见文案为加拿大英语 / 法语。侧栏可切换 Mock、模型与 **额外营销图数量**（默认 3 张）。")

    with st.sidebar:
        st.header("运行模式")
        mock = st.checkbox("Mock 模式（不调 GMI）", value=False)
        api_key = st.text_input("GMI API Key（可选，覆盖环境变量）", type="password", value="")
        st.header("图像与扩展图")
        mask_mode = st.selectbox(
            "Mask 模式（txt 四边形 → 蒙版擦除）",
            ["all", "overlay"],
            index=0,
            help="推荐 all：按标注全部擦除。overlay 依赖在线 VLM/中文启发式筛选；Mock+英文标注曾出现 0 框导致图不变。",
        )
        harmonize = st.checkbox("去字后模型 harmonize（融合擦除边缘）", value=True)
        annotation_audit = st.checkbox(
            "标注审核 Agent（真实模式：先 VLM 判断每框是否可用、是否需擦除）",
            value=True,
            help="Mock 下自动跳过。关闭则仅用 Mask 模式（all/overlay）选框。",
        )
        disable_restore = st.checkbox("跳过 step2 画质修复", value=False)
        extra_image_count = st.number_input(
            "额外营销图数量（0 = 不生成）",
            min_value=0,
            max_value=6,
            value=3,
            step=1,
            help="对应 CLI：`--additional-image-count`；0 时等价于 `--no-generate-additional-images`。",
        )
        erase_strategy = st.selectbox(
            "去字方式",
            ["model", "local"],
            index=0,
            help="model = 与下方扩展图同一套 Request Queue 模型（默认）；local = 本地白底+inpaint，不调去字 RQ。",
        )
        copy_understand_image = st.selectbox(
            "文案理解用图（Step3，在生成英法描述前）",
            ["final", "extra1", "source"],
            index=0,
            help="final=修复后主图（默认，避开水印原图）；extra1=先出第1张营销图并经与原图一致性审核后再理解；source=原始上架图。",
        )
        additional_image_model = st.text_input(
            "扩展图 / 去字模型（model 模式共用）",
            value=os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
        )
        st.header("文案")
        copy_mode = st.selectbox("Step4 模式", ["unified", "split"], index=0)
        skip_listing_review = st.checkbox(
            "跳过 Step4b/4c 质检（仅生成英法描述与参数）",
            value=False,
            help="不调 copy review / locale grammar；交付包不含 copy_review.md 与 locale_grammar_review.md。",
        )
        max_attempts = st.number_input("每步最大重试", min_value=1, max_value=5, value=2)

        with st.expander("模型 ID（高级）"):
            vision_model = st.text_input("VLM", value=os.getenv("GMI_VISION_MODEL", "Qwen/Qwen3-VL-235B"))
            annotation_audit_model = st.text_input(
                "标注审核 VLM（空 = 与上栏相同）",
                value=os.getenv("GMI_ANNOTATION_AUDIT_MODEL", ""),
            )
            unified_model = st.text_input(
                "Unified copy",
                value=os.getenv("GMI_UNIFIED_COPY_MODEL")
                or os.getenv("GMI_ENGLISH_COPY_MODEL", "openai/gpt-5.4-pro"),
            )
            english_model = st.text_input("English copy", value=os.getenv("GMI_ENGLISH_COPY_MODEL", "openai/gpt-5.4-pro"))
            french_model = st.text_input(
                "French copy", value=os.getenv("GMI_FRENCH_COPY_MODEL", "anthropic/claude-sonnet-4.6")
            )
            fb_en = st.text_input("Fallback EN", value=os.getenv("GMI_FALLBACK_ENGLISH_COPY_MODEL", "openai/gpt-5.4-mini"))
            fb_fr = st.text_input("Fallback FR", value=os.getenv("GMI_FALLBACK_FRENCH_COPY_MODEL", "openai/gpt-5.4-mini"))
            simple_model = st.text_input(
                "Simple recovery",
                value=os.getenv("GMI_SIMPLE_COPY_MODEL")
                or os.getenv("GMI_FALLBACK_ENGLISH_COPY_MODEL", "openai/gpt-5.4-mini"),
            )
            no_simple_recovery = st.checkbox("关闭 simple bilingual recovery", value=False)
            cr_en = st.text_input("Copy review EN", value=os.getenv("GMI_COPY_REVIEW_ENGLISH_MODEL", "openai/gpt-5.4"))
            cr_fr = st.text_input(
                "Copy review FR", value=os.getenv("GMI_COPY_REVIEW_FRENCH_MODEL", "anthropic/claude-sonnet-4.6")
            )
            lg_en = st.text_input(
                "Locale grammar EN", value=os.getenv("GMI_LOCALE_GRAMMAR_ENGLISH_MODEL", "openai/gpt-5.4-nano")
            )
            lg_fr = st.text_input(
                "Locale grammar FR", value=os.getenv("GMI_LOCALE_GRAMMAR_FRENCH_MODEL", "openai/gpt-5.4-nano")
            )

    if _PIPELINE_RESULT_KEY in st.session_state:
        pr = st.session_state.pop(_PIPELINE_RESULT_KEY)
        if pr.get("ok") and pr.get("run_dir"):
            st.success(f"完成。运行目录：`{pr['run_dir']}`")
            _render_deliverables(Path(pr["run_dir"]))
        else:
            st.error(pr.get("error") or "流水线执行失败。")

    st.subheader("1. 商品主图")
    up_img = st.file_uploader("上传图片（PNG / JPG / WebP）", type=["png", "jpg", "jpeg", "webp"])
    st.subheader("2. 文本框标注（MTWI 每行：x1,y1,…,x4,y4,文本）")
    up_txt = st.file_uploader("或上传 .txt", type=["txt"])
    default_txt = ""
    demo_txt_path = REPO_ROOT / "data/demo_one/txt_train/demo_item.txt"
    if demo_txt_path.is_file():
        default_txt = demo_txt_path.read_text(encoding="utf-8")
    ann_text = st.text_area("标注内容", value=default_txt, height=220, placeholder="粘贴 MTWI 格式多行标注…")
    st.subheader("3. 用户要求（可选）")
    user_copy = st.text_area("对 listing 文案的要求", height=80, placeholder="语气、长度、受众…")
    user_image = st.text_area("对图像处理 / 扩展图的要求", height=80, placeholder="背景、光线…")

    processing = _PIPELINE_JOB_KEY in st.session_state

    if processing:
        st.warning("**正在处理中** — 「开始处理」已禁用；请勿关闭页面，完成后会自动刷新并展示结果。")

    start_clicked = st.button(
        "开始处理",
        type="primary",
        disabled=processing,
        help="运行期间按钮会禁用，并显示「正在处理中」提示。",
    )

    if processing:
        job = st.session_state.pop(_PIPELINE_JOB_KEY)
        with st.spinner("运行流水线：去字 → 理解与英法文案 → 质检 → 扩展图 → 交付包 …"):
            _execute_pipeline_job(job)
        st.rerun()

    if start_clicked:
        if not mock and not (api_key.strip() or os.getenv("GMI_API_KEY", "").strip()):
            st.error("真实模式需要 GMI API Key：在侧栏填写或终端 `export GMI_API_KEY=...`，或勾选 Mock。")
        elif not up_img:
            st.error("请先上传商品主图。")
        else:
            raw_name = up_img.name or "upload.jpg"
            suf = Path(raw_name).suffix.lower()
            if suf not in {".png", ".jpg", ".jpeg", ".webp"}:
                suf = ".jpg"
            text_body = ann_text.strip()
            if up_txt is not None:
                text_body = up_txt.getvalue().decode("utf-8", errors="replace").strip()
            if not text_body:
                st.error("请填写或上传 MTWI 标注。")
            else:
                run_id = str(uuid.uuid4())[:12]
                run_dir = REPO_ROOT / "outputs" / "streamlit_runs" / run_id
                try:
                    _write_run_inputs(up_img.getvalue(), suf, text_body, run_dir)
                except OSError as exc:
                    st.error(f"写入临时文件失败: {exc}")
                else:
                    prev_key = os.environ.get("GMI_API_KEY")
                    argv = _build_argv(
                        run_dir,
                        mock=mock,
                        extra_count=int(extra_image_count),
                        mask_mode=mask_mode,
                        harmonize=harmonize,
                        disable_restore=disable_restore,
                        copy_mode=copy_mode,
                        user_copy=user_copy,
                        user_image=user_image,
                        vision_model=vision_model,
                        unified_model=unified_model,
                        english_model=english_model,
                        french_model=french_model,
                        fb_en=fb_en,
                        fb_fr=fb_fr,
                        simple_model=simple_model,
                        no_simple_recovery=no_simple_recovery,
                        cr_en=cr_en,
                        cr_fr=cr_fr,
                        lg_en=lg_en,
                        lg_fr=lg_fr,
                        additional_model=additional_image_model,
                        max_attempts=int(max_attempts),
                        annotation_audit=annotation_audit,
                        annotation_audit_model=annotation_audit_model,
                        erase_strategy=erase_strategy,
                        copy_understand_image=copy_understand_image,
                        skip_listing_review=skip_listing_review,
                    )
                    st.session_state[_PIPELINE_JOB_KEY] = {
                        "argv": argv,
                        "run_dir": str(run_dir),
                        "prev_key": prev_key,
                        "session_api_key": api_key.strip(),
                        "mock": mock,
                    }
                    st.rerun()


if __name__ == "__main__":
    main()
