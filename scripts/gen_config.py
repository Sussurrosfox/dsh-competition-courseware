#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "3.0"
r"""
gen_config.py — 由项目目录的 config.yaml 生成 LaTeX 参数文件与视图编译入口

输出:
  <project>/config-class.tex   (\PassOptionsToClass，须在 \documentclass 前 \input)
  <project>/config.tex         (geometry 版面、文本参数宏；视图模式含视图开关宏)
  <project>/<entry>.tex        (视图模式：views 声明的各视图编译入口)

两种模式：

  * 兼容模式（config.yaml 无 views 键）：行为与旧版 gen_config 2.x 完全一致，
    只生成 config-class.tex / config.tex，不读取也不触碰任何入口文件；
  * 视图模式（config.yaml 含 views 键）：按 views 声明生成视图开关宏与各
    入口文件。此模式要求 teaching/materials.json 存在（T1 统一内容源），
    用于核对块-视图对应并避免空入口：separate_hint / separate_review 视图
    若没有任何内容块进入，则不生成对应入口文件（不生成空提示/复盘页）。

views 配置（YAML 列表，每项字段）：

  id:             视图 ID（如 student_initial / teacher / hint-p1）；
  entry:          编译入口文件名（生成 <entry>.tex -> <entry>.pdf）；
  channel:        in_main / separate_hint / separate_review；
  audience:       student / teacher；
  title:          可选，入口页标题（缺省用 main_title）；
  problem_id:     可选，separate_* 视图关联的题目；
  teacher_flags:  可选 true/false；teacher 入口自动开启 \TeacherVersion。

约束（违反即报错，退出码 2；绝不静默忽略）：

  - 未知顶层键、未知 views 子键、非法枚举一律报错；
  - 视图模式必须恰好声明 student_initial(entry=student, audience=student)
    与 teacher(entry=teacher, audience=teacher) 两个 in_main 视图；
  - separate_hint 的 entry 须为 hint-*，separate_review 的 entry 须为 review-*；
  - entry 不得占用保留名（main/header/footer/config/config-class），
    不得与 sec* 切片命名冲突；
  - materials 块出现的视图必须在 views 中声明。

旧字段兼容：font_size / 版面几何 / columns / main_title / teacher_head_left /
competition_* 全部保留，语义与旧版一致；lesson_mode 唯一取值 guided
（分步引导）由 brief 契约承载，不映射到旧 practice_mode 字段。

配置语义（扁平 key: value，单行值；views 为嵌套列表，需要 PyYAML）：
  - 扁平值可用双引号或单引号包裹；引号内的 # 不视为注释；
  - 未加引号的值在第一个 " #" 处截断注释；加引号的值不支持行内注释。

用法: python gen_config.py --project <项目目录>
退出码: 0=成功, 2=配置缺失/解析或校验失败/材料缺失。
"""
import argparse
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 已知扁平字段：kind 决定校验与转义方式（与旧版 2.x 完全一致）
FIELDS = {
    "font_size": ("enum", ("10pt", "11pt", "12pt")),
    "paperwidth": ("dim", None),
    "paperheight": ("dim", None),
    "orientation": ("enum", ("landscape", "portrait")),
    "margin_top": ("dim", None),
    "margin_bottom": ("dim", None),
    "margin_left": ("dim", None),
    "margin_right": ("dim", None),
    "headheight": ("dim", None),
    "columns": ("int", None),
    "main_title": ("text", None),
    "teacher_head_left": ("text", None),
    # ---- 竞赛模式开关（旧字段兼容；导出为 \pdCompetition* 宏） ----
    "competition_mode": ("bool", None),
    "competition_min_independent_minutes": ("int", None),
    # ---- T2 自审开关 ----
    "enable_t2_self_review": ("bool", None),
    "enable_obstacle_study": ("bool", None),
    # ---- init_project.py 写入的元数据键（§5.3；不参与 LaTeX 导出） ----
    "schema_version": ("text", None),
    "brief_id": ("text", None),
    "brief_revision": ("int", None),
    "lesson_mode": ("enum", ("guided",)),
    "selected_problem_ids": ("text", None),
    "font_main": ("text", None),
    "font_math": ("text", None),
    "font_lock_note": ("text", None),
}
DIM_RE = re.compile(r"^\d+(\.\d+)?(mm|cm|in|pt|em|ex|bp|pc)$")

