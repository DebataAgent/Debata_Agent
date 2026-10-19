# 图文教程

从零开始，把 Debata 跑起来。遇到问题先看文末的 [常见问题](#常见问题)。

---

## 1.安装 NapCat

Debata 通过 [NapCat](https://napneko.github.io/guide/start-install) 接入 QQ。先到 NapCat 官网下载安装，按教程跑起来。

NapCat 启动后，记下它的 WebSocket 地址和 token（如果设了的话）——下一步要用。

---

## 2.安装 Debata

```bash
git clone https://github.com/binglang001/Debata_Agent.git
cd Debata_Agent

python -m pip install uv      # 没装 uv 的话先装
uv sync
uv run main.py
```

启动后终端会打印访问地址和令牌，浏览器打开即可。首次启动检测不到配置会自动进入配置向导，
以后启动直接进仪表盘。纯命令行环境跑 `uv run main.py --no-gui`，不启动 WebUI。
后台长期运行见 [后台运行指南](background_running.md)。

环境要求：Python 3.11+，一个 LLM API 密钥（推荐 [DeepSeek](https://platform.deepseek.com)）。

Linux 服务器或 SSH 环境可以直接跑纯 CLI 向导：

```bash
uv run main.py --no-gui --setup
```

CLI 向导覆盖主模型、子 Agent、图片理解、天气、TTS、RAG、NapCat 和人格等主要配置。密钥优先保存到系统 keyring；如果 Linux 没有可用 Secret Service，会自动退回数据目录下的 `rsa_private.pem` 本地私钥文件（默认 `~/.local/share/Debata_Agent/instances/default/rsa_private.pem`），并使用 0600 权限保护。

---

## 3.走完向导

推荐路径 7 步，约 5 分钟。每一步都可以"上一步"回去改。

### 第一步：选路径

![欢迎页](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_welcome.png)

推荐路径用 DeepSeek 主模型快速上手。想逐个调校每个参数选自定义。

### 第二步：主模型

![主模型](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_model_quick.png)

填入 [DeepSeek API 密钥](https://platform.deepseek.com/api_keys)，点「测试连接」验证。推荐先充 5 块钱，够测很久。

自定义路径可以选其他 provider，调 temperature、top_p 等参数。

![自定义模型](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_model_custom.png)

### 第三步：可选功能

![功能开关](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_features.png)

4 个可选功能，按需打开。以后在设置页随时能改：

- **看懂图片**（[Vision](feature_guides/vision.md)）：需要多模态模型，比如 GLM-4V 或 Qwen-VL
- **查天气**（[和风天气](feature_guides/weather.md)）：需要去和风控制台申请 API Key
- **用声音说话**（[TTS](feature_guides/tts_voxcpm.md)）：本地 VoxCPM2 或云端 TTS
- **联网搜索**（[DuckDuckGo](feature_guides/web_search.md)）：免费，不需要密钥，建议开启

### 第四步：记忆方式

![记忆方式](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_embedding.png)

选 [长期记忆](feature_guides/embedding_rag.md) 的工作方式：

- **文件模式**：零配置，直接写 JSON 文件，AI 调工具保存。单人聊天够用。
- **向量模式（RAG）**：配一个 embedding 服务，后台自动提取记忆做语义检索。多群多用户长期运行更靠谱。

### 第五步：接 NapCat

![NapCat](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_adapter.png)

在NapCat创建Server后（注意：NapCat填Server意味着程序要选择Client模式），填入 NapCat 的 WebSocket 地址和 token。白名单建议选「管理员审核」——陌生人加好友时你手动确认，避免意外费用。

### 第六步：选角色

![人格选择](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_persona.png)

三种方式：

- **仓库自带**：直接用 Debata，开箱即用
- **AI 辅助创建**：描述你想要的角色，配置完成后到「角色 → 新建角色」，AI 帮你写完整的 [人格档案](persona_writing_guide.md)
- **导入**：你已经写好了 `persona_prompt.py`，从磁盘导入

> 向导里选「生成一个新人格」时填的那段描述**不会丢**——它先存到数据目录，
> 配置完成后打开「角色 → 新建角色」会替你填好，直接点「生成」就行。
> 生成这一步要在配置完成后做，因为生成人格需要真的调用一次模型。

### 确认 & 启动

![确认](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/wizard_summary.png)

检查配置没问题，点「启动」。以后在设置页随时能改。

---

## 4.仪表盘

![总览](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/dashboard_overview.png)

左边 7 个导航：

| 页面   | 做什么                                      |
|------|------------------------------------------|
| 总览   | 渠道状态、模型健康、用量、KV 缓存命中率                    |
| 对话   | 看历史对话，展开 AI 的思考过程                        |
| 记忆   | 管理 Debata 记住的事（scope / pinned / 编辑 / 删除） |
| 日志   | 实时日志流，按等级和模块过滤                           |
| 角色   | 切换、新建、修改、导入导出人格                          |
| 模型管理 | 本地模型安装状态和指引                              |
| 设置   | 所有配置实时保存                                 |

---

## 5.常见操作

### 切换人格

角色页 → 选中目标 → 点「切换为当前」→ 重启 Debata。

### 新建一个角色（AI 生成）

角色页 → 「新建角色」：

1. 填**目录名**（字母开头，它会成为 `personas/<名字>/`）和**显示名**（可以中文）
2. 下面几栏是给 AI 的提示——性格、过往、说话的样子、不会做的事、绝不会说的话、
   对不同人的差别、敏感话题。想到什么写什么，空着也能生成
3. 点「生成」，十几秒后出草稿。预览按 `identity / past / personality / voice /
   boundaries / relation_with_user / consistency_anchors` 分节显示
4. 不满意就改改描述再点「再生成一次」——**草稿只在浏览器里，不写盘**
5. 满意后点「采用并保存」

### 修改一个角色（AI 改写）

角色页 → 目标行点「修改」：

1. 弹出当前人设（作为改写底稿）
2. 写一句想改的地方，比如「再冷淡一点」「少说反问句」「她的猫呢，提一句」
3. 点「生成修改版」，可以**反复生成第 N 版**，每版都能预览
4. 点「采用并覆盖」才写盘。**覆盖前自动备份**到
   `personas/<名字>/.backups/persona_prompt_<时间戳>.py`

角色名相同会拒绝覆盖，除非你确认；「复制」则以现有角色为模板建一个新的。

### 加表情包

侧栏「表情包」页 → 「添加表情包」：拖图片进去，或点开选择文件。可以在上传前逐个改名——
**名字就是 AI 引用时用的名字**，建议起容易懂的（如「笑哭」「困」而不是 `emoji_0142`）。

也可以直接把图片丢进数据目录的 `emoji/`（默认 `~/.local/share/Debata_Agent/instances/default/emoji/`），效果一样。支持 png / jpg / jpeg / gif，单个不超过 5MB。
重名会自动加 `_1` 后缀。

### 改模型 / 加中转站

设置 →「模型」：

- **添加提供商**：填名称，协议选「OpenAI 兼容」，Base URL 填中转站地址，粘 API Key。
  点「获取模型列表」能拉到就直接选，拉不到就手填模型 ID。
  点「测试连接」会真发一次最小请求，通过再保存。
- **换模型**：在下面的 Agent 卡片里选提供商、填模型 ID。
- **思考参数风格**：中转站拿不准就选 `none`，这样不会把厂商私有字段发给不认识它的网关。

改动需要重启 Runtime 才生效，页面会给出「立即重启」提示。

![设置模型](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/dashboard_settings_model.png)

设置页 → 模型 → 改 Provider 密钥或 Agent 模型。点「测试连接」确认可用。

### 调上下文预算

设置页 → Token预算 → 滚动摘要压缩，可以调触发滚动摘要的上下文占比、压缩后的活跃窗口目标，以及第一次压缩仍超预算时的重试压缩目标。当前默认是 50/30/20，并启用模型视图（只裁剪发给模型的旧运行时快照，不删除落盘历史）。正常使用建议保留默认值；这里不再提供“保留最近 N 条工作历史”一类旧裁剪开关。

### 调主题

![外观](https://raw.githubusercontent.com/binglang001/Debata_Agent/main/docs/images/dashboard_appearance.png)

设置页 → 软件行为 → 外观 → 深浅主题即时切换。

---

## 6.一份默认人格的快速演示

`personas/debata/`, 一个完整人格档案: 17岁女孩、慢热又毒舌、对长辈尊敬、对死党随便、对陌生人冷淡.这是项目最初的样品, 也是「关系矩阵 + 真人聊天」方法论的验证.你可以:

- **直接用 debata**: 首次配置时选默认人格
- **AI 现场捏一个**: 向导里描述「我想要一个 25 岁的程序员、嘴硬心软、爱猫」, 等几秒看 AI 写出来
- **手写**: 照 `personas/debata/persona_prompt.py` 葫芦画瓢, 放到 `personas/{你的名字}/`

#### 每个 persona 都有自己独立的 `memory/{name}/` 目录存历史与重要记忆（默认数据目录 `~/.local/share/Debata_Agent/instances/default/`），互不干扰.

---

## 7.后台运行

调试完成后，不要让 Debata 依赖 SSH 窗口。一次性安装后，用短命令管理：

```bash
cd ~/Debata_Agent
bash scripts/install_systemd_user.sh   # 一次性安装并启动

debata start       # 启动
debata stop        # 停止
debata log         # 查看日志
debata enable      # 开机自启
debata disable     # 取消开机自启
debata status      # 状态
```

`debata enable` 会尝试开启 linger；如果提示失败，执行
`sudo loginctl enable-linger "$USER"`。只跑 headless 用
`bash scripts/install_systemd_user.sh --headless`。
临时挂机可以用 `tmux new -s debata` 或 `screen -S debata`。
NapCat 需要单独保持运行。

完整说明（系统级服务、nohup、升级、排错、安全建议）见
[后台运行指南](background_running.md)。

---

## 常见问题

**NapCat 连不上？**
确认 NapCat 是否在跑，WS 地址和端口是否正确。设置页的「测试连接」可以诊断。

**API 密钥无效？**
确认密钥复制完整（sk- 开头），账户余额是否充足。

**没收到消息？**
检查白名单模式——如果是「白名单」模式且你的 QQ 不在名单里，Debata 不会回复。切到「管理员审核」试试。

**日志刷屏？**
设置页 → 日志与诊断 → 日志级别切到 INFO 或 WARNING。排查问题时再开 DEBUG。

**改配置不生效？**
部分配置（模型、Adapter、功能开关等）改完需要重启 Debata 服务。设置页底部有重启按钮。

---

## 更多

- [架构总览](architecture.md)
- [角色编写指南](persona_writing_guide.md)
- [KV 缓存实测报告](kv_cache_benchmark.md)
- [提供商开发指南](provider_development.md)
- [适配器开发指南](adapter_development.md)
