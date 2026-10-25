# -*- coding: utf-8 -*-
"""邮箱锚点保活与自证：定期登录 163，确认恢复份额邮件仍在且内容未被改动。

为什么必须有这个：
  163 / Gmail 对长期不活跃账号有冻结或回收策略。邮箱作为恢复份额的外部存储
  位置，一旦被静默回收，"链断了"这件事没有任何人会来通知我。所以锚点必须
  自己定期自证：登录(保活) + 邮件在不在 + 附件 sha256 是否仍与本地基准一致。

设计：
  - 凭据从加密凭据库取（core.cred_vault），绝不硬编码明文授权码；
  - 无控制台运行（pythonw）：不写 stdout，全部落日志文件；
  - 异常只记日志 + state 标记 alert，不抛异常打断计划任务。
"""
from __future__ import annotations

import hashlib
import imaplib
import json
import smtplib
import sys
import traceback
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_LOG = _ROOT / "logs" / "mail_anchor.log"
_STATE = _ROOT / "data" / "mail_anchor_state.json"
_BASELINE = _ROOT / "workspace" / "explore" / "recovery_shards" / "recovery_share_2.txt"
_MARK = "[SUYUE-RECOVERY]"

imaplib.Commands["ID"] = ("AUTH", "SELECTED")


def log(msg: str) -> None:
    line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def load_cred():
    from core import cred_vault  # noqa: E402

    c = cred_vault.get("email", "main") or {}
    return c.get("address") or "", c.get("password") or ""


def main() -> int:
    result = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "imap_ok": False,
        "smtp_ok": False,
        "mail_present": False,
        "sha_match": None,
        "alert": False,
        "note": "",
    }
    try:
        addr, code = load_cred()
        if not addr or not code:
            raise RuntimeError("凭据库缺 email/main 的 address 或 password")

        # 1) IMAP 登录 + 找份额邮件 + 校验附件
        im = imaplib.IMAP4_SSL("imap.163.com", 993, timeout=90)
        im.login(addr, code)
        im._simple_command("ID", '("name" "SuyueAgent" "version" "1.0.0" "vendor" "suyue-local")')
        result["imap_ok"] = True
        im.select("INBOX", readonly=True)
        typ, dat = im.search(None, "ALL")
        ids = dat[0].split() if dat and dat[0] else []
        target = None
        for uid in ids:
            typ, d = im.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
            hdr = b""
            for item in d:
                if isinstance(item, tuple):
                    hdr = item[1]
            if _MARK in hdr.decode("utf-8", "replace"):
                target = uid
        if target is None:
            result["alert"] = True
            result["note"] = "份额邮件在 INBOX 中找不到（可能被删/被回收）"
            log("ALERT: recovery mail not found in INBOX (total %d)" % len(ids))
        else:
            result["mail_present"] = True
            typ, dat = im.fetch(target, "(BODY.PEEK[])")
            raw = b""
            for item in dat:
                if isinstance(item, tuple):
                    raw = item[1]
            from email import policy
            from email.parser import BytesParser

            msg = BytesParser(policy=policy.default).parsebytes(raw)
            payload = b""
            for part in msg.iter_attachments():
                payload = part.get_payload(decode=True) or b""
            sha_remote = hashlib.sha256(payload).hexdigest()
            if _BASELINE.exists():
                sha_local = hashlib.sha256(_BASELINE.read_bytes()).hexdigest()
                result["sha_match"] = sha_remote == sha_local
                if not result["sha_match"]:
                    result["alert"] = True
                    result["note"] = "附件 sha256 与本地基准不符"
                    log("ALERT: shard sha mismatch remote=%s local=%s" % (sha_remote, sha_local))
                else:
                    log("ok: shard present, %d bytes, sha256=%s" % (len(payload), sha_remote))
            else:
                result["note"] = "本地基准缺失，仅确认邮件存在（sha256=%s）" % sha_remote
                log("warn: baseline missing, remote sha256=%s bytes=%d" % (sha_remote, len(payload)))
        im.logout()

        # 2) SMTP 登录（同样算活跃访问，双通道保活）
        s = smtplib.SMTP_SSL("smtp.163.com", 465, timeout=90)
        s.login(addr, code)
        try:
            s.noop()
        finally:
            s.quit()
        result["smtp_ok"] = True
        log("ok: smtp login alive")

    except Exception as e:
        result["alert"] = True
        result["note"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        log("ERROR: %s\n%s" % (result["note"], traceback.format_exc()[:1200]))

    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    log("done: alert=%s note=%s" % (result["alert"], result["note"]))
    return 0 if not result["alert"] else 1


if __name__ == "__main__":
    sys.exit(main())