# ---- views 配置约束 ----
VIEW_KEYS = {"id", "entry", "channel", "audience",
             "title", "problem_id", "teacher_flags"}
VIEW_REQUIRED = ("id", "entry", "channel", "audience")
VIEW_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
ENTRY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
CHANNELS = ("in_main", "separate_hint", "separate_review")
AUDIENCES = ("student", "teacher")
RESERVED_ENTRIES = {"main", "header", "footer",
                    "config", "config-class"}
SEC_ENTRY_RE = re.compile(r"^sec\d*$")
MATERIALS_REL = "teaching/materials.json"


def parse_flat_yaml(text):
    """解析扁平 YAML 子集（与旧版一致；遇到嵌套 views 节时跳过其缩进行）。

    与 PyYAML 在该子集上保持一致：单/双引号包裹的值原样取引号内内容
    （引号内 # 不是注释）；未包裹的值在 " #" 处截断注释并去首尾空白。
    """
    data = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        if line[:1] in (" ", "\t"):
            continue  # 嵌套内容（views 列表项）不属于扁平子集
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        key, _, val = s.partition(":")
        key, val = key.strip(), val.strip()
        if not val:
            continue  # "views:" 等嵌套节标题，扁平模式无值
        if val[:1] in ('"', "'"):
            q = val[0]
            end = val.find(q, 1)
            if end < 0:
                raise ValueError("config.yaml:%d: 引号未闭合: %s"
                                 % (lineno, line.strip()))
            val = val[1:end]
        else:
            val = val.split(" #", 1)[0].strip()
        data[key] = val
    return data


def has_views_key(text):
    """检测顶层 views: 键（非缩进行）。"""
    for line in text.splitlines():
        if line[:1] in (" ", "\t", "#"):
            continue
        if re.match(r"^views\s*:", line):
            return True
    return False


def load_config(path):
    """读取扁平的 key: value 配置（旧版行为，供旧检查脚本复用）。

    优先 PyYAML，缺失时用内置解析器；非标量值（如 views 列表）在扁平视图中
    被忽略——需要 views 时请用 load_config_full。
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse_flat_yaml(text)
    data = yaml.safe_load(text)
    out = {}
    for k, v in (data or {}).items():
        if isinstance(v, (list, dict)):
            continue  # 嵌套配置不属于扁平视图
        out[k] = str(v).lower() if isinstance(v, bool) else str(v)
    return out


def load_config_full(path):
    """完整读取 config.yaml（含 views 嵌套列表，原生类型）。

    返回 (flat_cfg, views_or_None)。views 键不存在时 views_or_None 为 None
    （兼容模式）；views 存在但需要 PyYAML 而缺失时抛 ValueError。
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml  # type: ignore
    except ImportError:
        if has_views_key(text):
            raise ValueError("config.yaml 含 views 嵌套配置，需要 PyYAML 解析"
                             "（pip install pyyaml），或移除 views 走兼容模式")
        return parse_flat_yaml(text), None
    data = yaml.safe_load(text) or {}
    flat = {}
    for k, v in data.items():
        if isinstance(v, (list, dict)):
            continue
        flat[k] = str(v).lower() if isinstance(v, bool) else str(v)
    views = data.get("views") if has_views_key(text) else None
    return flat, views


def validate_flat(cfg):
    """扁平字段类型、枚举与单位校验；返回错误消息列表。"""
    errors = []
    for k, v in cfg.items():
        if k not in FIELDS:
            errors.append("未知配置键 “%s”（已知键: %s，或嵌套节 views）"
                          % (k, ", ".join(sorted(FIELDS))))
            continue
        kind, enum = FIELDS[k]
        if kind == "enum" and v not in enum:
            errors.append("%s 取值 “%s” 非法，只能是: %s"
                          % (k, v, " / ".join(enum)))
        elif kind == "bool" and v not in ("true", "false"):
            errors.append("%s 取值 “%s” 非法，只能是: true / false" % (k, v))
        elif kind == "int":
            if not v.isdigit() or int(v) < 1:
                errors.append("%s 必须是正整数（收到 “%s”）" % (k, v))
        elif kind == "dim" and not DIM_RE.match(v):
            errors.append("%s 必须带单位（mm/cm/in/pt 等），收到 “%s”"
                          % (k, v))
    return errors


