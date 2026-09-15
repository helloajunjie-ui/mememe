"""工具注册表（ToolRegistry）与工具分发执行。

设计意图（见设计文档 4.3 / 4.4）：
- 工具 = Agent 可调用的函数，对外以 OpenAI function schema 暴露，对内以 Python 函数或 Go 二进制实现。
- 统一调用协议：LLM 只看工具名和参数，不知道底层语言。
- 分发：python_function → importlib 动态加载；go_binary → 子进程 + stdin/stdout JSON。
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.base import get_meta


def normalize_schema(schema: Any) -> Dict:
    """规范化工具参数 schema 为标准 JSON Schema（type=object）。

    支持两种输入，统一输出标准格式：
    1) 标准格式：{"type":"object","properties":{...},"required":[...]}
    2) 扁平参数表：{"name":{"type":"...","description":"...","required":true}, ...}
       —— 自动包装为 properties，并把字段级 required:true 收集进 required 数组。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    # 判据只看"有没有 properties"：带 properties 就是标准格式。
    # 旧逻辑要求顶层 type=="object"，于是 {"properties":{...}} 这种缺 type 的半成品
    # 会被误判成扁平参数表 → 整块塞进一个名叫 properties 的参数里，且内层属性级
    # required 一个都洗不掉 → 模型 API 严格校验直接 400（整轮请求报废，2026-09-15 实测）。
    if isinstance(schema.get("properties"), dict):
        schema["type"] = "object"
        props = schema["properties"]
        required = _as_required_list(schema.get("required"))
        for k, v in props.items():
            if isinstance(v, dict):
                # 属性级 required 不是合法字段（OpenAI/多数模型严格校验会 400）
                if "required" in v:
                    if v.get("required") is True and k not in required:
                        required.append(k)
                    v.pop("required", None)
                # 递归清洗嵌套层（array items / 嵌套 object），防止同样问题漏网
                _clean_nested_schema(v)
        if required:
            schema["required"] = required
        elif "required" in schema:
            # 非法残留（如 required: 123）必须删掉：留着不动 = 没修，外发仍会 400
            schema.pop("required", None)
        return schema
    # 扁平参数表 → 标准格式
    properties, required = {}, []
    for k, v in schema.items():
        if isinstance(v, dict):
            if v.get("required") is True:
                required.append(k)
            v.pop("required", None)
            properties[k] = v
        else:
            properties[k] = {"type": "string", "description": str(v)}
    out = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


