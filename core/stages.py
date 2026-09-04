"""阶段化任务记录器（TaskStageTracker）。

设计意图（见设计文档 5.8）：
- 白绫是"脑中心"：负责判断要做什么、如何做、评估结果、做决策；
  大多数机械执行交给工具/脚本完成，仅真正需要思考的内容才动用 LLM 推理。
- 每个任务按阶段推进，阶段 = 一个有明确目标的执行单元（一次工具调用/一次命令执行）。
- 阶段记录：目的 / 动作 / 结果摘要 / 状态。任务结束生成总结并存档（workspace/tasks/<task_id>/stages.md）。
- 价值：可回溯、可断点续传、可复盘沉淀经验。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Dict, List, Optional


class TaskStageTracker:
    def __init__(self, root: str = "workspace/tasks"):
        self.root = Path(root)
        self.task_id: str = ""
        self.dir: Optional[Path] = None
        self.goal: str = ""
        self.started: str = ""
        self.stages: List[Dict] = []

    def begin_task(self, goal: str, task_id: str = "") -> str:
        """开始一个任务：创建任务目录与档案。"""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.task_id = task_id or f"task_{ts}"
        self.dir = self.root / self.task_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.goal = goal[:200]
        self.started = datetime.datetime.now().isoformat(timespec="seconds")
        self.stages = []
        return str(self.dir)

    def add_stage(self, name: str, action: str = "", result: str = "",
                  status: str = "ok", note: str = "") -> None:
        """记录一个执行阶段。action/result 截断，保持档案精简。"""
        self.stages.append({
            "seq": len(self.stages) + 1,
            "name": name,
            "action": (action or "")[:300],
            "result": (result or "")[:300],
            "status": status,
            "note": (note or "")[:200],
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        })

    def finish_task(self, summary: str, success: bool = True, complete: bool = True,
                    note: str = "") -> str:
        """任务结束：生成阶段总结并存档，返回档案路径。

        complete=False 表示任务未完成（如工具步数超限），记录断点供后续续接，
        避免记忆断裂——下一轮可从断点继续思考。
        """
        if self.dir is None:
            raise RuntimeError("begin_task 未调用")
        finished = datetime.datetime.now().isoformat(timespec="seconds")
        state = "✅ 完成" if complete else "⏸ 未完成（可续接）"
        result_state = "✅ 成功" if success else "⚠️ 未完全成功"
        lines = [
            "# 任务档案",
            "",
            f"- 任务ID：`{self.task_id}`",
            f"- 目标：{self.goal}",
            f"- 开始：{self.started}",
            f"- 结束：{finished}",
            f"- 状态：{state}",
            f"- 结果：{result_state}",
        ]
        if note:
            lines.append(f"- 备注：{note}")
        lines += [
            "",
            "## 执行阶段",
            "",
        ]
        if not self.stages:
            lines.append("（无工具执行阶段）")
        for s in self.stages:
            icon = "✅" if s["status"] == "ok" else "❌"
            lines.append(f"{s['seq']}. **{s['name']}** {icon}")
            if s["action"]:
                lines.append(f"   - 动作：`{s['action']}`")
            if s["result"]:
                lines.append(f"   - 结果：{s['result']}")
            if s["note"]:
                lines.append(f"   - 备注：{s['note']}")
        lines += ["", "## 总结", "", summary, ""]
        (self.dir / "stages.md").write_text("\n".join(lines), encoding="utf-8")
        (self.dir / "meta.json").write_text(
            json.dumps({
                "task_id": self.task_id, "goal": self.goal,
                "started": self.started, "finished": finished,
                "success": success, "complete": complete,
                "stage_count": len(self.stages),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(self.dir / "stages.md")

    def archive_path(self) -> str:
        return str(self.dir / "stages.md") if self.dir else ""
