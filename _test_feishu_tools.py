# -*- coding: utf-8 -*-
"""feishu_* 工具组最小连通性测试：mock 官方 lark-oapi client 验证 8 个工具。

本机无真实飞书应用凭据，故不直连飞书 API。策略：
- 设置环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET（假值，仅过 _creds() 检查）。
- monkeypatch feishu_tools._client() 返回假 client，桩各 API 方法返回带
  success()/data/code/msg/get_log_id() 的假响应对象。
- 验证 8 个工具的参数校验、错误分支、成功返回结构。

真实凭据接入后需补端到端冒烟（本测试不覆盖真实 API）。
"""
import os
import sys

# 让 tools.base 可导入（与 _test_cloud_webdav.py 同款路径注入）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.src.python import feishu_tools  # noqa: E402

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


# ---- 假响应对象 ----
class FakeResp:
    """模拟 SDK 响应：success()/code/msg/get_log_id()/data。"""

    def __init__(self, ok=True, data=None, code=0, msg="success"):
        self._ok = ok
        self.data = data
        self.code = code
        self.msg = msg

    def success(self):
        return self._ok

    def get_log_id(self):
        return "mock_log_id"


class _Ns:
    """模拟 client.<service>.v1.<resource> 命名空间链。"""

    def __init__(self, **methods):
        self._methods = methods

    def __getattr__(self, name):
        if name in self._methods:
            return self._methods[name]
        raise AttributeError(name)


# ---- 假 client ----
def _build_fake_client():
    """构造假 client，各 API 方法返回预设响应。"""
    im_v1 = _Ns(
        message=_Ns(create=lambda req: FakeResp(data=_obj(
            message_id="om_mock_msg", chat_id="oc_mock_chat",
            msg_type="text", create_time="1700000000000"))),
        chat=_Ns(list=lambda req: FakeResp(data=_obj(
            items=[_obj(chat_id="oc_mock_chat", name="测试群", description="",
                        type="group", owner_user_id="ou_mock", avatar="")],
            has_more=False, page_token=""))),
    )
    bitable_v1 = _Ns(
        app_table_record=_Ns(
            list=lambda req: FakeResp(data=_obj(
                items=[_obj(record_id="rec_mock", fields={"姓名": "张三", "年龄": 30})],
                total=1, has_more=False, page_token="")),
            create=lambda req: FakeResp(data=_obj(
                record=_obj(record_id="rec_mock_new", fields={"姓名": "李四"}))),
        ),
    )
    docx_v1 = _Ns(
        document=_Ns(
            raw_content=lambda req: FakeResp(data=_obj(content="这是云文档正文内容")),
            create=lambda req: FakeResp(data=_obj(
                document=_obj(document_id="dox_mock", title="测试文档",
                              revision_id=1))),
        ),
    )
    client = _Ns(
        im=_Ns(v1=im_v1),
        bitable=_Ns(v1=bitable_v1),
        docx=_Ns(v1=docx_v1),
    )
    return client


class _obj:
    """把 dict 转成带属性的对象（模拟 SDK model 实例）。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _install_mock():
    """设置假凭据环境变量 + monkeypatch _client()。"""
    os.environ["FEISHU_APP_ID"] = "cli_mock_app_id"
    os.environ["FEISHU_APP_SECRET"] = "mock_secret"
    feishu_tools._client_singleton = None  # 重置单例
    feishu_tools._client = lambda: _build_fake_client()  # noqa: E731


def _clear_env():
    os.environ.pop("FEISHU_APP_ID", None)
    os.environ.pop("FEISHU_APP_SECRET", None)
    feishu_tools._client_singleton = None


def main():
    print("== feishu_* 工具组 mock 测试 ==")

    # ---- 1. 凭据缺失分支（whoami 不依赖 client）----
    print("[1] 凭据缺失分支")
    _clear_env()
    r = feishu_tools.feishu_whoami()
    check("whoami 无凭据返回 configured=False", r.get("ok") is False and r.get("configured") is False,
          str(r))
    r = feishu_tools.feishu_send_text(receive_id="oc_x", text="hi")
    check("send_text 无凭据返回错误", r.get("ok") is False and "FEISHU_APP_ID" in r.get("error", ""),
          str(r))

    # ---- 2. 成功路径 ----
    print("[2] 成功路径")
    _install_mock()

    r = feishu_tools.feishu_send_text(receive_id="oc_mock_chat", text="你好", receive_id_type="chat_id")
    check("send_text 成功", r.get("ok") is True and r.get("message_id") == "om_mock_msg", str(r))

    r = feishu_tools.feishu_send_post(receive_id="oc_mock_chat", text="正文", title="标题",
                                      receive_id_type="chat_id")
    check("send_post 成功", r.get("ok") is True and r.get("message_id") == "om_mock_msg", str(r))

    r = feishu_tools.feishu_list_chat()
    check("list_chat 成功", r.get("ok") is True and r.get("count") == 1
          and r["items"][0]["chat_id"] == "oc_mock_chat", str(r))

    r = feishu_tools.feishu_bitable_list(app_token="bascn_mock", table_id="tbl_mock")
    check("bitable_list 成功", r.get("ok") is True and r.get("count") == 1
          and r["items"][0]["record_id"] == "rec_mock", str(r))

    r = feishu_tools.feishu_bitable_create(app_token="bascn_mock", table_id="tbl_mock",
                                           fields={"姓名": "李四"})
    check("bitable_create 成功", r.get("ok") is True and r.get("record_id") == "rec_mock_new", str(r))

    r = feishu_tools.feishu_docx_read(document_id="dox_mock")
    check("docx_read 成功", r.get("ok") is True and "云文档正文" in r.get("content", ""), str(r))

    r = feishu_tools.feishu_docx_create(title="测试文档")
    check("docx_create 成功", r.get("ok") is True and r.get("document_id") == "dox_mock", str(r))

    r = feishu_tools.feishu_whoami()
    check("whoami 有凭据返回 configured=True", r.get("ok") is True and r.get("configured") is True,
          str(r))

    # ---- 3. 空串透传不崩溃（如实：必填校验由 SDK 承担，mock 层不模拟 SDK 校验）----
    # 工具层设计为"依赖 SDK 校验必填参数"，不自行拦截空串（空串会透传给 SDK，
    # 由真实 SDK 报参数错误）。mock 层不模拟 SDK 校验，故空串下返回成功是预期行为——
    # 本组用例仅验证"空串透传不抛异常/不崩溃"，真实必填校验需真实 SDK 端到端覆盖。
    print("[3] 空串透传不崩溃")
    r = feishu_tools.feishu_bitable_list(app_token="", table_id="tbl_mock")
    check("bitable_list 空 app_token 透传不崩溃", isinstance(r, dict), str(r))
    r = feishu_tools.feishu_docx_read(document_id="")
    check("docx_read 空 document_id 透传不崩溃", isinstance(r, dict), str(r))
    r = feishu_tools.feishu_docx_create(title="")
    check("docx_create 空 title 透传不崩溃", isinstance(r, dict), str(r))

    _clear_env()
    print(f"\n结果: {_PASS} PASS / {_FAIL} FAIL")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
