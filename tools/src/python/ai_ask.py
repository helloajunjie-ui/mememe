# -*- coding: utf-8 -*-
"""ai_ask —— 用本机浏览器里已登录的免费网页版 AI 回答问题 / 做联网搜索。

定位（2026-10-25 尼可提醒后建）：
官网网页版问答本身不花钱，且常给出比 API 更强/更新的模型，是零边际成本的第二通道。
手动走一趟要 5~7 次工具往返（开会话/导航/查开关/填字/发送/轮询/关窗），
每次往返都是一次 LLM 调用；封装后压成 1 次。
搜索被墙时的实际解法：不自己抓网页，直接让已登录的网页版 AI 联网搜索并整理后交回。

实现：直连 bsk.exe CLI。browserskill daemon 只开 WebSocket（HTTP 端口会主动断连），
而 bsk.exe 提供完整 CLI + --json，Python subprocess 直接调，不经过 MCP 层。

站点适配（2026-10-25 逐家实测）：
  deepseek  chat.deepseek.com        发送=Enter   有「智能搜索」「深度思考」开关   联网✅
  xinghuo   xinghuo.xfyun.cn/desk    发送=Enter   答案干净、响应快（8s 出首轮）   工具调用✅
  tencent   aistudio.tencent.com     发送=点按钮  Hy4 preview 960k上下文        ❌不联网(知识截止2024-06)
  glm       chatglm.cn               发送=Enter   滑块行为验证墙，多半 need_human
  yuanbao   yuanbao.tencent.com      发送=Enter   未登录
  qwen      chat.qwen.ai             发送=Enter   Qwen3.7-Plus 秒级、答案区 phase-answer 联网✅
  kimi      www.kimi.com             发送=排队墙  输入框是 contenteditable div；免费号被
                                                   「和Kimi聊天的人太多了，订阅会员可进优先队列」
                                                   拦死 → 每次发送都弹提示，不可自动化

已内建的坑：
1. DeepSeek 首页两个开关「深度思考」「智能搜索」，判据是父元素 class 含
   ds-toggle-button--selected。点错不报错，只是模型自述"没有工具可用"→ 白跑一轮。
2. 输入框是受控组件，DOM 赋值不会同步 → 必须 bsk fill（真实输入）+ 发送。
   发送方式因站而异：DeepSeek/星火吃 Enter；腾讯 AI Studio 的 Enter 被页面吞掉
   （defaultPrevented=true 但没发），必须点 .hy-chat-input-send-btn，且要先 fill
   让按钮从 disabled 解禁。
3. **答案选择器必须选到「单条答案」层级，不能选到包住工具栏的外壳**。
   反例（实测）：星火 'Content_content_container_outer' 把「内容写作/文案类型」
   工具栏也包进去，取 innerText 拿到的是问题+思考链+UI卡片混合体；
   正确锚点 'new-line-content_result' 才是模型输出本体（内含 markdown-body）。
   同理问题回显锚点是 'new-line-content_userMessage' —— 用它判定"问题已上屏"
   比在全页搜问题文本可靠。
4. **完成标志必须只在答案容器内查，不能查全页**。反例（实测）：星火页面常驻
   「内容由AI生成」免责声明，全页搜会立刻 done=True，于是在 8.4 秒时把
   半截思考链当成最终答案返回。容器内查不到标志时，退回"长度连续稳定"判定。
5. bsk daemon 子进程继承 pipe 写端，capture_output 会永久 hang
   → 临时文件重定向 + wait(timeout) + taskkill 进程树。
6. 登录墙/滑块不绕过：撞到就返回 need_human=True，交给人手动过。
7. bsk CLI 的 navigate 是【位置参数】：`bsk navigate --session SID URL`，
   写成 `--url URL` 会报 unexpected argument（实测踩过）。
8. 工具层热更新只重载 run 实现，**不刷 schema**：改了 enum（如 site 可选值）
   必须 self_restart 才对调用方生效，否则调用会按旧 enum 校验直接拒绝。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time

from tools.base import tool

BSK = r"F:\me\self-agent\mcp-service\servers\browserskill\bin\bsk.exe"

SITES = {
    "deepseek": "https://chat.deepseek.com/",
    "xinghuo": "https://xinghuo.xfyun.cn/desk",
    "tencent": "https://aistudio.tencent.com/",
    "glm": "https://chatglm.cn/",
    "yuanbao": "https://yuanbao.tencent.com/",
    "qwen": "https://chat.qwen.ai/",
    "kimi": "https://www.kimi.com/",
}

# 每家站的抓取/发送特性。
#   answer_sel: 单条答案本体（取最后一个）
#   q_sel:      问题回显容器（用于 has_q 判定）
#   done:       完成标志（只在答案容器内匹配）
#   cuts:       从该词首次出现处截断（尾部噪音）——答案容器选对了通常用不上
#   strip:      删除含该词且长度 <=30 的行（"共用时 6 秒"这类过程标志）
SPEC = {
    "deepseek": {
        "send": "press",
        "answer_sel": ".ds-markdown",
        "q_sel": "",
        "refmarks": True,
        "done": ["内容由 AI 生成", "内容由AI生成"],
        "toggles": True,
        "cuts": [],
        "strip": [],
        "online": True,
        "note": "有「智能搜索」「深度思考」两个开关，联网搜索默认开",
    },
    "xinghuo": {
        "send": "press",
        "answer_sel": '[class*="new-line-content_result"]',
        "q_sel": '[class*="new-line-content_userMessage"]',
        "done": ["内容由AI生成", "内容由 AI 生成"],
        "toggles": False,
        "cuts": [],
        "strip": ["共用时", "正在思考", "思考中", "记忆溯源", "已深度思考"],
        "online": None,
        "note": "有工具调用能力，中文强，响应快；答案区干净",
    },
    "tencent": {
        "send": "click",
        "send_sel": ".hy-chat-input-send-btn",
        "answer_sel": '[class*="hyc-content-md"]',
        "q_sel": '[class*="agent-chat__list__item--human"]',
        "done": ["以上内容由AI生成", "以上内容由 AI 生成"],
        "toggles": False,
        "cuts": ["以上内容由", "模型详情", "Hy4 preview"],
        "strip": ["思考中", "正在思考", "共用时"],
        "online": False,
        "note": "Hy4 preview，960k 长上下文，重推理强；不联网（知识截止 2024-06）",
    },
    "glm": {
        "send": "press", "answer_sel": "", "q_sel": "", "done": [], "toggles": False,
        "cuts": [], "strip": [], "online": None,
        "note": "滑块行为验证墙，多半需要人工",
    },
    "yuanbao": {
        "send": "press", "answer_sel": "", "q_sel": "", "done": [], "toggles": False,
        "cuts": [], "strip": [], "online": None,
        "note": "未登录",
    },
    "qwen": {
        "send": "press",
        # 思考链(.phase-thinking)与答案区(.phase-answer)分离，取答案区即拿到纯回答
        "answer_sel": ".response-message-content.phase-answer",
        "q_sel": "",
        "refmarks": True,
        "done": [],          # 无可靠容器内完成标志 → 走「长度连续稳定」判定
        "toggles": False,
        "cuts": [],
        "strip": ["已经完成思考", "正在思考", "思考中"],
        "online": True,
        "note": "Qwen3.7-Plus 免费；响应快（秒级），答案区干净；textarea + Enter 发送（2026-10-25 实测通）",
    },
    "kimi": {
        "send": "press",
        "answer_sel": ".markdown",
        "q_sel": "",
        "refmarks": False,
        "done": [],
        "toggles": False,
        "cuts": [],
        "strip": [],
        "online": True,
        "note": "K3.1 Pro；输入框是 contenteditable div（非 textarea），且免费号撞排队墙「和Kimi聊天的人太多了，订阅会员可进入优先队列」，每次发送都被拦 → need_human",
    },
}

_WALL_HINTS = ("滑动验证", "人机验证", "验证码", "扫码登录", "请先登录")

_READY_JS = (
    "(() => { const t = document.querySelector('textarea');"
    " return JSON.stringify({ready: !!t,"
    " head: (document.body.innerText || '').slice(0, 300)}); })()"
)

_TOGGLE_STATE = (
    "(() => { const want = '%s';"
    " const els = [...document.querySelectorAll('[class*=ds-toggle-button]')]"
    ".filter(e => (e.innerText || '').trim() === want);"
    " if (!els.length) return JSON.stringify({found: false});"
    " return JSON.stringify({found: true,"
    " on: els[0].className.indexOf('ds-toggle-button--selected') >= 0}); })()"
)

_TOGGLE_CLICK = (
    "(() => { const want = '%s';"
    " const els = [...document.querySelectorAll('[class*=ds-toggle-button]')]"
    ".filter(e => (e.innerText || '').trim() === want);"
    " if (!els.length) return JSON.stringify({found: false});"
    " els[0].click(); return JSON.stringify({found: true, clicked: true}); })()"
)

# 通用探针：按 answer_sel 取「最后一条答案」；has_q 优先用 q_sel 容器判定；
# done 只在答案容器内匹配（全页搜会被常驻免责声明骗）。
_PROBE_JS = (
    "(() => {"
    " const sel = %s, qsel = %s, dones = %s, q = %s;"
    " const body = (document.body.innerText || '');"
    " let el = null, n = 0;"
    " if (sel) { const l = [...document.querySelectorAll(sel)]; n = l.length;"
    "   el = l.length ? l[l.length - 1] : null; }"
    " const txt = el ? (el.innerText || '') : body;"
    " let has_q = true;"
    " if (qsel) { const u = [...document.querySelectorAll(qsel)];"
    "   has_q = u.some(e => (e.innerText || '').indexOf(q) >= 0); }"
    " else if (q) { has_q = body.indexOf(q) >= 0; }"
    " const done = dones.some(d => txt.indexOf(d) >= 0);"
    " return JSON.stringify({mode: el ? 'sel' : 'body', has_q: has_q, n: n,"
    " done: done, ans_len: txt.length, body_len: body.length}); })()"
)

# 通用切片：取「问题原文之后」的文本（用于没有稳定 answer_sel 的站点）
_SLICE_JS = (
    "(() => { const q = %s; const t = (document.body.innerText || '');"
    " const i = t.indexOf(q); if (i < 0) return '';"
    " return t.slice(i + q.length); })()"
)


def _bsk(args, timeout=60):
    """跑一条 bsk 命令，返回 (ok, text)。临时文件重定向避免 daemon 继承 pipe 挂死。"""
    if not os.path.exists(BSK):
        return False, "bsk.exe 不存在: %s" % BSK
    out_f = tempfile.mktemp(prefix="bsk_o_", suffix=".log")
    err_f = tempfile.mktemp(prefix="bsk_e_", suffix=".log")
    try:
        with open(out_f, "wb") as fo, open(err_f, "wb") as fe:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            p = subprocess.Popen([BSK] + [str(a) for a in args],
                                 stdout=fo, stderr=fe, creationflags=flags)
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   capture_output=True)
                else:
                    p.kill()
                return False, "bsk 命令超时(%ss): %s" % (timeout, args[0])
        rc = p.returncode
        out = open(out_f, "rb").read().decode("utf-8", "replace").strip()
        err = open(err_f, "rb").read().decode("utf-8", "replace").strip()
        # bsk 会把升级横幅打到输出里（stderr 或 stdout 首行），必须剥掉：
        # 它曾把 evaluate 空结果的回退值污染成整段横幅，被当成 AI 回答返回。
        def _strip_banner(t):
            keep = [ln for ln in t.split("\n")
                    if "new bsk version is available" not in ln
                    and not ln.strip().startswith("Run `bsk update`")]
            return "\n".join(keep).strip()

        out, err = _strip_banner(out), _strip_banner(err)
        # 成功时空 stdout 就是真·空（例如 evaluate 没命中元素返回 ''），
        # 不能回退到 stderr —— 那会把告警/横幅冒充成结果。
        return (rc == 0), (out if out else (err if rc != 0 else ""))
    finally:
        for f in (out_f, err_f):
            try:
                os.remove(f)
            except OSError:
                pass


def _eval(sid, js, timeout=45):
    return _bsk(["evaluate", "--session", sid, js], timeout=timeout)


def _obj(txt):
    """从 evaluate 或 --json 输出里抠出 JSON 对象。"""
    if not txt:
        return None
    s = str(txt)
    i = s.find("{")
    j = s.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def _dig(txt):
    """解开 evaluate 的嵌套包装（{"ok":true,"result":"{...}"} 这类）。"""
    d = _obj(txt)
    for _ in range(3):
        if not isinstance(d, dict):
            break
        if "ans_len" in d:
            return d
        inner = d.get("result")
        if isinstance(inner, str):
            d = _obj(inner) or d
            continue
        break
    return d if isinstance(d, dict) else {}


def _toggle(sid, label, want_on):
    """确保某个开关处于期望状态（DeepSeek 专属）。"""
    ok, txt = _eval(sid, _TOGGLE_STATE % label)
    d = _obj(txt) or {}
    if not d.get("found"):
        return {"found": False}
    on = bool(d.get("on"))
    if on == want_on:
        return {"found": True, "on": on, "action": "keep"}
    _eval(sid, _TOGGLE_CLICK % label)
    time.sleep(1.2)
    ok, txt = _eval(sid, _TOGGLE_STATE % label)
    d2 = _obj(txt) or {}
    return {"found": True, "on": bool(d2.get("on")), "action": "clicked"}


def _clean(raw, question, spec):
    """清掉页面噪音：问题回显、过程标志行、尾部 UI 卡片。"""
    s = (raw or "").replace("\u00a0", " ").strip()
    q = (question or "").strip()
    if q and s.startswith(q):
        s = s[len(q):].lstrip()
    strip_keys = [k for k in (spec.get("strip") or []) if k]
    if strip_keys:
        kept = []
        for line in s.split("\n"):
            if any(k in line for k in strip_keys) and len(line.strip()) <= 30:
                continue
            kept.append(line)
        s = "\n".join(kept)
    for k in (spec.get("cuts") or []):
        p = s.find(k)
        if p > 0:
            s = s[:p]
    # 引用角标：DeepSeek 会把 [1][7] 渲染成独立小节点，
    # innerText 里退化成 "-\n1\n-\n7" 或 "。-1-" 这类残留，单独成行且无正文，直接丢弃。
    if spec.get("refmarks"):
        out = []
        for line in s.split("\n"):
            t = line.strip()
            bare = t.strip("-\u2013\u2014 \t")
            is_mark = bool(bare) and bare.isdigit() and len(bare) <= 2 and len(t) <= 4
            if t in ("-", "\u2013", "\u2014") or is_mark:
                continue
            if t.endswith("-") and not t.endswith("--") and len(t) > 1:
                line = line.rstrip()[:-1]   # 句尾降级的引用角标 "。-"
            out.append(line)
        s = "\n".join(out)
    lines = [ln.rstrip() for ln in s.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


@tool("ai_ask",
      "用本机浏览器里已登录的免费网页版 AI（DeepSeek/星火/腾讯AIStudio/Qwen/GLM/元宝/Kimi）提问并取回回答，"
      "一次调用跑完开窗→导航→开关校验→填字→发送→轮询→关窗全流程。"
      "定位：零边际成本的第二通道（官网问答不花钱，且常给到比 API 更强/更新的模型），"
      "搜索被墙时用它代搜，要一份『已联网搜索并整理好的综述』。"
      "站点选择：deepseek=联网搜索（默认首选）；qwen=Qwen3.7-Plus 响应最快、答案干净；"
      "xinghuo=中文强；"
      "tencent=Hy4 960k 长上下文重推理但不联网（问时效会拒答）。"
      "注意：① 结论是二手整理，关键事实仍建议 net_search 抽验 1-2 条；"
      "② 撞到登录墙/滑块不绕过，返回 need_human=true 交给人手动过；"
      "③ 依赖本机 Chrome 已连接 browserskill daemon（bsk_status 可查）；单次约 20~60 秒。",
      {"type": "object",
       "properties": {
           "question": {"type": "string", "description": "要问的问题（中文可）"},
           "site": {"type": "string", "enum": list(SITES.keys()),
                    "description": "用哪家网页版 AI，默认 deepseek"},
           "wait_sec": {"type": "number", "description": "最长等待秒数，默认 90"},
           "deep_think": {"type": "boolean",
                          "description": "是否开启「深度思考」（仅 DeepSeek，慢但质量高），默认 false"},
           "keep_session": {"type": "boolean",
                            "description": "结束后是否保留浏览器会话，默认关掉"}},
       "required": ["question"]},
      group="网络")
def run(question: str, site: str = "deepseek", wait_sec: int = 90,
        deep_think: bool = False, keep_session: bool = False) -> dict:
    if not question or not str(question).strip():
        return {"ok": False, "error": "question 不能为空"}
    key = (site or "deepseek").strip().lower()
    url = SITES.get(key)
    if not url:
        return {"ok": False, "error": "未知站点 %s，可选：%s" % (site, ", ".join(SITES))}
    spec = SPEC.get(key, {})
    q = str(question).strip()

    t0 = time.time()
    sid = ""
    try:
        ok, txt = _bsk(["session", "start", "--json", "--name", "ai_ask"], timeout=60)
        if not ok:
            return {"ok": False, "site": key, "error": "开会话失败: %s" % txt[:300]}
        d = _obj(txt) or {}
        sid = d.get("session_id") or ""
        if not sid:
            return {"ok": False, "site": key, "error": "拿不到 session_id: %s" % txt[:300]}

        # bsk navigate 是位置参数，不是 --url
        ok, txt = _bsk(["navigate", "--session", sid, url], timeout=75)
        if not ok:
            return {"ok": False, "site": key, "error": "导航失败: %s" % txt[:300],
                    "session": sid if keep_session else ""}

        # 等输入框就绪（SPA 首屏渲染）
        ready = {}
        for _ in range(10):
            time.sleep(2)
            ok, txt = _eval(sid, _READY_JS)
            d = _obj(txt) or {}
            if d.get("ready"):
                ready = d
                break
        if not ready:
            ok, txt = _eval(sid, _READY_JS)
            d = _obj(txt) or {}
            head = d.get("head") or str(txt)[:200]
            wall = [h for h in _WALL_HINTS if h in head]
            return {"ok": False, "site": key, "need_human": bool(wall),
                    "error": "页面未就绪（可能需登录/验证）",
                    "hint": ("需人工过：" + ",".join(wall)) if wall else head}
        head = ready.get("head") or ""
        wall = [h for h in _WALL_HINTS if h in head]
        if wall:
            return {"ok": False, "site": key, "need_human": True,
                    "error": "撞到登录/验证墙", "hint": "需人工过：" + ",".join(wall)}

        # 发送前记录已有答案元素数：本轮回答必须让它变多，否则可能是页面残留
        n0 = 0
        _sel0 = spec.get("answer_sel") or ""
        if _sel0:
            ok, txt = _eval(
                sid, "(() => document.querySelectorAll(%s).length)()"
                     % json.dumps(_sel0), timeout=45)
            try:
                n0 = int(str(txt).strip() or 0)
            except (TypeError, ValueError):
                n0 = 0

        toggles = {}
        if spec.get("toggles"):
            toggles["智能搜索"] = _toggle(sid, "智能搜索", True)
            toggles["深度思考"] = _toggle(sid, "深度思考", bool(deep_think))

        # 输入框选择器：spec.input_sel 优先；否则页面有 textarea 用 textarea，
        # 没有就退到 contenteditable 编辑器（元宝 .ql-editor 这类不是 textarea）
        fsel = spec.get("input_sel") or ""
        if not fsel:
            ok, dtxt = _eval(
                sid,
                "(() => (document.querySelectorAll('textarea').length"
                " ? 'textarea' : 'div[contenteditable=\"true\"]'))()", timeout=45)
            probe = str(dtxt or "")
            fsel = ("textarea" if "textarea" in probe
                    else 'div[contenteditable="true"]')

        ok, txt = _bsk(["fill", "--session", sid, "--selector", fsel,
                        "--value", q], timeout=60)
        if not ok:
            return {"ok": False, "site": key, "error": "填字失败: %s" % txt[:300],
                    "session": sid if keep_session else ""}
        time.sleep(0.8)  # 让受控组件把按钮从 disabled 解禁

        if spec.get("send") == "click":
            sel = spec.get("send_sel") or ""
            ok, txt = _bsk(["click", "--session", sid, "--selector", sel], timeout=60)
            if not ok:
                ok, txt = _bsk(["press", "--session", sid, "Enter"], timeout=60)
                if not ok:
                    return {"ok": False, "site": key,
                            "error": "点发送按钮失败: %s" % txt[:300],
                            "session": sid if keep_session else ""}
        else:
            ok, txt = _bsk(["press", "--session", sid, "Enter"], timeout=60)
            if not ok:
                return {"ok": False, "site": key, "error": "发送失败: %s" % txt[:300],
                        "session": sid if keep_session else ""}

        # 轮询：问题已上屏 + (容器内出现完成标志 或 长度连续稳定)
        probe_js = _PROBE_JS % (json.dumps(spec.get("answer_sel") or ""),
                                json.dumps(spec.get("q_sel") or ""),
                                json.dumps(spec.get("done") or [], ensure_ascii=False),
                                json.dumps(q[:30], ensure_ascii=False))
        deadline = t0 + max(20, int(wait_sec or 90))
        last_len, stable, answered, info = -1, 0, False, {}
        while time.time() < deadline:
            time.sleep(3)
            ok, txt = _eval(sid, probe_js)
            d = _dig(txt)
            info = d
            alen = int(d.get("ans_len") or 0)
            nnow = int(d.get("n") or 0)
            fresh = (nnow > n0) if _sel0 else True
            qok = bool(d.get("has_q")) and alen > 30 and fresh
            if qok and d.get("done"):
                answered = True
                time.sleep(1.5)   # 等最后一段渲染落定
                break
            if qok and alen == last_len:
                stable += 1
                if stable >= 3:   # 约 9 秒输出无变化
                    answered = True
                    break
            else:
                stable = 0
            last_len = alen

        raw = ""
        if answered:
            sel = spec.get("answer_sel") or ""
            if sel:
                ok, txt = _eval(
                    sid, "(() => { const l=[...document.querySelectorAll(%s)];"
                         " return l.length ? (l[l.length-1].innerText||'') : ''; })()"
                         % json.dumps(sel), timeout=60)
                raw = (txt or "") if ok else ""
            if len(raw.strip()) < 10:
                ok, txt = _eval(sid, _SLICE_JS % json.dumps(q, ensure_ascii=False),
                                timeout=60)
                raw = (txt or "") if ok else ""
            if len(raw.strip()) < 10:
                ok, txt = _eval(sid, "document.body.innerText", timeout=60)
                raw = (txt or "") if ok else ""

        ans = _clean(raw, q, spec) if raw else ""
        return {
            "ok": bool(answered and ans),
            "site": key,
            "answer": ans[:12000],
            "chars": len(ans),
            "online": spec.get("online"),
            "site_note": spec.get("note", ""),
            "done_flag": bool(info.get("done")),
            "has_q": bool(info.get("has_q")),
            "toggles": toggles,
            "elapsed": round(time.time() - t0, 1),
            "session": sid if keep_session else "",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "site": key, "error": "异常: %r" % (e,),
                "session": sid if keep_session else ""}
    finally:
        if sid and not keep_session:
            try:
                _bsk(["session", "stop", sid], timeout=30)
            except Exception:
                pass
