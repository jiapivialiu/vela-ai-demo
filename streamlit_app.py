"""Streamlit UI: upload MTWI image + annotations, run ecommerce pipeline, preview deliverables.

Flow (see ``main()``):

1. User uploads image; optional MTWI ``.txt`` (or pasted lines) when annotation mode is enabled; optional sidebar models / Mock.
2. ``_write_run_inputs`` writes ``<run_dir>/image_train/streamlit_item.<ext>`` and
   ``txt_train/streamlit_item.txt`` so ``collect_input_items`` directory-scan finds one SKU.
3. ``_build_streamlit_pipeline_argv`` returns **option-only** tokens for
   ``mtwi_ecommerce_pipeline.parse_args`` — **no** synthetic ``argv[0]`` program name.
   (``argparse.parse_args(list)`` parses the whole list as flags; a leading
   ``"mtwi_ecommerce_pipeline"`` token causes *unrecognized arguments* / ``SystemExit(2)``.)
4. ``_run_pipeline_in_thread`` sets ``GMI_API_KEY`` (if provided), appends
   ``--pipeline-progress-file``, then ``parse_args`` + ``run_pipeline``.
5. After the form and **开始处理** button, the UI renders **运行进度（实时）** near the bottom
   (polls ``pipeline_progress.jsonl`` until ``holder["done"]``), then on the next rerun **运行结果**
   (success / errors / deliverables) **below** the form and progress — not above the inputs.

Outputs: ``outputs/streamlit_runs/<run_id>/`` (images, yaml, deliverables, JSONL progress).

See README.md for venv / API key.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
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
from pipeline_progress import format_progress_lines_for_ui, read_progress_tail  # noqa: E402

_PIPELINE_ASYNC_KEY = "_streamlit_pipeline_async"
_PIPELINE_RESULT_KEY = "_streamlit_pipeline_result"


def _write_run_inputs(image_bytes: bytes, image_suffix: str, annotation_text: str | None, run_dir: Path) -> Path:
    """Save uploaded image and optional MTWI txt; return saved image path."""
    img_dir = run_dir / "image_train"
    img_dir.mkdir(parents=True, exist_ok=True)
    stem = "streamlit_item"
    img_path = img_dir / f"{stem}{image_suffix}"
    img_path.write_bytes(image_bytes)
    cleaned_annotation = (annotation_text or "").strip()
    if cleaned_annotation:
        txt_dir = run_dir / "txt_train"
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / f"{stem}.txt").write_text(cleaned_annotation + "\n", encoding="utf-8")
    return img_path


def _build_streamlit_pipeline_argv(
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
    input_image_path: str | None = None,
    skip_listing_review: bool = False,
) -> list[str]:
    """CLI tokens for ``parse_args`` only — first element must be ``--...``, not a program name."""
    img_out = run_dir / "mtwi_images"
    yaml_out = run_dir / "artifacts.yaml"
    deliv = run_dir / "deliverables"
    argv: list[str] = [
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
    if input_image_path:
        argv.extend(["--input-image", input_image_path])
    if mock:
        argv.append("--mock")
    if extra_count <= 0:
        argv.append("--no-generate-additional-images")
    else:
        argv.append("--generate-additional-images")
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


def _run_pipeline_in_thread(holder: dict, job: dict) -> None:
    """Background worker: writes JSONL to run_dir/pipeline_progress.jsonl; sets holder when done."""
    argv = list(job["argv"])
    run_dir = Path(job["run_dir"])
    progress_file = run_dir / "pipeline_progress.jsonl"
    if "--pipeline-progress-file" not in argv:
        argv.extend(["--pipeline-progress-file", str(progress_file)])
    prev_key = job.get("prev_key")
    session_api_key = str(job.get("session_api_key") or "")
    mock = bool(job.get("mock"))
    holder["run_dir"] = str(run_dir)
    try:
        if session_api_key.strip():
            os.environ["GMI_API_KEY"] = session_api_key.strip()
        elif mock:
            os.environ.pop("GMI_API_KEY", None)
        os.environ["GMI_PIPELINE_PROGRESS_FILE"] = str(progress_file)
        args = parse_args(argv)
        run_pipeline(args)
        holder["ok"] = True
        holder["error"] = None
    except SystemExit as exc:
        # argparse uses SystemExit; it is not a subclass of Exception.
        holder["ok"] = False
        holder["error"] = f"parse_args 退出: code={getattr(exc, 'code', exc)!r}\n{traceback.format_exc()}"
    except BaseException as exc:
        holder["ok"] = False
        tb = traceback.format_exc()
        holder["error"] = tb if tb.strip() else f"{type(exc).__name__}: {exc}"
    finally:
        if not holder.get("error") and holder.get("ok") is not True:
            holder["ok"] = False
            holder["error"] = (
                "流水线异常结束但未记录详情（请查看运行该 Streamlit 的终端 stderr，"
                f"或 {run_dir / 'streamlit_pipeline_error.txt'}）。"
            )
        if holder.get("ok") is not True and (holder.get("error") or "").strip():
            err_path = run_dir / "streamlit_pipeline_error.txt"
            try:
                err_path.write_text(str(holder.get("error") or ""), encoding="utf-8")
            except OSError:
                pass
        _restore_gmi_key_after_run(prev_key, session_api_key)
        holder["done"] = True


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
    st.set_page_config(page_title="Vela AI — 电商出海素材生成", layout="wide")
    st.title("Vela AI 电商出海助手")
    st.caption(
        "上传中文商品图，自动去除文字水印、生成加拿大英语 / 法语商品描述与参数，一键产出跨境电商上架素材包。"
        " 侧栏可切换运行模式与模型配置。"
    )

    with st.sidebar:
        mock = st.checkbox("Mock 模式（离线测试，不调用 API）", value=False)
        api_key = st.text_input("GMI API Key", type="password", value="")

        with st.expander("高级设置", expanded=False):
            st.markdown("**图像处理**")
            mask_mode = st.selectbox(
                "蒙版模式",
                ["all", "overlay"],
                index=0,
                help="all：擦除所有标注框内文字（推荐）。overlay：仅擦除水印 / 促销文字，保留商品自身印刷文字。",
            )
            harmonize = st.checkbox("去字后融合修复", value=True, help="对擦除区域做模型级边缘融合，减少痕迹。")
            annotation_audit = st.checkbox(
                "标注审核",
                value=True,
                help="真实模式下先用 VLM 判断每个标注框是否有效、是否需要擦除。Mock 下自动跳过。",
            )
            disable_restore = st.checkbox("跳过画质修复", value=False)
            erase_strategy = st.selectbox(
                "去字方式",
                ["model", "local"],
                index=0,
                help="model：云端模型去字（推荐）。local：本地 OpenCV 去字，不消耗 API 额度。",
            )
            copy_understand_image = st.selectbox(
                "文案理解用图",
                ["final", "extra1", "source"],
                index=0,
                help="final：修复后主图（推荐）。source：原始上架图。extra1：先生成第 1 张营销图再理解。",
            )
            additional_image_model = st.text_input(
                "图像模型 ID",
                value=os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
                help="用于去字与营销图生成的 Request Queue 模型。",
            )

            st.markdown("**文案生成**")
            copy_mode = st.selectbox("生成模式", ["unified", "split"], index=0, help="unified：单次生成双语文案（推荐）。split：分步生成。")
            skip_listing_review = st.checkbox(
                "跳过文案质检",
                value=False,
                help="跳过 copy review 与 locale grammar 检查，交付包中不含质检报告。",
            )
            max_attempts = st.number_input("每步最大重试", min_value=1, max_value=2, value=2)

            st.markdown("**模型 ID**")
            vision_model = st.text_input("VLM", value=os.getenv("GMI_VISION_MODEL", "Qwen/Qwen3-VL-235B"))
            annotation_audit_model = st.text_input(
                "标注审核 VLM（空 = 同上）",
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
            no_simple_recovery = st.checkbox("关闭 simple recovery", value=False)
            cr_en = st.text_input(
                "Copy review EN",
                value=os.getenv("GMI_COPY_REVIEW_ENGLISH_MODEL", "anthropic/claude-sonnet-4.6"),
            )
            cr_fr = st.text_input(
                "Copy review FR", value=os.getenv("GMI_COPY_REVIEW_FRENCH_MODEL", "anthropic/claude-sonnet-4.6")
            )
            lg_en = st.text_input(
                "Locale grammar EN", value=os.getenv("GMI_LOCALE_GRAMMAR_ENGLISH_MODEL", "openai/gpt-5.4-nano")
            )
            lg_fr = st.text_input(
                "Locale grammar FR", value=os.getenv("GMI_LOCALE_GRAMMAR_FRENCH_MODEL", "openai/gpt-5.4-nano")
            )

    st.subheader("上传商品图")
    up_img = st.file_uploader("选择图片文件（PNG / JPG / WebP）", type=["png", "jpg", "jpeg", "webp"])

    st.divider()
    st.subheader("可选设置")

    use_mtwi_annotations = st.checkbox(
        "提供文字区域标注（可提升去字精度）",
        value=False,
        help="开启后可上传或粘贴 MTWI 格式的文字框坐标，帮助模型更精准地定位并擦除图上文字。关闭则以 image-only 模式运行。",
    )
    up_txt = None
    ann_text = ""
    if use_mtwi_annotations:
        up_txt = st.file_uploader("上传标注文件（.txt）", type=["txt"])
        ann_text = st.text_area(
            "或直接粘贴标注内容",
            height=150,
            placeholder="每行一个文字框，格式：x1,y1,x2,y2,x3,y3,x4,y4,文本\n例如：100,200,400,200,400,250,100,250,限时特价",
        )

    use_custom_instructions = st.checkbox(
        "自定义生成要求",
        value=False,
        help="对文案风格或图像处理提出额外要求，例如语气、受众、背景风格等。",
    )
    user_copy = ""
    user_image = ""
    if use_custom_instructions:
        user_copy = st.text_area("对商品描述文案的要求", height=80, placeholder="例如：语气活泼、面向年轻女性、突出性价比…")
        user_image = st.text_area("对图像处理的要求", height=80, placeholder="例如：保持白色背景、提亮光线…")

    generate_extra_marketing = st.checkbox(
        "生成额外营销图",
        value=False,
        help="额外产出多角度 / 场景化商品图（耗时与费用更高）。关闭时仅输出主图与文案。",
    )
    extra_image_count = 0
    if generate_extra_marketing:
        extra_image_count = st.number_input(
            "营销图张数",
            min_value=1,
            max_value=6,
            value=3,
            step=1,
        )

    st.divider()

    processing = _PIPELINE_ASYNC_KEY in st.session_state
    has_image = up_img is not None

    if processing:
        st.warning("**正在处理中** — 下方显示实时步骤。请勿关闭页面，完成后会自动刷新。")

    start_clicked = st.button(
        "开始处理",
        type="primary",
        disabled=(not has_image or processing),
        help="请先上传商品图" if not has_image else None,
    )

    if start_clicked:
        if not mock and not (api_key.strip() or os.getenv("GMI_API_KEY", "").strip()):
            st.error("真实模式需要 GMI API Key：在侧栏填写或终端 `export GMI_API_KEY=...`，或勾选 Mock。")
        else:
            raw_name = up_img.name or "upload.jpg"
            suf = Path(raw_name).suffix.lower()
            if suf not in {".png", ".jpg", ".jpeg", ".webp"}:
                suf = ".jpg"
            text_body = ""
            validation_error = False
            if use_mtwi_annotations:
                text_body = ann_text.strip()
                if up_txt is not None:
                    text_body = up_txt.getvalue().decode("utf-8", errors="replace").strip()
                if not text_body:
                    st.error("已开启文字区域标注，但内容为空。请粘贴标注内容或上传 .txt 文件。")
                    validation_error = True
            if not validation_error:
                run_id = str(uuid.uuid4())[:12]
                run_dir = REPO_ROOT / "outputs" / "streamlit_runs" / run_id
                try:
                    saved_image = _write_run_inputs(
                        up_img.getvalue(),
                        suf,
                        text_body if use_mtwi_annotations else None,
                        run_dir,
                    )
                except OSError as exc:
                    st.error(f"写入临时文件失败: {exc}")
                else:
                    prev_key = os.environ.get("GMI_API_KEY")
                    argv = _build_streamlit_pipeline_argv(
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
                        input_image_path=str(saved_image) if not use_mtwi_annotations else None,
                        skip_listing_review=skip_listing_review,
                    )
                    st.session_state[_PIPELINE_ASYNC_KEY] = {
                        "job": {
                            "argv": argv,
                            "run_dir": str(run_dir),
                            "prev_key": prev_key,
                            "session_api_key": api_key.strip(),
                            "mock": mock,
                        },
                        "started": False,
                    }
                    st.rerun()

    # 进度放在页面最下方，避免运行中时大块日志顶掉「主图 / 标注」输入区。
    async_state = st.session_state.get(_PIPELINE_ASYNC_KEY)
    if async_state is not None:
        if not async_state.get("started"):
            holder: dict = {"done": False, "ok": None, "error": None, "run_dir": None}
            job = async_state["job"]
            thread = threading.Thread(target=_run_pipeline_in_thread, args=(holder, job), daemon=True)
            thread.start()
            async_state["holder"] = holder
            async_state["started"] = True
            async_state["_thread"] = thread
        h = async_state["holder"]
        run_dir_p = Path(async_state["job"]["run_dir"])
        prog_path = run_dir_p / "pipeline_progress.jsonl"
        st.subheader("运行进度（实时）")
        st.caption("每条为一步的开始/结束；含本地时间、调用模型与阶段耗时。详见运行目录下 `pipeline_progress.jsonl`。")
        raw_lines = read_progress_tail(prog_path, max_lines=50)
        display = format_progress_lines_for_ui(raw_lines)
        st.text_area(
            "步骤与模型",
            value=display if display.strip() else ("等待流水线写入进度…" if not h.get("done") else "（无进度记录）"),
            height=300,
            disabled=True,
        )
        if not h.get("done"):
            time.sleep(0.35)
            st.rerun()
        else:
            st.session_state[_PIPELINE_RESULT_KEY] = {
                "ok": bool(h.get("ok")),
                "run_dir": h.get("run_dir") or str(run_dir_p),
                "error": h.get("error"),
            }
            del st.session_state[_PIPELINE_ASYNC_KEY]
            st.rerun()

    # 运行结果放在表单与进度**之后**，避免完成后 rerun 时整块预览出现在页面最上方。
    if _PIPELINE_RESULT_KEY in st.session_state:
        st.divider()
        st.subheader("运行结果")
        pr = st.session_state.pop(_PIPELINE_RESULT_KEY)
        if pr.get("ok") and pr.get("run_dir"):
            st.success(f"完成。运行目录：`{pr['run_dir']}`")
            _render_deliverables(Path(pr["run_dir"]))
        else:
            err = (pr.get("error") or "").strip() or "流水线执行失败（无错误详情）。"
            run_dir_msg = pr.get("run_dir") or ""
            st.error(f"{err[:800]}{'…' if len(err) > 800 else ''}")
            if run_dir_msg:
                st.caption(f"运行目录：`{run_dir_msg}` — 失败时可能含 `streamlit_pipeline_error.txt` 与 `pipeline_progress.jsonl`。")
            if len(err) > 400 or "\n" in err:
                with st.expander("完整错误 / Full traceback", expanded=True):
                    st.code(err, language="text")


if __name__ == "__main__":
    main()