def validate_views(views):
    """views 列表校验；返回 (规范化的视图列表, 错误消息列表)。"""
    errors = []
    norm = []
    if not isinstance(views, list) or not views:
        return norm, ["views 必须是非空列表（不需要视图时请移除整个 views 键，"
                      "走旧版兼容模式）"]
    seen_ids, seen_entries = {}, {}
    for i, item in enumerate(views):
        where = "views[%d]" % i
        if not isinstance(item, dict):
            errors.append("%s 必须是映射（收到 %r）" % (where, item))
            continue
        for k in item:
            if k not in VIEW_KEYS:
                errors.append("%s 含未知子键 “%s”（已知: %s)"
                              % (where, k, ", ".join(sorted(VIEW_KEYS))))
        missing = [k for k in VIEW_REQUIRED if k not in item]
        if missing:
            errors.append("%s 缺少必填子键: %s" % (where, ", ".join(missing)))
            continue
        v = {k: (str(item[k]) if not isinstance(item[k], bool)
                 else str(item[k]).lower()) for k in item}
        vid, entry = v["id"], v["entry"]
        if not VIEW_ID_RE.match(vid):
            errors.append("%s id “%s” 非法（字母开头，字母/数字/_/-）"
                          % (where, vid))
        if vid in seen_ids:
            errors.append("%s id “%s” 与 views[%d] 重复"
                          % (where, vid, seen_ids[vid]))
        seen_ids[vid] = i
        if not ENTRY_RE.match(entry):
            errors.append("%s entry “%s” 非法（字母开头，字母/数字/_/-）"
                          % (where, entry))
        if entry in seen_entries:
            errors.append("%s entry “%s” 与 views[%d] 重复"
                          % (where, entry, seen_entries[entry]))
        seen_entries[entry] = i
        if entry in RESERVED_ENTRIES or SEC_ENTRY_RE.match(entry):
            errors.append("%s entry “%s” 占用保留名/切片命名空间"
                          % (where, entry))
        if v["channel"] not in CHANNELS:
            errors.append("%s channel “%s” 非法，只能是: %s"
                          % (where, v["channel"], " / ".join(CHANNELS)))
        if v["audience"] not in AUDIENCES:
            errors.append("%s audience “%s” 非法，只能是: %s"
                          % (where, v["audience"], " / ".join(AUDIENCES)))
        if v["channel"] == "separate_hint" and not entry.startswith("hint-"):
            errors.append("%s separate_hint 视图的 entry 须为 hint-*（收到 "
                          "“%s”）" % (where, entry))
        if v["channel"] == "separate_review" \
                and not entry.startswith("review-"):
            errors.append("%s separate_review 视图的 entry 须为 review-*"
                          "（收到 “%s”）" % (where, entry))
        if v["channel"] == "in_main":
            ok = ((vid, entry, v["audience"]) in
                  (("student_initial", "student", "student"),
                   ("teacher", "teacher", "teacher")))
            if not ok:
                errors.append("%s in_main 视图只能是 student_initial"
                              "(entry=student, audience=student) 或 teacher"
                              "(entry=teacher, audience=teacher)；收到 "
                              "id=%s entry=%s audience=%s"
                              % (where, vid, entry, v["audience"]))
        tf = v.get("teacher_flags")
        if tf is not None and tf not in ("true", "false"):
            errors.append("%s teacher_flags 只能是 true / false（收到 “%s”）"
                          % (where, tf))
        v["teacher_flags"] = "true" if (
            tf == "true" or (tf is None and vid == "teacher")) else "false"
        norm.append(v)
    ids = {v["id"] for v in norm}
    for need in ("student_initial", "teacher"):
        if need not in ids:
            errors.append("views 缺少必需的 in_main 视图 “%s”" % need)
    return norm, errors


