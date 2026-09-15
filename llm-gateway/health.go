package main

// health.go —— config/llm_health.json 派生缓存 + probe + 并发扫描

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// HealthEntry 单模型健康记录
type HealthEntry struct {
	Ok        bool    `json:"ok"`
	Latency   float64 `json:"latency"`
	Error     string  `json:"error,omitempty"`
	CheckedAt any     `json:"checked_at,omitempty"` // 兼容 Python 历史 float/int
}

// SourceHealth 单个地址的健康信息
type SourceHealth struct {
	Models map[string]HealthEntry `json:"models"`
}

// HealthStore 健康表（与 Python model_health 文件格式一致：顶层按 base_url 分键）
type HealthStore struct {
	mu      sync.Mutex
	path    string
	Sources map[string]SourceHealth
	Updated any // 兼容 float/int/string
}

// UnmarshalJSON 兼容顶层分键格式：{base_url: {...}, "_updated_at": ...}
func (h *HealthStore) UnmarshalJSON(data []byte) error {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	h.Sources = map[string]SourceHealth{}
	for k, v := range raw {
		if k == "_updated_at" {
			_ = json.Unmarshal(v, &h.Updated)
			continue
		}
		var sh SourceHealth
		if err := json.Unmarshal(v, &sh); err != nil {
			return err
		}
		h.Sources[k] = sh
	}
	return nil
}

// MarshalJSON 顶层分键输出（与 Python 侧互读）
func (h *HealthStore) MarshalJSON() ([]byte, error) {
	out := map[string]any{}
	for k, v := range h.Sources {
		out[k] = v
	}
	out["_updated_at"] = h.Updated
	return json.Marshal(out)
}

func NewHealthStore(configDir string) *HealthStore {
	h := &HealthStore{
		path:    filepath.Join(configDir, "llm_health.json"),
		Sources: map[string]SourceHealth{},
	}
	data, err := readFile(h.path)
	if err == nil {
		if uerr := json.Unmarshal(data, h); uerr != nil {
			fmt.Printf("[health] 解析 %s 失败（将重建空健康表，可重新扫描）: %v\n", h.path, uerr)
			h.Sources = map[string]SourceHealth{}
		}
	}
	if h.Sources == nil {
		h.Sources = map[string]SourceHealth{}
	}
	return h
}

func (h *HealthStore) Save() {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.Updated = nowUnix()
	data, _ := json.MarshalIndent(h, "", "  ")
	tmp := h.path + ".tmp"
	_ = writeFile(tmp, data)
	_ = osRename(tmp, h.path)
}

// Entry 读取某 base+model 的健康记录（副本）
func (h *HealthStore) Entry(base, model string) (HealthEntry, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	src, ok := h.Sources[strings.TrimRight(base, "/")]
	if !ok {
		return HealthEntry{}, false
	}
	e, ok := src.Models[model]
	return e, ok
}

// OkModels 某 base 下健康表确认 ok 的模型（延迟升序）
func (h *HealthStore) OkModels(base string) []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	src, ok := h.Sources[strings.TrimRight(base, "/")]
	if !ok {
		return nil
	}
	type item struct {
		m string
		l float64
	}
	var items []item
	for m, e := range src.Models {
		if e.Ok {
			items = append(items, item{m, e.Latency})
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].l != items[j].l {
			return items[i].l < items[j].l
		}
		return items[i].m < items[j].m
	})
	out := make([]string, 0, len(items))
	for _, it := range items {
		out = append(out, it.m)
	}
	return out
}

// UsableBad 返回 (可用列表, 明确不可用列表)
func (h *HealthStore) UsableBad(base string) (usable, bad []string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	src, ok := h.Sources[strings.TrimRight(base, "/")]
	if !ok {
		return nil, nil
	}
	for m, e := range src.Models {
		if e.Ok {
			usable = append(usable, m)
		} else {
			bad = append(bad, m)
		}
	}
	sort.Strings(usable)
	sort.Strings(bad)
	return
}

// Snapshot 全量健康快照（API 展示）
func (h *HealthStore) Snapshot() map[string]SourceHealth {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := map[string]SourceHealth{}
	for k, v := range h.Sources {
		ms := map[string]HealthEntry{}
		for m, e := range v.Models {
			ms[m] = e
		}
		out[k] = SourceHealth{Models: ms}
	}
	return out
}

