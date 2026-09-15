package main

// api.go —— HTTP 管理 API（路径与现有前端兼容，未来前端切 base 即可无缝使用）
// 注册两组路径：/admin/*（网关语义）+ /api/*（与素月 webui 现有前端路径对齐）

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"
)

// failoverNow 当前是否处于容灾临时切换（当前 model ≠ 锚点 model）
func (g *Gateway) failoverNow(c *LLMConfig) bool {
	return c.PreferredModel != "" && (c.Model != c.PreferredModel)
}

func maskKey(k string) string {
	if len(k) <= 8 {
		return "****"
	}
	return k[:4] + "..." + k[len(k)-4:]
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func bodyJSON(r *http.Request, out any) error {
	defer r.Body.Close()
	return json.NewDecoder(r.Body).Decode(out)
}

// ---- 探活 ----

func (g *Gateway) hHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"ok": true, "ready": true, "service": "bailing-llm-gateway"})
}

// ---- /v1/chat 对话代理 ----

func (g *Gateway) hChat(w http.ResponseWriter, r *http.Request) {
	var req chatRequest
	if err := bodyJSON(r, &req); err != nil {
		writeJSON(w, 400, ChatResult{Error: "请求体解析失败: " + err.Error(), ErrorCode: "error"})
		return
	}
	if len(req.Messages) == 0 {
		writeJSON(w, 400, ChatResult{Error: "messages 不能为空", ErrorCode: "error"})
		return
	}
	// 入口：外部改配置热重载 + key 一致性自愈 + 锚点回切
	g.cfg.ReloadIfChanged()
	if g.cfg.EnsureKeyConsistency() {
		g.cfg.Update(func(c *LLMConfig) {}) // 触发落盘一致性（Ensure 已落盘）
	}
	if note := g.revertPreferred(); note != "" {
		fmt.Printf("[llm] %s\n", note)
	}

	cfg := g.cfg.Get()
	base := strings.TrimRight(cfg.BaseURL, "/")
	key := g.cfg.ResolveKey(&cfg)
	if key == "" {
		writeJSON(w, 503, ChatResult{Error: "未配置 API Key（渠道行填写或设置环境变量 BAILING_API_KEY）", ErrorCode: "auth"})
		return
	}
	if base == "" || cfg.Model == "" {
		writeJSON(w, 503, ChatResult{Error: "未配置 API 地址/模型", ErrorCode: "error"})
		return
	}

	parsed, status, err := chatOnce(base, key, cfg.Model, req, g.temperatureOf(), 60*time.Second)
	if err == nil {
		res := parseChatResp(parsed, cfg.Model, base)
		writeJSON(w, 200, res)
		return
	}
	// 失败 → 分型 → 容灾（冷却窗口内）
	cls := ErrClass(status, err.Error())
	if g.canFailover() {
		res := g.tryFailover(req, err.Error(), cls)
		writeJSON(w, 200, res)
		return
	}
	writeJSON(w, 200, ChatResult{Error: err.Error(), ErrorCode: cls,
		FailoverNote: "容灾冷却中，跳过自动切换"})
}

// ---- 配置 ----

func (g *Gateway) hConfigGet(w http.ResponseWriter, r *http.Request) {
	c := g.cfg.Get()
	writeJSON(w, 200, map[string]any{
		"ok":                 true,
		"base_url":           c.BaseURL,
		"model":              c.Model,
		"temperature":        c.Temperature,
		"max_tokens":         c.MaxTokens,
		"api_key_masked":     maskKey(c.APIKey),
		"has_key":            c.APIKey != "",
		"preferred_base_url": c.PreferredBase,
		"preferred_model":    c.PreferredModel,
		"failover":           g.failoverNow(&c),
	})
}

