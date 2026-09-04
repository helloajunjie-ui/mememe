"""白绫（Bailing）CLI 入口。

用法：
    python main.py                 # 交互模式
    python main.py --task "..."    # 单次任务模式
    python main.py --check         # 只运行环境/工具自检（不进入对话）
"""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="白绫 - 自我完善 AI 智能体")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--task", help="单次任务模式：传入一个任务即执行并退出")
    parser.add_argument("--check", action="store_true", help="环境/工具自检模式")
    args = parser.parse_args()

    from core.agent import Agent

    agent = Agent(args.config)

    if args.check:
        _run_check(agent)
        agent.close()
        return

    mode = agent.boot()

    if args.task:
        print("\n白绫 >", agent.turn(args.task))
        agent.close()
        return

    # 交互模式
    print(f"\n白绫已就绪（启动模式: {mode}）。输入 exit / quit 退出。\n")
    while True:
        try:
            user = input("你 > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user.strip().lower() in ("exit", "quit", "退出", "q"):
            break
        if not user.strip():
            continue
        try:
            reply = agent.turn(user.strip())
        except KeyboardInterrupt:
            print("\n（已中断本轮）")
            continue
        print(f"白绫 > {reply}")
    agent.close()


def _run_check(agent) -> None:
    """环境/工具自检：不调用 LLM。"""
    import json

    from core.deps import env_probe

    print("=== 平台 ===")
    print(json.dumps(agent.platform, ensure_ascii=False, indent=2))
    print("=== 环境画像 ===")
    profile = env_probe.full_probe()
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    print("=== 工具发现 ===")
    n = agent.registry.discover_builtin()
    print(f"发现 {n} 个内置工具:")
    for t in agent.registry.list_active():
        print(f"  - {t['name']}: {t['description']}")
    print("=== 工具冒烟测试 ===")
    r = agent.registry.execute("fs_list", {"path": "."})
    print(f"fs_list → {'ok' if r.get('ok') else 'error'}: {str(r)[:300]}")


if __name__ == "__main__":
    main()
