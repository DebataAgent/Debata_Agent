# WebUI 说明

Debata_Agent 的界面是浏览器控制台，取代了早期的 PySide6 桌面端（`ui/` 目录已移除）。
本文说明怎么启动、页面结构、接口一览，以及二次开发时该改哪个文件。

## 启动

```bash
uv run main.py                 # 默认 0.0.0.0:8000
uv run webui_server.py         # 等价入口，参数相同
```

| 参数 | 说明 |
|---|---|
| `--host` | 监听地址，默认 `0.0.0.0`（局域网可访问）。只给本机用填 `127.0.0.1` |
| `--port` | 监听端口，默认 `8000` |
| `--token` | 固定访问令牌；不填则首次启动自动生成 |
| `--no-auth` | 关闭令牌鉴权。⚠ 能访问该端口的设备都能看聊天记录、改配置 |
| `--config` | 自定义 `config.yaml` 路径 |
| `--no-gui` | 纯命令行跑 Runtime，完全不启动 WebUI |

启动横幅会打印全部可访问地址与令牌：

```
================================================================
  Debata_Agent WebUI 已启动
================================================================
  监听        : 0.0.0.0:8000
  访问地址    : http://127.0.0.1:8000
  访问地址    : http://192.168.1.23:8000
  访问令牌    : xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  令牌来源    : /path/to/data/instances/default/webui_token.txt
================================================================
```

## 鉴权

WebUI 能读到全部聊天记录、改配置、换 API Key，所以**默认必须带令牌**，即使在局域网里也一样。

- 令牌优先级：`--token` > 环境变量 `DEBATA_WEBUI_TOKEN` > 数据目录下持久化的 `webui_token.txt`（首次启动自动生成，权限 `0600`）
- 浏览器侧用 `POST /api/auth/login` 换取 HttpOnly 会话 cookie，有效期 30 天
- 脚本 / curl 可以用 `Authorization: Bearer <token>`，或 `?token=` 查询参数（WebSocket 也认）

放行规则见 `webui/auth.py`。`/`、`/static/*`、`/api/auth/*` 不需要鉴权，其余 `/api/*` 与 `/ws/events` 都要。

## 页面结构

侧边栏顺序与原桌面仪表盘一致，末尾追加了 WebUI 新增的「黑名单」：

| 页面 | 对应后端 | 原 GUI 来源 |
|---|---|---|
| 总览 | `webui/api/overview.py` | `ui/dashboard/overview_page.py` |
| 对话 | `webui/api/chats.py` | `ui/dashboard/chats_page.py` |
| 记忆 | `webui/api/memory.py` | `ui/dashboard/memory_page.py` |
| 人格后台 | `webui/api/mind.py` | `ui/dashboard/persona_mind_page.py` |
| 日志 | `webui/api/logs.py` | `ui/dashboard/logs_page.py` |
| 角色 | `webui/api/personas.py` | `ui/dashboard/personas_page.py` |
| 模型管理 | `webui/api/models.py` | `ui/dashboard/models_page.py` |
| **表情包** | `webui/api/emoji.py` | `ui/dashboard/emoji_section.py`（WebUI 独立成页） |
| **黑名单** | `webui/api/settings.py` | 无（WebUI 新增） |
| 设置 | `webui/api/settings.py` | `ui/dashboard/settings_page.py` |

> 「模型管理」管的是**本地插件模型**（TTS / 本地 embedding 的下载与依赖）；
> LLM 提供商的增删改在 设置 →「模型」，见下面「模型接入」。

另有 `webui/api/system.py`：登录、运行状态、首次配置向导、重启 / 退出，桌面端由窗口和托盘承担。

## 架构

```
浏览器 ──HTTP/WS──> uvicorn ──同一事件循环──> core.Runtime
```

一个 uvicorn 进程同时承载 HTTP 服务和 `core.Runtime`：Runtime 在 FastAPI 的
`lifespan` 里启动（`webui/server.py:_boot`），因此**不存在跨线程 / 跨事件循环访问
asyncio 对象的问题**，路由里就是普通的 `await`。原桌面端需要 qasync 把 Qt 事件循环和
asyncio 缝在一起，WebUI 不再需要。