func (g *Gateway) hConfigSave(w http.ResponseWriter, r *http.Request) {
	var body struct {
		BaseURL     string  `json:"base_url"`
		APIKey      string  `json:"api_key"`
		Model       string  `json:"model"`
		Temperature *string `json:"temperature"`
		MaxTokens   *int    `json:"max_tokens"`
	}
	if err := bodyJSON(r, &body); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "请求体解析失败"})
		return
	}
	cur := g.cfg.Get()
	// 坏模型校验：健康表明确 bad → 拒绝（防选到 503 坏模型）
	if body.Model != "" {
		if e, ok := g.health.Entry(strings.TrimRight(cur.BaseURL, "/"), body.Model); ok && !e.Ok {
			writeJSON(w, 400, map[string]any{"ok": false,
				"error": fmt.Sprintf("模型 %s 当前不可用（%s）——请选择可用模型，或先点「扫描健康」刷新", body.Model, truncate(e.Error, 140))})
			return
		}
	}
	_ = g.cfg.Update(func(c *LLMConfig) {
		if strings.TrimSpace(body.BaseURL) != "" {
			c.BaseURL = strings.TrimRight(strings.TrimSpace(body.BaseURL), "/")
		}
		if strings.TrimSpace(body.Model) != "" {
			c.Model = strings.TrimSpace(body.Model)
		}
		if body.Temperature != nil && strings.TrimSpace(*body.Temperature) != "" {
			var f float64
			if _, err := fmt.Sscanf(*body.Temperature, "%f", &f); err == nil {
				c.Temperature = f
			}
		}
		if body.MaxTokens != nil && *body.MaxTokens > 0 { // 0/负数不覆盖
			c.MaxTokens = *body.MaxTokens
		}
		if strings.TrimSpace(body.APIKey) != "" {
			c.APIKey = strings.TrimSpace(body.APIKey)
		} else {
			// 未显式传 key：按新 base 解析渠道/env（防错配）
			if k := g.cfg.ResolveKey(c); k != "" {
				c.APIKey = k
			}
		}
		// 用户手动保存 = 锚点
		c.PreferredBase = c.BaseURL
		c.PreferredModel = c.Model
	})
	c2 := g.cfg.Get()
	writeJSON(w, 200, map[string]any{
		"ok": true, "llm_ready": c2.APIKey != "" && c2.BaseURL != "" && c2.Model != "",
		"base_url": c2.BaseURL, "model": c2.Model, "temperature": c2.Temperature,
		"max_tokens": c2.MaxTokens, "api_key_masked": maskKey(c2.APIKey), "has_key": c2.APIKey != "",
		"preferred_base_url": c2.PreferredBase, "preferred_model": c2.PreferredModel,
		"failover": g.failoverNow(&c2),
	})
}

func (g *Gateway) hConfigTest(w http.ResponseWriter, r *http.Request) {
	var body struct {
		BaseURL string `json:"base_url"`
		APIKey  string `json:"api_key"`
		Model   string `json:"model"`
	}
	if err := bodyJSON(r, &body); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "请求体解析失败"})
		return
	}
	cur := g.cfg.Get()
	base := strings.TrimRight(body.BaseURL, "/")
	if base == "" {
		base = cur.BaseURL
	}
	model := strings.TrimSpace(body.Model)
	if model == "" {
		model = cur.Model
	}
	key := strings.TrimSpace(body.APIKey)
	if key == "" {
		key = KeyFor(&cur, base)
	}
	if key == "" {
		key = cur.APIKey
	}
	if key == "" {
		key = g.cfg.ResolveKey(&cur)
	}
	e := Probe(base, key, model, 12*time.Second)
	if e.Ok {
		writeJSON(w, 200, map[string]any{"ok": true, "latency": round2(e.Latency), "model": model, "reply": "连接成功"})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": false, "error": e.Error, "model": model})
}

// ---- 模型列表 ----

