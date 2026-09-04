// @tool name: go_sysinfo
// @desc 用 Go 获取本机系统信息（hostname/OS/架构/CPU 核数），演示 Go 工具链
// @schema {"type":"object","properties":{}}
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"
)

func main() {
	var input map[string]interface{}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		writeJSON(map[string]interface{}{"ok": false, "error": "参数解析失败: " + err.Error()})
		return
	}
	host, _ := os.Hostname()
	result := map[string]interface{}{
		"hostname": host,
		"os":       runtime.GOOS,
		"arch":     runtime.GOARCH,
		"go":       runtime.Version(),
		"cpu":      runtime.NumCPU(),
	}
	writeJSON(map[string]interface{}{"ok": true, "result": result})
}

func writeJSON(v map[string]interface{}) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		fmt.Fprintln(os.Stderr, "输出失败:", err)
		os.Exit(1)
	}
}
