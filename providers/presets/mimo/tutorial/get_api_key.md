# 获取小米 MiMo API Key

MiMo 是小米的模型系列，接口同时兼容 OpenAI 与 Anthropic 两种协议，
`mimo-v2.5-pro` 支持 1M 上下文，`mimo-v2.5` 额外支持图像 / 视频 / 音频理解。

## 1. 登录开放平台

打开 [小米 MiMo API 开放平台](https://platform.xiaomimimo.com)，用小米账号登录。
没有小米账号的话，去 [id.mi.com](https://id.mi.com/) 注册一个。

## 2. 创建 API Key

控制台左侧 → [API Keys](https://platform.xiaomimimo.com/#/console/api-keys) → 创建，
复制生成的 Key（`sk-` 开头）。

Key 只显示一次，立刻复制保存。建议按量付费先充一点余额试试。

## 3. 选择接入方式

| 方式 | Base URL | Key 前缀 |
|---|---|---|
| 按量付费 | `https://api.xiaomimimo.com/v1` | `sk-` |
| Token Plan 订阅 | `https://token-plan-cn.xiaomimimo.com/v1` | `tp-` |

两种方式的接口格式完全一致，只是 Base URL 和 Key 不同。

## 4. 填入 Debata

预设里填的是按量付费的 Base URL。如果你买的是 Token Plan，
到「设置 → 模型 → 编辑 MiMo」把 Base URL 换成订阅专属地址即可，不必改预设文件。

## 5. 一个注意点

思考模式下官方不支持自定义 `temperature` / `top_p`——传了不报错，
但会被模型强制成推荐值（1.0 / 0.95）。这不影响使用，知道就好。
