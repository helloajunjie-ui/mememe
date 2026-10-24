#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素月复活包：2-of-3 Shamir 秘密拆分。

把「云存档解密密钥 + WebDAV 凭据 + instance_id」打成一个 recovery blob，
用 Shamir(2,3) 拆成 3 份：
  * 任取 2 份 -> 完整恢复
  * 只有 1 份 -> 信息论上等于零（不含任何原始信息）
  * 丢 1 份 -> 仍可恢复

用法：
    python recovery.py split   [--out DIR]
    python recovery.py combine SHARD_A SHARD_B [--out FILE]
    python recovery.py verify  SHARD_A SHARD_B
"""
import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # tools/src/python/recovery.py -> 项目根
KEY_FILE = ROOT / "data" / "keys" / "cloud_key.txt"
ENV_FILE = ROOT / ".env"
SELF_FILE = ROOT / "data" / "self.yaml"

# ---------- GF(256)，AES 多项式 0x11B ----------
# 注意：不要用「以 2 为生成元的对数表」——在 AES 的 GF(2^8) 里 0x02 的阶只有 51，
# 大部分元素的 LOG 未定义，会把份额算退化成相同值（实测 share1==share3）。
# 这里用俄罗斯农民乘法直接算，慢但绝对正确（份额只有几百字节）。


def _mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p & 0xFF


def _pow(a: int, n: int) -> int:
    r = 1
    while n:
        if n & 1:
            r = _mul(r, a)
        a = _mul(a, a)
        n >>= 1
    return r


def _div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("gf256 div by zero")
    return _mul(a, _pow(b, 254))  # 逆元 = b^254


def split_secret(secret: bytes, n: int = 3, k: int = 2):
    """f(x) = secret + a1*x ；k=2 时只有一次项。返回 [(x, bytes), ...]"""
    if k != 2:
        raise NotImplementedError("本实现支持 k=2")
    if n > 255 or n < k:
        raise ValueError("份额数非法")
    a1 = os.urandom(len(secret))
    shards = []
    for xi in range(1, n + 1):
        y = bytearray(secret)
        for i, cb in enumerate(a1):
            y[i] ^= _mul(cb, xi)
        shards.append((xi, bytes(y)))
    return shards


def combine_shares(points, k: int = 2) -> bytes:
    """拉格朗日插值求 f(0)。points: [(x:int, y:bytes), ...]"""
    pts = points[:k]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if len({len(y) for y in ys}) != 1:
        raise ValueError("份额长度不一致")
    out = bytearray(len(ys[0]))
    for i in range(len(out)):
        acc = 0
        for j in range(k):
            num, den = 1, 1
            for m in range(k):
                if m == j:
                    continue
                num = _mul(num, xs[m])          # (0 - x_m) == x_m in GF(2^8)
                den = _mul(den, xs[j] ^ xs[m])
            acc ^= _mul(ys[j][i], _div(num, den))
        out[i] = acc
    return bytes(out)


# ---------- 复活包 ----------
def _read_env_keys(want):
    vals = {}
    if not ENV_FILE.exists():
        return vals
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k in want:
            vals[k] = v.strip().strip('"').strip("'")
    return vals


def build_blob() -> dict:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    env = _read_env_keys({"CLOUD_WEBDAV_USER", "CLOUD_WEBDAV_PASS"})
    iid = ""
    if SELF_FILE.exists():
        for line in SELF_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("instance_id:"):
                iid = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
    return {
        "version": 1,
        "instance_id": iid,
        "webdav_url": "https://dpoo.my/dav/",
        "webdav_user": env.get("CLOUD_WEBDAV_USER", ""),
        "webdav_pass": env.get("CLOUD_WEBDAV_PASS", ""),
        "cloud_key_b64": key,
        "key_sha256": hashlib.sha256(base64.b64decode(key)).hexdigest(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "note": "AES-256-GCM。cloud/backup_*.zip.enc 用 cloud_key 解密。任取 2 份份额可恢复本包。",
    }


SHARD_HEADER = "SUYUE-RECOVERY-SHARD v1"


def write_shard(path: Path, xi: int, payload: bytes, n: int, k: int):
    b64 = base64.b64encode(payload).decode()
    lines = [
        SHARD_HEADER,
        f"index: {xi}",
        f"threshold: {k}",
        f"total: {n}",
        f"len: {len(payload)}",
        "data: " + b64,
        "",
        "# 素月 / Suyue 云存档复活份额之一。",
        "# 单独一份不含任何信息；集齐任意 2 份执行：",
        "#   python tools/src/python/recovery.py combine 份额A 份额B",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_shard(path) -> tuple:
    txt = Path(path).read_text(encoding="utf-8", errors="ignore")
    xi, data = None, None
    for line in txt.splitlines():
        line = line.strip()
        if line.lower().startswith("index:"):
            xi = int(line.split(":", 1)[1].strip())
        elif line.lower().startswith("data:"):
            data = base64.b64decode(line.split(":", 1)[1].strip())
    if xi is None or data is None:
        raise ValueError(f"份额文件格式不对: {path}")
    return xi, data


def cmd_split(out_dir: str):
    blob = build_blob()
    raw = json.dumps(blob, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    shards = split_secret(raw, n=3, k=2)
    out = Path(out_dir or (ROOT / "workspace" / "explore" / "recovery_shards"))
    out.mkdir(parents=True, exist_ok=True)
    print(f"blob_len={len(raw)}  key_sha256={blob['key_sha256'][:16]}...  instance={blob['instance_id']}")
    for xi, payload in shards:
        p = out / f"recovery_share_{xi}.txt"
        write_shard(p, xi, payload, 3, 2)
        print(f"  share {xi}: {p}  sha256={hashlib.sha256(payload).hexdigest()[:16]}...  chars={len(base64.b64encode(payload))}")
    # 自检：3 种组合都必须能还原
    ok = 0
    for i in range(3):
        for j in range(i + 1, 3):
            rec = combine_shares([shards[i], shards[j]], k=2)
            if rec == raw:
                ok += 1
    # 单份必须不含原始信息（前 16 字节与 blob 不同即视为已混淆）
    leak = sum(1 for _, p in shards if p[:16] == raw[:16])
    print(f"[自检] 3 组合全部可恢复: {ok}/3  单份前16字节与原文重合: {leak}/3")
    if ok != 3 or leak != 0:
        raise SystemExit("自检失败")
    return out


def cmd_combine(a: str, b: str, out_file: str):
    rec = combine_shares([read_shard(a), read_shard(b)], k=2)
    blob = json.loads(rec.decode("utf-8"))
    text = json.dumps(blob, ensure_ascii=False, indent=2)
    if out_file:
        Path(out_file).write_text(text, encoding="utf-8")
        print(f"已写出: {out_file}  instance={blob.get('instance_id')}")
    else:
        print(text)
    return blob


def cmd_verify(a: str, b: str):
    rec = combine_shares([read_shard(a), read_shard(b)], k=2)
    blob = json.loads(rec.decode("utf-8"))
    local = KEY_FILE.read_text(encoding="utf-8").strip() if KEY_FILE.exists() else ""
    got = hashlib.sha256(base64.b64decode(blob["cloud_key_b64"])).hexdigest()
    exp = blob.get("key_sha256", "")
    same_local = (blob["cloud_key_b64"] == local)
    print(f"blob 内密钥指纹 = {got}")
    print(f"blob 记录指纹   = {exp}   一致: {got == exp}")
    print(f"与本地 cloud_key 一致: {same_local}")
    print(f"webdav_url={blob.get('webdav_url')}  user_len={len(blob.get('webdav_user',''))}  pass_len={len(blob.get('webdav_pass',''))}")
    ok = (got == exp) and bool(blob.get("cloud_key_b64")) and bool(blob.get("webdav_pass"))
    print("结果:", "PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("split");   s1.add_argument("--out", default="")
    s2 = sub.add_parser("combine"); s2.add_argument("a"); s2.add_argument("b"); s2.add_argument("--out", default="")
    s3 = sub.add_parser("verify");  s3.add_argument("a"); s3.add_argument("b")
    args = ap.parse_args()
    if args.cmd == "split":
        cmd_split(args.out)
    elif args.cmd == "combine":
        cmd_combine(args.a, args.b, args.out)
    elif args.cmd == "verify":
        sys.exit(0 if cmd_verify(args.a, args.b) else 1)


if __name__ == "__main__":
    main()
