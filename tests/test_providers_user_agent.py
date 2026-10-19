"""官方 SDK 的 User-Agent 被中转站风控拦截的回归测试。

背景（2026-09 实测）：``https://api.bingyun.vip`` 这类中转站会把
``OpenAI/Python <ver>`` / ``Anthropic/Python <ver>`` 直接判为风控对象，
返回 ``403 Your request was blocked``。SDK 把它抛成 ``PermissionDeniedError``，
看上去像密钥没权限，非常容易误判成「key 填错了」。

同一 key、同一 body，只把 UA 换成中性值就立刻 200，所以协议层默认换 UA；
但用户若显式配了自己的 ``User-Agent``，必须原样保留。
"""

from __future__ import annotations

from providers.base import (
    NEUTRAL_USER_AGENT,
    headers_with_neutral_user_agent,
)


def test_adds_neutral_user_agent_when_none_given():
    headers = headers_with_neutral_user_agent(None)
    assert headers is not None
    assert headers["User-Agent"] == NEUTRAL_USER_AGENT


def test_adds_neutral_user_agent_alongside_other_headers():
    headers = headers_with_neutral_user_agent({"X-Trace": "abc"})
    assert headers is not None
    assert headers["X-Trace"] == "abc"
    assert headers["User-Agent"] == NEUTRAL_USER_AGENT


def test_keeps_user_supplied_user_agent():
    """用户显式指定 UA 时不能覆盖——那可能是有意绕开别家风控用的。"""
    headers = headers_with_neutral_user_agent({"User-Agent": "MyClient/9"})
    assert headers == {"User-Agent": "MyClient/9"}


def test_user_agent_match_is_case_insensitive():
    for key in ("user-agent", "USER-AGENT", "User-agent"):
        headers = headers_with_neutral_user_agent({key: "MyClient/9"})
        assert headers == {key: "MyClient/9"}, f"{key} 应被识别为用户自定义 UA"


def test_does_not_mutate_input_dict():
    original = {"X-Trace": "abc"}
    headers_with_neutral_user_agent(original)
    assert original == {"X-Trace": "abc"}, "不应就地修改调用方传入的字典"
