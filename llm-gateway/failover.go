package main

// failover.go —— 容灾：候选生成 → probe 验证 → 切换 → 重试；锚点回切
// 设计约束：坏模型（健康表 bad）永不入候选；切换前必须实时 probe（ok 且延迟 ≤8s）。

import (
	"fmt"
	"strings"
	"time"
)

// failoverWindow 冷却：30s 窗口内最多 failoverFailMax 次（单消费方，防止死循环）
const (
	failoverWindow = 30 * time.Second
	failoverMax    = 2
	probeOKLatency = 8.0 // 秒
)

// candidates 候选：同源 ok 前 2 + 跨源 ok 前 2（坏模型自动排除）
func (g *Gateway) candidates(curBase, curModel string, skipSame bool) []cand {
	cfg := g.cfg.Get()
	var out []cand
	if !skipSame {
		for _, m := range g.health.OkModels(curBase) {
			if m == curModel {
				continue
			}
			out = append(out, cand{base: curBase, key: "", model: m})
			if len(out) >= 2 {
				break
			}
		}
	}
	for _, src := range EnabledSources(&cfg) {
		b := strings.TrimRight(src.BaseURL, "/")
		if b == "" || b == strings.TrimRight(curBase, "/") {
			continue
		}
		for _, m := range g.health.OkModels(b) {
			out = append(out, cand{base: b, key: strings.TrimSpace(src.APIKey), model: m})
			if len(out) >= 4 {
				return out
			}
		}
	}
	return out
}

type cand struct {
	base  string
	key   string
	model string
}

// tryFailover 容灾：分型 → 候选逐个 probe 验证 → 切换 → 重试一次。
// 返回最终 ChatResult（含切换说明）。
func (g *Gateway) tryFailover(req chatRequest, errMsg string, errClass string) ChatResult {
	cfg := g.cfg.Get()
	curBase := strings.TrimRight(cfg.BaseURL, "/")
	curModel := cfg.Model
	key := g.cfg.ResolveKey(&cfg)
	skipSame := errClass == "auth" || errClass == "forbidden"

	note := fmt.Sprintf("模型 %s 异常（%s），正在自动切换可用模型", curModel, errClass)
	for _, c := range g.candidates(curBase, curModel, skipSame) {
		ck := c.key
		if ck == "" {
			ck = key
		}
		if ck == "" {
			continue
		}
		e := Probe(c.base, ck, c.model, 5*time.Second)
		if !e.Ok || e.Latency > probeOKLatency {
			continue
		}
		// 切换（写配置 + 原子落盘）
		if err := g.switchTo(c.base, ck, c.model); err != nil {
			continue
		}
		g.health.Record(c.base, c.model, e)
		g.health.Save()
		// 重试当前请求
		parsed, status, err := chatOnce(c.base, ck, c.model, req, g.temperatureOf(), 60*time.Second)
		if err != nil {
			cls := ErrClass(status, err.Error())
			return ChatResult{Error: err.Error(), ErrorCode: cls,
				FailoverNote: fmt.Sprintf("%s → %s/%s，但重试仍失败（%s）", note, c.base, c.model, cls)}
		}
		res := parseChatResp(parsed, c.model, c.base)
		res.FailoverNote = fmt.Sprintf("%s → %s/%s，已重试成功", note, c.base, c.model)
		res.FailoverApplied = true
		return res
	}
	return ChatResult{Error: errMsg, ErrorCode: errClass,
		FailoverNote: "候选全部验证失败（无可切换的可用模型），已触发后台扫描供下一轮"}
}

// switchTo 写配置切换 base/key/model（含 key 一致性 + 锚点不动）
func (g *Gateway) switchTo(base, key, model string) error {
	return g.cfg.Update(func(c *LLMConfig) {
		c.BaseURL = base
		c.APIKey = key
		c.Model = model
	})
}

// temperatureOf 当前配置温度
func (g *Gateway) temperatureOf() float64 {
	c := g.cfg.Get()
	if c.Temperature <= 0 {
		return 0.7
	}
	return c.Temperature
}

// canFailover 冷却判断
func (g *Gateway) canFailover() bool {
	now := time.Now()
	if now.Sub(g.lastFailoverAt) > failoverWindow {
		g.lastFailoverAt = now
		g.failoverCount = 1
		return true
	}
	if g.failoverCount >= failoverMax {
		return false
	}
	g.failoverCount++
	return true
}

// revertPreferred 锚点回切：用户手动配置（preferred）恢复可用 → 自动切回（10 分钟防抖）
func (g *Gateway) revertPreferred() string {
	now := time.Now()
	if now.Sub(g.lastRevertAt) < 10*time.Minute {
		return ""
	}
	cfg := g.cfg.Get()
	pBase := strings.TrimRight(cfg.PreferredBase, "/")
	pModel := cfg.PreferredModel
	if pModel == "" {
		return ""
	}
	curBase := strings.TrimRight(cfg.BaseURL, "/")
	curModel := cfg.Model
	if pBase == curBase && pModel == curModel {
		return "" // 已是锚点
	}
	if pBase != curBase {
		return "" // 锚点源不同：不跨源回切（由用户手动/容灾管理）
	}
	e, ok := g.health.Entry(pBase, pModel)
	if !ok || !e.Ok {
		g.lastRevertAt = now
		return ""
	}
	if err := g.switchTo(pBase, KeyFor(&cfg, pBase), pModel); err != nil {
		return ""
	}
	g.lastRevertAt = now
	return fmt.Sprintf("锚点模型 %s 已恢复可用，自动切回", pModel)
}
