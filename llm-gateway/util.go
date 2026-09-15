package main

// util.go —— 基础小工具（os/io 别名封装、时间戳）

import (
	"io"
	"os"
	"time"
)

func osReadFile(p string) ([]byte, error) { return os.ReadFile(p) }

func osWriteFile(p string, b []byte, mode os.FileMode) error { return os.WriteFile(p, b, mode) }

func osRenameFn(a, b string) error { return os.Rename(a, b) }

type ioReader = io.Reader

func ioReadAllN(r io.Reader, n int64) ([]byte, error) {
	return io.ReadAll(io.LimitReader(r, n))
}

func nowUnix() int64 { return time.Now().Unix() }

func round2(f float64) float64 {
	return float64(int(f*100+0.5)) / 100
}
