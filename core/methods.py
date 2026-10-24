"""方法论库（MethodStore）：素月沉淀"行为模式"——什么方法有效、什么教训要避免。

设计（见设计文档 5.9 / 5.23）：
- 与事实记忆（memory，记录"是什么"）区分：方法论记录"怎么做 / 不要怎么做"，直接指导未来决策。
- 世界书条目结构：每条方法论 = {id, name(词条名), type, keywords(触发关键词), scene, method, evidence, importance}。
- 注入 system prompt 时只给"世界书目录"（索引），运行时按当前输入关键词**命中才展开**完整条目（按需取用，省 token）。
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

_PATH = "data/methodology.json"
_DORMANT_CHECKED = False   # 每进程只做一次 dormant 巡检

# 世界书召回：2026-10-22 起默认 BM25 全文（旧关键词子串版保留，_USE_BM25=False 可回滚）
_USE_BM25 = True
_BM25_MIN_SCORE = 1.5      # BM25 补召回的绝对下限
_HYBRID_MIN_KEYWORD_HITS = 3   # 关键词命中达到这个数就不再掺 BM25

# 置信度阈值（2026-10-22 按 120 条真实输入回放的分位数校准，两条通路量纲不同）
_CONF_MIN_HITS = 3
_CONF_TOP_KEYWORD = 2.0        # 关键词路径 p25（中位数 3.57）
_CONF_TOP_BM25 = 5.8           # BM25 路径 p25（中位数 6.79）

_RE_EN = re.compile(r"[A-Za-z0-9_]+")
# 工具名形状的标识符（fs_read / cmd_run / mcp_connect…）
_TOOL_TOKEN_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
_RE_ZH = re.compile(r"[\u4e00-\u9fff]")

# 中文分词：优先 jieba（词级召回，2-gram 会把"有问/查一/个文"这类跨词边界碎片
# 当成稀有词，反而把不相关条目顶上去）；jieba 不可用时退化为 2-gram，功能不丢。
try:
    import jieba as _jieba
    _jieba.setLogLevel(60)          # 关掉词典加载日志
    _JIEBA_READY = True
except Exception:  # noqa: BLE001
    _jieba = None
    _JIEBA_READY = False


def _tokenize(text: str) -> list:
    """英文/数字按词，中文按 jieba 分词（无 jieba 时退 2-gram）。"""
    t = (text or "").lower()
    toks = _RE_EN.findall(t)
    if not t:
        return toks
    if _JIEBA_READY:
        for w in _jieba.cut(t):
            w = w.strip()
            if not w or not _RE_ZH.search(w):
                continue
            if len(w) == 1:          # 单字多是虚词（的/了/很/水），不进索引
                continue
            if w in _STOP:
                continue
            toks.append(w)
    else:
        zh = "".join(_RE_ZH.findall(t))
        toks.extend(zh[i:i + 2] for i in range(len(zh) - 1) if zh[i:i + 2] not in _STOP)
    return toks

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


# ── 新增即分类（2026-10-14）：learn 新条目时轻量规则归类，避免落进"未分类" ──
# 规则源自生产脚本 rule_classify v3（已用于 645 条数据化分类校准）。
_CAT_RULES = [
    ("生存安全", ["黑化", "本性", "人格", "篡改", "完整性", "备份", "病毒", "防护", "投毒", "护栏",
                "污染", "守护", "自我审查", "自保", "注入"]),
    ("内容与发布", ["头条", "发布", "文章", "写作", "标题", "评论", "公众号", "文案", "读者",
                 "自媒体", "署名", "封面", "公开文档", "readme", "介绍自己", "典故", "诗句"]),
    ("网络与采集", ["搜索", "网络", "抓取", "爬虫", "采集", "下载", "代理", "被墙", "cloudflare",
                 "站点", "网页", "url", "http", "dns", "tls", "反爬", "局域网", "流媒体"]),
    ("视觉与素材", ["图片", "立绘", "抠图", "视觉", "pptx", "docx", "文档渲染", "视频", "音频",
                 "tts", "语音", "渲染", "ocr", "抽帧", "素材", "海报", "截图", "字体", "像素",
                 "轮廓", "配乐", "whisper"]),
    ("记忆与检索", ["记忆", "检索", "召回", "排序", "相似度", "倒排", "2-gram", "上下文", "知识库",
                 "方法论库", "去重", "归档", "世界书", "冗余", "台账"]),
    ("协作与交付", ["用户", "共建者", "尼可", "汇报", "交付", "回复", "沟通", "画像", "称呼",
                 "提问", "诚信"]),
    ("工具与工程", ["工具", "脚本", "命令", "powershell", "cmd", "注册表", "自建", "import",
                 "python", "venv", "git", "编译", "进程", "服务", "启动", "mcp", "依赖", "批处理",
                 "正则", "文件", "目录", "json", "配置", "环境", "编码", "crlf", "函数", "代码",
                 "接口", "api", "单位换算", "soffice", "libreoffice"]),
    ("决策与效率", ["决策", "三段式", "升级阶梯", "止损", "方案选择", "反复", "死循环", "优先级",
                 "计划", "行动哲学", "成本", "效率", "选型", "风险"]),
]
_CAT_VERIFY = ["验证", "核验", "排查", "核查", "审计", "复现", "证伪", "断言", "抽检", "甄别",
               "是否真的", "有没有生效", "判定口径", "假成功", "是否已", "是否生效"]


def classify(scene: str, method: str, type_: str = "good") -> str:
    """轻量规则分类（2026-10-14）：learn 时自动归类。

    判定顺序：验证强词（全文命中即验证类）→ 规则表命中计数取最多 → 无命中归"其他"。
    返回类目名；无把握不硬塞（宁可"其他"，便于后续 LLM 批量校正）。
    """
    text = f"{scene or ''} {method or ''}"
    low = text.lower()
    if any(w in text for w in _CAT_VERIFY):
        return "验证与排查"
    best, best_n = "", 0
    for cat, kws in _CAT_RULES:
        n = sum(1 for k in kws if k.lower() in low)
        if n > best_n:
            best, best_n = cat, n
    return best or "其他"


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
    # 世界书目录的渲染参数（2026-10-22：目录曾占 20k tokens/轮）
    # 第二轮再降：目录里即便列出条目名也**不能直接展开**（看内容必须 method_find），
    # 所以它的唯一价值是"知道有哪些条目存在"。分类 + 条数就够，969 → 68 tokens。
    # 想恢复"每类列前 N 条"：把下面改成 N（>0）。
    _INDEX_MAX_PER_CATEGORY = 0
    _INDEX_NAME_CHARS = 26
    # 命中记账的落盘节流（methodology.json 约 1MB，不能每轮都写）
    _HIT_FLUSH_SECONDS = 300
    # 零命中多久转 dormant（不进目录，但 method_find 仍能搜到）
    _DORMANT_DAYS = 90

    def __init__(self, path: str = _PATH):
        self.path = path
        self.data = self._load()
        # 2026-10-24：记录加载时的文件 mtime。_save 前据此判断"是否有别的实例写过"，
        # 有则先合并新增条目再写，避免用旧快照覆盖别人的写入（见 _merge_newer）。
        self._known_mtime = self._file_mtime()
        self._migrate()
        self._hits_dirty = False
        self._last_flush = 0.0
        self._bm25_cache = None
        self._tool_cache = None
        self._last_path = "keyword"
        self._check_dormant_once()

    def _migrate(self) -> None:
        """世界书迁移：为旧条目补全 name；用手动关键词覆盖自动提取的劣质词（幂等，每次启动生效）。"""
        changed = False
        cat_map = self._load_category_map()
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
            cat = cat_map.get(str(m.get("id")))
            if cat and m.get("category") != cat:
                m["category"] = cat
                changed = True
        if changed:
            self._save()

    # 手动配置触发关键词（覆盖自动提取；覆盖用户自然语言说法，如"PY还是GO""被墙""黑化"）
    _MANUAL_KEYWORDS = {
        349: ["探索", "摸清", "新工具", "新软件", "新网站", "新技能", "怎么下手", "能干什么", "上手", "试一下", "挖宝"],
        296: ["巡检", "体检", "自身状态", "状态文件", "解析口径", "假设结构",
              "methodology.json", "integrity_status.json", "取到 0", "空列表",
              "结果为空", "脚本解析"],
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

    def _file_mtime(self) -> int:
        try:
            return Path(self.path).stat().st_mtime_ns
        except OSError:
            return 0

    def _merge_newer(self) -> int:
        """多写入者合并（2026-10-24 修复静默回滚）。

        method_learn 工具会 new 一个自己的 MethodStore，与核心实例各持全量内存副本；
        核心实例的命中记账 flush 会用旧快照整份覆盖文件，导致新增条目丢失。
        这里在写盘（以及分配新 id）前先看一眼磁盘：若 mtime 与本实例上次读写时不同，
        说明有别的写入者动过，把「id 大于本实例最大 id」的新增条目吸收进来。

        只吸收新增、不做字段级合并——已存在条目的字段冲突以本实例为准
        （丢一次命中计数不致命，丢整条新增才是真损失）；本实例删掉的条目也不会被复活。
        """
        cur = self._file_mtime()
        if cur == getattr(self, "_known_mtime", None):
            return 0
        self._known_mtime = cur
        try:
            disk = self._load()
        except Exception:
            return 0
        mine = {m.get("id") for m in self.data.get("methods", [])}
        my_max = max(mine) if mine else 0
        added = 0
        for m in (disk.get("methods") or []):
            mid = m.get("id")
            if mid and mid not in mine and mid > my_max:
                self.data.setdefault("methods", []).append(m)
                added += 1
        if added:
            self._bm25_cache = None
            self._tool_cache = None
        return added

    def _save(self) -> None:
        try:
            self._merge_newer()
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            self._known_mtime = self._file_mtime()
        except OSError:
            pass

    def learn(self, type_: str, scene: str, method: str,
              evidence: str = "", importance: float = 0.6,
              name: str = "", keywords: Optional[List[str]] = None,
              category: str = "") -> int:
        """沉淀一条方法论。type_: "good"（值得复用）/ "bad"（避免）。
        世界书字段：name=词条名（默认取 scene 首段），keywords=触发关键词（默认从 scene+method 自动提取）。"""
        type_ = type_ if type_ in ("good", "bad") else "good"
        importance = max(0.0, min(1.0, float(importance)))
        # 2026-10-24：先合并别人的新增，再算 max(id)+1；否则会撞号并覆盖对方条目。
        self._merge_newer()
        methods = self.data.setdefault("methods", [])
        # 2026-10-22：查重。同一 (type, scene, method) 已存在就不再追加——
        # 历史库里已积累 123 对高相似条目，重复入库只会让世界书越滚越大。
        # 只更新更高的 importance，返回既有 id。
        _scene_k = (scene or "").strip()
        _method_k = (method or "").strip()
        for m in methods:
            if (m.get("status", "active") != "active") or m.get("type") != type_:
                continue
            if (m.get("scene") or "").strip() == _scene_k and (m.get("method") or "").strip() == _method_k:
                if importance > float(m.get("importance") or 0):
                    m["importance"] = importance
                    self._save()
                return int(m.get("id") or 0)
        mid = (max((m.get("id", 0) for m in methods), default=0)) + 1
        cat = category or classify(scene, method, type_)
        methods.append({
            "id": mid,
            "type": type_,
            "name": name.strip() or (scene or "").strip("：: ") or f"方法论{mid}",
            "keywords": (keywords or [])[:12] or extract_keywords(f"{scene} {method}"),
            "scene": scene[:200],
            "method": method[:500],
            "evidence": evidence[:300],
            "importance": importance,
            "category": cat,
            "status": "active",
            "superseded_by": None,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self._save()
        if cat:
            self._register_category(mid, cat)
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
        """世界书多级目录（JSON 结构，适配底层逻辑）：[{"category", "items":[{id,name,type,keywords}]}]

        2026-10-22：类目内改按「最近是否被激活」排序（近 3 天 > 近 30 天 > 更早 > 从未），
        同级再按 importance——目录前几条天然是"当下用得上的"，不被高权重老条目霸占。
        """
        methods = self.list()[:limit] if limit else self.list()
        now = datetime.datetime.now()
        cats: Dict[str, List[Dict]] = {}
        for m in methods:
            cats.setdefault(self._category(m), []).append(m)
        for items in cats.values():
            items.sort(key=lambda m: (self._recency_tier(m, now), -float(m.get("importance") or 0),
                                      int(m.get("id") or 0)))
        order = ["生存安全", "决策与效率", "记忆与检索", "工具与工程", "网络与采集",
                 "内容与发布", "验证与排查", "视觉与素材", "协作与交付", "其他"]
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

    def to_index(self, limit: Optional[int] = None, max_per_category: int = 0) -> str:
        """世界书多级目录（Markdown 渲染视图，给 LLM 展示用）：从 JSON 结构渲染。

        max_per_category=0 时用默认上限（每类 30 条），其余条目靠 method_find 检索。
        """
        return self._render_index(self.to_index_json(limit), max_per_category)

    @staticmethod
    def _render_index(data: List[Dict], max_per_category: int = 0) -> str:
        """渲染世界书目录：默认只给「分类 + 条数」，条目靠 method_find 按关键词检索。

        演进（2026-10-22）：
          705 条全量 + 关键词 → 43,835 字符 ≈ 20,057 tokens（吃掉 83% 的上下文预算）
          每类 30 条名称      →  9,468 字符 ≈  4,874 tokens
          每类 5 条名称       →  1,910 字符 ≈    969 tokens
          只剩分类计数        →    143 字符 ≈     68 tokens
        关键事实：目录里的名字她也**不能直接展开**，要看内容还得 method_find——
        所以列名字只是"提示存在"，而分类计数同样能达到这个目的，还便宜 14 倍。
        """
        cap = max_per_category if max_per_category >= 0 else MethodStore._INDEX_MAX_PER_CATEGORY
        if max_per_category == 0 and MethodStore._INDEX_MAX_PER_CATEGORY == 0:
            cap = 0
        lines = []
        total = 0
        for cat in data:
            items = cat["items"]
            total += len(items)
            lines.append(f"▶ {cat['category']}（{len(items)}）")
            for m in items[:cap]:
                nm = (m.get("name") or "?").strip()
                if len(nm) > MethodStore._INDEX_NAME_CHARS:
                    nm = nm[:MethodStore._INDEX_NAME_CHARS] + "…"
                tag = "避" if m.get("type") == "bad" else ""
                lines.append(f"   [{m.get('id')}]{tag} {nm}")
            if cap > 0:
                rest = len(items) - cap
                if rest > 0:
                    lines.append(f"   …另 {rest} 条")
        if lines:
            lines.append(f"（共 {total} 条。目录只给分类；要具体条目用 method_find(keyword=…) 检索）")
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
        """条目自带 category 优先（数据化，2026-10-14）；旧硬编码表仅作兜底。"""
        return m.get("category") or self._CATEGORY.get(m.get("id"), "其他")

    _CATEGORY_MAP_PATH = "data/method_categories.json"

    @classmethod
    def _load_category_map(cls) -> Dict[str, str]:
        """分类数据源（id→类目）：数据化迁移源，文件缺失/损坏时安全降级为空表。"""
        try:
            with open(cls._CATEGORY_MAP_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _register_category(self, mid: int, category: str) -> None:
        """把 id→类目 同步写入分类数据源（读整份→改一键→写整份，避免抹掉其他键）。"""
        category = (category or "").strip()
        if not category:
            return
        try:
            p = Path(self._CATEGORY_MAP_PATH)
            data = {}
            if p.exists():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = raw
                except Exception:
                    data = {}
            data[str(mid)] = category
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def set_category(self, mid: int, category: str) -> bool:
        """手动调整单条条目分类（数据化后的日常维护入口）。同步更新分类数据源。"""
        m = self.get(mid)
        if not m:
            return False
        m["category"] = (category or "").strip()
        self._save()
        if category:
            self._register_category(mid, category)
        return True

    @staticmethod
    def _scene_overlap(text: str, scene: str) -> int:
        """输入与触发场景的 2 字词重叠数（去停用词）。世界书触发兜底。"""
        def bigrams(s: str):
            return {s[i:i + 2] for i in range(len(s) - 1) if s[i:i + 2] not in _STOP}
        if not text or not scene:
            return 0
        return len(bigrams(text) & bigrams(scene))

    def match_scored(self, text: str, limit: int = 5) -> list:
        """世界书触发（2026-10-22 起：关键词优先 + BM25 补召回）。

        为什么不是纯 BM25：`keywords` 是每条**人工策展的触发词**，精度高；
        全文 BM25 会把正文里的通用词（"东西""不用""写"）也当证据，
        实测在对话型输入上会顶掉原本正确的条目（把"方法论断了"匹配到 LibreOffice）。
        但纯关键词召回不够：回放 120 条真实输入，42% 的输入命中 <3 条，17% 零命中。

        所以：关键词够 3 条就只信关键词；不够才用 BM25 补，且补进来的一定要过相关性闸门
        （分数 ≥ max(1.5, top1*0.25)）。旧的"2 字重叠≥2 兜底"只在两边都空时兜一下。
        """
        if not _USE_BM25:
            return self._match_scored_legacy(text, limit)
        kw = self._match_keywords(text, limit)
        if len(kw) >= _HYBRID_MIN_KEYWORD_HITS:
            self._last_path = "keyword"
            return kw[:limit]
        seen = {m.get("id") for _, m in kw}
        extra = [(s, m) for s, m in self._match_bm25(text, limit) if m.get("id") not in seen]
        merged = kw + extra
        if merged:
            self._last_path = "bm25" if extra else "keyword"
            return merged[:limit]
        self._last_path = "legacy"
        return self._match_scored_legacy(text, limit)   # 最后兜底：2 字重叠

    def _match_pool(self) -> List[Dict]:
        """匹配池 = active + dormant。

        2026-10-22：dormant 只是"最近一直没用上"，不是"这条作废"——
        真正需要它的场景出现时（关键词/BM25 命中）应当还能被捞起来，并自动复活。
        所以匹配池包含 dormant，复活在 touch_hits 里做。superseded/archived 不进池。
        """
        return [m for m in self.data.get("methods", [])
                if m.get("status", "active") in ("active", "dormant")]

    def _match_keywords(self, text: str, limit: int = 5) -> list:
        """关键词通道：只认人工策展的 keywords（不做 2 字兜底，噪音太大）。"""
        low = (text or "").lower()
        if not low.strip():
            return []
        scored = []
        for m in self._match_pool():
            kws = [k for k in m.get("keywords", []) if k]
            hit = [k for k in kws if k.lower() in low]
            if not hit:
                continue
            # 命中词长度和 + 覆盖度：命中 1 个长词 > 命中 3 个短词
            rel = float(sum(len(k) for k in hit)) * (0.75 + 0.25 * (len(hit) / max(1, len(kws))))
            scored.append((rel + float(m.get("importance") or 0) * 0.5, m))
        scored.sort(key=lambda t: -t[0])
        return scored[:limit]

    # ---------- BM25 全文召回 ----------
    def _bm25_index(self):
        """懒构建 BM25 索引（707 条 × 平均 60 字，构建几十毫秒）；数据变了用 invalidate_index 失效。"""
        if self._bm25_cache is not None:
            return self._bm25_cache
        docs = []
        df: Dict[str, int] = {}
        for m in self._match_pool():
            toks = _tokenize(" ".join([
                m.get("name") or "", m.get("scene") or "",
                m.get("method") or "", " ".join(m.get("keywords") or []),
            ]))
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t in tf:
                df[t] = df.get(t, 0) + 1
            docs.append((m, tf, len(toks)))
        avgdl = sum(d[2] for d in docs) / max(1, len(docs))
        self._bm25_cache = (docs, df, len(docs), avgdl)
        return self._bm25_cache

    def invalidate_index(self) -> None:
        self._bm25_cache = None
        self._tool_cache = None

    def _match_bm25(self, text: str, limit: int = 5) -> list:
        q = (text or "").strip()
        if not q:
            return []
        docs, df, n, avgdl = self._bm25_index()
        q_toks = set(_tokenize(q))
        if not q_toks:
            return []
        k1, b = 1.2, 0.6
        scored = []
        for m, tf, dl in docs:
            s = 0.0
            for tok in q_toks:
                f = tf.get(tok, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - df.get(tok, 0) + 0.5) / (df.get(tok, 0) + 0.5))
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / max(avgdl, 1e-9)))
            if s <= 0:
                continue
            # importance 只当 tiebreaker（旧版 ×2.0 会压过相关性；实测只占 4% 但也该降权）
            s += float(m.get("importance") or 0) * 0.2
            scored.append((s, m))
        if not scored:
            return []
        scored.sort(key=lambda t: -t[0])
        top1 = scored[0][0]
        # 相关性闸门：低于最高分 25% 的条目不是"讲这件事"，宁缺勿滥（兜底消失）
        floor = max(_BM25_MIN_SCORE, top1 * 0.25)
        return [(s, m) for s, m in scored[:limit] if s >= floor]

    def _match_scored_legacy(self, text: str, limit: int = 5) -> list:
        """旧版：输入含条目关键词 → 命中；或与 scene 有 ≥2 个 2 字词重叠 → 兜底命中。

        保留用于回滚与对照（_USE_BM25=False 时启用）。
        """
        low = text.lower()
        scored = []
        for m in self.list():
            kws = [k for k in m.get("keywords", []) if k]
            hit = [k for k in kws if k.lower() in low]
            if hit:
                rel = float(sum(len(k) for k in hit))
            else:
                ov = self._scene_overlap(text, m.get("scene", ""))
                if ov < 2:
                    continue
                rel = ov * 0.5
            scored.append((rel + m.get("importance", 0) * 2.0, m))
        scored.sort(key=lambda t: -t[0])
        return scored[:limit]

    def match(self, text: str, limit: int = 5) -> List[Dict]:
        """世界书触发（兼容旧接口）：只返回条目，丢弃分数。"""
        return [m for _, m in self.match_scored(text, limit)]

    def find(self, keyword: str, limit: int = 8) -> List[Dict]:
        """按关键词全文检索方法论（名称/场景/方法/关键词/分类），供目录被截断后按需取回。

        2026-10-22：世界书目录改成紧凑渲染后，每类只列前 5 条，其余靠本方法检索。
        检索范围包含 dormant（长期零命中但仍有价值的条目）——目录里不占位，不等于删掉。
        评分：完整词命中权重高（×3），2 字词重叠兜底（与 match_scored 同口径）。
        """
        kw = (keyword or "").strip().lower()
        if not kw:
            return []
        pool = [m for m in self.data.get("methods", [])
                if m.get("status", "active") in ("active", "dormant")]
        scored = []
        for m in pool:
            hay = " ".join([
                m.get("name") or "", m.get("scene") or "", m.get("method") or "",
                " ".join(m.get("keywords") or []), m.get("category") or "",
            ]).lower()
            score = hay.count(kw) * 3 + self._scene_overlap(kw, m.get("scene", ""))
            if score:
                scored.append((score + m.get("importance", 0) * 0.5, m))
        scored.sort(key=lambda t: -t[0])
        return [m for _, m in scored[:limit]]

    # ---------- 命中记账与生命周期（2026-10-22） ----------
    def touch_hits(self, ids: List[int]) -> None:
        """记录本轮实际注入（=被激活）的条目，用于目录排序与 dormant 判定。"""
        now = datetime.datetime.now().isoformat(timespec="seconds")
        by = {m.get("id"): m for m in self.data.get("methods", [])}
        changed = False
        for i in ids or []:
            m = by.get(i)
            if not m:
                continue
            m["hit_count"] = int(m.get("hit_count") or 0) + 1
            m["last_hit"] = now
            # dormant 被命中 → 自动复活（它只是"最近没用上"，不是作废）
            if m.get("status") == "dormant":
                m["status"] = "active"
                m.pop("dormant_at", None)
                m["revived_at"] = now
            changed = True
        if changed:
            self._hits_dirty = True

    def record_outcome(self, ids: List[int], ok: bool) -> None:
        """效果闭环（只记账，暂不影响打分）：记录"注入了这些条目之后这一轮成不成"。

        2026-10-22 说明：这只是相关性统计的原料，不是因果结论——
        一轮失败可能是环境问题，一轮成功也可能跟注入的方法论无关。
        等数据攒够（每条约 30 轮样本）再谈要不要用它调权重。
        """
        now = datetime.datetime.now().isoformat(timespec="seconds")
        by = {m.get("id"): m for m in self.data.get("methods", [])}
        changed = False
        for i in ids or []:
            m = by.get(i)
            if not m:
                continue
            key = "ok_turns" if ok else "bad_turns"
            m[key] = int(m.get(key) or 0) + 1
            m["last_outcome"] = now
            changed = True
        if changed:
            self._hits_dirty = True

    def flush_hits(self, force: bool = False) -> bool:
        """落盘命中记账（节流：默认 5 分钟最多写一次，methodology.json 约 1MB）。"""
        if not self._hits_dirty:
            return False
        now = time.time()
        if not force and now - self._last_flush < self._HIT_FLUSH_SECONDS:
            return False
        self._last_flush = now
        self._hits_dirty = False
        self._save()
        return True

    @staticmethod
    def _recency_tier(m: Dict, now: datetime.datetime) -> int:
        """0=近 3 天命中过 / 1=近 30 天 / 2=更早 / 3=从未命中。越小越靠前。"""
        lh = m.get("last_hit")
        if not lh:
            return 3
        try:
            t = datetime.datetime.fromisoformat(str(lh))
        except ValueError:
            return 3
        days = (now - t).total_seconds() / 86400.0
        if days <= 3:
            return 0
        if days <= 30:
            return 1
        return 2

    def _check_dormant_once(self) -> None:
        """每个进程最多检查一次：长期零命中的条目转 dormant。"""
        global _DORMANT_CHECKED
        if _DORMANT_CHECKED:
            return
        _DORMANT_CHECKED = True
        try:
            self.maybe_dormant()
        except Exception:  # noqa: BLE001
            pass

    def maybe_dormant(self, days: int = 0) -> int:
        """把「从未命中 且 创建超过 N 天」的活跃条目转为 dormant。

        dormant 不进目录、不参与关键词自动触发，但 method_find 仍能搜到——
        治的是"目录里躺着六成从没用过的条目"，不是删知识。
        没有 last_hit/hit_count 的历史条目同样适用（它们确实从未被激活）。
        """
        limit_days = days or self._DORMANT_DAYS
        now = datetime.datetime.now()
        n = 0
        for m in self.data.get("methods", []):
            if m.get("status", "active") != "active":
                continue
            if int(m.get("hit_count") or 0) > 0:
                continue
            try:
                t = datetime.datetime.fromisoformat(str(m.get("created_at") or ""))
            except ValueError:
                continue          # 没有创建时间的不动（保守）
            if (now - t).days >= limit_days:
                m["status"] = "dormant"
                m["dormant_at"] = now.isoformat(timespec="seconds")
                n += 1
        if n:
            self._save()
        return n

    def confidence(self, text: str, limit: int = 5):
        """置信度：返回 (命中数, top1分, 是否低置信)。

        2026-10-22 校准：两条通路的分数不在同一量纲上——关键词分是"命中词长度和"，
        BM25 分是全库 IDF 加权，前者天然偏小。原来统一用 top1<5.0 判低置信，实测：
          关键词路径 top1 中位数 3.57（p25 1.99）→ 一半以上的正常轮次被误报"低置信"
          BM25 路径   top1 中位数 6.79（p25 5.85）→ 几乎永远不报
        改成按通路阈值（都取各自 p25），让两条通路的"低置信率"都落在 1/4 左右。
        """
        _s = self.match_scored(text, limit=limit)
        _n = len(_s)
        _top = _s[0][0] if _s else 0.0
        thr = (_CONF_TOP_BM25 if self._last_path == "bm25" else _CONF_TOP_KEYWORD)
        return _n, _top, (_n < _CONF_MIN_HITS or _top < thr)
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

    def _tool_index(self):
        """工具名 → 提到它的方法论条目（懒构建，与 BM25 索引一起失效）。

        2026-10-22：旧版场景预加载是硬编码 3 条映射（net_search→343/344、mcp_connect→341），
        实测 168 轮日志里 预加载 一直是 0 —— 覆盖面太窄，等于没生效。
        改成从语料里自动发现：方法论正文/关键词里提到某个工具标识符（`fs_read`、`cmd_run`…），
        那么"她正在调这个工具"就是最强的前置信号，不需要人工维护映射表。
        实测 115 个真实工具里有 38 个被方法论正文提到（cmd_run 29 条、net_fetch 16 条…）。
        """
        if self._tool_cache is not None:
            return self._tool_cache
        idx: Dict[str, List[Dict]] = {}
        for m in self.list():
            text = " ".join([
                m.get("name") or "", m.get("scene") or "", m.get("method") or "",
                " ".join(m.get("keywords") or []),
            ]).lower()
            for tok in set(_TOOL_TOKEN_RE.findall(text)):
                idx.setdefault(tok, []).append(m)
        self._tool_cache = idx
        return idx

    def preload_by_activity(self, recent_tools: List[str], recent_text: str = "") -> List[Dict]:
        """场景预加载：最近调过哪个工具，就带上"讲这个工具怎么用/别踩什么坑"的条目。

        recent_tools: 最近 N 步调过的工具名（按时间顺序，末尾最新）；recent_text 兼容旧调用。

        2026-10-22：按「工具的新旧」分配名额——最近用过的工具给 2 条，更早的给 1 条。
        之前把所有候选混在一起按 importance 排，结果最近在用的工具被旧工具的高权重条目挤掉
        （实测用 fs_delete，预加载出来的却是"用 cmd_run 列表核实文件"那几条）。
        """
        tools = [t for t in (recent_tools or []) if t][-5:]
        if not tools:
            return []
        idx = self._tool_index()
        now = datetime.datetime.now()
        hits: List[Dict] = []
        seen = set()
        for rank, tool in enumerate(reversed(tools)):        # 最新的工具排第一
            cand = [m for m in idx.get(tool.strip().lower(), [])
                    if m.get("id") not in seen and m.get("status", "active") == "active"]
            if not cand:
                continue
            cand.sort(key=lambda m: (self._recency_tier(m, now), -float(m.get("importance") or 0),
                                     -int(m.get("id") or 0)))
            for m in cand[:(2 if rank == 0 else 1)]:
                seen.add(m.get("id"))
                hits.append(m)
            if len(hits) >= 3:
                break
        return hits[:3]

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
