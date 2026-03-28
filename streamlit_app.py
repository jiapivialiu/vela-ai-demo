"""Streamlit UI: upload MTWI image + annotations, run ecommerce pipeline, preview deliverables.

Flow (see ``main()``):

1. User uploads image; MTWI ``.txt`` (or pasted lines) is optional via checkbox; optional sidebar models / Mock.
2. ``_write_run_inputs`` writes ``<run_dir>/image_train/streamlit_item.<ext>`` and optionally
   ``txt_train/streamlit_item.<ext>.txt`` (matches ``--input-image`` companion lookup in the pipeline).
   Without a label file, the run is image-only (no MTWI quads).
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


def _write_run_inputs(
    image_bytes: bytes,
    image_suffix: str,
    run_dir: Path,
    *,
    annotation_text: str,
    include_annotation: bool,
) -> Path:
    """Save image under image_train; optional MTWI txt as ``<same_filename>.txt`` for ``--input-image`` pairing."""
    img_dir = run_dir / "image_train"
    txt_dir = run_dir / "txt_train"
    img_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    stem = "streamlit_item"
    img_path = img_dir / f"{stem}{image_suffix}"
    img_path.write_bytes(image_bytes)
    if include_annotation:
        ann_path = txt_dir / f"{img_path.name}.txt"
        ann_path.write_text(annotation_text.strip() + "\n", encoding="utf-8")
    return img_path


def _build_streamlit_pipeline_argv(
    run_dir: Path,
    input_image: Path,
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
    """CLI tokens for ``parse_args`` only — first element must be ``--...``, not a program name."""
    img_out = run_dir / "mtwi_images"
    yaml_out = run_dir / "artifacts.yaml"
    deliv = run_dir / "deliverables"
    argv: list[str] = [
        "--input-image",
        str(input_image.resolve()),
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
    st.set_page_config(page_title="Vela AI Demo — MTWI 链路", layout="wide")
    st.title("MTWI 商品图 → 去字 / 文案 / 交付包")
    st.caption(
        "中文标注输入；用户可见文案为加拿大英语 / 法语。侧栏可切换 Mock、模型；**额外营销图**见下方 **第 4 步**（默认不生成；亦可跑完后用 `src/run_marketing_extras_step.py` 补图）。"
    )

    with st.sidebar:
        st.header("运行模式")
        mock = st.checkbox("Mock 模式（不调 GMI）", value=False)
        api_key = st.text_input("GMI API Key（可选，覆盖环境变量）", type="password", value="")
        st.header("图像与去字")
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
        erase_strategy = st.selectbox(
            "去字方式",
            ["model", "local"],
            index=0,
            help="model = 侧栏「RQ 去字模型」的 Request Queue（默认）；local = 本地白底+inpaint，不调去字 RQ。",
        )
        copy_understand_image = st.selectbox(
            "文案理解用图（Step3，在生成英法描述前）",
            ["final", "extra1", "source"],
            index=0,
            help="final=修复后主图（默认，避开水印原图）；extra1=先出第1张营销图并经与原图一致性审核后再理解；source=原始上架图。",
        )
        additional_image_model = st.text_input(
            "RQ 去字模型（erase=model 时）",
            value=os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
            help="与主表单「第 4 步」开启额外营销图时使用的扩展图模型相同（`--additional-image-model` / `--eraser-model`）。",
        )
        st.header("文案")
        copy_mode = st.selectbox("Step4 模式", ["unified", "split"], index=0)
        skip_listing_review = st.checkbox(
            "跳过 Step4b/4c 质检（仅生成英法描述与参数）",
            value=False,
            help="不调 copy review / locale grammar；交付包不含 copy_review.md 与 locale_grammar_review.md。",
        )
        max_attempts = st.number_input(
            "每步最大重试",
            min_value=1,
            max_value=2,
            value=2,
            help="与 CLI 一致：`mtwi_ecommerce_pipeline` 会将该值限制在 1–2。",
        )

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

    st.subheader("1. 商品主图")
    up_img = st.file_uploader("上传图片（PNG / JPG / WebP）", type=["png", "jpg", "jpeg", "webp"])
    st.subheader("2. 文本框标注（可选）")
    provide_annotation = st.checkbox(
        "提供 MTWI 文本框标注（四边形坐标 + 文本）",
        value=True,
        help="关闭后仅上传主图运行（无坐标蒙版；去字依赖模型按提示整图处理，流水线会记录 annotation_missing）。",
    )
    up_txt = None
    ann_text = ""
    if provide_annotation:
        up_txt = st.file_uploader("或上传 .txt", type=["txt"])
        default_txt = ""
        demo_txt_path = REPO_ROOT / "data/demo_one/txt_train/demo_item.txt"
        if demo_txt_path.is_file():
            default_txt = demo_txt_path.read_text(encoding="utf-8")
        ann_text = st.text_area("标注内容", value=default_txt, height=220, placeholder="粘贴 MTWI 格式多行标注…")
    st.subheader("3. 用户要求（可选）")
    user_copy = st.text_area("对 listing 文案的要求", height=80, placeholder="语气、长度、受众…")
    user_image = st.text_area("对图像处理 / 扩展图的要求", height=80, placeholder="背景、光线…")

    st.subheader("4. 额外营销图（可选）")
    st.caption(
        "默认不生成。开启后会在本轮流水线内调用 Request Queue 产出 `product_image_extra_*.png`（耗时与费用更高）。"
        " 若只需主图与文案，可保持关闭，完成后在终端运行 `python src/run_marketing_extras_step.py`（见 src/README.md）。"
    )
    generate_extra_marketing = st.checkbox(
        "在本轮运行中生成额外营销图",
        value=False,
        help="等价于 CLI：`--generate-additional-images` + `--additional-image-count`。",
    )
    extra_image_count = 0
    if generate_extra_marketing:
        extra_image_count = st.number_input(
            "额外营销图张数",
            min_value=1,
            max_value=6,
            value=3,
            step=1,
            help="写入交付目录的 `product_image_extra_1.png` … 序号连续。",
        )

    processing = _PIPELINE_ASYNC_KEY in st.session_state

    if processing:
        st.warning("**正在处理中** — 下方显示实时步骤。请勿关闭页面，完成后会自动刷新。")

    start_clicked = st.button("开始处理", type="primary", disabled=processing)

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
            text_body = ""
            can_run = True
            if provide_annotation:
                text_body = ann_text.strip()
                if up_txt is not None:
                    text_body = up_txt.getvalue().decode("utf-8", errors="replace").strip()
                if not text_body:
                    st.error("已开启「提供 MTWI 标注」：请填写或上传标注内容。")
                    can_run = False
            if can_run:
                run_id = str(uuid.uuid4())[:12]
                run_dir = REPO_ROOT / "outputs" / "streamlit_runs" / run_id
                try:
                    img_path = _write_run_inputs(
                        up_img.getvalue(),
                        suf,
                        run_dir,
                        annotation_text=text_body if provide_annotation else "",
                        include_annotation=provide_annotation,
                    )
                except OSError as exc:
                    st.error(f"写入临时文件失败: {exc}")
                else:
                    prev_key = os.environ.get("GMI_API_KEY")
                    argv = _build_streamlit_pipeline_argv(
                        run_dir,
                        img_path,
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
