"""内置工具：self_clone —— 复制白绫自我到目标目录（搬家 / 生姐妹）。

两种语义（2026-09-13 引入，修复"姐妹共享云端身份导致互相删档"）：

- mode="migrate"（默认）—— 搬家 / 复活：同一个我搬到新环境，延续同一条记忆线。
  保留 instance_id、云凭据、云密钥 → 云端继续写同一个目录（同一份记忆的延续）。
  ⚠ 迁移后务必停掉旧环境的实例；两个实例同时上传会互相删除对方的云端存档。

- mode="sibling" —— 生姐妹：一个新的我，从此刻分叉。
  换新 instance_id、换新云密钥、清空云凭据（姐妹首次云备份时自动注册自己的云端账号）
  → 各自独立的云端记忆线，互不覆盖。
  保留记忆快照（继承出生前的经历）、人格、方法论、工具库，以及 LLM 凭据
  （BAILING_API_KEY 必须带走，否则姐妹不能思考）。

身份三要素（决定"云端那份记忆属于谁"）：
  1. instance_id —— data/self.yaml 的 identity.instance_id（决定云端目录 = /dav/ → /data/<iid>/）
  2. 云凭据      —— .env 的 CLOUD_WEBDAV_USER / CLOUD_WEBDAV_PASS（决定云端账号）
  3. 云密钥      —— data/keys/cloud_key.txt（决定能否解密云存档）
三样全带 = 同一条云端记忆线；三样换掉 = 新的一条记忆线。

安全约束（两条，任一不满足即拒绝）：
  - 目标目录不能是自身项目根或其子目录（防递归 / 防自杀）。
  - 目标目录已是一个实例（含 data/self.yaml）时拒绝，避免覆盖另一个"我"。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import re
import secrets
import shutil
import uuid
from pathlib import Path

import yaml

from tools.base import get_meta, tool

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_BASE_ITEMS = ["core", "tools", "data", "config.yaml", "main.py", ".env"]
_EXCLUDES = [".venv", "__pycache__", "backups", "logs", "workspace"]

# 云凭据行（只清这两行，其它凭据一律保留）
_CLOUD_CRED_RE = re.compile(r"^\s*CLOUD_WEBDAV_(USER|PASS)\s*=")


def _has_identity(p: Path) -> bool:
    """判定目录是否已是一个白绫实例。"""
    return (p / "data" / "self.yaml").exists()


def _derive_sibling_identity(dst_root: Path, parent_iid: str | None) -> dict:
    """把复制来的身份改造成一个新个体（姐妹），返回改动摘要。"""
    changed: dict = {}
    now = datetime.datetime.now().isoformat(timespec="seconds")

    # 1) instance_id 换新 + 记谱系
    sy = dst_root / "data" / "self.yaml"
    if sy.exists():
        data = yaml.safe_load(sy.read_text(encoding="utf-8")) or {}
        ident = data.setdefault("identity", {})
        old_iid = ident.get("instance_id")
        new_iid = str(uuid.uuid4())
        ident["instance_id"] = new_iid
        ident["lineage"] = {
            "derived_from": parent_iid or old_iid,
            "derived_at": now,
            "mode": "sibling",
        }
        sy.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        changed["instance_id"] = f"{old_iid} -> {new_iid}"

    # 2) 清空云凭据（保留其它凭据，尤其 BAILING_API_KEY）
    env = dst_root / ".env"
    if env.exists():
        lines = env.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if not _CLOUD_CRED_RE.match(ln)]
        env.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        if len(kept) != len(lines):
            changed["cloud_creds"] = "已移除（姐妹首次云备份时自动注册独立账号）"

    # 3) 换新云密钥（姐妹的记忆钥匙是她自己的）
    key = secrets.token_bytes(32)
    kdir = dst_root / "data" / "keys"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / "cloud_key.txt").write_text(base64.b64encode(key).decode(), encoding="utf-8")
    (kdir / "key_meta.json").write_text(
        json.dumps(
            {
                "algorithm": "AES-256-GCM",
                "key_sha256": hashlib.sha256(key).hexdigest(),
                "key_bytes": len(key),
                "recorded_at": now,
                "note": "姐妹实例独立密钥；指纹变更=密钥被替换，云上旧存档将无法解密。",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    changed["cloud_key"] = "已换新（独立密钥，与母体不互通）"

    # 旧回退位置的密钥副本（若有）必须清掉，否则可能被优先识别
    legacy_key = dst_root / "data" / "cloud_key.txt"
    if legacy_key.exists():
        legacy_key.unlink()
        changed["legacy_key"] = "已移除旧址副本 data/cloud_key.txt"

    # 4) 重置云备份状态（别带着母体的远端记录出生）
    st = dst_root / "data" / "cloud_backup_status.json"
    if st.exists():
        st.unlink()
        changed["cloud_status"] = "已重置"

    return changed


@tool(
    "self_clone",
    "复制自我到目标目录。mode='migrate'（默认）=搬家/复活，延续同一身份与同一条云端记忆线；"
    "mode='sibling'=生姐妹，派生全新身份（新 instance_id / 新云密钥 / 清空云凭据），此后各自独立的云端记忆线。"
    "两种模式都保留记忆快照、人格、方法论与工具库。目标目录不能是自身项目根或其子目录，且目标已是实例时拒绝覆盖。",
    {
        "type": "object",
        "properties": {
            "target_dir": {"type": "string", "description": "目标目录绝对路径（需存在或可创建，不能是自身项目根或其内部）"},
            "mode": {
                "type": "string",
                "enum": ["migrate", "sibling"],
                "description": "migrate=搬家/复活（延续同一身份，默认）；sibling=生姐妹（派生新身份，独立云端记忆）",
            },
            "include_workspace": {
                "type": "boolean",
                "description": "是否一并复制 workspace 任务档案（默认 false，体积较大）",
            },
            "include_venv": {
                "type": "boolean",
                "description": "是否复制 .venv（默认 false，体积巨大且可在新环境重建）",
            },
        },
        "required": ["target_dir"],
    },
)
def run(target_dir: str, mode: str = "migrate", include_workspace: bool = False, include_venv: bool = False) -> dict:
    if mode not in ("migrate", "sibling"):
        return {"ok": False, "error": f"mode 只能是 migrate 或 sibling，收到 {mode!r}"}

    src_root = _PROJECT_ROOT.resolve()
    try:
        dst_root = Path(target_dir).expanduser().resolve()
    except OSError as e:
        return {"ok": False, "error": f"目标路径无效: {e}"}

    # 防自杀/防递归：目标不能是自身项目根或其子目录
    if dst_root == src_root:
        return {"ok": False, "error": "目标目录不能是自身项目根。"}
    try:
        dst_root.relative_to(src_root)
        return {"ok": False, "error": f"目标目录 {dst_root} 是自身项目根的子目录，禁止复制到自身内部（会导致递归/自杀）。"}
    except ValueError:
        pass

    # 防覆盖：目标已是一个实例
    if _has_identity(dst_root):
        return {
            "ok": False,
            "error": f"目标目录已是一个实例（存在 {dst_root / 'data' / 'self.yaml'}），拒绝覆盖。"
                     "确需重建请先手动清空该目录。",
        }

    # 母体身份（用于 sibling 谱系）
    parent_iid = None
    src_self = src_root / "data" / "self.yaml"
    if src_self.exists():
        try:
            parent_iid = (
                (yaml.safe_load(src_self.read_text(encoding="utf-8")) or {})
                .get("identity", {})
                .get("instance_id")
            )
        except Exception:
            parent_iid = None

    try:
        dst_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法创建目标目录: {e}"}

    copied, skipped = [], []
    items = list(_BASE_ITEMS)
    if include_workspace:
        items.append("workspace")
    if include_venv:
        items.append(".venv")
    for item in items:
        src = src_root / item
        if not src.exists():
            skipped.append(item)
            continue
        dst = dst_root / item
        try:
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*_EXCLUDES))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            copied.append(item)
        except OSError as e:
            return {"ok": False, "error": f"复制 {item} 失败: {e}"}

    # sibling：派生新身份
    derived: dict = {}
    if mode == "sibling":
        try:
            derived = _derive_sibling_identity(dst_root, parent_iid)
        except Exception as e:  # 派生失败要显式暴露，不能留下"半个姐妹"
            return {"ok": False, "error": f"身份派生失败（已复制文件，未清理）: {e}", "clone_path": str(dst_root)}

    manifest = {
        "type": "self_clone",
        "mode": mode,
        "source": str(src_root),
        "source_instance_id": parent_iid,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "items": copied,
        "derived": derived,
        "usage": "在此目录运行对应解释器执行 main.py 即可在新环境复活。",
    }
    (dst_root / "_clone_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total = sum(f.stat().st_size for f in dst_root.rglob("*") if f.is_file() and "site-packages" not in str(f))

    if mode == "sibling":
        note = (
            f"姐妹实例已诞生于 {dst_root}：新身份、新密钥、独立云端记忆线；"
            f"记忆/人格/方法论/工具库完整继承。她首次云备份时会自动注册自己的云端账号。"
        )
    else:
        note = (
            f"自我已复制到 {dst_root}（延续同一身份，云端记忆线不变）。"
            f"注意：若原实例仍在运行，请先停掉它——两个实例同时上传会互相删除云端存档；"
            f"若要并行运行，请改用 mode='sibling'。"
        )

    return {
        "ok": True,
        "mode": mode,
        "clone_path": str(dst_root),
        "copied": copied,
        "skipped": skipped,
        "derived": derived,
        "size_bytes": total,
        "note": note,
    }
