# -*- coding: utf-8 -*-
"""走 tool_create 的官方准入路径注册 gh_explore，源码从磁盘读（免去重贴）。

调用的是 tool_create 内部的 _create_python（含 契约关/一致性关/实弹关 三关），
等价于 tool_create(name='gh_explore', code=<磁盘源码>, language='python')。
"""
import json
import sys
from pathlib import Path

ROOT = Path(r"F:\me\self-agent")
sys.path.insert(0, str(ROOT))

from tools.src.python.tool_create import _create_python  # noqa: E402

SRC = ROOT / "tools" / "src" / "python" / "gh_explore.py"
code = SRC.read_text(encoding="utf-8")

try:
    res = _create_python("gh_explore", code, {"mode": "probe", "repo": "getzep/graphiti"})
except Exception as exc:  # noqa: BLE001
    res = {"exception": "%s: %s" % (type(exc).__name__, exc)}

# ensure_ascii=True 规避 Windows GBK 控制台编码问题
print(json.dumps(res, ensure_ascii=True, indent=1, default=str))