# G1.3：LaTeX 特殊字符全集转义表（\ { } $ & # _ % ^ ~ 一个不漏）
_TEX_ESC_MAP = {
    "\\": "\\textbackslash{}",
    "{": "\\{",
    "}": "\\}",
    "$": "\\$",
    "&": "\\&",
    "#": "\\#",
    "_": "\\_",
    "%": "\\%",
    "^": "\\textasciicircum{}",
    "~": "\\textasciitilde{}",
}

# esc() 输出中允许出现的反斜杠形式；其余一律视为活跃控制序列
_ESCAPED_FORM_RE = re.compile(
    r"\\(?:textbackslash\{\}|textasciicircum\{\}|textasciitilde\{\}|"
    r"[{}$&#_%])")


def esc(v):
    """完整转义普通文本参数中的 LaTeX 特殊字符（配置为受信纯文本）。"""
    return "".join(_TEX_ESC_MAP.get(c, c) for c in v)


def _active_control_sequences(escaped):
    """返回转义后文本中仍活跃的控制序列列表；空列表即安全（fail-closed）。"""
    bad = []
    i = 0
    while i < len(escaped):
        if escaped[i] != "\\":
            i += 1
            continue
        m = _ESCAPED_FORM_RE.match(escaped, i)
        if m:
            i = m.end()
            continue
        tail = escaped[i:]
        name = re.match(r"\\[A-Za-z]+", tail)
        bad.append(name.group(0) if name else tail[:2])
        i += len(bad[-1])
    return bad


def esc_checked(v, field):
    """esc() + 双向校验：转义结果不得残留活跃控制序列。

    标题等文本字段进入 config.tex 前后均校验；发现残留即 raise ValueError
    （fail-closed，绝不静默写入）。
    """
    out = esc(str(v))
    bad = _active_control_sequences(out)
    if bad:
        raise ValueError("%s 转义后仍含活跃控制序列 %s（拒绝写入 config.tex）"
                         % (field, bad))
    return out


def banner():
    return ("% Auto-generated by scripts/gen_config.py from config.yaml\n"
            "% 请勿手改本文件；修改 config.yaml 后重新运行 gen_config.py。\n")


def gen_class_tex(cfg):
    return (banner() + "\\PassOptionsToClass{%s}{ctexart}\n"
            % cfg.get("font_size", "12pt"))


def gen_config_tex(cfg, views):
    """config.tex：geometry + 文本参数宏 +（视图模式）视图开关宏。"""
    geo_keys = ["paperwidth", "paperheight", "orientation", "margin_top",
                "margin_bottom", "margin_left", "margin_right", "headheight"]
    geo_map = {"margin_top": "top", "margin_bottom": "bottom",
               "margin_left": "left", "margin_right": "right"}
    opts = []
    for k in geo_keys:
        if k not in cfg:
            continue
        if k == "orientation":
            opts.append(cfg[k])
        else:
            opts.append("%s=%s" % (geo_map.get(k, k), cfg[k]))
    if opts:
        geometry = "\\usepackage[\n  " + ",\n  ".join(opts) + "\n]{geometry}\n"
    else:
        geometry = "\\usepackage{geometry}\n"

    text_keys = ["columns", "main_title", "teacher_head_left"]
    defaults = {"columns": "2"}
    competition_keys = [k for k in FIELDS if k.startswith("competition_")]

    def csname(k):  # 下划线转 CamelCase（@ 在导言区不是字母，不可用）
        return "pd" + "".join(w.capitalize() for w in k.split("_"))

    macros = []
    for k in text_keys:
        val = cfg.get(k, defaults.get(k, ""))
        if FIELDS[k][0] == "text":
            val = esc_checked(val, k)
        macros.append("\\providecommand{\\%s}{%s}" % (csname(k), val))
    for k in competition_keys:
        val = cfg.get(k, "")
        macros.append("\\providecommand{\\%s}{%s}" % (csname(k), val))

    out = (banner() + "\n% --- 纸张与版面几何 ---\n" + geometry
           + "\n% --- 文本参数 (\\pd<Key>) ---\n"
           + "\n".join(macros) + "\n")

    if views is not None:
        known = ",".join(v["id"] for v in views)
        out += ("\n% --- 视图开关（views 配置生成） ---\n"
                "% 入口文件以 \\def\\pdCurrentView{<id>} 声明当前视图；\n"
                "% 未声明的视图直接编译报错，声明后定义 \\pdView@<id> 开关。\n"
                "\\makeatletter\n"
                "\\newcommand{\\pdKnownViews}{" + known + "}\n"
                "\\ifdefined\\pdCurrentView\n"
                "  \\def\\pd@knownview{0}%\n"
                "  \\@for\\pd@kv:=\\pdKnownViews\\do{%\n"
                "    \\edef\\pd@cur{\\pd@kv}%\n"
                "    \\ifx\\pdCurrentView\\pd@cur\\def\\pd@knownview{1}\\fi}%\n"
                "  \\ifnum\\pd@knownview=0\n"
                "    \\PackageError{gen-config}{pdCurrentView 未在 "
                "config.yaml 的 views 中声明}{入口文件声明的视图必须是 "
                "views 中已声明的 id 之一；修改 config.yaml 后重新运行 "
                "gen_config.py。}%\n"
                "  \\fi\n"
                "  \\expandafter\\def\\csname pdView@\\pdCurrentView"
                "\\endcsname{}%\n"
                "\\fi\n"
                "\\makeatother\n")
    return out