func (g *Gateway) hModelsGet(w http.ResponseWriter, r *http.Request) {
	c := g.cfg.Get()
	base := strings.TrimRight(c.ModelsBaseURL, "/")
	usable, bad := g.health.UsableBad(base)
	writeJSON(w, 200, map[string]any{
		"ok": true, "models": c.Models, "models_base_url": c.ModelsBaseURL,
		"updated_at": c.ModelsUpdated, "usable": usable, "bad": bad,
	})
}

// FetchModelsFor 拉取端点模型列表
func (g *Gateway) FetchModelsFor(base, key string) ([]string, error) {
	if base == "" || key == "" {
		return nil, fmt.Errorf("未配置 API 地址/Key，无法获取模型列表")
	}
	u := normalizeURL(base) + "/models"
	req, _ := http.NewRequest("GET", u, nil)
	req.Header.Set("Authorization", "Bearer "+key)
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := ioReadAllLimit(resp.Body, 1<<20)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, truncate(string(raw), 160))
	}
	var parsed struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("响应解析失败: %v", err)
	}
	seen := map[string]bool{}
	var ids []string
	for _, d := range parsed.Data {
		if d.ID != "" && !seen[d.ID] {
			seen[d.ID] = true
			ids = append(ids, d.ID)
		}
	}
	sort.Strings(ids)
	if len(ids) == 0 {
		return nil, fmt.Errorf("端点未返回任何模型")
	}
	return ids, nil
}

func (g *Gateway) hModelsFetch(w http.ResponseWriter, r *http.Request) {
	var body struct {
		BaseURL string `json:"base_url"`
		APIKey  string `json:"api_key"`
	}
	if err := bodyJSON(r, &body); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "请求体解析失败"})
		return
	}
	cur := g.cfg.Get()
	base := strings.TrimRight(body.BaseURL, "/")
	if base == "" {
		base = cur.BaseURL
	}
	key := strings.TrimSpace(body.APIKey)
	if key == "" {
		key = KeyFor(&cur, base)
	}
	if key == "" {
		key = cur.APIKey
	}
	ids, err := g.FetchModelsFor(base, key)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": "获取模型列表失败: " + err.Error()})
		return
	}
	_ = g.cfg.Update(func(c *LLMConfig) {
		c.ModelsBaseURL = base
		c.Models = ids
		c.ModelsUpdated = nowUnix()
	})
	writeJSON(w, 200, map[string]any{"ok": true, "models": ids, "count": len(ids), "source": base})
}

// ---- 健康 ----

func (g *Gateway) hHealthGet(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{
		"ok": true,
		"health": map[string]any{
			"sources":    g.health.Snapshot(),
			"_updated_at": g.health.Updated,
		},
	})
}

func (g *Gateway) hScan(w http.ResponseWriter, r *http.Request) {
	sum := g.ScanAll()
	writeJSON(w, 200, map[string]any{
		"ok": true,
		"health": map[string]any{
			"sources":    g.health.Snapshot(),
			"_updated_at": g.health.Updated,
		},
		"summary": sum,
	})
}

// ---- 渠道 ----

func (g *Gateway) hSourcesGet(w http.ResponseWriter, r *http.Request) {
	c := g.cfg.Get()
	type srcView struct {
		Name    string   `json:"name"`
		BaseURL string   `json:"base_url"`
		KeyMask string   `json:"api_key_masked"`
		Models  []string `json:"models"`
		Enabled bool     `json:"enabled"`
	}
	var list []srcView
	for _, s := range c.Sources {
		list = append(list, srcView{Name: s.Name, BaseURL: s.BaseURL,
			KeyMask: maskKey(s.APIKey), Models: s.Models, Enabled: s.Enabled})
	}
	writeJSON(w, 200, map[string]any{
		"ok": true, "sources": list,
		"current": map[string]any{"base_url": c.BaseURL, "model": c.Model},
	})
}