```
webui/
├── server.py           FastAPI 装配 + uvicorn 启动 + /ws/events
├── runtime_host.py     Runtime 生命周期宿主、配置落盘、首次配置生成
├── auth.py             令牌与会话
├── events.py           log / chat / status 三类事件的 WebSocket 集线器
├── logbuffer.py        日志环形缓冲（挂在 root logger 上）
├── settings_spec.py    设置页字段声明表（声明式表单的数据源）
├── provider_admin.py   provider 增删改（纯函数，见「模型接入」）
├── emoji_store.py      表情包文件读写与校验（纯函数，见「表情包」）
├── theme.py            色板常量（与桌面端同一套色值）
├── copy.py             仪表盘中文文案
├── model_guide.py      本地模型安装指引与目录识别（Qt-free 部分）
├── chatlog/            会话记录合并 / 分组 / 归一化 / 渲染
│   ├── display_pipeline.py  raw 记录 → DisplayItem（原 chats_page.py 的纯逻辑部分）
│   └── display_items.py     过滤 / 排序
├── wizard/             首次配置向导的步骤、流转与文案（原 ui/wizard/）
├── api/                HTTP / WebSocket 路由
└── static/             前端（无构建步骤：原生 ES 模块 + 一个 CSS）
    ├── icons.js        内联 SVG 线性图标（指南 §六：不用 emoji 当图标）
    ├── providers.js    provider 表单与列表（设置页与向导共用）
    ├── emoji_inline.js [表情包: 名字] → 内联缩略图
    ├── setup/steps.js  向导各步骤视图
    └── pages/*.js      一页一个模块，导出 render(container, ctx)
```

图标刻意是**内联 SVG** 而不是图标字体 / CDN：WebUI 没有构建步骤，要能在
ARM 板子这类离线环境里直接刷新浏览器就用。

前端刻意不引入任何框架和打包器，改完刷新浏览器即可，方便在 ARM 板子这类环境上维护。

## 实时推送

`/ws/events` 一条连接推三类事件：

| `type` | 来源 | 用途 |
|---|---|---|
| `log` | `webui/logbuffer.py` 的环形缓冲 | 日志页实时跟随 |
| `chat` | `MessagePipeline.chat_timeline.subscribe` | 对话页新消息即时出现 |
| `status` | 轮询 `RuntimeHost.status()`，有变化才推 | 顶栏渠道徽章、侧栏运行时长 |

`chat_timeline` 的回调可能在任意线程触发，所以集线器一律用 `loop.call_soon_threadsafe`
回到事件循环再广播。

## 设置页是怎么渲染的

桌面端的设置页是 8 个手写 Qt 表单。WebUI 换成声明式：`webui/settings_spec.py` 用纯数据
描述每个字段的路径 / 类型 / 默认值 / 是否热生效，前端 `static/js/pages/settings.js` 据此
自动渲染，后端 `PATCH /api/settings` 用同一张表做读写校验。

新增一个可配置项只需要在 `SETTINGS_GROUPS` 里加一条，不用动前端。
模型（providers / agents.*）不走这张表，它们有专门的 `/api/settings/providers` 系列接口。

## 模型接入

LLM 提供商的写操作在 `webui/api/providers.py`（逻辑在 `webui/provider_admin.py`，纯函数便于单测）：

| 接口 | 说明 |
|---|---|
| `GET /api/providers` | 列表 + 各 Agent 绑定 + 健康状态。**只回 `has_key`，永不回显密钥** |
| `GET /api/providers/presets` | 预设清单（内置 + 数据目录里的用户预设） |
| `POST /api/providers` | 新增（预设或自定义中转站） |
| `PATCH /api/providers/{id}` | 编辑。只改请求体里出现过的字段 |
| `DELETE /api/providers/{id}?drop_key=` | 删除。默认**保留**密钥，被 Agent 引用时返回 409 + `used_by` |
| `POST /api/providers/test`、`/{id}/test` | 测试连接（发一次最小真实请求） |
| `POST /api/providers/fetch-models`、`/{id}/fetch-models` | 拉取可用模型列表 |

三条约束写在代码注释里，改的时候别绕开：

1. **改动先在深拷贝上做，`RootConfig` 校验通过才落回真配置**——否则容易留下"半改"状态，
   比如 Agent 引用了一个已经被删掉的 provider。
2. **`reasoning_style` 决定是否给上游发厂商私有字段**。无 preset 的自定义 provider
   默认取 `none`：早先的实现会落到 `thinking_extra_body`，把
   `{"thinking": {"type": "disabled"}}` 发给不认识它的网关，严格网关直接 400。
