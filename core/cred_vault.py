# -*- coding: utf-8 -*-
"""凭据库核心（cred_vault）：多账户凭据的加密存储 + 解析回退。

背景（消除技术债）：
- 外部服务工具（dropbox/gdrive/feishu/webdav）的凭据一律读环境变量 → 进程内同一时间
  只能承载一套账户；多账户并存时互相踩踏（想换账号只能改环境变量+重启）。
- 本模块提供两层能力：
  1) 存储层：按 service/account 把凭据字段加密落盘（Fernet，密钥存 data/credentials/.vault_key）。
     同一服务可存多套账户，互不干扰。
  2) 解析层：resolve() —— account 为空走环境变量（兼容现状、零破坏）；account 指定则
     从 vault 读取，vault 中非空字段覆盖环境变量默认值（环境变量 = 默认账户）。

安全边界（如实）：
- 密文落盘 data/credentials/vault.bin；密钥 .vault_key 同目录。
- 两文件均不入公开仓库（.gitignore 隔离 data/credentials/），也不进私有备份清单
  （backup_private.py 为显式清单，vault 不在其内）——凭据只留本机，不随任何备份/云端流转。
- 凭据不写日志、不打印明文；list 只返回账户名。
- 删除需 confirm="REMOVE" 二次确认（防误删）。
- 依赖 cryptography（requirements.txt 已含）；缺失时抛清晰错误，不静默降级（凭据安全优先）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---- 服务凭据字段白名单：新增外部服务接入时在此登记 ----
SERVICES: Dict[str, List[str]] = {
    "dropbox": ["access_token", "refresh_token", "app_key", "app_secret"],
    "gdrive": ["token_json", "sa_json"],
    "feishu": ["app_id", "app_secret"],
    "webdav": ["base_url", "username", "password"],
}

# 账户名合法字符（小写字母/数字/中划线/下划线/点）
_ACCOUNT_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")

_DIR = Path(__file__).resolve().parents[1] / "data" / "credentials"
_STORE_FILE = "vault.bin"      # Fernet 密文（整个 vault 一个文件）
_KEY_FILE = ".vault_key"       # Fernet 密钥（二进制）

_FERNET = None


class VaultError(Exception):
    """凭据库错误（账户缺失/字段非法/解密失败等）。"""


# ---------- 密钥与加密原语 ----------
def _fernet():
    """懒加载 Fernet（cryptography）。"""
    global _FERNET
    if _FERNET is not None:
        return _FERNET
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:  # pragma: no cover
        raise VaultError(
            "缺少 cryptography 依赖，无法使用凭据库加密存储。"
            "请先安装：pip install cryptography"
        ) from e
    _DIR.mkdir(parents=True, exist_ok=True)
    key_path = _DIR / _KEY_FILE
    if not key_path.exists():
        # 首次使用：生成密钥。密钥与密文同目录、不入仓库不入备份 —— 本机信任域。
        key_path.write_bytes(Fernet.generate_key())
    _FERNET = Fernet(key_path.read_bytes())
    return _FERNET


def _encrypt(plain: bytes) -> bytes:
    return _fernet().encrypt(plain)


def _decrypt(token: bytes) -> bytes:
    try:
        return _fernet().decrypt(token)
    except Exception as e:  # noqa: BLE001（密钥变更/文件损坏均在此暴露）
        raise VaultError(f"凭据库解密失败（密钥变更或文件损坏）: {type(e).__name__}: {e}") from e


# ---------- 持久化 ----------
def _read() -> Dict:
    """读取整个 vault（密文 → dict）。文件不存在/为空 → 空库。"""
    p = _DIR / _STORE_FILE
    if not p.exists():
        return {}
    try:
        raw = _decrypt(p.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise VaultError(f"凭据库内容损坏: {e}") from e


def _write(data: Dict) -> None:
    """整个 vault 加密落盘（原子：先写临时再替换）。"""
    _DIR.mkdir(parents=True, exist_ok=True)
    token = _encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    tmp = _DIR / f"{_STORE_FILE}.tmp"
    tmp.write_bytes(token)
    os.replace(tmp, _DIR / _STORE_FILE)


def _check(service: str, account: str, fields: Optional[Dict] = None) -> None:
    """入参校验：service 已知、account 合法、fields 字段在白名单内。"""
    if service not in SERVICES:
        raise VaultError(
            f"未知服务 '{service}'。已知服务: {', '.join(sorted(SERVICES))}"
        )
    account = (account or "").strip().lower()
    if not account:
        raise VaultError("account（账户别名）不能为空")
    if not set(account) <= _ACCOUNT_CHARS or len(account) > 64:
        raise VaultError(f"account 命名非法（仅小写字母/数字/_-.，≤64字符）: {account!r}")
    if fields is not None:
        allowed = set(SERVICES[service])
        extra = set(fields) - allowed
        if extra:
            raise VaultError(
                f"字段 {sorted(extra)} 不在 {service} 允许范围内 {sorted(allowed)}"
            )


# ---------- 对外 API ----------
def list_accounts(service: str = "") -> Dict[str, List[str]]:
    """列出凭据库中的账户（只返回账户名，绝不返回密文值）。"""
    data = _read()
    if service:
        if service not in SERVICES:
            raise VaultError(f"未知服务 '{service}'。已知服务: {', '.join(sorted(SERVICES))}")
        return {service: sorted(data.get(service, {}).keys())}
    out = {}
    for svc in SERVICES:
        accounts = sorted(data.get(svc, {}).keys())
        if accounts:
            out[svc] = accounts
    return out


def get(service: str, account: str) -> Optional[Dict]:
    """读取某服务某账户的凭据字段 dict；不存在返回 None。"""
    _check(service, account)
    data = _read()
    entry = data.get(service, {}).get(account.strip().lower())
    return dict(entry) if entry else None


def put(service: str, account: str, fields: Dict) -> Dict:
    """新增/更新某服务某账户的凭据。fields 全量替换该账户（至少一个非空字段）。"""
    fields = {k: str(v).strip() for k, v in (fields or {}).items()}
    _check(service, account, fields)
    if not any(fields.get(k) for k in SERVICES[service]):
        raise VaultError(f"至少提供一个非空字段: {SERVICES[service]}")
    account = account.strip().lower()
    data = _read()
    data.setdefault(service, {})[account] = {
        k: fields.get(k, "") for k in SERVICES[service]
    }
    _write(data)
    return {"ok": True, "service": service, "account": account,
            "fields": sorted(k for k in SERVICES[service] if fields.get(k))}


def remove(service: str, account: str, confirm: str = "") -> Dict:
    """删除某服务某账户。confirm 必须为 REMOVE。"""
    _check(service, account)
    if confirm != "REMOVE":
        return {"ok": False, "error": f"删除需二次确认：请传 confirm=\"REMOVE\"（将删除 {service}/{account}）"}
    account = account.strip().lower()
    data = _read()
    svc = data.get(service, {})
    if account not in svc:
        return {"ok": False, "error": f"{service} 下无账户 '{account}'（当前: {sorted(svc) or '空'}）"}
    del svc[account]
    if not svc:
        data.pop(service, None)
    _write(data)
    return {"ok": True, "removed": f"{service}/{account}"}


def resolve(service: str, account: str, env_fields: Dict) -> Tuple[Dict, str]:
    """凭据解析（核心入口，供各组外部服务工具调用）。

    规则：
    - account 为空 → 直接用 env_fields（环境变量 = 默认账户，兼容现状）。
    - account 非空 → 从 vault 读；读到则 vault 非空字段覆盖 env_fields 默认值；
      读不到 → 抛 VaultError（提示先 account_add）。
    返回 (凭据 dict, 来源说明 'env' | 'vault:<account>')。
    """
    env = {k: (v or "").strip() for k, v in (env_fields or {}).items()}
    if not (account or "").strip():
        return env, "env"
    v = get(service, account)
    if v is None:
        raise VaultError(
            f"凭据库中无 {service} 账户 '{account.strip().lower()}'。"
            f"请先用 account_add(service={service!r}, account=...) 添加，"
            f"或留空 account 走环境变量默认账户。"
        )
    merged = dict(env)
    for k, val in v.items():
        if val:
            merged[k] = val
    return merged, f"vault:{account.strip().lower()}"
