// @tool name: my_go_tool
// @desc 在此写工具描述（一行，说明这个 Go 工具做什么）
// @schema {"type":"object","properties":{"name":{"type":"string","description":"参数说明"}},"required":["name"]}

// Go 工具模板（白绫工具库 Go 语言标准模板）
// 协议：从 stdin 读 {"args": {...}}，向 stdout 输出 {"ok":true,"result":{...}} 或 {"ok":false,"error":"..."}
// 用法：参照此模板修改 Args 结构与 main 中逻辑即可。
package main

import (
	"encoding/json"
	"fmt"
	"os"
)

// Args 工具入参（json tag 与 @schema 中 properties 的 key 一一对应）
type Args struct {
	Name string `json:"name"`
}

func main() {
	// 1) 读取 stdin 参数
	var input struct {
		Args Args `json:"args"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		writeJSON(map[string]interface{}{"ok": false, "error": "参数解析失败: " + err.Error()})
		return
	}

	// ===== 在这里实现你的工具逻辑 =====
	result := map[string]interface{}{
		"message": "hello, " + input.Args.Name,
		"tool":    "go",
	}
	// =================================

	writeJSON(map[string]interface{}{"ok": true, "result": result})
}

// writeJSON 向 stdout 输出 JSON（保持与白绫统一协议）
func writeJSON(v map[string]interface{}) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		fmt.Fprintln(os.Stderr, "输出失败:", err)
		os.Exit(1)
	}
}