def gen_entry_tex(view):
    """生成单个视图的编译入口文件内容。"""
    vid = view["id"]
    head = (banner()
            + "% 视图入口: " + vid + "（channel=" + view["channel"]
            + ", audience=" + view["audience"] + "）\n")
    if view["channel"] == "in_main" and view["audience"] == "teacher":
        return (head
                + "% teacher 视图：完整教师资料（开启 \\TeacherVersion）。\n"
                "\\def\\TeacherVersion{}\n"
                "\\def\\pdCurrentView{" + vid + "}\n"
                "\\input{main.tex}\n")
    if view["channel"] == "in_main":
        return (head
                + "% student_initial 视图：首轮发放内容"
                "（available_after=initial 的 L0 块）。\n"
                "\\def\\pdCurrentView{" + vid + "}\n"
                "\\input{main.tex}\n")
    # separate_hint / separate_review 独立发放页
    kind = "提示页" if view["channel"] == "separate_hint" else "复盘页"
    title = view.get("title") or (kind + "："
                                  + (view.get("problem_id") or vid))
    return (head
            + "% " + kind + "独立发放页：静态 PDF 不会因印在留白之后就"
            "延迟揭示，\n% 延迟揭示只能靠本入口按需分阶段发放"
            "（release-later/）。\n"
            "\\def\\pdCurrentView{" + vid + "}\n"
            "\\def\\pdViewTitle{" + esc_checked(title, "views.title") + "}\n"
            "\\def\\pdCompactPage{}\n"
            "\\input{main.tex}\n")


def load_materials_views(tdir):
    """读取 teaching/materials.json 中出现的视图集合。

    返回 (views_set, error)。materials.json 缺失或非法为错误。
    """
    path = os.path.join(tdir, MATERIALS_REL.replace("/", os.sep))
    if not os.path.isfile(path):
        return None, ("视图模式需要 %s（T1 统一内容源）判断各视图是否有内容块；"
                      "请先完成 T1，或移除 config.yaml 的 views 键走兼容模式"
                      % MATERIALS_REL)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return None, "%s 不是合法 JSON: %s" % (MATERIALS_REL, exc)
    views = set()
    for b in doc.get("blocks", []):
        views.update(b.get("views", []))
    return views, None


def write_if_changed(path, content, label):
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == content:
        print("[gen_config] %s 无变化" % label)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("[gen_config] 已生成 %s" % path)


