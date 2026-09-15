package main

// main.go —— 白绫 LLM 网关（Go 实现）
// 独立进程，只为白绫服务：对话代理 / 容灾 / 健康扫描 / 渠道与配置管理。
// 配置与健康数据沿用现有 config/llm.json、config/llm_health.json（无缝迁移）。

import (
	"flag"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// Gateway 网关主体
type Gateway struct {
	cfg     *ConfigStore
	health  *HealthStore
	// 容灾冷却状态
	lastFailoverAt time.Time
	failoverCount  int
	// 锚点回切防抖
	lastRevertAt time.Time
}

func main() {
	var (
		port     = flag.Int("port", 8766, "监听端口")
		configDir = flag.String("config", "", "配置目录（默认：程序运行目录上层的 config/）")
	)
	flag.Parse()

	dir := *configDir
	if dir == "" {
		wd, err := os.Getwd()
		if err != nil {
			wd = "."
		}
		// 预期运行目录 = self-agent 根（config/ 在根下）；也兼容直接放 llm-gateway/ 运行
		if _, err := os.Stat(filepath.Join(wd, "config", "llm.json")); err == nil {
			dir = filepath.Join(wd, "config")
		} else {
			dir = filepath.Join(wd, "..", "config")
		}
	}

	cfg, err := NewConfigStore(dir, "BAILING_API_KEY")
	if err != nil {
		fmt.Fprintln(os.Stderr, "配置加载失败:", err)
		os.Exit(1)
	}
	g := &Gateway{cfg: cfg, health: NewHealthStore(dir)}
	g.cfg.EnsureKeyConsistency()

	mux := http.NewServeMux()
	// 探活
	mux.HandleFunc("/health", g.hHealth)
	// 对话代理（白绫面）
	mux.HandleFunc("/v1/chat", g.hChat)
	// 管理 API：/admin/* + /api/* 双路径（前端切 base 即可复用现有面板）
	reg := func(admin, api string, h http.HandlerFunc) {
		if admin != "" {
			mux.HandleFunc(admin, h)
		}
		if api != "" {
			mux.HandleFunc(api, h)
		}
	}
	reg("/admin/config", "/api/config", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			g.hConfigSave(w, r)
			return
		}
		g.hConfigGet(w, r)
	})
	reg("/admin/test", "/api/config/test", g.hConfigTest)
	reg("/admin/models", "/api/models", g.hModelsGet)
	reg("/admin/models/fetch", "/api/models/fetch", g.hModelsFetch)
	reg("/admin/health", "/api/llm/health", g.hHealthGet)
	reg("/admin/scan", "/api/llm/scan", g.hScan)
	reg("/admin/switch", "/api/llm/switch", g.hSwitch)
	reg("/admin/sources", "/api/llm/sources", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			g.hSourcesSave(w, r)
			return
		}
		g.hSourcesGet(w, r)
	})

	addr := fmt.Sprintf("127.0.0.1:%d", *port)
	fmt.Printf("[gateway] 白绫 LLM 网关启动: http://%s（配置: %s）\n", addr, filepath.Join(dir, "llm.json"))
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	if err := srv.ListenAndServe(); err != nil {
		fmt.Fprintln(os.Stderr, "监听失败（端口被占用？）:", err)
		os.Exit(1)
	}
}
