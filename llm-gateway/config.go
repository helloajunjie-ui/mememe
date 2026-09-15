package main

// config.go —— config/llm.json 读写（唯一配置源）
// 核心不变式：base_url 与 api_key 永远匹配（按 base 匹配渠道源 key → 顶层 → 环境变量）。

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Source 渠道源
type Source struct {
	Name    string   `json:"name"`
	BaseURL string   `json:"base_url"`
	APIKey  string   `json:"api_key,omitempty"`
	Enabled bool     `json:"enabled"`
	Models  []string `json:"models,omitempty"`
}

// LLMConfig 与现有 config/llm.json 结构完全兼容
type LLMConfig struct {
	BaseURL        string   `json:"base_url"`
	APIKey         string   `json:"api_key,omitempty"`
	Model          string   `json:"model"`
	Temperature    float64  `json:"temperature"`
	MaxTokens      int      `json:"max_tokens"`
	Sources        []Source `json:"sources"`
	PreferredBase  string   `json:"preferred_base_url"`
	PreferredModel string   `json:"preferred_model"`
	ModelsBaseURL  string   `json:"models_base_url"`
	Models         []string `json:"models"`
	ModelsUpdated  any      `json:"models_updated_at"` // 兼容历史 string/int
}

// ConfigStore 配置存储：进程内锁 + 原子写 + mtime 热重载
type ConfigStore struct {
	mu      sync.Mutex
	path    string // config/llm.json
	cfg     LLMConfig
	envName string // 兜底环境变量名
	lastMod int64  // 文件 mtime（热重载检测）
}

func NewConfigStore(configDir, envName string) (*ConfigStore, error) {
	if envName == "" {
		envName = "BAILING_API_KEY"
	}
	s := &ConfigStore{
		path:    filepath.Join(configDir, "llm.json"),
		envName: envName,
	}
	if err := s.load(); err != nil {
		return nil, fmt.Errorf("加载配置失败: %w", err)
	}
	return s, nil
}

// ReloadIfChanged 外部改动 llm.json → 热重载（每请求入口调用）；返回是否重载
func (s *ConfigStore) ReloadIfChanged() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	fi, err := os.Stat(s.path)
	if err != nil {
		return false
	}
	mod := fi.ModTime().UnixNano()
	if mod == s.lastMod {
		return false
	}
	s.lastMod = mod
	if err := s.loadLocked(); err != nil {
		fmt.Printf("[config] 热重载失败（沿用内存配置）: %v\n", err)
		return false
	}
	return true
}

func (s *ConfigStore) load() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.loadLocked()
}

func (s *ConfigStore) loadLocked() error {
	data, err := os.ReadFile(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			// 缺失 → 种子初始化（最小默认，等 /admin/config 或扫描填充）
			s.cfg = LLMConfig{
				BaseURL:     "https://api.deepseek.com",
				Model:       "deepseek-chat",
				Temperature: 0.7,
				MaxTokens:   4096,
			}
			return s.saveLocked()
		}
		return err
	}
	if fi, err2 := os.Stat(s.path); err2 == nil {
		s.lastMod = fi.ModTime().UnixNano()
	}
	if err := json.Unmarshal(data, &s.cfg); err != nil {
		// 损坏 → 备份后重建（自愈），不阻塞启动
		backup := s.path + ".corrupt-" + ts()
		_ = os.Rename(s.path, backup)
		s.cfg = LLMConfig{
			BaseURL:     "https://api.deepseek.com",
			Model:       "deepseek-chat",
			Temperature: 0.7,
			MaxTokens:   4096,
		}
		_ = s.saveLocked()
	}
	return nil
}

// saveLocked 原子写（调用方须持锁）
func (s *ConfigStore) saveLocked() error {
	data, err := json.MarshalIndent(s.cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, s.path) // Windows 下 Go 的 Rename 覆盖已存在文件
}

// Get 返回当前配置副本（外部不得持有锁，取副本即可）
func (s *ConfigStore) Get() LLMConfig {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.cfg
}

// Update 应用修改并原子落盘；fn 在锁内修改 cfg。
func (s *ConfigStore) Update(fn func(*LLMConfig)) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	fn(&s.cfg)
	return s.saveLocked()
}

// KeyFor 按 base_url 从渠道源匹配 key；匹配不到返回空
func KeyFor(cfg *LLMConfig, base string) string {
	b := strings.TrimRight(base, "/")
	for _, src := range cfg.Sources {
		if strings.TrimRight(src.BaseURL, "/") == b && strings.TrimSpace(src.APIKey) != "" {
			return strings.TrimSpace(src.APIKey)
		}
	}
	return ""
}

// ResolveKey 一致性解析：按 base 匹配渠道源 → 顶层 → 环境变量
func (s *ConfigStore) ResolveKey(cfg *LLMConfig) string {
	if cfg == nil {
		c := s.Get()
		cfg = &c
	}
	if k := KeyFor(cfg, cfg.BaseURL); k != "" {
		return k
	}
	if strings.TrimSpace(cfg.APIKey) != "" {
		return strings.TrimSpace(cfg.APIKey)
	}
	return os.Getenv(s.envName)
}

// EnsureKeyConsistency 自愈：顶层 api_key 与当前 base 的渠道 key 失配 → 修正并落盘
func (s *ConfigStore) EnsureKeyConsistency() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	b := strings.TrimRight(s.cfg.BaseURL, "/")
	if b == "" {
		return false
	}
	var srcKey, srcName string
	for _, src := range s.cfg.Sources {
		if strings.TrimRight(src.BaseURL, "/") == b && strings.TrimSpace(src.APIKey) != "" {
			srcKey = strings.TrimSpace(src.APIKey)
			srcName = src.Name
			break
		}
	}
	if srcKey != "" && strings.TrimSpace(s.cfg.APIKey) != srcKey {
		s.cfg.APIKey = srcKey
		if err := s.saveLocked(); err == nil {
			fmt.Printf("[llm] 自愈: 顶层 api_key 与 base_url 失配，已按渠道「%s」修正\n", srcName)
			return true
		}
	}
	return false
}

// SourceByName 按渠道名找源
func SourceByName(cfg *LLMConfig, name string) *Source {
	for i := range cfg.Sources {
		if cfg.Sources[i].Name == name {
			return &cfg.Sources[i]
		}
	}
	return nil
}

// EnabledSources 启用的渠道
func EnabledSources(cfg *LLMConfig) []Source {
	var out []Source
	for _, src := range cfg.Sources {
		if src.Enabled {
			out = append(out, src)
		}
	}
	return out
}

func ts() string {
	return fmt.Sprintf("%d", nowUnix())
}
