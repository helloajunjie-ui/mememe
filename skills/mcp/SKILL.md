---
name: mcp
triggers: [mcp_connect, mcp_install, mcp_source_install, mcp_disconnect, mcp_scan, 软件接口]
description: 连 MCP / 调 MCP 工具前后必须知道的坑
---

# MCP · 动手前扫一眼（30 秒）

## 选不选 MCP
- 别默认翻 MCP 市场是唯一路——目标有 API/OpenAPI，先用 gutenberg 从 API 面直接生成工具；现成 MCP 嫌烧 token，用 cli-use add 转成 CLI；两条都不行才翻市场或自写。
- 别在判「这个得自己写脚本」之前跳过 mcp_list——零成本只读，一次白干的代价大得多。

## 连接
- 别在 mcp_connect 成功后手动跑这个服务的 CLI start/launch——daemon 已由 MCP 托管，再起一次会重复启动，可能挂死整轮。
- 别以为连过一次就一直在——工具按需注入，空闲 30 分钟自动释放；要用先 mcp_list 看目录里有没有。

## 超时与报错
- 别在首次超时后就判工具坏了——先查该 daemon 端口是否 LISTENING；在监听就是冷启动慢（常超 MCP 包装层 25s 阈值），直接重试同一调用。
- 别在报超时后立刻重试——先查底层产物（操作日志、列表接口）有没有已经变化；可能已经成功，重试会做第二遍。
- 别卡在「MCP 工具没注入」上——读 mcp-service/servers/*/xxx_mcp.py 拿 ENTRY 路径，用 cmd_run 直接调底层 CLI。

## 数据可疑
- 别只盯着返回值猜——先读包装层源码看它怎么解析，再绕过包装层跑底层 CLI 拿原始 JSON 逐项对照，才能分清是底层数据错还是解析错。

## 改完代码 / 配置
- 别把「重启 mcp-service」当「重启核心」——核心是 8765（pythonw.exe），MCP 服务是 8767（python.exe），两个独立进程；改服务端代码，核心侧的工具定义不会跟着变。
- 别只改完文件就声称生效——核对文件 LastWriteTime < 进程 StartTime，才算真加载。
- 别起了服务再查端口——先 Get-NetTCPConnection -State Listen 确认目标端口空闲，被占用就换，后台 job 不一定回显 stderr。
- 别把 Windows 路径裸写进 Python 字符串（如 servers\urara\）——\u 会被当 unicode 转义，整个文件 SyntaxError 加载不了；用 r""" 或双反斜杠。

## 反复要跑的流程
- 别一直手动走多步流程——跑通一次就 tool_create 封成单工具，把踩过的坑写进 docstring。

## 卡住了回查原文
方法 id：156 244 246 249 262 263 298 309 312 326 341 438 618 636 828

上次核对: 2026-10-26
