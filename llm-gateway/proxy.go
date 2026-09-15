package main

// proxy.go —— /v1/chat 代理（OpenAI 兼容）+ 错误分型
// 关键：手写 HTTP，不做 SDK；错误以 HTTP 状态码优先分型，杜绝 SDK 崩 AttributeError 类问题。

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// chatRequest 白绫→网关的请求体（OpenAI chat.completions 格式子集）
type chatRequest struct {
	Messages    []map[string]any `json:"messages"`
	Tools       []map[string]any `json:"tools,omitempty"`
	ToolChoice  string           `json:"tool_choice,omitempty"`
	Temperature *float64         `json:"temperature,omitempty"`
	MaxTokens   *int             `json:"max_tokens,omitempty"`
	Thinking    map[string]any   `json:"thinking,omitempty"` // DeepSeek 思考预算（如 {"type":"enabled","budget_tokens":4096}）
}

// ChatResult 网关→白绫的响应
type ChatResult struct {
	Reply           string           `json:"content,omitempty"`
	Reasoning       string           `json:"reasoning_content,omitempty"`
	ToolCalls       []map[string]any `json:"tool_calls,omitempty"`
	FinishReason    string           `json:"finish_reason,omitempty"`
	Usage           map[string]int   `json:"usage,omitempty"`
	Raw             json.RawMessage  `json:"raw,omitempty"`
	FailoverNote    string           `json:"failover_note,omitempty"`
	FailoverApplied bool             `json:"failover_applied,omitempty"`
	Error           string           `json:"error,omitempty"`
	ErrorCode       string           `json:"error_code,omitempty"`
	ModelUsed       string           `json:"model,omitempty"`
	BaseUsed        string           `json:"base_url,omitempty"`
}

// ErrClass 错误分型（对齐设计文档第 6.1 节）
func ErrClass(status int, body string) string {
	bodyLow := strings.ToLower(body)
	switch {
	case status == 401:
		return "auth"
	case status == 403:
		return "forbidden"
	case status == 429 || strings.Contains(bodyLow, "rate limit"):
		return "rate_limit"
	case status == 404 || strings.Contains(bodyLow, "model not found") || strings.Contains(bodyLow, "model_not_found"):
		return "model_missing"
	case status >= 500 && status < 600, strings.Contains(bodyLow, "no available channel"):
		return "unavailable"
	case strings.Contains(bodyLow, "timed out"), strings.Contains(bodyLow, "timeout"):
		return "timeout"
	case strings.Contains(bodyLow, "connection"), strings.Contains(bodyLow, "no such host"):
		return "network"
	default:
		if status > 0 {
			return fmt.Sprintf("http_%d", status)
		}
		return "error"
	}
}

// chatOnce 单次调用某 base/key/model，返回解析后的 openai 响应
func chatOnce(base, key, model string, req chatRequest, temperature float64, timeout time.Duration) (map[string]any, int, error) {
	u := normalizeURL(base) + "/chat/completions"
	payload := map[string]any{
		"model":    model,
		"messages": req.Messages,
	}
	if len(req.Tools) > 0 {
		payload["tools"] = req.Tools
		if req.ToolChoice != "" {
			payload["tool_choice"] = req.ToolChoice
		}
	}
	if req.Temperature != nil {
		payload["temperature"] = *req.Temperature
	} else {
		payload["temperature"] = temperature
	}
	if req.MaxTokens != nil && *req.MaxTokens > 0 {
		payload["max_tokens"] = *req.MaxTokens
	}
	if req.Thinking != nil {
		payload["thinking"] = req.Thinking
	}
	data, _ := json.Marshal(payload)
	hreq, err := http.NewRequest("POST", u, bytes.NewReader(data))
	if err != nil {
		return nil, 0, err
	}
	hreq.Header.Set("Content-Type", "application/json")
	hreq.Header.Set("Authorization", "Bearer "+key)
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(hreq)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20)) // 4MB 上限
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, resp.StatusCode, fmt.Errorf("HTTP %d: %s", resp.StatusCode, truncate(string(raw), 300))
	}
	var parsed map[string]any
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, resp.StatusCode, fmt.Errorf("响应解析失败: %v (%.120s)", err, string(raw))
	}
	return parsed, resp.StatusCode, nil
}

// parseChatResp openai 响应 → ChatResult
func parseChatResp(parsed map[string]any, modelUsed, baseUsed string) ChatResult {
	r := ChatResult{ModelUsed: modelUsed, BaseUsed: baseUsed}
	choices, _ := parsed["choices"].([]any)
	if len(choices) > 0 {
		first, _ := choices[0].(map[string]any)
		if msg, ok := first["message"].(map[string]any); ok {
			if c, ok := msg["content"].(string); ok {
				r.Reply = c
			}
			if rc, ok := msg["reasoning_content"].(string); ok {
				r.Reasoning = rc
			}
			if tc, ok := msg["tool_calls"].([]any); ok {
				// openai 返回嵌套 {id, function:{name, arguments}} → 扁平化为白绫格式 {id,name,arguments}
				for _, item := range tc {
					m, _ := item.(map[string]any)
					fn, _ := m["function"].(map[string]any)
					r.ToolCalls = append(r.ToolCalls, map[string]any{
						"id":        m["id"],
						"name":      fn["name"],
						"arguments": fn["arguments"],
					})
				}
			}
		}
		if fr, ok := first["finish_reason"].(string); ok {
			r.FinishReason = fr
		}
	}
	if u, ok := parsed["usage"].(map[string]any); ok {
		usage := map[string]int{}
		for k, v := range u {
			if n, ok := v.(float64); ok {
				usage[k] = int(n)
			}
		}
		r.Usage = usage
	}
	return r
}
