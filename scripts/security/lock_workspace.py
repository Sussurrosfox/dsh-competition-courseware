#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""lock_workspace.py — 零信任工作区存储隔离控制工具（OS 级 ACL 赋权）。

规范依据（block_it.md 支柱一与路线 A）：
  若主 Agent 拥有对 scripts/、schemas/、teaching/generated/、release/ 的写权限，
  它可以通过修改 Python 脚本或直接创建发布物绕过所有门禁。
  本工具通过 Windows icacls 或 Linux chmod 实施特权降级：
  - 核心只读目录（R/O）：scripts/、schemas/、teaching/generated/、teaching/content/、release/
  - 暂存可写目录（R/W）：staging/、issues/

用法：
  python scripts/security/lock_workspace.py --lock <项目根>
  python scripts/security/lock_workspace.py --unlock <项目根>
  python scripts/security/lock_workspace.py --status <项目根>
"""
import argparse
import os
import platform
import subprocess
import sys

RO_DIRS = ["scripts", "schemas", "teaching/generated", "teaching/content", "release"]
RW_DIRS = ["staging", "issues"]


def is_windows():
    return platform.system().lower() == "windows"


def lock_workspace(project_root):
    root = os.path.abspath(project_root)
    print(f"[lock_workspace] 正在对项目实施零信任写隔离: {root}")
    
    # 确保所有目录存在以便赋予 ACL
    for d in RO_DIRS + RW_DIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
        
    if is_windows():
        username = os.environ.get("USERNAME", "Everyone")
        for d in RO_DIRS:
            target = os.path.join(root, d)
            # 继承重置并赋予只读读取+执行权限 (RX)，/t 递归应用到目录下所有文件
            cmd = ["icacls", target, "/inheritance:r", "/grant:r", f"{username}:(OI)(CI)(RX)", "/t"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                print(f"  [锁定 R/O] {d} -> 成功 ({username} 仅有 RX 权限)")
            else:
                print(f"  [警告] {d} icacls 失败: {r.stderr.strip() or r.stdout.strip()}")
        for d in RW_DIRS:
            target = os.path.join(root, d)
            cmd = ["icacls", target, "/grant", f"{username}:(OI)(CI)(M)", "/t"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                print(f"  [开放 R/W] {d} -> 成功 ({username} 具有 Modify 权限)")
            else:
                print(f"  [警告] {d} icacls 失败: {r.stderr.strip() or r.stdout.strip()}")
    else:
        for d in RO_DIRS:
            target = os.path.join(root, d)
            subprocess.run(["chmod", "-R", "555", target], capture_output=True)
            print(f"  [锁定 R/O] {d} -> chmod 555")
        for d in RW_DIRS:
            target = os.path.join(root, d)
            subprocess.run(["chmod", "-R", "777", target], capture_output=True)
            print(f"  [开放 R/W] {d} -> chmod 777")
            
    print("[lock_workspace] 锁定完成：主 Agent 仅能写入 staging/，核心脚本与发布目录已受只读保护。")
    return 0


def unlock_workspace(project_root):
    root = os.path.abspath(project_root)
    print(f"[lock_workspace] 正在解除项目写隔离（恢复开发权限）: {root}")
    if is_windows():
        username = os.environ.get("USERNAME", "Everyone")
        for d in RO_DIRS + RW_DIRS:
            target = os.path.join(root, d)
            if os.path.isdir(target):
                # 恢复继承并赋予完全控制
                cmd = ["icacls", target, "/inheritance:e", "/grant", f"{username}:(OI)(CI)(F)", "/t"]
                subprocess.run(cmd, capture_output=True, text=True)
                print(f"  [恢复 R/W] {d} -> 完全控制 (F)")
    else:
        for d in RO_DIRS + RW_DIRS:
            target = os.path.join(root, d)
            if os.path.isdir(target):
                subprocess.run(["chmod", "-R", "755", target], capture_output=True)
                print(f"  [恢复 R/W] {d} -> chmod 755")
    print("[lock_workspace] 解除完成。")
    return 0


def check_status(project_root):
    root = os.path.abspath(project_root)
    print(f"[lock_workspace] 检查权限状态: {root}")
    for d in RO_DIRS + RW_DIRS:
        target = os.path.join(root, d)
        if not os.path.isdir(target):
            print(f"  - {d}: 目录尚未建立")
            continue
        if is_windows():
            r = subprocess.run(["icacls", target], capture_output=True, text=True)
            first_line = r.stdout.splitlines()[0] if r.stdout.splitlines() else ""
            print(f"  - {d}: {first_line}")
        else:
            mode = oct(os.stat(target).st_mode)[-3:]
            print(f"  - {d}: 权限模式 {mode}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="零信任工作区存储隔离控制工具")
    ap.add_argument("project_root", help="项目根路径")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--lock", action="store_true", help="锁定工作区（实施特权隔离）")
    group.add_argument("--unlock", action="store_true", help="解除锁定（恢复完全读写）")
    group.add_argument("--status", action="store_true", help="查看当前 ACL 状态")
    args = ap.parse_args()

    if args.lock:
        return lock_workspace(args.project_root)
    elif args.unlock:
        return unlock_workspace(args.project_root)
    elif args.status:
        return check_status(args.project_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
