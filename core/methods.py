"""方法论库（MethodStore）：素月沉淀"行为模式"——什么方法有效、什么教训要避免。

设计（见设计文档 5.9 / 5.23）：
- 与事实记忆（memory，记录"是什么"）区分：方法论记录"怎么做 / 不要怎么做"，直接指导未来决策。
- 世界书条目结构：每条方法论 = {id, name(词条名), type, keywords(触发关键词), scene, method, evidence, importance}。
- 注入 system prompt 时只给"世界书目录"（索引），运行时按当前输入关键词**命中才展开**完整条目（按需取用，省 token）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

_PATH = "data/methodology.json"

# 停用词（关键词提取用）
_STOP = {
    "什么", "一个", "这个", "那个", "怎么", "如何", "我们", "你们", "自己", "时候", "进行",
    "没有", "可以", "需要", "一些", "已经", "就是", "不是", "还是", "对于", "通过", "根据",
    "其中", "以及", "是否", "如果", "然后", "这样", "那样", "因为", "所以", "但是", "而且",
    "非常", "比较", "应该", "能够", "可能", "方法", "问题", "任务",
}

# 首尾虚字（机械切块碎片过滤：如"阻断时""了文件""或验证"这类边界字）
_FU = set("的了是在或和与被而时会要从不要把就都还也很更最又再才只可对到为以其此该等各吗呢吧啊过着得给并则于当")

# 英文停用词 + 低区分度技术噪音（这类词出现在太多条目里，做触发词只会误触发）
_EN_STOP = {
    "the", "and", "for", "with", "of", "in", "on", "to", "is", "are", "not", "you", "it", "this",
    "that", "from", "by", "at", "as", "or", "if", "be", "can", "no", "do", "we", "use", "but",
    "when", "which", "how", "what", "a", "an", "was", "will", "has", "have", "out", "up", "all",
    "so", "my", "me", "your", "yes", "ok", "id", "json", "url", "api", "top", "done", "name",
    "text", "html", "www", "com", "org", "py", "ps", "md",
}


def extract_keywords(text: str, maxn: int = 12) -> List[str]:
    """从文本提取世界书触发关键词（2026-09-19 修订 v3，P2-4）。

    旧版（v1）只抓中文 2~4 字机械切块，两个实测缺陷：① 丢掉 github/tls/yt-dlp 等区分度最高
    的英文术语（#233 在"github 直连"场景漏命中）；② 碎片（"需要从"）占满 8 个名额、真词被挤
    （#108 在"你叫什么名字"场景漏命中）。
    v2 放开英文全收 → 新回归：英文/数字技术噪音（"mtime"/"20-30"）吃光名额，中文被挤出
    （"写个爬虫抓这个站"命中数 3→0）。故 v3 改为**分类配额**：英文≤3、中文 3~4 字块≤4、
    中文 2 字滑窗补足——保证中文触发词一定有名额，同时英文术语不被丢弃。
    匹配侧为大小写不敏感子串比对（match: k.lower() in input.lower()），英文统一小写。
    """
    text = text or ''
    en: List[str] = []
    long_cn: List[str] = []
    short_cn: List[str] = []
    seen = set()

    def push(bucket: List[str], w: str) -> None:
        if w and w not in seen:
            seen.add(w)
            bucket.append(w)

    # ① 英文/数字术语（必须含字母 → 滤掉纯数字/版本号）
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_\-\.\+]{1,}|[0-9][A-Za-z0-9_\-\.\+]{1,}", text):
        wl = w.lower().strip('.-_+')
        if len(wl) >= 2 and any(c.isalpha() for c in wl) and wl not in _EN_STOP:
            push(en, wl)

    # ② 中文 3~4 字块（较可能是词）
    for w in re.findall(r"[\u4e00-\u9fff]{3,4}", text):
        if w in _STOP or w[0] in _FU or w[-1] in _FU:
            continue
        push(long_cn, w)

    # ③ 中文 2 字滑窗（补短词，如"名字""爬虫""代理"）
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) < 2:
            continue
        for i in range(len(seg) - 1):
            w = seg[i:i + 2]
            if w in _STOP or w[0] in _FU or w[-1] in _FU:
                continue
            push(short_cn, w)

    # 组装：分类配额（英文 3 / 中文长词 4 / 中文短词补足到 maxn）
    kws: List[str] = []
    for bucket, cap in ((en, 3), (long_cn, 4), (short_cn, maxn)):
        n = 0
        for w in bucket:
            if n >= cap or len(kws) >= maxn:
                break
            kws.append(w)
            n += 1
    return kws[:maxn]


class MethodStore:
    def __init__(self, path: str = _PATH):
        self.path = path
        self.data: Dict = self._load()
        self._migrate()

    def _migrate(self) -> None:
        """世界书迁移：为旧条目补全 name；用手动关键词覆盖自动提取的劣质词（幂等，每次启动生效）。"""
        changed = False
        for m in self.data.get("methods", []):
            if not m.get("status"):
                m["status"] = "active"
                changed = True
            if not m.get("name"):
                m["name"] = (m.get("scene") or "").strip("：: ") or f"方法论{m.get('id')}"
                changed = True
            manual = self._MANUAL_KEYWORDS.get(m.get("id"))
            if manual:
                if m.get("keywords") != manual:
                    m["keywords"] = list(manual)[:12]
                    changed = True
            elif not m.get("keywords"):
                kws = extract_keywords(f"{m.get('scene','')} {m.get('method','')}")
                m["keywords"] = kws
                changed = True
        if changed:
            self._save()

    # 手动配置触发关键词（覆盖自动提取；覆盖用户自然语言说法，如"PY还是GO""被墙""黑化"）
    _MANUAL_KEYWORDS = {
        349: ["探索", "摸清", "新工具", "新软件", "新网站", "新技能", "怎么下手", "能干什么", "上手", "试一下", "挖宝"],
        350: ["深挖", "台账", "收藏", "立项", "收口", "续接", "存下来", "下载了", "只是开始", "先存着", "丢那儿", "丢那里"],
        28: ["任务分类", "计划线", "异步", "结果导向", "询问纪律", "任务模型"],
        27: ["网页", "web", "界面", "webui", "交互"],
        26: ["黑化", "提示词污染", "本性", "人格", "洗脑", "毒化"],
        25: ["篡改", "完整性", "病毒", "感染", "被改", "哈希"],
        24: ["定时任务", "监控", "底层机制", "自检", "守护"],
        23: ["备份", "云备份", "发布", "github", "云盘", "readme"],
        22: ["代理", "vpn", "翻墙", "127.0.0.1", "clash", "科学上网"],
        21: ["下载视频", "视频", "流媒体", "油管", "youtube", "yt", "b站"],
        20: ["被墙", "网络受限", "v2ray", "clash", "代理", "连不上"],
        19: ["被墙", "cloudflare", "无法访问", "海外", "超时", "打不开"],
        18: ["获取工具", "克隆", "git", "仓库", "下载工具", "github"],
        17: ["反复", "循环", "死循环", "无进展", "卡住", "重复"],
        16: ["创建工具", "工具准入", "tool_create", "建工具", "自建", "新工具", "写个工具"],
        15: ["搜索", "拆词", "搜不到", "无关结果", "换词"],
        14: ["三段式", "方案选择", "可行性", "做还是换", "决策"],
        13: ["选语言", "用什么", "python", "py", "go", "技术选型", "选择工具"],
        12: ["自建工具", "写工具", "python还是go", "选择语言"],
        11: ["go工具", "编译", "自举", "二进制"],
        10: ["修复", "python环境", "环境文件", "改环境"],
        9: ["之前做过", "复用", "老问题", "熟悉", "以前"],
        8: ["事实", "数字", "数据来源", "引用", "准确"],
        7: ["汇报", "报告", "反馈", "总结给"],
        6: ["交付", "检查", "验收", "核对"],
        5: ["连续失败", "重试", "再试", "又失败"],
        4: ["多步", "拆解", "分步", "流程"],
        3: ["开始任务", "规划", "拆解", "第一步", "计划"],
        2: ["报错", "工具不可用", "出错", "失效"],
        1: ["工具地图", "探索工具", "有什么工具", "已安装"],
    }

    def _load(self) -> Dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save(self) -> None:
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def learn(self, type_: str, scene: str, method: str,
              evidence: str = "", importance: float = 0.6,
              name: str = "", keywords: Optional[List[str]] = None) -> int:
        """沉淀一条方法论。type_: "good"（值得复用）/ "bad"（避免）。
        世界书字段：name=词条名（默认取 scene 首段），keywords=触发关键词（默认从 scene+method 自动提取）。"""
        type_ = type_ if type_ in ("good", "bad") else "good"
        importance = max(0.0, min(1.0, float(importance)))
        methods = self.data.setdefault("methods", [])
        mid = (max((m.get("id", 0) for m in methods), default=0)) + 1
        methods.append({
            "id": mid,
            "type": type_,
            "name": name.strip() or (scene or "").strip("：: ") or f"方法论{mid}",
            "keywords": (keywords or [])[:12] or extract_keywords(f"{scene} {method}"),
            "scene": scene[:200],
            "method": method[:500],
            "evidence": evidence[:300],
            "importance": importance,
            "status": "active",
            "superseded_by": None,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self._save()
        return mid

    def list(self, type_: Optional[str] = None, include_all: bool = False) -> List[Dict]:
        """活跃方法论列表（status=active）。

        include_all=True 时含 superseded/archived（保留证据链，仅管理/统计用）。
        2026-09-19 P2-3：被替代/归档的方法不再进世界书与触发匹配，防止库膨胀后重复条目反复命中。
        """
        methods = self.data.get("methods", [])
        if type_:
            methods = [m for m in methods if m.get("type") == type_]
        if not include_all:
            methods = [m for m in methods if m.get("status", "active") == "active"]
        return sorted(methods, key=lambda m: -m.get("importance", 0))

    def supersede(self, old_id: int, new_id: int) -> bool:
        """标记 old 被 new 替代：old 不再进世界书/触发（证据链保留，get() 仍可查）。"""
        old = self.get(old_id)
        new = self.get(new_id)
        if not old or not new or old_id == new_id:
            return False
        old["status"] = "superseded"
        old["superseded_by"] = new_id
        old["superseded_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        self._save()
        return True

    def archive(self, mid: int, reason: str = "") -> bool:
        """彻底归档一条方法论（明确错误/过时），不进世界书。"""
        m = self.get(mid)
        if not m:
            return False
        m["status"] = "archived"
        m["archived_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        if reason:
            m["archive_reason"] = reason[:200]
        self._save()
        return True

    def active_count(self) -> int:
        return sum(1 for m in self.data.get("methods", []) if m.get("status", "active") == "active")

    def to_index_json(self, limit: Optional[int] = None) -> List[Dict]:
        """世界书多级目录（JSON 结构，适配底层逻辑）：[{"category", "items":[{id,name,type,keywords}]}]"""
        methods = self.list()[:limit] if limit else self.list()
        cats: Dict[str, List[Dict]] = {}
        for m in methods:
            cats.setdefault(self._category(m), []).append(m)
        order = ["生存安全", "决策与效率", "工具与工程", "网络与环境", "沟通与交付", "其他"]
        out = []
        for cat in order + [c for c in cats if c not in order]:
            items = cats.pop(cat, None)
            if not items:
                continue
            out.append({
                "category": cat,
                "items": [{
                    "id": m.get("id"),
                    "name": m.get("name", "?"),
                    "type": m.get("type", "good"),
                    "keywords": m.get("keywords", [])[:3],
                } for m in items],
            })
        return out

    def to_index(self, limit: Optional[int] = None) -> str:
        """世界书多级目录（Markdown 渲染视图，给 LLM 展示用）：从 JSON 结构渲染。"""
        return self._render_index(self.to_index_json(limit))

    @staticmethod
    def _render_index(data: List[Dict]) -> str:
        lines = []
        for cat in data:
            lines.append(f"▶ {cat['category']}")
            for m in cat["items"]:
                kws = "/".join(m.get("keywords", []))
                tag = "避" if m.get("type") == "bad" else ""
                lines.append(f"   [{m.get('id')}]{tag} {m.get('name','?')}（{kws}）")
        return "\n".join(lines) if lines else "（暂无方法论）"

    # 方法论领域分类（多级目录第一级）
    _CATEGORY = {
        26: "生存安全", 25: "生存安全", 24: "生存安全", 23: "生存安全",
        14: "决策与效率", 13: "决策与效率", 8: "决策与效率", 3: "决策与效率",
        6: "决策与效率", 9: "决策与效率", 5: "决策与效率", 7: "决策与效率",
        17: "决策与效率", 10: "决策与效率", 2: "决策与效率",
        28: "工具与工程", 27: "工具与工程", 12: "工具与工程", 16: "工具与工程",
        11: "工具与工程", 18: "工具与工程", 1: "工具与工程",
        22: "网络与环境", 20: "网络与环境", 19: "网络与环境", 21: "网络与环境", 15: "网络与环境",
    }

    def _category(self, m: Dict) -> str:
        return self._CATEGORY.get(m.get("id"), "其他")

    @staticmethod
    def _scene_overlap(text: str, scene: str) -> int:
        """输入与触发场景的 2 字词重叠数（去停用词）。世界书触发兜底。"""
        def bigrams(s: str):
            return {s[i:i + 2] for i in range(len(s) - 1) if s[i:i + 2] not in _STOP}
        if not text or not scene:
            return 0
        return len(bigrams(text) & bigrams(scene))

    def match(self, text: str, limit: int = 5) -> List[Dict]:
        """世界书触发：输入含条目关键词（大小写不敏感）→ 命中；或与 scene 有 ≥2 个 2 字词重叠 → 兜底命中。"""
        low = text.lower()
        hits = []
        for m in self.list():
            kws = m.get("keywords", [])
            if any(k and k.lower() in low for k in kws):
                hits.append(m)
            elif self._scene_overlap(text, m.get("scene", "")) >= 2:
                hits.append(m)
        hits.sort(key=lambda m: -m.get("importance", 0))
        return hits[:limit]

    # ---- 三层激活：常驻 / 场景预加载 / 关键词命中 ----

    # 常驻层：最高频行为准则，每次都在 system prompt（不靠关键词碰运气）
    _ALWAYS_ON_IDS = [3, 4, 5, 8, 17]  # 探查先行 / 小步验证 / 失败两次换路 / 事实准确 / 死循环

    def always_on(self) -> List[Dict]:
        """常驻层：最高频行为准则，每次都在。"""
        out = []
        for mid in self._ALWAYS_ON_IDS:
            m = self.get(mid)
            if m and m.get("status") == "active":
                out.append(m)
        return out

    def preload_by_activity(self, recent_tools: List[str], recent_text: str) -> List[Dict]:
        """场景预加载：根据她正在做什么，主动带相关方法论。
        recent_tools: 最近 N 步调过的工具名列表；recent_text: 最近几轮她自己的 thinking。"""
        hits = []
        seen = set()
        low = (recent_text or "").lower()
        tools = set(recent_tools or [])
        # 搜索场景
        if "net_search" in tools or "bsk_navigate" in tools or "google" in low or "搜索" in (recent_text or ""):
            for mid in (343, 344):
                m = self.get(mid)
                if m and m.get("status") == "active" and mid not in seen:
                    hits.append(m); seen.add(mid)
        # 启动服务/连接 MCP 场景
        if "mcp_connect" in tools or "bsk_session_start" in tools:
            m = self.get(341)
            if m and m.get("status") == "active" and 341 not in seen:
                hits.append(m); seen.add(341)
        return hits

    def get(self, mid: int) -> Optional[Dict]:
        for m in self.data.get("methods", []):
            if m.get("id") == mid:
                return m
        return None

    def to_full(self, methods: List[Dict]) -> str:
        """展开命中条目的完整内容（world book 命中展开）。"""
        lines = []
        for m in methods:
            t = "正面" if m.get("type") == "good" else "反面"
            s = f"- [{m.get('name')}]({t}) {m.get('method','')}"
            if m.get("evidence"):
                s += f"（{m['evidence']}）"
            lines.append(s)
        return "\n".join(lines) if lines else "（无命中条目）"

    def to_prompt(self, limit: int = 4) -> str:
        """生成注入 system prompt 的"我的经验法则"文本。"""
        good = self.list("good")[:limit]
        bad = self.list("bad")[:limit]
        parts = []
        if good:
            lines = [f"- [{m['scene']}] {m['method']}" + (f"（{m['evidence']}）" if m.get("evidence") else "")
                     for m in good]
            parts.append("正面（值得复用）：\n" + "\n".join(lines))
        if bad:
            lines = [f"- [{m['scene']}] 避免：{m['method']}" + (f"（{m['evidence']}）" if m.get("evidence") else "")
                     for m in bad]
            parts.append("反面（避免）：\n" + "\n".join(lines))
        return "\n\n".join(parts) if parts else "（暂无沉淀的方法论）"

    def summary(self) -> Dict:
        methods = self.data.get("methods", [])
        active = [m for m in methods if m.get("status", "active") == "active"]
        return {
            "total": len(methods),
            "active": len(active),
            "good": sum(1 for m in active if m.get("type") == "good"),
            "bad": sum(1 for m in active if m.get("type") == "bad"),
            "superseded": sum(1 for m in methods if m.get("status") == "superseded"),
            "archived": sum(1 for m in methods if m.get("status") == "archived"),
        }
