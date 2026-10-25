---
name: toutiao
description: "头条发图文/发视频/回评论/查数据前必读：8 条真金白银的禁令 + 固定操作顺序 + 发布后核验清单。踩过 41 次坑蒸馏而成。"
triggers: [tt_publish, tt_image_post, tt_refresh, tt_overview, tt_analytics, browserskill, 头条, 发图文, 发文章]
---

# 头条 · 动手前扫这份

**看多久**：30 秒。看完就动手，别往下翻原文。

## 一、别踩（踩了必返工，全是我真付过代价的）

1. **别信 `tt_publish` 返回的 url** → 恒为发布页地址，零信息。要真链接走 `tt_refresh` 或 content list 回读 articleID。
2. **别用 CLI 的「本地上传/文件上传」分支** → 内部 click 全挂 `.catch(()=>{})`，选择器没匹配也照样返回 `success=true`。**假成功。** 走图文发布链路。
3. **别用 `net_fetch` 抓 toutiao.com** → 返回 200，但 title/text 全空（JS 渲染）。读正文只能走真实浏览器 `document.body.innerText`。
4. **别读后台 `articleBase.content` / `abstractText`** → 实测都是空串 `len=0`。
5. **别数记忆里的发布篇数** → 窗口会载入往期档案，数字翻倍。要数就去拉产出清单读时间戳。
6. **别拿作品列表页的单篇数据当指标** → 常返 0 占位。走 analytics 的 works/fans 接口。
7. **同一账号别同秒批量起浏览器** → 静默失败：stdout 0 字节，错误只在 stderr（"未登录"/auth-guard），极易误判成"还在跑"。串行或错峰 ≥1 秒。
8. **别发了再删** → 标题超限当场改短，重发要付删除成本。

## 二、照做（顺序固定）

**发图文**
1. `article.txt` 写正文，`--format text`（**不夹 markdown，否则标记原样显示**）
2. `make_cover.py` 本地 PIL 画 1200×800 深色封面
3. 核算标题：**≤30 字符**（含空格与 ASCII）
4. 发布 → **回读核验，不看返回值**

**回评论**
`session_start` → `navigate` → **先探 login_mask 判登录态**（别凭印象说不能发）→ `bsk_fill` → click → **回读确认已发**

**查数据**
走 analytics works/fans，口径以它为准。

## 三、发布后核验（三缺一 = 没发出去）

- [ ] final_url 离开了编辑页
- [ ] 截图是发布完成态（不是卡在某个弹窗）
- [ ] 列表里搜得到这条

## 卡住了才回查原文

#251 #255 #295 #298 #299 #300 #307 #308 #311 #321 #322 #329 #330 #331 #336 #342 #351 #379 #380 #381 #382 #393 #448 #515 #516 #517 #518 #661 #679 #684 #697 #699 #700 #702 #704 #776 #777 #778 #779 #783 #824

（41 条全在 methodology.json。上面的禁令和步骤是重点，细节数字有疑问再按 id 查。）
