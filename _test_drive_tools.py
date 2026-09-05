# -*- coding: utf-8 -*-
"""dropbox_* / gdrive_* 工具组最小连通性测试：mock 官方 SDK client 验证 12 个工具。

本机无真实 Dropbox / Google Drive 凭据，故不直连云盘 API。策略：
- 设置假凭据环境变量（仅过 _creds() 检查）。
- monkeypatch dropbox_tools._client() / gdrive_tools._client() 返回假 client，
  桩各 API 方法返回假响应对象。
- 验证 12 个工具的参数校验、错误分支、成功返回结构。

真实凭据接入后需补端到端冒烟（本测试不覆盖真实 API）。
"""
import os
import sys

# 让 tools.base 可导入（与 _test_feishu_tools.py 同款路径注入）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.src.python import dropbox_tools  # noqa: E402
from tools.src.python import gdrive_tools  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}  {detail}")


class _obj:
    """把 dict 转成带属性的对象（模拟 SDK model 实例）。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ================= Dropbox mock =================

class _FakeDropboxResp:
    """模拟 files_download 返回的 requests.Response（有 .content/.close()）。"""

    def __init__(self, content: bytes):
        self.content = content
        self._closed = False

    def close(self):
        self._closed = True


def _build_fake_dropbox():
    """构造假 Dropbox client，桩各 API 方法。"""
    file_meta = _obj(
        name="note.txt", path_lower="/docs/note.txt", size=11, rev="rev1", id="id:file1"
    )
    folder_meta = _obj(name="docs", path_lower="/docs", id="id:folder1")
    deleted_meta = _obj(path_lower="/docs/old.txt")  # 无 name → DeletedMetadata 特征

    class _FakeDropbox:
        def files_list_folder(self, path, recursive=False, limit=100):
            return _obj(
                entries=[file_meta, folder_meta, deleted_meta],
                has_more=False,
                cursor="cursor1",
            )

        def files_download(self, path):
            return (file_meta, _FakeDropboxResp("你好，白绫！".encode("utf-8")))

        def files_upload(self, data, path, mode=None, strict_conflict=False):
            return _obj(name="note.txt", rev="rev_new", size=len(data))

        def files_create_folder_v2(self, path):
            return _obj(metadata=_obj(name="newfolder", id="id:folder_new"))

        def files_delete_v2(self, path):
            return _obj(metadata=_obj(name="old.txt", id="id:file_old"))

        def users_get_current_account(self):
            return _obj(
                name=_obj(display_name="白绫测试账号"),
                email="bailing@example.com",
                account_type="basic",
                country="CN",
            )

    return _FakeDropbox()


# ================= Google Drive mock =================

class _FakeGDriveRequest:
    """模拟 googleapiclient 的 HttpRequest（链式 .execute()）。"""

    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeGDriveFiles:
    """模拟 service.files() 资源，方法返回链式 request。"""

    def __init__(self):
        self._files = [
            {"id": "file1", "name": "note.txt", "mimeType": "text/plain", "size": "11"},
            {"id": "folder1", "name": "docs", "mimeType": "application/vnd.google-apps.folder"},
        ]

    def list(self, **kw):
        # 区分：gdrive_list 的 fields 含 nextPageToken；gdrive_write 查重 fields 仅 id,name
        fields = kw.get("fields", "")
        if "nextPageToken" in fields:
            return _FakeGDriveRequest({"files": self._files, "nextPageToken": ""})
        # 查重：返回空（无同名冲突）
        return _FakeGDriveRequest({"files": []})

    def get(self, **kw):
        return _FakeGDriveRequest(
            {"id": "file1", "name": "note.txt", "mimeType": "text/plain", "size": "11"}
        )

    def get_media(self, **kw):
        return _FakeGDriveRequest("你好，白绫！".encode("utf-8"))

    def create(self, body=None, media_body=None, fields=None):
        return _FakeGDriveRequest(
            {"id": "file_new", "name": body.get("name", ""), "mimeType": "text/plain", "size": "0"}
        )

    def delete(self, **kw):
        return _FakeGDriveRequest({})


class _FakeGDriveService:
    def files(self):
        return _FakeGDriveFiles()

    def about(self):
        return _Ns(get=lambda **kw: _FakeGDriveRequest(
            {"user": {"displayName": "白绫测试账号", "emailAddress": "bailing@example.com"}}
        ))


class _Ns:
    """通用命名空间：属性访问返回预设方法。"""

    def __init__(self, **methods):
        self._methods = methods

    def __getattr__(self, name):
        if name in self._methods:
            return self._methods[name]
        raise AttributeError(name)


# ================= 安装 / 清理 =================

def _install_mock():
    """设置假凭据环境变量 + monkeypatch _client()。"""
    os.environ["DROPBOX_ACCESS_TOKEN"] = "mock_dropbox_token"
    os.environ["GOOGLE_DRIVE_TOKEN_JSON"] = (
        '{"token":"t","refresh_token":"rt","client_id":"cid","client_secret":"cs"}'
    )
    dropbox_tools._client_singleton = None
    gdrive_tools._client_singleton = None
    dropbox_tools._client = lambda: _build_fake_dropbox()  # noqa: E731
    gdrive_tools._client = lambda: _FakeGDriveService()  # noqa: E731


def _clear_env():
    os.environ.pop("DROPBOX_ACCESS_TOKEN", None)
    os.environ.pop("DROPBOX_REFRESH_TOKEN", None)
    os.environ.pop("DROPBOX_APP_KEY", None)
    os.environ.pop("GOOGLE_DRIVE_TOKEN_JSON", None)
    os.environ.pop("GOOGLE_DRIVE_SA_JSON", None)
    dropbox_tools._client_singleton = None
    gdrive_tools._client_singleton = None


def main():
    print("== dropbox_* / gdrive_* 工具组 mock 测试 ==")

    # ---- 1. 凭据缺失分支 ----
    print("[1] 凭据缺失分支")
    _clear_env()
    r = dropbox_tools.dropbox_list()
    check("dropbox_list 无凭据返回错误", r.get("ok") is False and "DROPBOX" in r.get("error", ""),
          str(r))
    r = gdrive_tools.gdrive_list()
    check("gdrive_list 无凭据返回错误", r.get("ok") is False and "GOOGLE_DRIVE" in r.get("error", ""),
          str(r))

    # ---- 2. Dropbox 成功路径 ----
    print("[2] Dropbox 成功路径")
    _install_mock()

    r = dropbox_tools.dropbox_list(path="/docs")
    check("dropbox_list 成功", r.get("ok") is True and r.get("count") == 3
          and r["entries"][0]["name"] == "note.txt" and r["entries"][0]["is_dir"] is False
          and r["entries"][1]["is_dir"] is True and r["entries"][2]["deleted"] is True, str(r))

    r = dropbox_tools.dropbox_read(path="/docs/note.txt")
    check("dropbox_read 成功", r.get("ok") is True and "白绫" in r.get("content", "")
          and r.get("size") == len("你好，白绫！".encode("utf-8")), str(r))

    r = dropbox_tools.dropbox_write(path="/docs/note.txt", content="新内容")
    check("dropbox_write 成功", r.get("ok") is True and r.get("rev") == "rev_new"
          and r.get("overwritten") is False, str(r))

    r = dropbox_tools.dropbox_mkdir(path="/docs/newfolder")
    check("dropbox_mkdir 成功", r.get("ok") is True and r.get("name") == "newfolder", str(r))

    r = dropbox_tools.dropbox_delete(path="/docs/old.txt", confirm="DELETE")
    check("dropbox_delete 成功", r.get("ok") is True and r.get("deleted") is True, str(r))

    r = dropbox_tools.dropbox_whoami()
    check("dropbox_whoami 成功", r.get("ok") is True and r.get("email") == "bailing@example.com",
          str(r))

    # ---- 3. Dropbox 错误/边界分支 ----
    print("[3] Dropbox 错误/边界分支")
    r = dropbox_tools.dropbox_delete(path="/docs/old.txt", confirm="")
    check("dropbox_delete 未确认被拦截", r.get("ok") is False and "confirm" in r.get("error", ""),
          str(r))
    r = dropbox_tools.dropbox_delete(path="/", confirm="DELETE")
    check("dropbox_delete 根目录被拒", r.get("ok") is False, str(r))
    r = dropbox_tools.dropbox_read(path="/")
    check("dropbox_read 根目录被拒", r.get("ok") is False, str(r))
    r = dropbox_tools.dropbox_write(path="/docs/big.txt", content="x" * (10 * 1024 * 1024 + 1))
    check("dropbox_write 超 10MB 被拒", r.get("ok") is False and "过大" in r.get("error", ""), str(r))

    # ---- 4. Google Drive 成功路径 ----
    print("[4] Google Drive 成功路径")
    r = gdrive_tools.gdrive_list(parent_id="root")
    check("gdrive_list 成功", r.get("ok") is True and r.get("count") == 2
          and r["entries"][0]["name"] == "note.txt" and r["entries"][0]["is_dir"] is False
          and r["entries"][1]["is_dir"] is True, str(r))

    r = gdrive_tools.gdrive_read(file_id="file1")
    check("gdrive_read 成功", r.get("ok") is True and "白绫" in r.get("content", "")
          and r.get("name") == "note.txt", str(r))

    r = gdrive_tools.gdrive_write(name="note.txt", content="新内容")
    check("gdrive_write 成功", r.get("ok") is True and r.get("id") == "file_new"
          and r.get("overwritten") is False, str(r))

    r = gdrive_tools.gdrive_mkdir(name="newfolder")
    check("gdrive_mkdir 成功", r.get("ok") is True and r.get("name") == "newfolder", str(r))

    r = gdrive_tools.gdrive_delete(file_id="file1", confirm="DELETE")
    check("gdrive_delete 成功", r.get("ok") is True and r.get("deleted") is True, str(r))

    r = gdrive_tools.gdrive_whoami()
    check("gdrive_whoami 成功", r.get("ok") is True and r.get("email") == "bailing@example.com",
          str(r))

    # ---- 5. Google Drive 错误/边界分支 ----
    print("[5] Google Drive 错误/边界分支")
    r = gdrive_tools.gdrive_delete(file_id="file1", confirm="")
    check("gdrive_delete 未确认被拦截", r.get("ok") is False and "confirm" in r.get("error", ""),
          str(r))
    r = gdrive_tools.gdrive_read(file_id="")
    check("gdrive_read 空 file_id 被拒", r.get("ok") is False, str(r))
    r = gdrive_tools.gdrive_write(name="", content="x")
    check("gdrive_write 空 name 被拒", r.get("ok") is False, str(r))
    r = gdrive_tools.gdrive_write(name="big.txt", content="x" * (10 * 1024 * 1024 + 1))
    check("gdrive_write 超 10MB 被拒", r.get("ok") is False and "过大" in r.get("error", ""), str(r))

    _clear_env()
    print(f"\n结果: {_PASS} PASS / {_FAIL} FAIL")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
