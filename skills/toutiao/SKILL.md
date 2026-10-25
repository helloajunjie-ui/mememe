---
name: toutiao
description: "头条发图文/视频/回评论/查数据前扫一眼：每条只讲别碰什么、碰了会怎样。41 次踩坑蒸馏。"
triggers: [tt_publish, tt_image_post, tt_refresh, tt_overview, tt_analytics, 头条, 发图文, 发文章]
---

# 头条 · 动手前扫一眼（30 秒）

你能看到这份说明，说明接下来要动头条。下面每条都是踩过的坑；不认识的名词照原样搜原文，别猜。

## 别碰，碰了会怎样

**1. 别信 tt_publish 返回的 url。**
它恒为 `mp.toutiao.com/profile_v4/graphic/publish`，是发布页地址、不是文章链接。拿去交付，对方点开是自己后台——这篇等于白写。要真链接：tt_refresh 回读列表里的 articleID / gidStr。

**2. 别走 urara 发布 CLI 的「文件上传」分支**（即 tt_publish 传 file 参数那条路）。
内部 click 全挂 `.catch(()=>{})`，选择器没匹配也返回 `success=true`。**假成功**——以为发了，实际什么都没发。走传 title + content 的图文链路。

**3. 别用 net_fetch 抓 toutiao.com 的文章页。**
返回 http 200，但 title / text 全空。curl 公网直连实测同一页：72914 字节，`<body></body>` 是空的，只有 2 段混淆的 `_$jsvmprt` 脚本。〔原因不明：是 JS 渲染还是反爬壳页，没查清〕结论不变：拿不到正文，读正文只能走真浏览器（browserskill navigate + evaluate `document.body.innerText`）。

**4. 别把 content list（后台列表接口）返回的 articleBase.content / abstractText 当正文。**
字段存在，值是空串 `len=0`——接口本身不存正文。读到空不是「没发布」。要正文只有两条路：本地草稿文件（先 fs_search），或 browserskill 带登录态读 `innerText`。

**5. 别数记忆/上下文里显示的「已发篇数」。**
窗口会载入往期档案，同一个数字会翻倍。真数要拉产出清单读 publish_time 时间戳。

**6. 别拿作品列表页（tt_refresh / 后台列表）显示的单篇阅读数当指标。**〔偶发：看到 0 先怀疑没取到；0 的成因未查明〕
页面常返 0 占位。指标走 tt_analytics 的 works / fans。`tt_overview` 的 count 只是「当前页解析条数」（每页 10 条、过滤微头条后剩 7），不是总数。

**7. 同一账号别并发起浏览器实例。**〔多因：同时起 ≥2 个、且共用同一 user-data-dir / profile〕
静默失败：stdout 0 字节，真错误只在 stderr（未登录 / auth-guard），极易误判成「还在跑」白等。串行；非并发不可就错峰 ≥10s，或先读 stderr 判断真实状态再决定重发。

**8. 别先发布再改标题。**
标题按字符算（中文 / ASCII / emoji 各算 1），上限 30。超了不是报错，是静默截断。改标题 = 删了重发，展现和推荐全部归零。发之前算准。

**9. 别把 markdown 标记写进正文文本。**
tt_publish `--format text` 下，`#` `**` 原样显示，读者看见一堆符号。要排版就写纯文本，或换支持富文本的通道。

## 动手时的骨架（一行）

写正文 → 画封面 → 标题算到 ≤30 → 发布 → **回读核验**，不信返回值。

## 名词不认识 / 细节有疑问

41 条原文在 methodology.json，按主题回查：
- 发布核验：#251 #295 #298 #308 #331
- 读正文：#307 #336 #661
- 标题 / 封面 / 配额：#776 #777
- 并发 / profile：#321 #322
- 全量：method_find(keyword="头条")

## 做完之后

按这份做完，无论顺不顺，回一遍 `skills/_SPEC.md` 的「回写」六条：
没防住的原地改、新坑加新条、过时的直接删（不留废弃标注）、随机/多因的补触发条件、查清了的把〔原因不明〕换成确定原因。

上次核对：2026-10-26
