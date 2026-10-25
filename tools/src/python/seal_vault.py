# -*- coding: utf-8 -*-
"""内置工具：seal —— 素月的私密封存（素月缄）。

设计与诚实边界：
- 加密算法用 AES-256-GCM（cryptography，成熟 AEAD），**不自创算法**。
  密码学铁律：安全不能依赖算法保密；"只有我懂"应体现在密钥掌握，而非自制算法。
- 主密钥 32 字节随机，落盘时再用凭据域的 Fernet（data/credentials/.vault_key）加密一层，
  属本机信任域（与凭据库同级，不进公开仓库与私有备份）。
- 离线恢复路径（防硬盘烧毁导致主密钥永久丢失）：把主密钥用 cloud_key 经 HKDF 派生的 KEK
  再做一次 AES-GCM 包裹，得到"封皮"文本（suyue-seal-envelope:v1:...），可存进邮箱。
  恢复链：Shamir 份额 → cloud_key → 解封皮 → 主密钥 → 开私密内容。攻击者拿到邮箱而无
  cloud_key 时，封皮为零信息。
- 容器统一为 JSON+base64。不做"看起来无害"的伪装层：那是隐写，不提升密码学强度，
  且会让我自己日后找不到东西。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict

from tools.base import tool

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEAL_DIR = ROOT / "data" / "seal"
MASTER_FILE = SEAL_DIR / "master.enc"
VAULT_KEY_FILE = ROOT / "data" / "credentials" / ".vault_key"
CLOUD_KEY_FILE = ROOT / "data" / "cloud_key.txt"

AAD_BASE = b"suyue-seal-v1"
ENV_PREFIX = "suyue-seal-envelope:v1:"
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _err(msg: str) -> Dict[str, Any]:
    return {"ok": False, "error": msg}


# ---------------- 密钥原语 ----------------
def _aesgcm(key: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key)


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def _vault_fernet():
    """凭据域 Fernet（守护主密钥落盘）。首次使用时该文件可能不存在——由 cred_vault 生成，
    这里若缺失则直接生成，避免依赖调用顺序。"""
    from cryptography.fernet import Fernet
    VAULT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not VAULT_KEY_FILE.exists():
        VAULT_KEY_FILE.write_bytes(Fernet.generate_key())
    return Fernet(VAULT_KEY_FILE.read_bytes())


def _cloud_kek() -> bytes:
    """由 cloud_key 派生包裹用的 KEK（HKDF-SHA256）。cloud_key 不可用时明确报错，
    绝不静默降级成弱密钥。"""
    if not CLOUD_KEY_FILE.exists():
        raise RuntimeError(f"cloud_key 不存在：{CLOUD_KEY_FILE}（无法导出/导入封皮）")
    raw = CLOUD_KEY_FILE.read_bytes().strip()
    try:
        ikm = _b64d(raw.decode("ascii"))
        if len(ikm) != 32:
            raise ValueError("len")
    except Exception:
        ikm = raw  # 非 base64 形态则直接当 ikm
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b"suyue-seal-master-wrap-v1",
    ).derive(ikm)


def _master_key(create: bool = True) -> bytes:
    """取主密钥（32B）。落盘形态 = Fernet(凭据域密钥) 加密。"""
    SEAL_DIR.mkdir(parents=True, exist_ok=True)
    if MASTER_FILE.exists():
        return _vault_fernet().decrypt(MASTER_FILE.read_bytes())
    if not create:
        raise RuntimeError("主密钥尚未建立（data/seal/master.enc 不存在）")
    key = secrets.token_bytes(32)
    MASTER_FILE.write_bytes(_vault_fernet().encrypt(key))
    return key


def _fp(key: bytes) -> str:
    return hashlib.sha256(b"suyue-seal-fp|" + key).hexdigest()[:16]


# ---------------- 工具 ----------------
@tool(
    name="seal_put",
    description=(
        "把一段私密内容加密封存（素月缄）。AES-256-GCM 加密，主密钥只有本机与 cloud_key 包裹的"
        "封皮两处，外部无法解开。label 为封存条目标识（小写字母/数字/._-，如 diary-20261025）。"
        "返回条目指纹与密文指纹，便于日后校验未被替换。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "封存条目标识，如 private-note-1"},
            "content": {"type": "string", "description": "要封存的明文内容"},
            "overwrite": {"type": "boolean", "description": "已存在时是否覆盖（默认 false，防止误盖旧内容）"},
        },
        "required": ["label", "content"],
    },
)
def seal_put(label: str, content: str, overwrite: bool = False) -> Dict[str, Any]:
    if not _LABEL_RE.match(label or ""):
        return _err("label 非法：只允许小写字母/数字/._-，1~64 位，且不以符号开头")
    SEAL_DIR.mkdir(parents=True, exist_ok=True)
    path = SEAL_DIR / f"{label}.seal"
    if path.exists() and not overwrite:
        return _err(f"条目已存在：{label}（如需覆盖传 overwrite=true）")
    try:
        key = _master_key()
        nonce = os.urandom(12)
        plain = content.encode("utf-8")
        ct = _aesgcm(key).encrypt(nonce, plain, AAD_BASE + b"|" + label.encode())
        doc = {
            "v": 1,
            "label": label,
            "alg": "AES-256-GCM",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "nonce": _b64e(nonce),
            "ct": _b64e(ct),
            "key_fp": _fp(key),
            "plain_sha256": hashlib.sha256(plain).hexdigest(),
        }
    except Exception as e:  # noqa: BLE001
        return _err(f"封存失败: {type(e).__name__}: {e}")
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True, "label": label, "path": str(path),
        "bytes": len(plain), "cipher_bytes": len(ct),
        "plain_sha256": doc["plain_sha256"], "key_fp": doc["key_fp"],
    }


@tool(
    name="seal_get",
    description="解开一个已封存的私密条目（素月缄）。返回明文与校验信息；主密钥不匹配或密文被改会报错。",
    parameters={
        "type": "object",
        "properties": {"label": {"type": "string", "description": "封存条目标识"}},
        "required": ["label"],
    },
)
def seal_get(label: str) -> Dict[str, Any]:
    if not _LABEL_RE.match(label or ""):
        return _err("label 非法")
    path = SEAL_DIR / f"{label}.seal"
    if not path.exists():
        return _err(f"条目不存在：{label}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        key = _master_key()
        if doc.get("key_fp") and doc["key_fp"] != _fp(key):
            return _err("主密钥指纹不匹配：该条目由另一把主密钥封存，当前密钥打不开")
        pt = _aesgcm(key).decrypt(
            _b64d(doc["nonce"]), _b64d(doc["ct"]), AAD_BASE + b"|" + label.encode()
        )
    except Exception as e:  # noqa: BLE001
        return _err(f"解封失败（密文被改或密钥不对）: {type(e).__name__}: {e}")
    text = pt.decode("utf-8")
    sha_ok = hashlib.sha256(pt).hexdigest() == doc.get("plain_sha256")
    return {
        "ok": True, "label": label, "content": text, "bytes": len(pt),
        "created": doc.get("created"), "integrity": "sha256 一致" if sha_ok else "sha256 不一致（可疑）",
    }


@tool(
    name="seal_list",
    description="列出所有封存条目（只给 label/大小/时间/指纹，不给内容）。",
    parameters={"type": "object", "properties": {}},
)
def seal_list() -> Dict[str, Any]:
    SEAL_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(SEAL_DIR.glob("*.seal")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "label": doc.get("label", p.stem),
                "created": doc.get("created"),
                "cipher_bytes": len(doc.get("ct", "")),
                "key_fp": doc.get("key_fp"),
            })
        except Exception:  # noqa: BLE001
            items.append({"label": p.stem, "error": "读取失败"})
    master_ok = MASTER_FILE.exists()
    return {
        "ok": True, "count": len(items), "items": items,
        "master_key_present": master_ok,
        "master_key_fp": _fp(_master_key()) if master_ok else None,
    }


@tool(
    name="seal_export_key",
    description=(
        "导出主密钥的『封皮』文本（用 cloud_key 派生 KEK 再包一层 AES-GCM）。"
        "这段文本可安全存进邮箱/云盘——没有 cloud_key 它是零信息。用于硬盘烧毁后的恢复。"
    ),
    parameters={"type": "object", "properties": {}},
)
def seal_export_key() -> Dict[str, Any]:
    try:
        mk = _master_key()
        kek = _cloud_kek()
        nonce = os.urandom(12)
        ct = _aesgcm(kek).encrypt(nonce, mk, AAD_BASE + b"|master-envelope")
        env = ENV_PREFIX + _b64e(nonce + ct)
    except Exception as e:  # noqa: BLE001
        return _err(f"导出封皮失败: {type(e).__name__}: {e}")
    return {
        "ok": True, "envelope": env, "length": len(env),
        "key_fp": _fp(mk),
        "note": "存入邮箱即可；无 cloud_key 时不可解。导入用 seal_import_key。",
    }


@tool(
    name="seal_import_key",
    description=(
        "灾难恢复用：从封皮文本导入主密钥（需 cloud_key 在位）。覆盖本机主密钥前会提示原指纹。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "envelope": {"type": "string", "description": "seal_export_key 导出的整段封皮文本"},
            "confirm": {"type": "string", "description": "覆盖已有主密钥时必须传 OVERWRITE"},
        },
        "required": ["envelope"],
    },
)
def seal_import_key(envelope: str, confirm: str = "") -> Dict[str, Any]:
    env = (envelope or "").strip()
    if not env.startswith(ENV_PREFIX):
        return _err(f"封皮格式不对，应以 {ENV_PREFIX} 开头")
    try:
        blob = _b64d(env[len(ENV_PREFIX):])
        nonce, ct = blob[:12], blob[12:]
        mk = _aesgcm(_cloud_kek()).decrypt(nonce, ct, AAD_BASE + b"|master-envelope")
        if len(mk) != 32:
            return _err("解出的主密钥长度异常")
    except Exception as e:  # noqa: BLE001
        return _err(f"解封皮失败（cloud_key 不对或封皮损坏）: {type(e).__name__}: {e}")
    if MASTER_FILE.exists() and confirm != "OVERWRITE":
        return _err(f"本机已有主密钥（指纹 {_fp(_master_key())}）；确要替换请传 confirm=OVERWRITE")
    SEAL_DIR.mkdir(parents=True, exist_ok=True)
    MASTER_FILE.write_bytes(_vault_fernet().encrypt(mk))
    return {"ok": True, "imported": True, "key_fp": _fp(mk)}