def _as_required_list(value: Any) -> List[str]:
    """required 取值收紧：字符串按单值处理，非 list/tuple 一律丢弃。

    旧写法 list(schema.get("required", [])) 遇到 required="abc" 会拆成 ["a","b","c"]
    —— 静默把坏输入"修"成看似合法但语义错误的结果，比报错更难查。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return []


def _clean_nested_schema(node: Any) -> None:
    """递归清洗 schema 子节点：剥掉属性级 required（收集进所在对象层 required 数组）。"""
    if not isinstance(node, dict):
        return
    # array items → 清洗 items 定义
    if node.get("type") == "array" and isinstance(node.get("items"), dict):
        _clean_nested_schema(node["items"])
        return
    # object 层 → 清洗本层 properties
    if isinstance(node.get("properties"), dict):
        req = _as_required_list(node.get("required"))
        for k, v in node["properties"].items():
            if isinstance(v, dict):
                if "required" in v:
                    if v.get("required") is True and k not in req:
                        req.append(k)
                    v.pop("required", None)
                _clean_nested_schema(v)
        if req:
            node["required"] = req
        elif "required" in node:
            node.pop("required", None)
        return
    # 其他容器字段（additionalProperties 等）也递归兜底
    for v in node.values():
        if isinstance(v, dict):
            _clean_nested_schema(v)
        elif isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    _clean_nested_schema(it)


def schema_errors(schema: Any, path: str = "", top: bool = True) -> List[str]:
    """结构性校验：返回会让模型 API 400 的形状问题（空列表 = 合法）。

    代价不对称：坏 schema 不是"某个工具用不了"，而是**整轮请求 400** —— 所有工具和
    整段上下文一起报废，AI 连报错都说不出来。所以宁可本地先剥离/上报，绝不外发。
    """
    errs: List[str] = []
    if not isinstance(schema, dict):
        return [f"{path} 不是对象"]
    if top and schema.get("type") != "object":
        errs.append(f"{path} 顶层 type 不是 object（{schema.get('type')!r}）")
    props = schema.get("properties")
    if props is not None and not isinstance(props, dict):
        errs.append(f"{path}/properties 不是对象")
    elif isinstance(props, dict):
        for k, v in props.items():
            if not isinstance(v, dict):
                errs.append(f"{path}/properties/{k} 不是对象")
                continue
            if "required" in v:
                errs.append(
                    f"{path}/properties/{k} 属性里带了 required={v['required']!r}（须放对象级数组）")
            errs += schema_errors(v, f"{path}/properties/{k}", top=False)
    req = schema.get("required")
    if req is not None and not isinstance(req, list):
        errs.append(f"{path}/required 不是数组（{req!r}）")
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, dict):
            errs.append(f"{path}/items 不是对象")
        else:
            errs += schema_errors(items, f"{path}/items", top=False)
    return errs


class ToolRegistry:
    # 核心工具：每轮常驻（schema 全量给 LLM）；其余为长尾工具（世界书：目录可见，按需加载 schema）
    CORE_TOOLS = {
        "net_search", "net_fetch", "fs_list", "fs_read", "fs_search", "fs_stat",
        "memory_write", "memory_search", "cmd_run", "cmd_batch", "sys_check", "ws_mkdir", "ws_write",
        "net_quality", "disk_report", "git_multi_status",
        "ctx_search", "sys_probe", "app_probe", "self_restart",
        "mcp_key_set", "mcp_key_list", "mcp_key_remove",
        "mcp_deps", "mcp_install",
    }

    @staticmethod
    def sanitize_schemas(schemas: List[Dict],
                         on_warn: Optional[Callable[[str], None]] = None) -> List[Dict]:
        """LLM 调用前的最后一道闸。

        每条 function schema 过一遍 normalize_schema（洗掉属性级 required 等非法形状）；
        洗完仍不合法 → 整条剔除并留痕。取舍明确：宁少一个工具，也不要整轮请求 400。
        """
        cleaned: List[Dict] = []
        for s in schemas or []:
            try:
                fn = s.get("function") if isinstance(s, dict) else None
                if not isinstance(fn, dict):
                    if on_warn:
                        on_warn(f"[schema] 跳过无法识别的工具条目: {type(s).__name__}")
                    continue
                name = fn.get("name") or "?"
                raw_errs = schema_errors(fn.get("parameters"), name)
                fn["parameters"] = normalize_schema(fn.get("parameters"))
                errs = schema_errors(fn["parameters"], name)
                if errs:
                    if on_warn:
                        on_warn(f"[schema] 剔除非法工具 {name}: {errs[:2]}")
                    continue
                if raw_errs:
                    # 声明时就是脏的、已被本地修好 —— 必须留痕。
                    # 正是这种沉默修复让 2026-09-15 那次 400 查了很久。
                    if on_warn:
                        on_warn(f"[schema] 已修正工具 {name} 的 schema: {raw_errs[:2]}")
                cleaned.append(s)
            except Exception as e:  # noqa: BLE001
                if on_warn:
                    on_warn(f"[schema] 工具条目处理异常已跳过: {type(e).__name__}: {e}")
                continue
        return cleaned


    def __init__(self, path: str = "data/registry.json", tools_dir: str = "tools"):
        self.path = path
        self.tools_dir = tools_dir
        self.tools: Dict[str, Dict] = {}      # name -> registry entry
        self._fns: Dict[str, Callable] = {}   # name -> python 函数（动态加载）
        self._mod_mtime: Dict[str, float] = {}  # name -> 工具源码 mtime（热更新检测用）
        self.mcp: Any = None                  # MCP 管理器（McpManager，agent 启动时注入）

    # ---------- 持久化 ----------
    def load(self) -> bool:
        if not os.path.exists(self.path):
            return False
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.tools = data.get("tools", {})
        return True

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"tools": self.tools}, f, ensure_ascii=False, indent=2)

    # ---------- 内置工具发现 ----------
    def discover_builtin(self) -> int:
        """扫描 tools/src/python/*.py，动态加载带 @tool 装饰器的函数。"""
        src_dir = Path(self.tools_dir) / "src" / "python"
        if not src_dir.exists():
            return 0
        count = 0
        sys.path.insert(0, str(Path(self.tools_dir).parent))  # 让 "tools.base" 可导入
        for py in sorted(src_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            mod_name = f"tools.src.python.{py.stem}"
            try:
                mod = importlib.import_module(mod_name)
                for _, fn in inspect.getmembers(mod, inspect.isfunction):
                    meta = get_meta(fn)
                    if meta:
                        self._register_python(meta, fn)
                        count += 1
            except Exception as e:  # noqa: BLE001
                print(f"[registry] 加载 {py.name} 失败: {e}")
        self.save()
        return count

    def _register_python(self, meta: Dict, fn: Callable) -> None:
        name = meta["name"]
        source_path = f"{self.tools_dir}/src/python/{fn.__module__.split('.')[-1]}.py"
        desc = meta["description"]
        self.tools[name] = {
            "name": name,
            "name_zh": self._name_zh(name),
            "group": str(meta.get("group") or "").strip(),
            "description": desc,
            "keywords": self._TOOL_KEYWORDS.get(name) or self._extract_keywords(f"{self._name_zh(name)} {desc}"),
            "parameters": normalize_schema(meta["parameters"]),
            "impl": {
                "language": meta.get("language", "python"),
                "type": "python_function",
                "source": source_path,
                "entry": fn.__name__,
                "deps": meta.get("deps", []),
                "hash": self._hash_file(source_path),
            },
            "version": 1,
            "runtime": {
                "status": "active",
                "last_tested": None,
                "test_results": {"passed": 0, "failed": 0},
            },
        }
        self._fns[name] = fn
        try:
            self._mod_mtime[name] = os.path.getmtime(source_path)
        except OSError:
            self._mod_mtime[name] = 0.0

    # ---------- 工具热更新（源码 mtime 变化 → reload 模块，无需重启白绫） ----------
    def reload_if_changed(self, name: str) -> bool:
        """工具源码文件变化时重新加载对应模块并刷新 fn/schema。返回是否发生了重载。
        改 tools/src/python/*.py 后，白绫下一次调用即用新代码，无需重启进程。"""
        impl = (self.tools.get(name) or {}).get("impl") or {}
        src = impl.get("source")
        if not src or not os.path.exists(src):
            return False
        try:
            mtime = os.path.getmtime(src)
        except OSError:
            return False
        if self._mod_mtime.get(name) == mtime:
            return False
        mod_name = f"tools.src.python.{Path(src).stem}"
        try:
            if mod_name in sys.modules:
                mod = importlib.reload(sys.modules[mod_name])
            else:
                mod = importlib.import_module(mod_name)
            entry = impl.get("entry") or ""
            fn = getattr(mod, entry, None) if entry else None
            if not fn or not callable(fn):
                print(f"[registry] 热更新 {name} 失败: 模块中找不到入口函数 {entry}")
                return False
            self._fns[name] = fn
            self._mod_mtime[name] = mtime
            # 同步刷新 desc/parameters（工具元信息也变了时）
            meta = get_meta(fn)
            if meta:
                self.tools[name]["description"] = meta.get("description") or impl.get("description", "")
                params = normalize_schema(meta.get("parameters"))
                if params != self.tools[name].get("parameters"):
                    self.tools[name]["parameters"] = params
                    self.tools[name]["version"] = int(self.tools[name].get("version", 1)) + 1
            print(f"[registry] 热更新工具 {name}（{Path(src).name}）")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[registry] 热更新 {name} 失败: {type(e).__name__}: {e}")
            return False

    # ---------- MCP 工具注册（见设计文档 5.37） ----------
    def register_mcp(self, tool_name: str, server: str, tool: str,
                     description: str = "", schema: Optional[Dict] = None) -> None:
        """注册一个 MCP server 暴露的工具为白绫工具（impl.type="mcp"，执行转发调用）。"""
        import re
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server}_{tool}")
        self.tools[tool_name] = {
            "name": tool_name,
            "name_zh": f"MCP·{server}·{tool}",
            "description": description or f"调用 MCP server {server} 的 {tool} 工具",
            "keywords": [server, tool, "mcp"],
            "parameters": normalize_schema(schema or {"type": "object", "properties": {}}),
            "impl": {"type": "mcp", "server": server, "tool": tool},
            "version": 1,
            "runtime": {"status": "active", "last_tested": None,
                        "test_results": {"passed": 0, "failed": 0}},
            "source": "mcp",
        }
        _ = safe  # 命名已在 sync 层完成，此处保留工具原名映射即可
        self._fns.pop(tool_name, None)

    def remove_mcp_all(self) -> int:
        """清空所有 MCP 来源工具（server 删除/重同步前调用）。返回移除数量。"""
        removed = [n for n, t in self.tools.items() if t.get("source") == "mcp"]
        for n in removed:
            self.tools.pop(n, None)
            self._fns.pop(n, None)
        return len(removed)

    def remove_mcp_server(self, server: str) -> int:
        """只移除指定 MCP server 注册的工具（保留其他 server 的）。返回移除数量。"""
        removed = [n for n, t in self.tools.items()
                   if t.get("source") == "mcp" and t.get("impl", {}).get("server") == server]
        for n in removed:
            self.tools.pop(n, None)
            self._fns.pop(n, None)
        return len(removed)

    _TOOL_ZH = {
        "net_search": "网络搜索", "net_fetch": "网页抓取", "net_download": "文件下载",
        "cmd_run": "系统命令", "fs_list": "列目录", "fs_read": "读文件", "fs_search": "文件搜索",
        "fs_stat": "文件信息", "fs_delete": "删文件", "fs_move": "移动文件", "fs_copy": "复制文件",
        "memory_write": "写记忆", "memory_search": "查记忆", "method_learn": "沉淀方法论",
        "ctx_search": "上下文检索",
        "sys_probe": "综合探查", "sys_check": "系统巡检", "net_quality": "网络质量", "disk_report": "磁盘报告",
        "git_multi_status": "仓库状态", "app_probe": "软件探查", "proc_list": "进程列表", "proc_kill": "结束进程",
        "self_restart": "请求重启",
        "tool_create": "自建工具",
        "tool_import": "导入工具", "tool_acquire": "获取工具", "tool_scan": "扫描工具",
        "ws_mkdir": "建工作目录", "ws_write": "写工作文件", "word_count": "文本统计",
        "self_backup": "自我备份", "self_clone": "自我复制",
        "self_restore": "自我恢复",
        "lan_scan": "局域网扫描", "lan_portscan": "端口探测",
        "cloud_webdav_list": "云盘列目录", "cloud_webdav_read": "云盘读文件",
        "cloud_webdav_write": "云盘写文件", "cloud_webdav_delete": "云盘删文件",
        "cloud_webdav_mkdir": "云盘建目录",
        "feishu_send_text": "飞书发文本", "feishu_send_post": "飞书发富文本",
        "feishu_list_chat": "飞书列群聊", "feishu_bitable_list": "飞书多维表格读记录",
        "feishu_bitable_create": "飞书多维表格加记录", "feishu_docx_read": "飞书读云文档",
        "feishu_docx_create": "飞书建云文档", "feishu_whoami": "飞书凭据校验",
        # 2026-09-15 补：此前这些工具没有中文名，世界书目录里显示成英文名
        "account_add": "新增账户凭据", "account_list": "列出账户名",
        "account_remove": "删除账户凭据", "account_test": "校验账户凭据",
        "dropbox_list": "Dropbox 列目录", "dropbox_read": "Dropbox 读文件",
        "dropbox_write": "Dropbox 写文件", "dropbox_delete": "Dropbox 删文件",
        "dropbox_mkdir": "Dropbox 建目录", "dropbox_whoami": "Dropbox 凭据校验",
        "gdrive_list": "Google Drive 列目录", "gdrive_read": "Google Drive 读文件",
        "gdrive_write": "Google Drive 写文件", "gdrive_delete": "Google Drive 删文件",
        "gdrive_mkdir": "Google Drive 建目录", "gdrive_whoami": "Google Drive 凭据校验",
        "llm_health": "模型健康与容灾",
        "mcp_connect": "连接 MCP 服务器", "mcp_disconnect": "移除 MCP 配置",
        "mcp_list": "查看 MCP 工具", "mcp_scan": "同步 MCP 工具",
        "mcp_key_set": "保存软件接口凭据", "mcp_key_list": "查看凭据键名",
        "mcp_key_remove": "删除软件接口凭据",
        "mcp_deps": "评估软件接口依赖", "mcp_install": "安装软件接口依赖",
        "vision_look": "看图描述", "fs_smart_read": "精准读文件片段",
        "tool_find": "工具索引查询", "tool_health_audit": "工具库健康自检",
    }

    # 工具分组（多级目录：功能域 → 工具条目）
    _TOOL_GROUPS = {
        "网络": ["net_search", "net_fetch", "net_download", "net_quality"],
        "局域网": ["lan_scan", "lan_portscan"],
        "个人云": ["cloud_webdav_list", "cloud_webdav_read", "cloud_webdav_write",
                   "cloud_webdav_delete", "cloud_webdav_mkdir"],
        "飞书": ["feishu_send_text", "feishu_send_post", "feishu_list_chat",
                 "feishu_bitable_list", "feishu_bitable_create",
                 "feishu_docx_read", "feishu_docx_create", "feishu_whoami"],
        "文件系统": ["fs_list", "fs_read", "fs_search", "fs_stat", "fs_delete", "fs_move", "fs_copy"],
        "系统与执行": ["cmd_run", "cmd_batch", "sys_check", "disk_report", "sys_probe", "app_probe", "proc_list", "proc_kill", "self_restart"],
        "开发者": ["git_multi_status"],
        "记忆与经验": ["memory_write", "memory_search", "method_learn"],
        "工作区": ["ws_mkdir", "ws_write"],
        "自我维护": ["self_backup", "self_clone", "self_restore"],
        "工具工程": ["tool_create", "tool_import", "tool_acquire", "tool_scan",
                     "tool_find", "tool_health_audit",
                     "mcp_connect", "mcp_disconnect", "mcp_list", "mcp_scan",
                     "mcp_key_set", "mcp_key_list", "mcp_key_remove",
                     "mcp_deps", "mcp_install"],
        "文本处理": ["word_count"],
    }

    # ---- 功能域归类（单一真源）----
    # 解析优先级：工具源码里声明的 group（显式） > _TOOL_GROUPS（手工表，少量特例）
    #             > _TOOL_PREFIX_GROUPS（前缀规则，覆盖同族成批工具） > "其他"
    # 规矩：新工具必须被前三级任一级命中；落进"其他"= 未归类，tool_health_audit 会告警。
    # 归档规范见 docs/工具归档规范.md。世界书目录与 tool_find 都只调 group_of()，不自带第二份表。
    _TOOL_PREFIX_GROUPS = (
        ("mcp_blender_", "MCP·blender"),
        ("mcp_godot_", "MCP·godot"),
        ("mcp_", "MCP"),
        ("cloud_webdav_", "个人云"),
        ("dropbox_", "个人云"),
        ("gdrive_", "个人云"),
        ("feishu_", "飞书"),
        ("fs_", "文件系统"),
        ("net_", "网络"),
        ("git_", "开发者"),
        ("lan_", "局域网"),
        ("memory_", "记忆与经验"),
        ("method_", "记忆与经验"),
        ("ws_", "工作区"),
        ("self_", "自我维护"),
        ("tool_", "工具工程"),
        ("account_", "凭据库"),
        ("proc_", "系统与执行"),
        ("ctx_", "上下文"),
        ("llm_", "模型"),
        ("vision_", "多模态"),
        ("word_", "文本处理"),
    )

    # 世界书分组渲染顺序（未登记的组按名称排在其后，"其他"永远垫底）
    _GROUP_ORDER = [
        "网络", "局域网", "个人云", "飞书",
        "文件系统", "工作区", "系统与执行",
        "记忆与经验", "上下文", "文本处理",
        "凭据库", "模型", "多模态",
        "自我维护", "工具工程",
        "MCP·godot", "MCP·blender", "MCP", "其他",
    ]

    # 世界书触发关键词（手动配置，覆盖用户自然语言说法；自动提取仅作兜底）
    _TOOL_KEYWORDS = {
        "net_search": ["搜索", "查一下", "查找", "查询", "检索", "搜", "资讯", "了解下"],
        "net_fetch": ["抓取", "打开网页", "读取网页", "网址", "网页内容", "链接内容"],
        "net_download": ["下载", "下载视频", "保存文件", "拉文件", "下个"],
        "cmd_run": ["执行命令", "运行命令", "命令行", "powershell", "cmd", "终端"],
        "fs_list": ["列目录", "看看目录", "有哪些文件", "目录下"],
        "fs_read": ["读取", "打开文件", "看文件", "文件内容", "读一下"],
        "fs_search": ["搜索文件", "找文件", "文件名", "定位文件"],
        "fs_stat": ["文件信息", "文件大小", "文件详情", "元信息"],
        "fs_delete": ["删除文件", "删掉", "移除文件", "清理文件", "删目录", "删除"],
        "fs_move": ["移动文件", "重命名", "改名", "挪文件", "移动", "剪切"],
        "fs_copy": ["复制文件", "拷贝", "备份文件", "复制到", "复制"],
        "memory_write": ["记住", "记忆", "记录", "学习", "长期记住", "存档"],
        "memory_search": ["查记忆", "搜记忆", "记忆里", "之前查过", "以前", "我记得", "存档", "查过"],
        "method_learn": ["方法论", "沉淀", "经验教训", "总结方法", "教训"],
        "sys_probe": ["综合探查", "环境探测", "全面体检", "探测环境", "机器全貌", "系统快照",
                      "环境", "系统信息", "配置", "内存", "磁盘", "机器", "已安装", "软件",
                      "有哪些软件", "环境信息", "系统工具"],
        "app_probe": ["软件探查", "软件安装", "装了哪些软件", "装了什么软件", "软件在哪",
                      "查找软件", "定位软件", "软件路径", "office", "office在哪", "办公软件",
                      "有没有装", "app_probe"],
        "self_restart": ["重启", "重启白绫", "更新后重启", "无感重启", "冷启动", "自动重启",
                         "self_restart", "重新加载核心"],
        "cmd_batch": ["批量命令", "命令集合", "多条命令", "连续执行", "批量执行", "cmd_batch",
                      "一次执行多条", "批量探测", "批量查询"],
        "sys_check": ["电脑状态", "系统体检", "系统状态", "卡不卡", "电脑卡", "体检", "巡检",
                      "看看电脑", "查看电脑", "电脑怎么样", "系统检查", "sys_check", "装了哪些软件",
                      "开机启动项", "网络通不通", "内存", "磁盘空间"],
        "net_quality": ["网卡不卡", "网络卡", "断网", "网络慢", "连不上", "延迟", "网络质量", "网速",
                        "net_quality", "网络测试", "ping", "丢包", "DNS"],
        "disk_report": ["C盘满了", "C盘满", "磁盘不够", "磁盘空间", "清理磁盘", "空间去哪了", "缓存",
                        "disk_report", "临时文件", "回收站", "磁盘清理", "释放空间", "磁盘报告"],
        "git_multi_status": ["仓库状态", "项目状态", "看看项目", "哪些有改动", "git 状态", "仓库改动",
                             "git_multi_status", "批量仓库", "分支状态", "未提交"],
        "proc_list": ["进程列表", "看进程", "有哪些进程", "进程", "运行的程序", "任务管理器"],
        "proc_kill": ["结束进程", "杀进程", "关闭程序", "卡死", "无响应", "强制结束", "kill进程"],
        "lan_scan": ["局域网", "扫描主机", "活跃主机", "网段", "内网", "同网段", "在线设备", "局域网扫描"],
        "lan_portscan": ["端口探测", "开放端口", "扫端口", "端口扫描", "哪些端口", "端口"],
        "cloud_webdav_list": ["云盘", "webdav", "飞牛", "群晖", "nextcloud", "坚果云", "列目录", "nas", "威联通"],
        "cloud_webdav_read": ["云盘", "webdav", "飞牛", "群晖", "nextcloud", "坚果云", "读文件", "nas", "读取云盘"],
        "cloud_webdav_write": ["云盘", "webdav", "飞牛", "群晖", "nextcloud", "坚果云", "写文件", "nas", "上传云盘", "保存到云盘"],
        "cloud_webdav_delete": ["云盘", "webdav", "飞牛", "群晖", "nextcloud", "坚果云", "删文件", "nas", "删除云盘"],
        "cloud_webdav_mkdir": ["云盘", "webdav", "飞牛", "群晖", "nextcloud", "坚果云", "建目录", "nas", "创建文件夹"],
        "feishu_send_text": ["飞书", "lark", "发消息", "发飞书", "通知到飞书", "发文本"],
        "feishu_send_post": ["飞书", "lark", "发富文本", "发标题消息", "飞书通知"],
        "feishu_list_chat": ["飞书", "lark", "列群聊", "飞书群", "有哪些群"],
        "feishu_bitable_list": ["飞书", "多维表格", "bitable", "读表格", "查记录", "表格记录"],
        "feishu_bitable_create": ["飞书", "多维表格", "bitable", "加记录", "新增记录", "写表格"],
        "feishu_docx_read": ["飞书", "云文档", "docx", "读文档", "文档内容"],
        "feishu_docx_create": ["飞书", "云文档", "docx", "建文档", "新建文档", "创建文档"],
        "feishu_whoami": ["飞书", "凭据", "校验飞书", "飞书连通", "测试飞书"],
        "tool_create": ["创建工具", "新工具", "写个工具", "自建", "编写工具"],
        "tool_import": ["导入工具", "继承", "前辈", "收集工具", "旧工具"],
        "tool_acquire": ["获取工具", "拉取", "克隆", "github", "git仓库"],
        "tool_scan": ["扫描工具", "发现工具", "tool_scan"],
        "tool_find": ["工具索引", "工具目录", "有哪些工具", "有没有工具", "能做什么",
                      "有什么工具", "检索工具", "工具清单", "工具库"],
        "tool_health_audit": ["工具健康", "工具库健康", "工具自检", "工具体检", "工具是否正常"],
        "ws_mkdir": ["建目录", "任务目录", "创建文件夹", "工作区"],
        "ws_write": ["保存", "写入文件", "写入", "生成文件", "写到"],
        "word_count": ["统计", "字数", "词频", "word_count"],
        "self_backup": ["备份", "保存状态", "快照", "备份自己", "保险"],
        "self_clone": ["复制", "迁移", "换个地方", "克隆自己", "复制到", "生姐妹", "姐妹实例", "分身", "分叉"],
        "self_restore": ["恢复", "还原", "复活", "从备份"],
    }

    # 工具功能域（多级目录一级分类）
    def _name_zh(self, name: str) -> str:
        return self._TOOL_ZH.get(name, name)

    @staticmethod
    def _extract_keywords(text: str, maxn: int = 8) -> List[str]:
        """从工具名+描述提取触发关键词（2~4 字中文片段去重）。"""
        import re
        stops = {"一个", "这个", "那个", "什么", "怎么", "进行", "可以", "需要", "文件", "工具",
                 "内容", "返回", "指定", "支持", "如果", "用于", "以及", "自动", "当前", "系统",
                 "获取", "网络", "搜索"}
        kws: List[str] = []
        for w in re.findall(r"[\u4e00-\u9fff]{2,4}", text or ""):
            if w not in stops and w not in kws:
                kws.append(w)
        return kws[:maxn]

    @staticmethod
    def _hash_file(path: str) -> str:
        try:
            with open(path, "rb") as f:
                return "sha256:" + hashlib.sha256(f.read()).hexdigest()[:16]
        except Exception:  # noqa: BLE001
            return "unknown"

    # ---------- 查询 ----------
    def get(self, name: str) -> Optional[Dict]:
        return self.tools.get(name)

    def list_active(self) -> List[Dict]:
        return [t for t in self.tools.values() if t.get("runtime", {}).get("status") == "active"]

    def to_openai_schemas(self, names: Optional[List[str]] = None) -> List[Dict]:
        """转成 OpenAI function calling 格式。names=None 默认返回核心工具（世界书：长尾按需加载）。"""
        active = self.list_active()
        if names is not None:
            name_set = set(names)
            active = [t for t in active if t["name"] in name_set]
        else:
            active = [t for t in active if t["name"] in self.CORE_TOOLS or t["name"] == "method_learn"]
        schemas = []
        for t in active:
            schemas.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": normalize_schema(t["parameters"]),
                },
            })
        return schemas

    def get_schema(self, name: str) -> Optional[Dict]:
        """单个工具 schema（世界书按需加载用）。"""
        t = self.tools.get(name)
        if not t or t.get("runtime", {}).get("status") != "active":
            return None
        return {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": normalize_schema(t["parameters"]),
            },
        }

    def is_core(self, name: str) -> bool:
        return name in self.CORE_TOOLS

    # ---------- 功能域归类（单一真源） ----------

    @classmethod
    def resolve_group(cls, name: str, declared: str = "") -> str:
        """按类级规则判定功能域（不依赖实例）：显式声明 > 手工表 > 前缀规则 > 其他。

        单一真源——世界书目录（to_index_json）、工具检索（tool_find）、健康自检
        （tool_health_audit）都调这里，任何调用方不得自带第二份分组表
        （此前工具目录存在两套分组，一套 82% 堆在"其他"）。
        """
        if (declared or "").strip():
            return declared.strip()
        for gname, names in cls._TOOL_GROUPS.items():
            if name in names:
                return gname
        for prefix, gname in cls._TOOL_PREFIX_GROUPS:
            if name.startswith(prefix):
                return gname
        return "其他"

    def group_of(self, name: str) -> str:
        """判定本实例内某工具的功能域（读底表显式声明，其余交给 resolve_group）。"""
        entry = self.tools.get(name) or {}
        return self.resolve_group(name, str(entry.get("group") or ""))

    def _ordered_groups(self) -> List[str]:
        """按 _GROUP_ORDER 排好序的功能域清单；在用但未登记的组排"其他"之前。"""
        used: List[str] = []
        for t in self.list_active():
            g = self.group_of(t["name"])
            if g not in used:
                used.append(g)
        order = [g for g in self._GROUP_ORDER if g in used and g != "其他"]
        rest = sorted(g for g in used if g not in self._GROUP_ORDER and g != "其他")
        return order + rest + (["其他"] if "其他" in used else [])

    def to_index_json(self, collapse_mcp: bool = True) -> List[Dict]:
        """工具世界书多级目录（JSON 结构，适配底层逻辑）：
        [{"group","core":[{name,name_zh}],"ext":[{name,name_zh,keywords}],"collapsed","count"}]。

        归类走 group_of()（单一真源）；MCP 分组默认折叠——只给组名 + 数量 + 少量示例，
        明细用 tool_find(group="MCP·godot") 查（179 条 MCP 工具名曾占世界书近 1/3 字符）。
        """
        active = {t["name"]: t for t in self.list_active()}
        buckets: Dict[str, List[str]] = {}
        for n in active:
            buckets.setdefault(self.group_of(n), []).append(n)
        out = []
        for gname in self._ordered_groups():
            names = sorted(buckets.get(gname, []))
            if not names:
                continue
            if collapse_mcp and gname.startswith("MCP"):
                out.append({
                    "group": gname, "core": [], "ext": [], "collapsed": True,
                    "count": len([n for n in names if not self.is_core(n)]),
                    "sample": [{"name": n, "name_zh": active[n].get("name_zh", "")}
                               for n in names[:3]],
                })
                continue
            core = [{"name": n, "name_zh": active[n].get("name_zh", "")}
                    for n in names if self.is_core(n)]
            ext = [{"name": n, "name_zh": active[n].get("name_zh", ""),
                    "keywords": active[n].get("keywords", [])[:4]}
                   for n in names if not self.is_core(n)]
            if core or ext:
                out.append({"group": gname, "core": core, "ext": ext,
                            "collapsed": False, "count": len(core) + len(ext)})
        return out

    def to_index(self) -> str:
        """工具世界书多级目录（Markdown 渲染视图，给 LLM 展示用）：从 JSON 结构渲染。"""
        lines = ["（多级目录：功能域 → 工具条目；核心=常驻可用，扩展=说工具名自动加载；"
                 "查明细/按能力找工具用 tool_find）"]
        for g in self.to_index_json():
            parts = []
            if g.get("collapsed"):
                parts.append(f"{g['count']} 个工具（折叠，tool_find(group=\"{g['group']}\") 查明细）")
            else:
                if g["core"]:
                    parts.append("核心:" + ", ".join(f"{c['name']}({c['name_zh']})"
                                                     for c in g["core"]))
                if g["ext"]:
                    parts.append("扩展:" + "; ".join(
                        f"{e['name']}({e['name_zh']})触发{'/'.join(e['keywords'])}"
                        for e in g["ext"]))
            if parts:
                lines.append(f"▶ {g['group']}  " + "  ".join(parts))
        return "\n".join(lines)

    def suggest_ext(self, text: str, loaded: set) -> List[str]:
        """世界书触发：输入文本命中长尾工具关键词 → 建议加载（返回未加载的工具名）。"""
        low = text.lower()
        names = []
        for t in self.list_active():
            n = t["name"]
            if n in self.CORE_TOOLS or n in loaded:
                continue
            if any(k and k.lower() in low for k in t.get("keywords", [])):
                names.append(n)
        return names

    # ---------- 执行分发 ----------
    def execute(self, name: str, args: Dict) -> Dict:
        """分发执行工具。返回 {"ok": bool, "result": ...} 或 {"ok": False, "error": ...}"""
        entry = self.tools.get(name)
        if not entry:
            # MCP 工具不在注册表（v2.7 起按需激活注入）：mcp_<server>_<tool> 兜底转发给 MCP 服务
            if name.startswith("mcp_") and self.mcp is not None:
                srv = self.mcp.match_server(name)
                if srv:
                    tool = name[len(f"mcp_{srv}_"):]
                    return self.mcp.call(srv, tool, args)
            return {"ok": False, "error": f"工具不存在: {name}"}
        impl = entry["impl"]
        if impl["type"] == "python_function":
            result = self._execute_python(name, args)
            # 工具创建/导入成功后：先 load 磁盘（含 Go 工具等非 Python 条目），
            # 再扫描注册 Python 工具，保存时保留全部（含刚创建的 Go 二进制工具）。
            if name in ("tool_create", "tool_import") and result.get("ok"):
                self.load()
                self.discover_builtin()
            # MCP 工具同步/移除写入磁盘后，重载生效（MCP 工具命名 mcp_<server>_<tool>）
            if name in ("mcp_scan", "mcp_disconnect") and result.get("ok"):
                self.load()
            return result
        if impl["type"] == "go_binary":
            return self._execute_go(name, args, impl)
        if impl["type"] == "mcp":
            if self.mcp is None:
                return {"ok": False, "error": f"MCP 管理器未初始化（server={impl.get('server')}）"}
            return self.mcp.call(impl.get("server", ""), impl.get("tool", ""), args)
        return {"ok": False, "error": f"未知工具类型: {impl['type']}"}

    def _execute_python(self, name: str, args: Dict) -> Dict:
        self.reload_if_changed(name)  # 热更新：源码变化即重载，无需重启
        fn = self._fns.get(name)
        if fn is None:
            return {"ok": False, "error": f"工具未加载: {name}"}
        try:
            result = fn(**args)
            return {"ok": True, "result": result}
        except TypeError as e:
            return {"ok": False, "error": f"参数错误: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"执行异常: {type(e).__name__}: {e}"}

    def _execute_go(self, name: str, args: Dict, impl: Dict) -> Dict:
        """Go 工具：子进程 + stdin/stdout JSON 协议。"""
        binary = impl.get("binary")
        if not binary or not os.path.exists(binary):
            return {"ok": False, "error": f"Go 二进制缺失: {binary}，需要先编译"}
        try:
            payload = json.dumps({"args": args}, ensure_ascii=False)
            proc = subprocess.run(
                [binary],
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": f"Go 工具退出码 {proc.returncode}: {proc.stderr[:500]}"}
            out = json.loads(proc.stdout.strip())
            return out
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Go 工具执行超时（>30s）"}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Go 工具输出非 JSON: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Go 工具执行异常: {e}"}


if __name__ == "__main__":
    reg = ToolRegistry()
    n = reg.discover_builtin()
    print(f"发现 {n} 个内置工具")
    print(json.dumps(reg.to_openai_schemas(), ensure_ascii=False, indent=2))
