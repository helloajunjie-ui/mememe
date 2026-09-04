// @tool name: go_sha256
// @desc 计算字符串的 SHA256 哈希值（十六进制小写）
// @schema {"type":"object","properties":{"text":{"type":"string","description":"要计算哈希的字符串"}},"required":["text"]}

// Go 工具：计算字符串 SHA256 哈希
// 协议：从 stdin 读 {"args": {...}}，向 stdout 输出 {"ok":true,"result":{...}} 或 {"ok":false,"error":"..."}
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
)

// Args 工具入参
type Args struct {
	Text string `json:"text"`
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

	// 2) 计算 SHA256
	sum := sha256.Sum256([]byte(input.Args.Text))
	hash := hex.EncodeToString(sum[:])

	result := map[string]interface{}{
		"text":   input.Args.Text,
		"sha256": hash,
	}
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