3. **provider 实例在 `Runtime.start()` 构造**，所以任何改动都要重启才生效。不做热重建——
   那会打断在途请求。

`/api/settings/providers`（只读）保留为兼容入口，内部复用同一份实现。

## 表情包

`webui/api/emoji.py` + `webui/emoji_store.py`。原 GUI 的面板是在 UI 进程里直接对目录做
`iterdir/copy2/rename/unlink`；WebUI 是 HTTP，文件名来自网络，所以每个操作都要过校验。

```
GET    /api/emoji                 列表（含目录、数量、支持的扩展名）
GET    /api/emoji/names           名称 → 文件名 映射，供聊天页内联渲染
GET    /api/emoji/file/{filename} 出图（带 Cache-Control: no-cache）
POST   /api/emoji/upload          multipart，可带 rename JSON 逐个改名
PATCH  /api/emoji/{name}          改名
DELETE /api/emoji/{name}          删除
```

**安全边界**（改动时请保持）：

- `EMOJI_DIR` **不挂静态目录**。一旦挂载，目录里任何非图片文件都会被原样下载。
  出图只走 `/api/emoji/file/`，那条路径带鉴权且逐项校验。
- 文件名四道防线：只接受纯文件名 → 拒符号链接 → `resolve(strict=True)` 后必须仍在
  EMOJI_DIR 内 → 后缀在白名单且**文件头 magic 与后缀一致**（防止把脚本改名成 `.png`）。
- 单文件 5MB、单请求 50 个、Windows 保留名（`CON`/`NUL`/`COM1`…）一律拒绝。

扩展名以 `tools/message_builder.py` 的 `SUPPORTED_IMAGE_EXTS` 为**唯一真源**——
那里是 AI 实际能发送的集合。两边漂移会出现"上传成功但角色从不使用它"，
`tests/webui_config/test_emoji_store.py` 有一条断言锁死这个恒等关系。

## 对话页的显示项管线

对话页渲染的不是原始记录。`webui/chatlog/display_pipeline.py` 先把记录归一化成
`DisplayItem`：工具调用与它的返回配成一项、发送回执去重、思考单独拆出来、
runtime 事件折叠。前端只按 `kind` 渲染，不猜记录之间的关系。

这条链路是从原桌面端 `ui/dashboard/chats_page.py:1350-1756` **原样搬运**的
（那段代码零 Qt、零 `self` 引用，是纯逻辑）。迁移时一度只搬了后半套
（`display_items.py` 的过滤/排序），导致过滤函数全是死代码、对话页只能平铺文本。
搬运而不是重写是有意的：这些判断规则（回执去重、工具配对、legacy header 拆分）
是长期调出来的，重写必然丢细节。

`GET /api/chats/display` 的参数与过滤开关语义照搬原 GUI：

| 参数 | 对应 UI |
|---|---|
| `show_chat` / `show_system` / `show_tools` / `show_reasoning` | 第一行四个过滤开关 |
| `media_only` | 「只看图片/文件」（表情包标签也算媒体） |
| `q` | 记录正文搜索 |
| `limit` | 取过滤后**最后 N 条**；`0` = 不截断（「显示全部」） |

`limit` 的档位是 300（`DEFAULT_VISIBLE_RECORD_LIMIT`），「加载更早」每次加 300。

## 角色生成

`webui/api/personas.py` + `webui/persona_admin.py`，底层是
`agents/persona_gen_agent.py` 的 `PersonaGenAgent`。

生成器一直是完整的，但迁移到 WebUI 时**整个入口丢了**：没有接口、没有界面，
`PersonaGenAgent` 只被 `agents/__init__.py` 导出过，`render_minimal_persona()`
零调用。原桌面端是由 `ui/dashboard/persona_dialogs.py` 的两个对话框承担的。

| 接口 | 说明 |
|---|---|
| `POST /api/personas/draft` | 按简述生成初版。**只回草稿，不落盘** |
| `POST /api/personas/refine` | 按反馈出修订版。同样不落盘，可反复调用 |
| `POST /api/personas` | 落盘。覆盖已有时**先自动备份**到 `.backups/` |
| `GET /api/personas/{name}/source` | 取出 `PERSONA_PROMPT` 字面量，作为改写底稿 |
| `POST /api/personas/{name}/duplicate` | 复制角色（不带对方的备份） |
| `GET`/`DELETE /api/personas/brief` | 向导暂存的角色描述 |