func (g *Gateway) hSourcesSave(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Sources []Source `json:"sources"`
	}
	if err := bodyJSON(r, &body); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "请求体解析失败"})
		return
	}
	if len(body.Sources) == 0 {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "至少保留一个有效渠道"})
		return
	}
	_ = g.cfg.Update(func(c *LLMConfig) {
		// 旧源按 base_url 匹配（改名不丢 key/models）
		oldByBase := map[string]Source{}
		for _, o := range c.Sources {
			oldByBase[strings.TrimRight(o.BaseURL, "/")] = o
		}
		var cleaned []Source
		for _, s := range body.Sources {
			s.BaseURL = strings.TrimRight(strings.TrimSpace(s.BaseURL), "/")
			if s.BaseURL == "" {
				continue
			}
			o, ok := oldByBase[s.BaseURL]
			if ok {
				if strings.TrimSpace(s.APIKey) == "" {
					s.APIKey = o.APIKey
				}
				if len(s.Models) == 0 {
					s.Models = o.Models
				}
			}
			if !s.Enabled {
				s.Enabled = false
			}
			cleaned = append(cleaned, s)
		}
		c.Sources = cleaned
		// 同步顶层 key：当前 base 对应渠道 key 非空 → 写顶层（防错配）
		for _, s := range cleaned {
			if strings.TrimRight(s.BaseURL, "/") == strings.TrimRight(c.BaseURL, "/") && strings.TrimSpace(s.APIKey) != "" {
				c.APIKey = strings.TrimSpace(s.APIKey)
				break
			}
		}
	})
	c2 := g.cfg.Get()
	writeJSON(w, 200, map[string]any{"ok": true, "count": len(c2.Sources), "sources": c2.Sources})
}

func (g *Gateway) hSwitch(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SourceName string `json:"source_name"`
		Model      string `json:"model"`
	}
	if err := bodyJSON(r, &body); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "请求体解析失败"})
		return
	}
	name := strings.TrimSpace(body.SourceName)
	model := strings.TrimSpace(body.Model)
	cur := g.cfg.Get()
	src := SourceByName(&cur, name)
	if src == nil {
		writeJSON(w, 200, map[string]any{"ok": false, "error": fmt.Sprintf("渠道「%s」不存在", name)})
		return
	}
	base := strings.TrimRight(src.BaseURL, "/")
	key := strings.TrimSpace(src.APIKey)
	if key == "" {
		key = g.cfg.ResolveKey(&cur)
	}
	if base == "" {
		writeJSON(w, 200, map[string]any{"ok": false, "error": fmt.Sprintf("渠道「%s」缺少 API 地址", name)})
		return
	}
	if key == "" {
		writeJSON(w, 200, map[string]any{"ok": false, "error": fmt.Sprintf("渠道「%s」未配置 API Key（渠道行填写或设置环境变量 BAILING_API_KEY）", name)})
		return
	}
	if model == "" {
		model = ""
		if len(src.Models) > 0 {
			model = src.Models[0]
		}
	}
	if model == "" {
		writeJSON(w, 200, map[string]any{"ok": false, "error": "渠道无可用模型，请先扫描健康"})
		return
	}
	// probe 验证通过才切换（防切坏模型）
	e := Probe(base, key, model, 8*time.Second)
	if !e.Ok {
		writeJSON(w, 200, map[string]any{"ok": false, "error": fmt.Sprintf("模型 %s 不可用: %s", model, e.Error)})
		return
	}
	if err := g.switchTo(base, key, model); err != nil {
		writeJSON(w, 200, map[string]any{"ok": false, "error": "切换失败: " + err.Error()})
		return
	}
	// 用户手动切换 = 锚点
	_ = g.cfg.Update(func(c *LLMConfig) {
		c.PreferredBase = base
		c.PreferredModel = model
	})
	g.health.Record(base, model, e)
	g.health.Save()
	writeJSON(w, 200, map[string]any{"ok": true, "base_url": base, "model": model})
}

// ioReadAllLimit 限长读取
func ioReadAllLimit(r ioReader, n int64) ([]byte, error) {
	return ioReadAllN(r, n)
}