def main():
    ap = argparse.ArgumentParser(
        description="由 config.yaml 生成 LaTeX 参数文件与视图编译入口")
    ap.add_argument("--project", required=True,
                    help="项目目录（含 config.yaml；生成物写于此）")
    args = ap.parse_args()
    tdir = os.path.abspath(args.project)
    cfg_path = os.path.join(tdir, "config.yaml")
    if not os.path.exists(cfg_path):
        print("[gen_config] 缺少 %s" % cfg_path)
        return 2
    try:
        cfg, views_raw = load_config_full(cfg_path)
    except ValueError as e:
        print("[gen_config] 配置解析失败: %s" % e)
        return 2

    errors = validate_flat(cfg)

    # ---- 用户前置对齐断言（PHASE 0 BREAKPOINT 核验）----
    # user_alignment 是嵌套结构，从 load_config_full 的完整 YAML 中读取。
    try:
        import yaml as _yaml
        with open(cfg_path, encoding="utf-8") as _f:
            _full = _yaml.safe_load(_f.read()) or {}
    except ImportError:
        print("[gen_config] [致命错误] 缺少 PyYAML，无法核验 user_alignment 嵌套区块。")
        print("  请先安装 PyYAML（pip install pyyaml）再重新运行本脚本。")
        return 2
    _alignment = _full.get("user_alignment") or {}
    if not _alignment.get("confirmed") or _alignment.get("confirmed") is False:
        print("[gen_config] [致命错误] 用户前置对齐未完成：")
        print("  config.yaml 中 user_alignment.confirmed 不为 true。")
        print("  请先在 SKILL.md 要求的 PHASE 0 BREAKPOINT 中向用户确认：")
        print("  1) 学案标题 (main_title)")
        print("  2) 教学模式 (lesson_mode，固定 guided 分步引导)")
        print("  确认后将答案写入 config.yaml 的 user_alignment 区块，")
        print("  并将 user_alignment.confirmed 改为 true，再重新运行本脚本。")
        return 2
    _ua_title = _alignment.get("main_title")
    if _ua_title is None:
        print("[gen_config] [致命错误] user_alignment.main_title 未填写（为 null）；"
              "必须经用户确认后填写。")
        return 2
    # 若用户对齐的标题与 main_title 字段不一致，以 user_alignment.main_title 为准
    cfg["main_title"] = str(_ua_title)

    views = None
    if views_raw is not None:
        views, verrs = validate_views(views_raw)
        errors.extend(verrs)
    if errors:
        print("[gen_config] 配置校验失败:")
        for e in errors:
            print("  - " + e)
        return 2

    try:
        write_if_changed(os.path.join(tdir, "config-class.tex"),
                         gen_class_tex(cfg), "config-class.tex")
        write_if_changed(os.path.join(tdir, "config.tex"),
                         gen_config_tex(cfg, views), "config.tex")
    except ValueError as e:
        print("[gen_config] [致命错误] %s" % e)
        return 2

    if views is None:
        return 0  # 兼容模式：不触碰任何入口文件

    # ---- 视图模式：materials 核对 + 入口生成 ----
    used, err = load_materials_views(tdir)
    if err:
        print("[gen_config] " + err)
        return 2
    declared = {v["id"] for v in views}
    undeclared = sorted(used - declared)
    if undeclared:
        print("[gen_config] 配置校验失败:")
        for u in undeclared:
            print("  - teaching/materials.json 的块进入了未声明的视图 “%s”；"
                  "请在 config.yaml views 中声明或修正材料" % u)
        return 2
    for v in views:
        entry_path = os.path.join(tdir, v["entry"] + ".tex")
        if v["channel"] in ("separate_hint", "separate_review") \
                and v["id"] not in used:
            if os.path.exists(entry_path):
                print("[gen_config] [警告] 视图 %s 在 materials.json 中没有"
                      "任何内容块，但入口 %s.tex 已存在；请删除旧入口避免"
                      "构建空页" % (v["id"], v["entry"]))
            else:
                print("[gen_config] 视图 %s 无内容块，按规则不生成空入口 %s.tex"
                      % (v["id"], v["entry"]))
            continue
        try:
            write_if_changed(entry_path, gen_entry_tex(v),
                             v["entry"] + ".tex")
        except ValueError as e:
            print("[gen_config] [致命错误] %s" % e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
