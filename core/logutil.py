# -*- coding: utf-8 -*-
"""core/logutil.py —— 轻量日志轮转（无依赖，进程内调用）。

为什么需要：`data/operation.log`（8MB）、`logs/agent.log`（3.5MB）、`logs/cmd.log`（3.1MB）
原本全都没有上限，按 +1MB/天 的速度无限增长。日志一大，grep 复盘和出错排查都会变慢。

策略（简单优先，够用即可）：
- 写之前查大小，超过 max_bytes 就把当前文件挪成 `<path>.1`（只留 1 份，覆盖旧的）；
- 轮转失败（被占用/权限）不抛异常，继续往后写——宁可日志大，也不能因为轮转把主流程打断；
- 并发写场景下最多丢一行（挪文件瞬间另一个写者打开的是旧 inode），可接受。
"""
from __future__ import annotations

import os

DEFAULT_MAX_BYTES = 8 * 1024 * 1024   # 8MB
DEFAULT_KEEP = 1                      # 只留 .1 一份


def rotate_if_needed(path: str, max_bytes: int = DEFAULT_MAX_BYTES,
                     keep: int = DEFAULT_KEEP) -> bool:
    """文件超限则轮转，返回是否发生了轮转。"""
    try:
        if not path or not os.path.exists(path):
            return False
        if os.path.getsize(path) <= max_bytes:
            return False
        if keep <= 1:
            os.replace(path, path + ".1")
            return True
        for i in range(keep, 0, -1):
            src = path if i == 1 else f"{path}.{i - 1}"
            if os.path.exists(src):
                os.replace(src, f"{path}.{i}")
        return True
    except OSError:
        return False


def append_line(path: str, line: str, max_bytes: int = DEFAULT_MAX_BYTES,
                keep: int = DEFAULT_KEEP, encoding: str = "utf-8") -> bool:
    """追加一行（写前自动检查轮转）。返回是否写成功。"""
    rotate_if_needed(path, max_bytes, keep)
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding=encoding) as f:
            f.write(line + "\n")
        return True
    except OSError:
        return False


def iter_lines(path: str, encoding: str = "utf-8", max_segments: int = 10):
    """按时间顺序遍历日志的全部片段（.N … .1 → 当前文件）。

    2026-10-22：日志加了轮转之后，只读 `operation.log` 的统计脚本会突然"丢了历史"
    （实测 7 天统计从 1109 条掉到 10 条）。读日志一律走这个函数，片段全带上。
    """
    segs = []
    d = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path)
    have_base = os.path.exists(path)
    for i in range(max_segments, 0, -1):
        p = os.path.join(d, f"{base}.{i}")
        if os.path.exists(p):
            segs.append(p)
    if have_base:
        segs.append(path)
    for p in segs:
        try:
            with open(p, "r", encoding=encoding, errors="replace") as f:
                for line in f:
                    yield line
        except OSError:
            continue

