"""配置改动的原子提交 —— 改副本、校验、通过了才搬回原对象。

WebUI 的写配置接口都遵守同一条规则：**校验失败的改动不能留在内存里**。

反面教材（真实事故）：``PATCH /api/settings`` 先把值写进 ``rt.config`` 再校验，
校验失败只回了一个 400，坏值却已经留在内存里；之后任意一个不校验的接口
（比如「模型」页换 Agent 的 provider）落盘一次，就把这份启动时才炸的配置
写进了 config.yaml，Runtime 下次启动直接起不来。
"""

from __future__ import annotations

from collections.abc import Callable

from app_config.schema import RootConfig


def validate_config(cfg: RootConfig) -> None:
    """跑一遍完整校验（含 provider 引用），不合法抛 ``ValidationError``。"""
    RootConfig.model_validate(cfg.model_dump())


def apply_validated(cfg: RootConfig, mutate: Callable[[RootConfig], None]) -> None:
    """在深拷贝上执行 ``mutate``，校验通过后整份搬回 ``cfg``。

    搬回是逐字段赋值，调用方持有的 ``cfg`` 引用不变（Runtime、各服务都握着它）。
    校验不通过时 ``cfg`` 一个字段都没动过。
    """
    draft = cfg.model_copy(deep=True)
    mutate(draft)
    validate_config(draft)
    for field_name in type(cfg).model_fields:
        setattr(cfg, field_name, getattr(draft, field_name))