// Record 记录单模型探测结果
func (h *HealthStore) Record(base, model string, e HealthEntry) {
	h.mu.Lock()
	defer h.mu.Unlock()
	base = strings.TrimRight(base, "/")
	src, ok := h.Sources[base]
	if !ok {
		src = SourceHealth{Models: map[string]HealthEntry{}}
	}
	if src.Models == nil {
		src.Models = map[string]HealthEntry{}
	}
	e.CheckedAt = nowUnix()
	src.Models[model] = e
	h.Sources[base] = src
}

// normalizeURL OpenAI 兼容 URL 规范化：不以 /v1 结尾补 /v1（防打到根路径拿 HTML）
func normalizeURL(base string) string {
	base = strings.TrimRight(base, "/")
	if !strings.HasSuffix(base, "/v1") {
		base += "/v1"
	}
	return base
}

// Probe 单模型联通探测：POST /chat/completions max_tokens=4
func Probe(base, key, model string, timeout time.Duration) HealthEntry {
	u := normalizeURL(base) + "/chat/completions"
	body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"ping"}],"max_tokens":4}`, model)
	start := time.Now()
	req, err := http.NewRequest("POST", u, bytes.NewBufferString(body))
	if err != nil {
		return HealthEntry{Ok: false, Latency: time.Since(start).Seconds(), Error: err.Error()}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(req)
	lat := time.Since(start).Seconds()
	if err != nil {
		return HealthEntry{Ok: false, Latency: lat, Error: classifyNetErr(err)}
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return HealthEntry{Ok: true, Latency: lat}
	}
	return HealthEntry{Ok: false, Latency: lat, Error: fmt.Sprintf("HTTP %d: %s", resp.StatusCode, truncate(string(b), 200))}
}

// ScanAll 并发扫描全部渠道（源并发 + 源内模型并发），返回摘要
func (g *Gateway) ScanAll() map[string]ScanSummary {
	cfg := g.cfg.Get()
	srcs := EnabledSources(&cfg)
	type res struct {
		base string
		sum  ScanSummary
	}
	results := make(chan res, len(srcs))
	var wg sync.WaitGroup
	for _, src := range srcs {
		wg.Add(1)
		go func(src Source) {
			defer wg.Done()
			models, err := g.FetchModelsFor(src.BaseURL, src.APIKey)
			sum := ScanSummary{}
			if err != nil {
				sum.Error = err.Error()
				results <- res{src.BaseURL, sum}
				return
			}
			type mres struct {
				m string
				e HealthEntry
			}
			mc := make(chan mres, len(models))
			var mw sync.WaitGroup
			for _, m := range models {
				mw.Add(1)
				go func(m string) {
					defer mw.Done()
					mc <- mres{m, Probe(src.BaseURL, src.APIKey, m, 6 * time.Second)}
				}(m)
			}
			mw.Wait()
			close(mc)
			var okList []string
			for r := range mc {
				g.health.Record(src.BaseURL, r.m, r.e)
				if r.e.Ok {
					okList = append(okList, r.m)
				}
			}
			sort.Strings(okList)
			sum.Ok = len(okList)
			sum.Total = len(models)
			sum.OkModels = okList
			results <- res{src.BaseURL, sum}
		}(src)
	}
	wg.Wait()
	close(results)
	out := map[string]ScanSummary{}
	for r := range results {
		out[r.base] = r.sum
	}
	g.health.Save()
	// 回写各渠道可用模型列表（仅 ok）
	_ = g.cfg.Update(func(c *LLMConfig) {
		for i := range c.Sources {
			if s, ok := out[strings.TrimRight(c.Sources[i].BaseURL, "/")]; ok {
				c.Sources[i].Models = s.OkModels
			}
		}
	})
	return out
}

// ScanSummary 单源扫描摘要
type ScanSummary struct {
	Ok       int      `json:"ok"`
	Total    int      `json:"total"`
	OkModels []string `json:"ok_models,omitempty"`
	Error    string   `json:"error,omitempty"`
}

// classifyNetErr 网络层错误 → 短描述
func classifyNetErr(err error) string {
	s := err.Error()
	switch {
	case strings.Contains(s, "timeout"), strings.Contains(s, "deadline exceeded"):
		return "timeout"
	case strings.Contains(s, "connection refused"), strings.Contains(s, "no such host"),
		strings.Contains(s, "connection reset"), strings.Contains(s, "EOF"):
		return "network: " + truncate(s, 80)
	default:
		return truncate(s, 80)
	}
}

func truncate(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// ---- 小工具（避免引入 os 别名的混乱） ----

func readFile(p string) ([]byte, error) {
	return osReadFile(p)
}

func writeFile(p string, b []byte) error {
	return osWriteFile(p, b, 0o644)
}

func osRename(a, b string) error {
	return osRenameFn(a, b)
}
