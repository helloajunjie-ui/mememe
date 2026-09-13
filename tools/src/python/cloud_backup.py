# -*- coding: utf-8 -*-
"""白绫云存档：instance_id 身份 + 自动注册 + AES-256-GCM 加密上传 + 多版本回滚。

架构（与本地私有备份互补）：
- 本地 backup_private.py   → backups/private_<ts>.zip（本地复活点，30 份）
- 本工具 cloud_backup.py   → https://dpoo.my/dav/<instance_id>/backup_<ts>.zip.enc（云端灾备）

安全模型：
- 每个实例首次运行自动生成 UUID instance_id，持久化到 data/self.yaml（identity.instance_id），永不改变。
- 自动注册：POST https://dpoo.my/api/register {instance_id} → 服务器分配独立 WebDAV 账号（幂等，
  重复注册返回同一账号）。凭据存本地 .env（CLOUD_WEBDAV_USER/CLOUD_WEBDAV_PASS）。
- 加密：AES-256-GCM。密钥 data/keys/cloud_key.txt（私密钥匙区：随本地私有备份，
  绝不进云端包——打包守卫会拒绝任何把密钥写进云端包的行为）。
  云端只有密文 → 即使服务器被攻破，别人拿到的是一堆无法解密的密文。
- 多版本：文件名带时间戳 backup_<YYYYmmdd_HHMMSS>.zip.enc，远端保留最近 14 份（可回滚）。
- 恢复：列远端版本 → 下载 → 解密 → 解压到 backups/restore_<ts>/（安全目录，覆盖动作由用户确认）。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import secrets
import sys
import uuid
import zipfile
from pathlib import Path

import httpx
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA = _PROJECT_ROOT / "data"
_BACKUP_ROOT = _PROJECT_ROOT / "backups"
_ENV_FILE = _PROJECT_ROOT / ".env"
_SELF_YAML = _DATA / "self.yaml"
# 密钥位置（2026-09-13 迁入私密钥匙区）
# 主位置：data/keys/cloud_key.txt（随本地私有备份，绝不进云端包）
# 回退位置：data/cloud_key.txt（旧址，兼容从旧备份恢复出来的实例）
_KEY_FILE = _DATA / "keys" / "cloud_key.txt"
_LEGACY_KEY_FILE = _DATA / "cloud_key.txt"
_KEY_META = _DATA / "keys" / "key_meta.json"
_KEY_DIR_REL = "data/keys"
_LEGACY_KEY_REL = "data/cloud_key.txt"
_STATUS_FILE = _DATA / "cloud_backup_status.json"

# 云端
_REGISTER_URL = "https://dpoo.my/api/register"
_DAV_BASE = "https://dpoo.my/dav/"
_HTTP_TIMEOUT = 30.0
_KEEP_REMOTE = 14  # 云端保留最近 14 份

# 与 backup_private 一致的私有数据清单（复活必需）
_PRIVATE_FILES = [
    "data/memory.db",
    "data/self.yaml",
    "data/methodology.json",
    "data/registry.json",
    "data/snapshots.json",
    "data/env_profile.json",
]
# 私有目录（递归打包）——2026-09-13 与 backup_private 同步：
# library/ 资料库、data/credentials/ 凭据库，此前是云端灾备的盲区
_PRIVATE_DIRS = ["library", "data/credentials"]
_CORE_FILES = ["config.yaml", ".env"]

# 加密文件头：BLENC1 + key_id + nonce + tag + ciphertext
_MAGIC = b"BLENC1"


# ---------- 身份 ----------

def ensure_instance_id() -> str:
    """读取或生成 instance_id（UUID），持久化到 self.yaml 的 identity.instance_id。"""
    if _SELF_YAML.exists():
        data = yaml.safe_load(_SELF_YAML.read_text(encoding="utf-8")) or {}
        ident = data.get("identity") or {}
        if ident.get("instance_id"):
            return str(ident["instance_id"])
    iid = str(uuid.uuid4())
    data = yaml.safe_load(_SELF_YAML.read_text(encoding="utf-8")) if _SELF_YAML.exists() else {}
    data.setdefault("identity", {})["instance_id"] = iid
    _SELF_YAML.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return iid


# ---------- 凭据（.env） ----------

def _load_env() -> dict:
    env = {}
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _save_env(updates: dict) -> None:
    """更新 .env：保留注释/未知键，更新/追加指定键。"""
    env = _load_env()
    env.update(updates)
    lines = []
    if _ENV_FILE.exists():
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    written = set()
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                written.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in written:
            out.append(f"{k}={v}")
    _ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def load_cloud_creds():
    env = _load_env()
    user = env.get("CLOUD_WEBDAV_USER", "").strip()
    pwd = env.get("CLOUD_WEBDAV_PASS", "").strip()
    return (user, pwd) if user and pwd else None


def register() -> dict:
    """自动注册：POST /api/register → 持久化凭据。"""
    iid = ensure_instance_id()
    r = httpx.post(_REGISTER_URL, json={"instance_id": iid}, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"注册失败: {body}")
    _save_env({"CLOUD_WEBDAV_USER": body["username"], "CLOUD_WEBDAV_PASS": body["password"]})
    return body


def ensure_registered():
    """确保已注册，返回 (username, password)。"""
    creds = load_cloud_creds()
    if creds:
        return creds
    body = register()
    return body["username"], body["password"]


def _key_path() -> Path:
    """密钥实际位置：优先私密钥匙区，其次旧址（兼容从旧备份恢复的实例）。"""
    if _KEY_FILE.exists():
        return _KEY_FILE
    if _LEGACY_KEY_FILE.exists():
        return _LEGACY_KEY_FILE
    return _KEY_FILE


def _chmod_key() -> None:
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass


def _write_key_meta(key: bytes) -> None:
    """记录密钥指纹：指纹变了 = 密钥被替换，云上旧存档将永远打不开。"""
    meta = {
        "algorithm": "AES-256-GCM",
        "key_sha256": hashlib.sha256(key).hexdigest(),
        "key_bytes": len(key),
        "recorded_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": "指纹变更=密钥被替换；若非有意轮换，请立即从私有备份恢复密钥。",
    }
    _KEY_META.parent.mkdir(parents=True, exist_ok=True)
    _KEY_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def get_or_create_key(force_new: bool = False) -> bytes:
    """读取 AES-256 密钥（data/keys/cloud_key.txt，base64 编码 32 字节）。

    安全（2026-09-13 修复）：密钥缺失时**不再静默重建**。
    钥匙区留有指纹记录（_KEY_META）说明曾经存在密钥 —— 此时直接报错，
    避免新密钥把云上旧存档变成永久无法解密的垃圾。确需轮换请显式 force_new=True。
    """
    if force_new:
        key = secrets.token_bytes(32)
        _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KEY_FILE.write_text(base64.b64encode(key).decode(), encoding="utf-8")
        _chmod_key()
        _write_key_meta(key)
        return key

    path = _key_path()
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        try:
            key = base64.b64decode(raw)
        except Exception as exc:
            raise RuntimeError(
                "密钥文件内容非法（不是 base64）：%s -> %s\n"
                "  请从私有备份恢复该文件，不要手工编辑。" % (path, exc)
            ) from exc
        if not _KEY_META.exists():
            _write_key_meta(key)  # 自愈：补齐指纹，让“密钥曾经存在”有据可查
        return key

    if _KEY_META.exists():
        raise RuntimeError(
            "云存档密钥缺失，但钥匙区存在指纹记录 -> 拒绝自动生成新密钥。\n"
            "  期望位置: %s\n"
            "  回退位置: %s\n"
            "  恢复方法: 从 backups/private_*.zip 或 backups/self_*_full/data/keys/ 取回 cloud_key.txt\n"
            "  若确认云端已无历史存档、要启用全新密钥: cloud_backup.py newkey" % (_KEY_FILE, _LEGACY_KEY_FILE)
        )

    key = secrets.token_bytes(32)
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_text(base64.b64encode(key).decode(), encoding="utf-8")
    _chmod_key()
    _write_key_meta(key)
    return key


def _key_hint() -> str:
    return ("云存档加密密钥已生成: %s\n"
            "该文件已纳入本地私有备份；不要删、不要手工改。\n"
            "云端只存密文，丢失此密钥 = 云端存档永远无法解密。" % _KEY_FILE)
# ---------- 加密 / 解密 ----------

def encrypt_bytes(data: bytes, key: bytes, key_id: int = 1) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, data, b"bailing-cloud")
    return _MAGIC + bytes([key_id]) + nonce + ct


def decrypt_bytes(data: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not data.startswith(_MAGIC):
        raise ValueError("不是白绫加密存档（缺文件头）")
    off = len(_MAGIC)
    key_id = data[off]
    if key_id != 1:
        raise ValueError(f"不支持的密钥版本: {key_id}")
    nonce = data[off + 1:off + 13]
    ct = data[off + 13:]
    return AESGCM(key).decrypt(nonce, ct, b"bailing-cloud")


# ---------- 打包 ----------

def _assert_packable(rel: str) -> None:
    """云端打包守卫：密钥及其目录绝不允许进入云端包（防密文与密钥同处）。"""
    r = rel.replace("\\", "/").strip("/")
    if r == _KEY_DIR_REL or r.startswith(_KEY_DIR_REL + "/") or r == _LEGACY_KEY_REL:
        raise RuntimeError("拒绝打包：%s 属于密钥区（%s），绝不允许上传云端。" % (rel, _KEY_DIR_REL))


def pack(note: str = "") -> tuple[Path, dict]:
    """打包私有数据为本地 zip（与 backup_private 同清单 + 目录递归），返回 (zip_path, manifest)。"""
    _BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = _BACKUP_ROOT / f"cloud_{ts}.zip"
    added, missing = [], []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _PRIVATE_FILES + _CORE_FILES:
            _assert_packable(rel)
            src = _PROJECT_ROOT / rel
            if src.exists():
                zf.write(src, arcname=rel)
                added.append(rel)
            else:
                missing.append(rel)
        for rel_dir in _PRIVATE_DIRS:
            _assert_packable(rel_dir)
            d = _PROJECT_ROOT / rel_dir
            if not d.is_dir():
                missing.append(rel_dir)
                continue
            for f in sorted(d.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    arc = f.relative_to(_PROJECT_ROOT).as_posix()
                    _assert_packable(arc)
                    zf.write(f, arcname=arc)
                    added.append(arc)
    return zip_path, {"files": added, "missing": missing, "note": note, "ts": ts}
# ---------- 上传 ----------

def _dav(path: str) -> str:
    """拼 WebDAV 完整 URL（路径逐段 quote）。"""
    from urllib.parse import quote
    segs = [quote(s, safe="") for s in path.strip("/").split("/") if s]
    return _DAV_BASE + "/".join(segs)


def upload_zip(zip_path: Path, key: bytes) -> dict:
    """加密 zip 并上传到云端实例目录，清理远端旧版。
    注意：WebDAV 的 scope 即实例根目录（/dav/ 已映射到 /data/<iid>/），
    路径直接用文件名，不带实例 ID 前缀。"""
    iid = ensure_instance_id()
    user, pwd = ensure_registered()
    enc_data = encrypt_bytes(zip_path.read_bytes(), key)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    remote_name = f"backup_{ts}.zip.enc"
    headers = {"User-Agent": "BailingAgent/0.2 (cloud backup)"}
    with httpx.Client(auth=(user, pwd), timeout=_HTTP_TIMEOUT, headers=headers) as c:
        # 上传（scope 根已存在，无需建目录）
        r = c.put(_dav(remote_name), content=enc_data)
        if r.status_code not in (200, 201, 204):
            return {"ok": False, "error": f"上传失败: HTTP {r.status_code}"}
        # 列远端，清理旧版（保留最近 _KEEP_REMOTE 份）
        removed = _prune_remote(c)
    return {
        "ok": True,
        "instance_id": iid,
        "remote": _DAV_BASE + remote_name,
        "remote_name": remote_name,
        "size_bytes": len(enc_data),
        "pruned_remote": removed,
    }


def _prune_remote(c: httpx.Client) -> list:
    """列出 scope 根目录，删除最旧的 .enc 超量版本。返回删除的文件名列表。"""
    import posixpath
    from xml.etree import ElementTree as ET
    r = c.request("PROPFIND", _dav(""), headers={"Depth": "1"})
    if r.status_code not in (200, 207):
        return []
    ns = "{DAV:}"
    files = []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    for resp in root.findall(f"{ns}response"):
        href_el = resp.find(f"{ns}href")
        if href_el is None or not href_el.text:
            continue
        name = href_el.text.rstrip("/").split("/")[-1]
        if name.startswith("backup_") and name.endswith(".enc"):
            files.append(name)
    files.sort(reverse=True)  # 时间戳倒序：最新在前
    removed = []
    for old in files[_KEEP_REMOTE:]:
        rr = c.request("DELETE", _dav(old))
        if rr.status_code in (200, 204):
            removed.append(old)
    return removed


# ---------- 状态 ----------

def _write_status(payload: dict) -> None:
    try:
        _STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------- 主流程 ----------

def run(note: str = "") -> dict:
    """完整云备份：打包 → 加密 → 上传 → 清理本地临时 zip → 写状态。"""
    try:
        iid = ensure_instance_id()
        key = get_or_create_key()
        zip_path, manifest = pack(note)
        result = upload_zip(zip_path, key)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "上传失败"))
        # 上传成功：删除本地临时 zip（云端已存密文，本地复活点由 backup_private 负责）
        try:
            zip_path.unlink()
            cleaned = True
        except OSError:
            cleaned = False
        result.update({
            "type": "cloud_backup",
            "local_zip_cleaned": cleaned,
            "files": manifest["files"],
            "missing": manifest["missing"],
            "note": note,
        })
        _write_status(result)
        return result
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "type": "cloud_backup", "error": f"云备份失败: {e}", "note": note}
        _write_status(result)
        return result


# ---------- 恢复 ----------

def list_remote() -> dict:
    """列出云端实例目录（scope 根）下的备份版本。"""
    iid = ensure_instance_id()
    user, pwd = ensure_registered()
    from xml.etree import ElementTree as ET
    with httpx.Client(auth=(user, pwd), timeout=_HTTP_TIMEOUT) as c:
        r = c.request("PROPFIND", _dav(""), headers={"Depth": "1"})
        if r.status_code not in (200, 207):
            return {"ok": False, "error": f"列云端失败: HTTP {r.status_code}"}
        ns = "{DAV:}"
        versions = []
        try:
            root = ET.fromstring(r.content)
        except Exception:
            return {"ok": False, "error": "解析云端列表失败"}
        for resp in root.findall(f"{ns}response"):
            href_el = resp.find(f"{ns}href")
            if href_el is None or not href_el.text:
                continue
            name = href_el.text.rstrip("/").split("/")[-1]
            if not (name.startswith("backup_") and name.endswith(".enc")):
                continue
            size = 0
            for ps in resp.findall(f"{ns}propstat"):
                prop = ps.find(f"{ns}prop")
                if prop is not None:
                    el = prop.find(f"{ns}getcontentlength")
                    if el is not None and el.text:
                        try:
                            size = int(el.text)
                        except ValueError:
                            pass
            versions.append({"name": name, "size": size})
        versions.sort(key=lambda v: v["name"], reverse=True)
        return {"ok": True, "instance_id": iid, "versions": versions}


def restore(remote_name: str = "") -> dict:
    """下载并解密指定云端版本（默认最新），解压到 backups/restore_<ts>/（不覆盖现场）。"""
    iid = ensure_instance_id()
    user, pwd = ensure_registered()
    key = get_or_create_key()
    lst = list_remote()
    if not lst.get("ok") or not lst["versions"]:
        return {"ok": False, "error": "云端没有可恢复的备份"}
    if not remote_name:
        remote_name = lst["versions"][0]["name"]
    elif remote_name not in [v["name"] for v in lst["versions"]]:
        return {"ok": False, "error": f"云端不存在该版本: {remote_name}"}
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dl_dir = _BACKUP_ROOT / "cloud_download"
    out_dir = _BACKUP_ROOT / f"restore_{ts}"
    dl_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(auth=(user, pwd), timeout=_HTTP_TIMEOUT) as c:
        r = c.get(_dav(remote_name))
        if r.status_code != 200:
            return {"ok": False, "error": f"下载失败: HTTP {r.status_code}"}
        enc_path = dl_dir / remote_name
        enc_path.write_bytes(r.content)
        try:
            plain = decrypt_bytes(r.content, key)
        except Exception as e:
            return {"ok": False, "error": f"解密失败（密钥不符或文件损坏）: {e}",
                    "note": "如果本地密钥丢失，云端存档无法恢复；请找回 data/cloud_key.txt"}
        zip_path = dl_dir / remote_name[:-4]
        zip_path.write_bytes(plain)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)
    return {
        "ok": True,
        "restored_from": remote_name,
        "restore_dir": str(out_dir),
        "files": [p.name for p in out_dir.rglob("*") if p.is_file()],
        "note": "已解压到安全目录，核对无误后再覆盖 data/ 与 config.yaml",
    }


# ---------- 入口 ----------

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "run":
        result = run(note="命令行手动云备份")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif mode == "newkey":
        k = get_or_create_key(force_new=True)
        print(json.dumps({"ok": True, "key_file": str(_KEY_FILE),
                          "key_sha256": hashlib.sha256(k).hexdigest(),
                          "warn": "密钥已轮换：云端既有存档将无法再解密。"},
                         ensure_ascii=False, indent=2))
    elif mode == "list":
        print(json.dumps(list_remote(), ensure_ascii=False, indent=2))
    elif mode == "restore":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(restore(name), ensure_ascii=False, indent=2))
    else:
        print("用法: cloud_backup.py [run|newkey|list|restore [版本名]]")