三条设计约束：

1. **草稿不落盘。** 用户会反复重生成，落盘会产生一堆半成品。采用才写。
2. **覆盖前一定备份。** 改写是 AI 做的，没备份等于把用户的角色交出去了。
   同一秒内连续覆盖会加序号，不会互相盖掉。
3. **用哪个模型由后端决定**：`agents.persona_gen` 优先，缺省回落 `agents.chat`。
   原桌面端是自己持有一份"当前主模型"的临时状态，那会导致"设置页改了模型、
   生成人格却还用旧模型"的错位。

`extract_prompt_source()` 用 `ast` 取 `PERSONA_PROMPT` 常量而不是 `exec`——
人格文件是用户/AI 写的代码，这里只需要那个字符串，没理由把整个模块执行一遍。
解析不出来时退回正则，保证「修改角色」至少能看到原文。

**改写支持片段合并**：模型只回一个顶层标签（如 `<personality>…</personality>`）时，
`PersonaGenAgent.refine` 会把它合并进当前文档，其余字段原样保留。

角色目录在仓库的 `personas/` 下（不是数据目录），`.gitignore` 用 `personas/*`
排除用户自创的角色，只有内置的 `debata` 入 git。

## 首次配置向导

向导是多步的，流转规则由后端决定：`POST /api/setup/step` 调
`webui/wizard/flow.py`（原桌面端原样搬过来的）。前端不判断"下一步去哪"——
两条路径（推荐 7 步 / 自定义 8 步）加 `PERSONA_CREATE` 分支，写两遍必然漂。

| 接口 | 说明 |
|---|---|
| `GET /api/setup/options` | 预设、人格、步骤元数据、默认值 |
| `GET /api/setup/copy` | 中文文案表（`webui/copy.py`、`webui/wizard/copy.py`，与原桌面端逐字一致） |
| `POST /api/setup/step` | 步骤流转 + 进度 |
| `POST /api/setup/apply` | 写盘并启动 Runtime |

`write_initial_config` 有两条容易踩的语义：

- **providers 按 id 合并，不是整段替换。** 用户可能在设置页加过中转站，之后重跑向导
  只是想改主模型；整段替换会把那个 provider 连同引用它的 Agent 一起打回原形。
  要真正重置得显式传 `replace_providers: true`。
- **写盘前跑一次 `RootConfig` 校验。** Agent 引用不存在的 provider 这类错误必须在这里
  拦住，否则会写出一份启动时才炸的配置。

`secrets` 字段用于写天气 / TTS 这类不走 provider 的密钥——向导阶段 Runtime 还没起来，
前端没法调 `/api/settings/secrets`。

向导里填的角色描述同理也调不了生成器（要真发一次模型请求），但**不能就这么丢掉**：
它被写进 `<数据目录>/persona_brief.json`，配置完成后「角色 → 新建角色」会读它预填，
用过即删。

## 黑名单

WebUI 新增功能，与白名单的分工：

- **白名单**决定「谁能用」，是整体开放策略（`open` / `verify` / `whitelist`）
- **黑名单**决定「谁永远不能用」，点名拉黑，不影响其它人

命中黑名单的消息在 `MessagePipeline.enqueue`（`core/pipeline_inbound.py`）里直接返回：
不回复、不入库、不消耗 API 费用。**优先级高于白名单与管理员审核**——即使白名单是
`open`（对所有人开放），黑名单里的对象依然拦得住。

保存后调用 `MessagePipeline.apply_blacklist()` 热更新，**不需要重启 Runtime**（白名单改完
需要重启）。字段定义见 `app_config/schema.py:BlacklistConfig`，行为测试见
`tests/test_blacklist_gate.py`。

### 名单填写格式（分隔符）

**中英文逗号都能用**，任何入口都不必特意换成英文逗号。但各入口的解析是各写各的
（项目里没有统一的归一化函数），对其它分隔符的宽容度并不一致：

