#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "2.0"
r"""
make_review_pack.py — 生成按发放阶段组织的审查包（T3 独立审查的受控输入）

验收者不得直接读含答案的 TeX 源。两种模式：

  * 视图模式（teaching/materials.json 存在）：生成 teaching/review-pack/：
      stages.json        按发放阶段组织的块清单：student_initial 首轮块 →
                         各延迟块（按 available_after 事件分组，separate_hint
                         / separate_review 渠道）→ teacher 页块
      blocks.md          各阶段块正文（content_ref 全文）与元数据
      pages-<entry>.txt  各视图 PDF 的按页文本（已构建时；pdftotext 抽取）
      manifest.json      清单 + production_digest（scripts/contract.py 的
                         compute_production_digest：materials + 各块正文 +
                         配置/模板/装配顺序）+ 各 PDF 摘要
    production_digest 是 T3 审查记录与 deliver 判定“旧 PASS 是否仍有效”
    的共同依据：受审内容改动后摘要改变，旧验收结论自动失效。

  * 旧项目模式：生成 <project>/.pd/review/（student-pages.txt +
    manifest.json），行为与旧版一致。

注意：pdftotext 的抽取顺序对双栏/图文混排可能不可靠；顺序存疑时验收者
应直接审读渲染页（PDF），本包只提供受控、可追溯的文本视图。

用法: python make_review_pack.py <project> [--json PATH]
退出码: 0=成功, 2=缺少依赖或输入。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

# contract.py 与本文件同目录（scripts/），import 实际由脚本目录
# sys.path[0] 解析。下方 sys.path.insert 指向的 ../../shared 是历史仓库
# 布局残留（本仓库无该目录，插入为空操作），仅保留行为不变，未删除。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..",
                                                 "shared")))
try:
    import contract  # noqa: E402
except ImportError:  # 独立拷贝使用（旧项目模式）时允许缺失
    contract = None

CONTENT_FILES = ("header.tex", "footer.tex", "config.tex",
                 "config-class.tex", "student.tex", "teacher.tex",
                 "config.yaml")
IMG_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}")
MATERIALS_REL = "teaching/materials.json"


def sha256_file(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def content_files(pdir, sec_names):
    """受审内容文件：切片 + 竞赛台账/档案/探索与来源目录。"""
    files = [s for s in sec_names if os.path.exists(os.path.join(pdir, s))]
    for dname in ("maps", "dossiers", "explorations",
                  "problem-source", "solution-source", "knowledge-source"):
        dpath = os.path.join(pdir, dname)
        if os.path.isdir(dpath):
            for root, dirs, names in os.walk(dpath):
                dirs.sort()
                for fn in sorted(names):
                    if dname == "maps" and not fn.endswith(".md"):
                        continue
                    files.append(os.path.relpath(os.path.join(root, fn),
                                                 pdir))
    return files


def content_digest(pdir, sec_names):
    """受审内容摘要：内容源文件 + 模板 + 配置，按名排序拼接。"""
    h = hashlib.sha256()
    for name in sorted(content_files(pdir, sec_names)) + list(CONTENT_FILES):
        d = sha256_file(os.path.join(pdir, name))
        h.update(name.encode())
        h.update((d or "MISSING").encode())
    return h.hexdigest()


def sec_names(pdir):
    main = os.path.join(pdir, "main.tex")
    names = []
    if os.path.exists(main):
        with open(main, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        # 去掉注释，避免把模板中注释掉的 % \input{sec2.tex} 计入
        raw = "\n".join(strip_comment_line(l) for l in raw.splitlines())
        for m in re.finditer(r"\\input\{(sec[^/\\}.]+?)(?:\.tex)?\}", raw):
            names.append(m.group(1) + ".tex")
    if not names:
        names = sorted(f for f in os.listdir(pdir)
                       if re.fullmatch(r"sec.*\.tex", f))
    return names


def strip_comment_line(line):
    r"""去掉行内注释；\% 是转义字符，不作为注释起点。"""
    i = 0
    while True:
        j = line.find("%", i)
        if j < 0:
            return line
        k = j - 1
        while k >= 0 and line[k] == "\\":
            k -= 1
        if (j - 1 - k) % 2 == 1:
            i = j + 1
        else:
            return line[:j]


def pdf_pages(pdir, pdf_name):
    """pdftotext 按页抽取；PDF 缺失返回 None，抽取失败返回 "ERROR"。"""
    pdf = os.path.join(pdir, pdf_name)
    if not os.path.exists(pdf):
        return None
    # -enc UTF-8：部分平台默认编码非 UTF-8 会丢失 CJK 文本，显式指定。
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", pdf, "-"], capture_output=True)
    if r.returncode != 0:
        return "ERROR"
    return r.stdout.decode("utf-8", errors="replace").split("\x0c")


# ---------------------------------------------------------------------------
# 视图模式
# ---------------------------------------------------------------------------

def organize_stages(materials):
    """按发放阶段组织块：student_initial → 各延迟块（按 available_after
    首次出现顺序）→ teacher-only 块。返回 [(阶段名, 说明, [block])]。"""
    blocks = materials.get("blocks", [])
    initial = [b for b in blocks if "student_initial" in b.get("views", [])]
    delayed = [b for b in blocks
               if "student_initial" not in b.get("views", [])
               and "student" in b.get("audience", [])]
    teacher_only = [b for b in blocks
                    if "student" not in b.get("audience", [])]
    rest = [b for b in blocks
            if b not in initial and b not in delayed and b not in teacher_only]
    stages = [("student_initial",
               "首轮发放（available_after=initial 的 L0 块）", initial)]
    events = []
    for b in delayed:
        ev = b.get("available_after", "?")
        if ev not in events:
            events.append(ev)
    for ev in events:
        grp = [b for b in delayed if b.get("available_after") == ev]
        stages.append(("after %s" % ev,
                       "延迟发放（%s；渠道 %s）"
                       % (ev, "/".join(sorted({b.get("delivery_channel", "?")
                                               for b in grp}))), grp))
    if rest:
        stages.append(("unclassified", "未能归类的块（请人工复核）", rest))
    stages.append(("teacher", "教师资料页（audience 仅 teacher）",
                   teacher_only))
    return stages


def make_review_pack_v2(pdir, materials, outdir):
    if contract is None:
        print("[review-pack] 缺少 scripts/contract.py（视图模式需要共享摘要"
              "实现），请从 skills-c-v2 完整目录运行。")
        return 2
    stages = organize_stages(materials)
    secs = sec_names(pdir)

    # 视图 → 入口 PDF（config.yaml views）
    view_pdf = {}
    cfg_path = os.path.join(pdir, "config.yaml")
    if os.path.isfile(cfg_path):
        try:
            import yaml  # type: ignore
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            for v in cfg.get("views") or []:
                if isinstance(v, dict) and v.get("id") and v.get("entry"):
                    view_pdf[str(v["id"])] = str(v["entry"]) + ".pdf"
        except Exception:
            pass
    view_pdf.setdefault("student_initial", "student.pdf")
    view_pdf.setdefault("teacher", "teacher.pdf")

    os.makedirs(outdir, exist_ok=True)

    # ---- blocks.md：各阶段块正文与元数据 ----
    lines = ["# 审查包：按发放阶段组织的内容块",
             "",
             "> 生成于 %s；块正文为 teaching/content/ 全文，审读者无需打开"
             " TeX 装配源。" % time.strftime("%Y-%m-%dT%H:%M:%S%z"), ""]
    for stage, desc, grp in stages:
        lines.append("## 阶段 %s" % stage)
        lines.append("")
        lines.append(desc)
        lines.append("")
        if not grp:
            lines.append("（本阶段无内容块）")
            lines.append("")
            continue
        for b in grp:
            lines.append("### 块 %s" % b.get("block_id"))
            lines.append("")
            lines.append("- problem_id: %s / task_id: %s"
                         % (b.get("problem_id"), b.get("task_id")))
            lines.append("- level=%s, audience=%s, available_after=%s, "
                         "delivery_channel=%s, views=%s"
                         % (b.get("level"), b.get("audience"),
                            b.get("available_after"),
                            b.get("delivery_channel"), b.get("views")))
            lines.append("- source_refs: %s" % b.get("source_refs"))
            lines.append("- content_ref: %s" % b.get("content_ref"))
            lines.append("")
            cref = b.get("content_ref")
            text = "(内容文件缺失)"
            if cref:
                ap = contract.resolve_path(pdir, cref)
                if ap and os.path.isfile(ap):
                    with open(ap, encoding="utf-8", errors="replace") as f:
                        text = f.read().strip()
            lines.append("```tex")
            lines.append(text)
            lines.append("```")
            lines.append("")
    with open(os.path.join(outdir, "blocks.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # ---- pages-<entry>.txt：各视图 PDF 按页文本 ----
    pdf_digests = {}
    pages_written = []
    for view, pdf_name in sorted(view_pdf.items()):
        pages = pdf_pages(pdir, pdf_name)
        pdf_path = os.path.join(pdir, pdf_name)
        if os.path.exists(pdf_path):
            pdf_digests[pdf_name] = sha256_file(pdf_path)
        if pages in (None, "ERROR"):
            if pages == "ERROR":
                print("[review-pack] [警告] pdftotext 抽取 %s 失败，跳过文本"
                      "视图（审读者请直接读 PDF）。" % pdf_name)
            continue
        out = os.path.join(outdir, "pages-%s.txt" % pdf_name[:-4])
        with open(out, "w", encoding="utf-8") as f:
            for i, page in enumerate(pages, 1):
                if not page.strip():
                    continue
                f.write("===== %s 第 %d 页 =====\n%s\n"
                        % (pdf_name, i, page.strip()))
        pages_written.append(pdf_name)

    # ---- stages.json ----
    stages_doc = [
        {"stage": stage, "description": desc,
         "block_ids": [b.get("block_id") for b in grp]}
        for stage, desc, grp in stages]
    with open(os.path.join(outdir, "stages.json"), "w", encoding="utf-8") as f:
        json.dump(stages_doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # ---- production_digest（contract.compute_production_digest 摘要；
    # 装配顺序敏感） ----
    extra_refs = ["config.yaml", "config.tex", "config-class.tex",
                  "header.tex", "main.tex"] + secs + ["footer.tex"]
    for pdf_view in sorted(view_pdf):
        entry_tex = view_pdf[pdf_view][:-4] + ".tex"
        if os.path.exists(os.path.join(pdir, entry_tex)):
            extra_refs.append(entry_tex)
    digest_info = contract.compute_production_digest(pdir, materials,
                                                     extra_refs=extra_refs)
    for m in digest_info["missing"]:
        print("[review-pack] [警告] 摘要输入缺失: %s" % m)

    manifest = {
        "tool": "make_review_pack.py", "tool_version": TOOL_VERSION,
        "mode": "views",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "production_digest": digest_info["digest"],
        "digest_inputs": digest_info["inputs"],
        "digest_missing": digest_info["missing"],
        "stages": stages_doc,
        "pdfs": pdf_digests,
        "pages_written": pages_written,
        "note": "文本视图仅供受控审读；阅读顺序存疑时审读渲染页（PDF）。"
                "production_digest 改动即旧验收结论失效。",
    }
    with open(os.path.join(outdir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("[review-pack] 审查包已生成: %s" % outdir)
    print("[review-pack] production_digest=%s（T3 审查记录须引用此摘要）"
          % digest_info["digest"])
    return 0


# ---------------------------------------------------------------------------
# 旧项目模式
# ---------------------------------------------------------------------------

def make_review_pack_legacy(pdir, outdir):
    pdf = os.path.join(pdir, "student.pdf")
    if not os.path.exists(pdf):
        print("[review-pack] 缺少 student.pdf，请先用 build.py 构建草稿学生版。")
        return 2
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", pdf, "-"], capture_output=True)
    if r.returncode != 0:
        print("[review-pack] pdftotext 抽取 student.pdf 失败。")
        return 2
    text = r.stdout.decode("utf-8", errors="replace")
    pages = text.split("\x0c")

    secs = sec_names(pdir)
    images = {}
    for name in secs:
        path = os.path.join(pdir, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for img in IMG_RE.findall(f.read()):
                ip = os.path.join(pdir, img)
                images[img] = sha256_file(ip) or "MISSING"

    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "student-pages.txt"), "w",
              encoding="utf-8") as f:
        for i, page in enumerate(pages, 1):
            if not page.strip():
                continue
            f.write("===== 第 %d 页 =====\n%s\n" % (i, page.strip()))

    digest = content_digest(pdir, secs)
    manifest = {
        "tool": "make_review_pack.py", "tool_version": TOOL_VERSION,
        "mode": "legacy",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "content_digest": digest,
        "sections": secs,
        "student_pdf_sha256": sha256_file(pdf),
        "images": images,
        "note": "文本视图仅供受控审读；阅读顺序存疑时审读渲染页。",
    }
    with open(os.path.join(outdir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("[review-pack] 审读包已生成: %s" % outdir)
    print("[review-pack] content_digest=%s（验收记录须引用此摘要）" % digest)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="生成按发放阶段组织的审查包（T3 独立审查受控输入）")
    ap.add_argument("project", nargs="?", default=".")
    args = ap.parse_args()
    pdir = os.path.abspath(args.project)
    if not os.path.isdir(pdir):
        print("[review-pack] 项目目录不存在: %s" % pdir)
        return 2
    if not shutil.which("pdftotext"):
        print("[review-pack] 未找到 pdftotext（poppler）。")
        return 2

    mpath = os.path.join(pdir, MATERIALS_REL.replace("/", os.sep))
    if os.path.isfile(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                materials = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print("[review-pack] %s 不是合法 JSON: %s" % (MATERIALS_REL, exc))
            return 2
        return make_review_pack_v2(pdir, materials,
                                   os.path.join(pdir, "teaching",
                                                "review-pack"))
    return make_review_pack_legacy(pdir, os.path.join(pdir, ".pd", "review"))


if __name__ == "__main__":
    sys.exit(main())
