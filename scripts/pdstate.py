#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "2.0"
r"""
pdstate.py — 任务级运行状态辅助跟踪（薄兼容层，无数据库）

**定位与取舍（v2 说明）**：旧版 pdstate 管理切片任务状态机与候选提交。
两阶段（教研 R0—R4 / 教学 T0—T4）体系下，**权威状态**是
research/run.json + research/freeze.json + teaching/ 下各检查报告
（check_teaching / build / check_numbering 等），本脚本**不保留第二套
冲突真相**：它只做任务级辅助跟踪（派发、候选原子提升、成本记录），
不得作为交付或验收依据。sync 子命令从权威来源**只读汇总**两阶段状态，
不写回任何文件。

管理 <project>/.pd/run.json（辅助状态）：
  schema_version、run_id、切片顺序（以 main.tex 为准）、输入摘要
  （SHA256）与任务表。任务状态机：
    planned → ready → running → produced → checked → accepted
  另有 blocked / failed / stale。这些是运行状态，不是教学质量等级；
  学案可交付还需 T3 独立审查与技术门禁。
  两阶段项目中任务名建议用 R_* / T_* 前缀（如 R1-audit、T2-build），
  仅为人工可读约定，脚本不据此推断权威状态。

子命令（均在 <project> 下操作）：
  init [--tasks a,b,c]      建立/重建清单（默认任务=main.tex 装配切片；
                            --tasks 显式给定任务名，如 R1-audit,T1-draft）
  status                    打印任务表与各状态计数
  mark <task> <status>      主 Agent 推进状态（planned/ready/running/checked/
                            accepted/blocked/failed；produced 只能由 submit 设置）
  submit <task>             校验 input_digest 未变后，将 .pd/candidates/<task>/
                            中的候选原子提升为正式切片；迟到结果不得覆盖新版本
  invalidate                重新计算输入摘要，受影响任务标 stale
  record <task> [--tokens-in N] [--tokens-out N] [--retries N] [--model M]
                            记录每任务成本（宿主提供 usage 时）
  sync                      只读汇总权威两阶段状态：research/run.json、
                            research/freeze.json、teaching/checks/*.json、
                            teaching/materials.json 绑定版本（不写回）

退出码: 0=成功, 1=契约冲突（stale/状态非法/候选缺失）, 2=缺少输入。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = 1
STATUS_FLOW = ("planned", "ready", "running", "produced", "checked",
               "accepted")
STATUS_EXTRA = ("blocked", "failed", "stale")
# 每个任务的输入摘要覆盖：模板骨架、配置、竞赛 maps 四表与全部
# 档案/探索/来源目录（保守失效：任一共享输入变化，相关任务需重跑）
TASK_INPUT_FILES = ("header.tex", "footer.tex", "config.tex",
                    "config-class.tex", "student.tex", "teacher.tex",
                    "maps/tool-map.md", "maps/problem-dossiers.md",
                    "maps/exploration-log.md")
TASK_INPUT_DIRS = ("dossiers", "explorations",
                   "problem-source", "solution-source", "knowledge-source")
# 两阶段项目的共享输入（存在时纳入摘要）
TASK_INPUT_FILES_V2 = ("teaching/materials.json",)
INPUT_RE = re.compile(r"\\input\{(sec[^/\\}.]+?)(?:\.tex)?\}")


def sha256(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_run(pdir):
    path = os.path.join(pdir, ".pd", "run.json")
    if not os.path.exists(path):
        return None, path
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def save_run(path, run):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子替换


def strip_comment(line):
    r"""去掉行内注释；\% 是转义字符，不作为注释起点。"""
    i = 0
    while True:
        j = line.find("%", i)
        if j < 0:
            return line
        k = j - 1
        while k >= 0 and line[k] == "\\":
            k -= 1
        if (j - 1 - k) % 2 == 1:  # 奇数个反斜杠 → \% 转义
            i = j + 1
        else:
            return line[:j]


def project_slices(pdir):
    main = os.path.join(pdir, "main.tex")
    if not os.path.exists(main):
        return []
    with open(main, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    out = []
    for line in lines:
        for m in INPUT_RE.finditer(strip_comment(line)):
            name = m.group(1) + ".tex"
            if name not in out:
                out.append(name)
    return out


def task_input_digest(pdir, task_id):
    """任务输入摘要：骨架 + 配置 + maps 四表 + 档案/探索/来源目录
    （两阶段项目另含 teaching/materials.json）。"""
    h = hashlib.sha256()
    files = list(TASK_INPUT_FILES)
    files += [f for f in TASK_INPUT_FILES_V2
              if os.path.isfile(os.path.join(pdir,
                                             f.replace("/", os.sep)))]
    for dname in TASK_INPUT_DIRS:
        dpath = os.path.join(pdir, dname)
        if os.path.isdir(dpath):
            for root, dirs, names in os.walk(dpath):
                dirs.sort()
                for fn in sorted(names):
                    files.append(os.path.relpath(os.path.join(root, fn), pdir))
    for name in files:
        d = sha256(os.path.join(pdir, name))
        h.update(name.encode())
        h.update((d or "MISSING").encode())
    return h.hexdigest()


def cmd_init(pdir, args):
    run, path = load_run(pdir)
    if run is not None:
        print("[pdstate] run.json 已存在（run_id=%s）；如需重建请先备份删除，"
              "或用 invalidate 更新摘要。" % run["run_id"])
        return 1
    if args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
        if not task_ids:
            print("[pdstate] --tasks 为空")
            return 2
    else:
        task_ids = project_slices(pdir)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": time.strftime("%Y%m%d-%H%M%S") + "-%04x" % (os.getpid() % 0xffff),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "辅助任务跟踪；权威状态见 research/run.json + freeze.json "
                "+ teaching/ 检查报告（sync 子命令只读汇总）。",
        "slices": project_slices(pdir),
        "digests": {n: sha256(os.path.join(pdir, n))
                    for n in ("header.tex", "config.yaml", "config.tex")},
        "tasks": {},
    }
    for s in task_ids:
        run["tasks"][s] = {"status": "planned", "attempts": [],
                           "input_digest": task_input_digest(pdir, s),
                           "usage": {}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_run(path, run)
    print("[pdstate] 已建立 run.json：run_id=%s，%d 个任务（planned）"
          % (run["run_id"], len(task_ids)))
    return 0


def cmd_status(pdir, run):
    counts = {}
    for tid, t in run["tasks"].items():
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    print("[pdstate] run_id=%s schema=%d（辅助跟踪，非权威状态；权威状态用 "
          "sync 查看）" % (run["run_id"], run["schema_version"]))
    for tid in run["tasks"]:
        t = run["tasks"].get(tid, {})
        print("  %-16s %-9s attempts=%d" % (tid, t.get("status", "?"),
                                            len(t.get("attempts", []))))
    print("[pdstate] 状态计数: %s" % (", ".join(
        "%s=%d" % kv for kv in sorted(counts.items())) or "无任务"))
    return 0


def cmd_mark(pdir, run, path, args):
    tid, status = args.task, args.status
    if tid not in run["tasks"]:
        print("[pdstate] 未知任务 %s" % tid)
        return 2
    if status == "produced":
        print("[pdstate] produced 只能由 submit 设置")
        return 1
    if status not in STATUS_FLOW + STATUS_EXTRA:
        print("[pdstate] 非法状态 %s（允许: %s）"
              % (status, "/".join(STATUS_FLOW + STATUS_EXTRA)))
        return 2
    run["tasks"][tid]["status"] = status
    save_run(path, run)
    print("[pdstate] %s → %s" % (tid, status))
    return 0


def cmd_submit(pdir, run, path, args):
    tid = args.task
    if tid not in run["tasks"]:
        print("[pdstate] 未知任务 %s" % tid)
        return 2
    task = run["tasks"][tid]
    cand = os.path.join(pdir, ".pd", "candidates", tid.replace(".tex", ""), tid)
    if not os.path.exists(cand):
        print("[pdstate] 候选不存在: %s" % cand)
        return 2
    cur_digest = task_input_digest(pdir, tid)
    if cur_digest != task["input_digest"]:
        # 输入已变化：迟到的结果不得覆盖当前版本
        task["status"] = "stale"
        save_run(path, run)
        print("[pdstate] [冲突] %s 的输入摘要已变化，候选结果作废（stale）；"
              "请按新输入重新派发，不得以迟到结果覆盖当前版本。" % tid)
        return 1
    target = os.path.join(pdir, tid)
    with open(cand, "rb") as f:
        data = f.read()
    tmp = target + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, target)  # 原子提升为正式切片
    task["attempts"].append({
        "attempt_id": len(task["attempts"]) + 1,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "output_digest": hashlib.sha256(data).hexdigest(),
    })
    task["status"] = "produced"
    save_run(path, run)
    print("[pdstate] %s 已提升为正式文件（attempt #%d），状态 produced；"
          "内容与技术检查后请 mark checked/accepted。"
          % (tid, len(task["attempts"])))
    return 0


def cmd_invalidate(pdir, run, path):
    n_stale = 0
    for tid, task in run["tasks"].items():
        if task_input_digest(pdir, tid) != task["input_digest"]:
            if task["status"] not in ("planned", "stale"):
                task["status"] = "stale"
                n_stale += 1
            task["input_digest"] = task_input_digest(pdir, tid)
    save_run(path, run)
    print("[pdstate] 摘要已刷新，%d 个任务标记为 stale（需重跑）" % n_stale)
    return 0


def cmd_record(pdir, run, path, args):
    tid = args.task
    if tid not in run["tasks"]:
        print("[pdstate] 未知任务 %s" % tid)
        return 2
    u = run["tasks"][tid].setdefault("usage", {})
    for k, v in (("tokens_in", args.tokens_in), ("tokens_out", args.tokens_out),
                 ("retries", args.retries), ("model", args.model)):
        if v is not None:
            u[k] = v
    save_run(path, run)
    print("[pdstate] %s 成本记录: %s" % (tid, u))
    return 0


def _load_json(pdir, rel):
    path = os.path.join(pdir, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def cmd_sync(pdir):
    """只读汇总权威两阶段状态（不写回任何文件）。"""
    found = False
    # ---- 教研阶段（R0—R4）权威：research/run.json + freeze.json ----
    rrun = _load_json(pdir, "research/run.json")
    freeze = _load_json(pdir, "research/freeze.json")
    brief = _load_json(pdir, "research/brief.json")
    if rrun is not None or freeze is not None:
        found = True
        print("[sync] 教研阶段（权威来源 research/）：")
        if rrun is not None:
            print("  run.json: run_id=%s" % rrun.get("run_id", "?"))
        if brief is not None:
            print("  brief: brief_id=%s revision=%s schema=%s"
                  % (brief.get("brief_id"), brief.get("revision"),
                     brief.get("schema_version")))
        if freeze is not None:
            print("  freeze: brief_id=%s revision=%s digest=%s…"
                  % (freeze.get("brief_id"), freeze.get("revision"),
                     str(freeze.get("brief_digest"))[:12]))
        else:
            print("  freeze: 缺失（无有效教研冻结，教学侧不得接包）")
    # ---- 教学阶段（T0—T4）权威：teaching/ 检查报告 + materials 绑定 ----
    materials = _load_json(pdir, "teaching/materials.json")
    checks_dir = os.path.join(pdir, "teaching", "checks")
    if materials is not None or os.path.isdir(checks_dir):
        found = True
        print("[sync] 教学阶段（权威来源 teaching/ 检查报告）：")
        if materials is not None:
            stale = ""
            if freeze is not None and materials.get("brief_revision") \
                    != freeze.get("revision"):
                stale = "（stale：绑定 revision=%s，当前 freeze revision=%s）" \
                    % (materials.get("brief_revision"), freeze.get("revision"))
            print("  materials: blocks=%d brief_revision=%s%s"
                  % (len(materials.get("blocks", [])),
                     materials.get("brief_revision"), stale))
        if os.path.isdir(checks_dir):
            for fn in sorted(os.listdir(checks_dir)):
                if not fn.endswith(".json"):
                    continue
                rep = _load_json(pdir, "teaching/checks/" + fn)
                if isinstance(rep, dict) and "status" in rep:
                    print("  %s: status=%s" % (fn, rep.get("status")))
    if not found:
        print("[sync] 未发现 research/ 或 teaching/ 权威状态文件；"
              "本脚本只做任务级辅助跟踪，权威状态以 research/run.json + "
              "freeze.json + teaching/ 检查报告为准。")
        return 2
    print("[sync] 说明：以上为只读汇总；pdstate 的 .pd/run.json 是任务级"
          "辅助跟踪，不构成第二套权威状态。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="任务级运行状态辅助跟踪（薄兼容层）")
    ap.add_argument("project", help="项目目录")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--tasks", default=None,
                   help="显式任务名列表（逗号分隔，如 R1-audit,T1-draft）；"
                        "缺省取 main.tex 装配切片")
    sub.add_parser("status")
    p = sub.add_parser("mark"); p.add_argument("task"); p.add_argument("status")
    p = sub.add_parser("submit"); p.add_argument("task")
    sub.add_parser("invalidate")
    p = sub.add_parser("record")
    p.add_argument("task")
    p.add_argument("--tokens-in", type=int)
    p.add_argument("--tokens-out", type=int)
    p.add_argument("--retries", type=int)
    p.add_argument("--model")
    sub.add_parser("sync")
    args = ap.parse_args()
    pdir = os.path.abspath(args.project)
    if not os.path.isdir(pdir):
        print("[pdstate] 项目目录不存在: %s" % pdir)
        return 2

    if args.cmd == "init":
        return cmd_init(pdir, args)
    if args.cmd == "sync":
        return cmd_sync(pdir)
    run, path = load_run(pdir)
    if run is None:
        print("[pdstate] 缺少 .pd/run.json，请先运行 init。")
        return 2
    if run.get("schema_version") != SCHEMA_VERSION:
        print("[pdstate] schema_version 不兼容（%s != %d）"
              % (run.get("schema_version"), SCHEMA_VERSION))
        return 2
    if args.cmd == "status":
        return cmd_status(pdir, run)
    if args.cmd == "mark":
        return cmd_mark(pdir, run, path, args)
    if args.cmd == "submit":
        return cmd_submit(pdir, run, path, args)
    if args.cmd == "invalidate":
        return cmd_invalidate(pdir, run, path)
    if args.cmd == "record":
        return cmd_record(pdir, run, path, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