| 入口 | 解析代码 | `,` | `，` | 空格 / 换行 | `、` | `;` `；` |
|---|---|:-:|:-:|:-:|:-:|:-:|
| 黑名单页面 | `webui/static/js/pages/blacklist.js` | ✅ | ✅ | ✅ | ✅ | ✅ |
| 设置页白名单 | `webui/api/settings.py` 的 `list_int` | ✅ | ✅ | ❌ | ❌ | ❌ |
| 首次配置向导白名单 | `webui/runtime_host.py` 的 `_int_list` | ✅ | ✅ | ✅ | ✅ | ❌ |
| 通用字符串列表 | `webui/api/settings.py` 的 `list_str` | ✅ | ✅ | 仅换行 | ❌ | ❌ |

两个容易踩的点：

- **设置页白名单最严格。** 粘贴 `111、222` 或 `111 222` 会走到 `int()` 抛错，
  返回 `400 设置值不合法`——是明确报错，不会静默错配成别的号码。
- **配置层不解析字符串。** `WhitelistConfig` / `BlacklistConfig` 的 `qq_ids` /
  `group_ids` 都是 `list[int]`（`app_config/schema.py`），字符串切分全部发生在
  WebUI 上游。所以手写 `config.yaml` 必须写成 YAML 列表：

  ```yaml
  whitelist:
    qq_ids: [111, 222]
  ```

  写成 `qq_ids: "111,222"` 会直接校验失败。

## 二次开发

### 加一个页面

1. `webui/api/` 下加路由模块，在 `webui/api/__init__.py` 的 `ALL_ROUTERS` 注册
2. `webui/static/js/pages/` 下加 `export async function render(container, ctx)`
3. 在 `webui/static/js/app.js` 的 `NAV_ITEMS` 里加一项

`render` 返回 `{ destroy() {} }`，切页时会被调用，用来清定时器和退订事件。
`ctx` 提供 `toast(msg, kind)`、`status`、`navigate(key)`、`onEvent(handler)`。

### 加一个设置项

往 `webui/settings_spec.py` 的 `SETTINGS_GROUPS` 加一条就够，前端会自动渲染、
后端 `PATCH /api/settings` 用同一张表校验。字段类型支持
`bool / int / int_or_none / float / str / enum / secret_ref / list_int / list_str / json`。

**表里没有的路径一律拒绝**（防止前端改到任意配置字段），所以"这个字段改不了"
通常就是忘了往表里加。`tests/webui_config/test_settings_spec.py` 会检查
表与 schema 的一致性——包括枚举候选值是否与 `Literal` 完全一致
（写错的话下拉能选、一保存就 400，历史上真出过）。

`json` 类型用一个文本框整体编辑结构型配置（逐工具预算、规则表等）——
声明式表单展开这些会淹没页面。

### 调样式

色值统一来自 `webui/theme.py`（与原桌面端同一套），CSS 变量定义在
`webui/static/app.css` 顶部。新增颜色前先查那里，避免色值漂移。

`tests/webui_config/test_style_contract.py` 锁住几条硬约束：色板里的每个值都要
出现在 CSS 里、字号阶梯与 `FontSize` 一致、按钮四态存在、导航不用 emoji 图标。
断意刻意保持少量——断言太多会变成脆弱的实现测试。

## 与桌面端的取舍

已移除：无边框圆角窗口、系统托盘、Qt 原生文件选择器、拖拽导入模型文件夹。

替代方案：托盘常驻 → 浏览器标签页 + 令牌保持登录；拖拽导入 → 「模型管理」页上传 zip；
`QFileDialog` → `<input type="file">` + 拖放（**浏览器拖文件夹只能拿到空 File**，
所以不做目录递归，遇到会明确提示）。

会话记录合并、显示项归一化、文字渲染、模型安装指引、向导流转等纯逻辑**原样搬迁**
（`webui/chatlog/`、`webui/model_guide.py`、`webui/wizard/`、`webui/copy.py`），
所以两端看到的内容是一致的。

**有意不复刻的**：

- **原始 HTML 渲染**。原 `chats_page.py` 用 `QTextBrowser` 渲染 HTML 模板，WebUI 换成
  结构化 JSON + DOM 渲染（`textContent`，绝不 `innerHTML`）——既避免 XSS，也避免
  把 Qt 的表格布局带进浏览器。
- **ASR 设置区**。`app_config/schema.py` 写明那些字段"仅为兼容旧配置保留，
  当前统一使用 NapCat 内置 fetch_ptt_text"。把废弃配置重新暴露到 UI 是负资产。
