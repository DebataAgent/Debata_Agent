// 首次配置向导的各步骤视图。
//
// 对应原桌面端的 ui/wizard/step_views/*.py。两步之间的流转**不在这里判断**——
// 那由后端 /api/setup/step 调 webui/wizard/flow.py 决定（推荐 7 步 / 自定义 8 步，
// 外加 PERSONA_CREATE 分支）。前端只负责渲染当前这一步并做基础校验。
//
// 每个 renderStep 返回 { validate(): string|null }：
//   返回字符串 = 有错，向导停在原地并把这句话显示出来；
//   返回 null   = 可以继续。

import { h, fill, field, empty } from '../dom.js';
import { icon } from '../icons.js';
import { endpoints } from '../api.js';
import { providerFields, readProviderFields } from '../providers.js';

const PERSONA_SOURCE = [
  ['builtin', '用内置人格', '先用自带的 debata 试跑，之后随时能换'],
  ['create', '生成一个新人格', '填几个关键词，让模型帮你写人设'],
];

// ============================================================
// 1. 欢迎 + 选路径
// ============================================================

export function renderWelcome(host, ctx, state) {
  const cards = [
    {
      id: 'recommended',
      title: '推荐',
      desc: '只要一个主模型就能跑起来，大约 3 分钟。',
      detail: '主模型 → 功能 → 记忆 → 渠道 → 角色 → 完成',
    },
    {
      id: 'custom',
      title: '自定义',
      desc: '每个 Agent 用哪个模型、参数怎么调，都由你决定。',
      detail: '主模型 → 子 Agent → 功能 → 记忆 → 渠道 → 角色 → 完成',
    },
  ];

  const nodes = cards.map((item) => {
    const el = h('button', {
      class: `choice ${state.path === item.id ? 'active' : ''}`,
      onclick: () => {
        state.path = item.id;
        render(node);
      },
    },
      h('div', { class: 'choice-title' }, item.title),
      h('div', { class: 'choice-desc' }, item.desc),
      h('div', { class: 'field-help' }, item.detail),
    );
    return el;
  });

  const node = h('div', { class: 'choice-grid' }, ...nodes);
  fill(host, h('p', { class: 'card-sub' }, '选一条路。两条路配出来的东西是一样的，只是问的问题多少不同。'), node);
  return { validate: () => null };
}

// ============================================================
// 2a. 主模型（推荐路径）
// ============================================================

export function renderMainModelQuick(host, ctx, state) {
  const quick = state.presets.filter((p) => p.id === 'deepseek')[0] || state.presets[0];
  if (!quick) {
    fill(host, empty('没有可用的提供商预设', {
      subtitle: '预设目录是空的，检查一下安装是否完整',
      iconName: 'models',
    }));
    return { validate: () => '没有可用的提供商预设' };
  }

  // 换预设时把 id/地址/模型一起带过去，用户只需要填密钥
  function usePreset(preset) {
    state.provider.id = preset.id === 'deepseek' ? 'deepseek_main' : `${preset.id}_main`;
    state.provider.preset = preset.id;
    state.provider.display_name = preset.display_name;
    state.provider.protocol = preset.protocol;
    state.provider.base_url = preset.base_url;
    state.provider.reasoning_style = preset.reasoning_style || '';
    state.provider.model = (preset.models[0] || {}).id || '';
  }
  if (!state.provider.preset) usePreset(quick);

  const presetSelect = h('select', {
    onchange: (event) => {
      const preset = state.presets.find((p) => p.id === event.target.value);
      if (preset) {
        usePreset(preset);
        render(host, ctx, state);
      }
    },
  });
  for (const preset of state.presets) {
    presetSelect.append(h('option', {
      value: preset.id,
      selected: preset.id === state.provider.preset,
    }, preset.display_name));
  }

  const keyInput = h('input', {
    type: 'password',
    autocomplete: 'new-password',
    placeholder: 'sk-…',
    value: state.provider.api_key || '',
    oninput: (event) => { state.provider.api_key = event.target.value.trim(); },
  });

  const current = state.presets.find((p) => p.id === state.provider.preset) || quick;
  const link = current.registration_url
    ? h('a', {
        href: current.registration_url, target: '_blank', rel: 'noreferrer',
        class: 'btn-text',
      }, '去拿一个密钥 →')
    : null;

  fill(host,
    h('p', { class: 'card-sub' },
      '选一个平台，把 API Key 粘进来。不确定选哪个就用 DeepSeek——中文便宜、缓存命中率高。'),
    field('平台', presetSelect),
    field('API Key', keyInput, '只写进加密的密钥库，不会出现在 config.yaml'),
    link,
  );

  return {
    validate() {
      if (!state.provider.api_key) return '还没填 API Key';
      return null;
    },
  };
}

// ============================================================
// 2b. 主模型（自定义路径）
// ============================================================

export function renderMainModelCustom(host, ctx, state) {
  const fields = providerFields(state, {
    includeId: true,
    idLocked: !!state.editingExisting,
  });

  const temp = h('input', {
    type: 'number', step: '0.1', min: '0', max: '2',
    value: state.chat.temperature,
    oninput: (e) => { state.chat.temperature = Number(e.target.value); },
  });
  const topP = h('input', {
    type: 'number', step: '0.05', min: '0', max: '1',
    value: state.chat.top_p,
    oninput: (e) => { state.chat.top_p = Number(e.target.value); },
  });
  const maxTokens = h('input', {
    type: 'number', min: '1',
    value: state.chat.max_tokens,
    oninput: (e) => { state.chat.max_tokens = Number(e.target.value); },
  });
  const reasoning = h('input', {
    type: 'checkbox', checked: !!state.chat.reasoning,
    onchange: (e) => {
      state.chat.reasoning = e.target.checked;
      render(host, ctx, state);
    },
  });

  const nodes = [
    h('p', { class: 'card-sub' },
      '中转站也走这里：预设选「自定义」，把 Base URL 换成中转地址即可。'),
    ...fields.nodes,
  ];

  if (state.chat.reasoning) {
    const budget = h('select', {
      onchange: (e) => { state.chat.reasoning_budget = e.target.value; },
    });
    for (const [value, label] of [['low', '低'], ['medium', '中'], ['high', '高']]) {
      budget.append(h('option', {
        value, selected: state.chat.reasoning_budget === value,
      }, label));
    }
    nodes.push(field('思考预算', budget, '越高越慢越贵，复杂任务再调高'));
  }

  nodes.push(
    h('div', { class: 'grid grid-3' },
      field('温度', temp),
      field('top_p', topP),
      field('最大输出 token', maxTokens),
    ),
    h('label', { class: 'switch' }, reasoning, '启用思考模式'),
  );

  fill(host, ...nodes);

  return {
    validate() {
      const error = readProviderFields(state, fields);
      if (error) return error;
      if (!state.provider.model) return '还没填模型 ID';
      return null;
    },
  };
}

// ============================================================
// 3. 子 Agent（自定义路径）
// ============================================================

const SUB_AGENTS = [
  ['proactive', '主动思考', '隔一段时间自己想想有没有话要说。想让它更主动就开着。', 0.3, 64],
  ['summary', '滚动摘要', '上下文快满时把旧对话压成摘要。长对话必备。', 0.1, 8192],
  ['persona_gen', '人格生成', '后台按时间慢慢丰满角色设定。', 0.7, 8192],
];

export function renderOtherAgents(host, ctx, state) {
  const nodes = [h('p', { class: 'card-sub' },
    '这三个可以各自用不同的模型。不折腾的话让它们跟随主模型就行。')];

  for (const [key, label, desc, defaultTemp, defaultTokens] of SUB_AGENTS) {
    const cfg = state.agents[key] || (state.agents[key] = {
      enabled: true, temperature: defaultTemp, max_tokens: defaultTokens,
    });
    const toggle = h('input', {
      type: 'checkbox', checked: cfg.enabled,
      onchange: (e) => { cfg.enabled = e.target.checked; render(host, ctx, state); },
    });
    const block = h('div', { class: 'card' },
      h('label', { class: 'switch' }, toggle, h('strong', {}, label)),
      h('div', { class: 'field-help', style: { margin: '4px 0 12px' } }, desc),
    );
    if (cfg.enabled) {
      const providerSelect = h('select', {
        onchange: (e) => { cfg.provider = e.target.value; },
      });
      providerSelect.append(h('option', { value: '' }, `跟随主模型（${state.provider.id}）`));
      for (const preset of state.presets) {
        providerSelect.append(h('option', {
          value: preset.id, selected: cfg.provider === preset.id,
        }, preset.display_name));
      }
      const modelInput = h('input', {
        value: cfg.model || '', placeholder: '留空则跟随主模型',
        oninput: (e) => { cfg.model = e.target.value.trim(); },
      });
      const tempInput = h('input', {
        type: 'number', step: '0.1', min: '0', max: '2', value: cfg.temperature,
        oninput: (e) => { cfg.temperature = Number(e.target.value); },
      });
      const tokenInput = h('input', {
        type: 'number', min: '1', value: cfg.max_tokens,
        oninput: (e) => { cfg.max_tokens = Number(e.target.value); },
      });
      block.append(
        h('div', { class: 'grid grid-2' },
          field('提供商', providerSelect),
          field('模型 ID', modelInput),
        ),
        h('div', { class: 'grid grid-2' },
          field('温度', tempInput),
          field('最大输出 token', tokenInput),
        ),
      );
    }
    nodes.push(block);
  }

  fill(host, ...nodes);
  return { validate: () => null };
}

// ============================================================
// 4. 功能
// ============================================================

export function renderFeatures(host, ctx, state) {
  const f = state.features;

  function toggleRow(key, label, desc) {
    const section = f[key] || (f[key] = {});
    const input = h('input', {
      type: 'checkbox', checked: !!section.enabled,
      onchange: (e) => { section.enabled = e.target.checked; render(host, ctx, state); },
    });
    return h('div', { style: { marginBottom: '12px' } },
      h('label', { class: 'switch' }, input, h('strong', {}, label)),
      h('div', { class: 'field-help' }, desc),
    );
  }

  const nodes = [h('p', { class: 'card-sub' },
    '都可以之后再开，这里不选也不影响启动。')];

  // 视觉
  nodes.push(toggleRow('vision', '看懂图片', '需要选一个支持图像输入的模型。'));
  if (f.vision && f.vision.enabled) {
    const provider = h('input', {
      value: f.vision.provider || state.provider.id,
      oninput: (e) => { f.vision.provider = e.target.value.trim(); },
    });
    const model = h('input', {
      value: f.vision.model || '', placeholder: '如 mimo-v2.5 / gpt-5.4',
      oninput: (e) => { f.vision.model = e.target.value.trim(); },
    });
    nodes.push(h('div', { class: 'grid grid-2' },
      field('视觉 provider', provider, '引用上面配好的 provider ID'),
      field('视觉模型', model),
    ));
  }

  // 联网搜索
  nodes.push(toggleRow('web_search', '联网搜索', '让角色能查网上的信息（DuckDuckGo，免费）。'));

  // 天气
  nodes.push(toggleRow('weather', '天气查询', '需要和风天气的 Key，免费额度够个人用。'));
  if (f.weather && f.weather.enabled) {
    const hostInput = h('input', {
      value: f.weather.host || 'devapi.qweather.com',
      oninput: (e) => { f.weather.host = e.target.value.trim(); },
    });
    const key = h('input', {
      type: 'password', value: f.weather.api_key || '',
      oninput: (e) => { f.weather.api_key = e.target.value.trim(); },
    });
    nodes.push(h('div', { class: 'grid grid-2' },
      field('API Host', hostInput), field('和风天气 Key', key),
    ));
  }

  // 语音
  nodes.push(toggleRow('tts', '语音合成', '让角色能发语音。edge-tts 免费且不用密钥。'));
  if (f.tts && f.tts.enabled) {
    const type = h('select', {
      onchange: (e) => { f.tts.type = e.target.value; render(host, ctx, state); },
    });
    for (const [value, label] of [['api', '云端 API'], ['local', '本地 VoxCPM2']]) {
      type.append(h('option', { value, selected: (f.tts.type || 'api') === value }, label));
    }
    nodes.push(field('类型', type));
    if ((f.tts.type || 'api') === 'api') {
      const provider = h('select', {
        onchange: (e) => { f.tts.provider = e.target.value; },
      });
      for (const [value, label] of [['edge', 'edge-tts（免费，无需密钥）'],
                                    ['xfyun', '讯飞（需 API Key）']]) {
        provider.append(h('option', {
          value, selected: (f.tts.provider || 'edge') === value,
        }, label));
      }
      nodes.push(field('云端厂商', provider));
    } else {
      nodes.push(h('div', { class: 'field-help' },
        '本地模型首次使用时会提示下载，也可以在「模型管理」里提前装好。'));
    }
  }

  fill(host, ...nodes);
  return { validate: () => null };
}

// ============================================================
// 5. 记忆 / Embedding
// ============================================================

export function renderEmbedding(host, ctx, state) {
  const mem = state.features.long_term_memory || (state.features.long_term_memory = {});
  const emb = state.features.embedding || (state.features.embedding = {});

  const mode = h('select', {
    onchange: (e) => {
      mem.mode = e.target.value;
      render(host, ctx, state);
    },
  });
  for (const [value, label, desc] of [
    ['file', '重要记忆文件', '角色自己判断什么值得记，写进文件。够用且省钱。'],
    ['rag', '向量检索', '把对话存进向量库按相似度检索。对话多时更聪明，要多配一个 embedding。'],
  ]) {
    mode.append(h('option', { value, selected: (mem.mode || 'file') === value }, label));
  }

  const nodes = [
    h('p', { class: 'card-sub' }, '决定角色怎么"记住"以前的事。'),
    field('长期记忆方式', mode),
  ];

  if ((mem.mode || 'file') === 'rag') {
    emb.enabled = true;
    const type = h('select', {
      onchange: (e) => { emb.type = e.target.value; render(host, ctx, state); },
    });
    for (const [value, label] of [['api', '云端 API'], ['local', '本地模型']]) {
      type.append(h('option', { value, selected: (emb.type || 'api') === value }, label));
    }
    nodes.push(field('Embedding 类型', type));

    if ((emb.type || 'api') === 'api') {
      const provider = h('input', {
        value: emb.provider || '', placeholder: '引用一个支持 embeddings 的 provider ID',
        oninput: (e) => { emb.provider = e.target.value.trim(); },
      });
      const model = h('input', {
        value: emb.api_model || '', placeholder: '如 text-embedding-3-small',
        oninput: (e) => { emb.api_model = e.target.value.trim(); },
      });
      nodes.push(h('div', { class: 'grid grid-2' },
        field('provider', provider), field('模型', model),
      ));
    } else {
      const quality = h('select', {
        onchange: (e) => { emb.local_quality = e.target.value; },
      });
      for (const [value, label] of [
        ['performance', 'performance — all-MiniLM-L6（约 23MB，更快）'],
        ['quality', 'quality — bge-large-zh（约 400MB，中文更好）'],
      ]) {
        quality.append(h('option', {
          value, selected: (emb.local_quality || 'performance') === value,
        }, label));
      }
      nodes.push(field('本地档位', quality));
      nodes.push(h('div', { class: 'field-help' },
        '本地模型首次使用时会自动下载，也可以在「模型管理」里提前装好。'));
    }
  } else {
    emb.enabled = false;
  }

  fill(host, ...nodes);

  return {
    validate() {
      if ((mem.mode || 'file') === 'rag' && (emb.type || 'api') === 'api'
          && !emb.provider) {
        return '选了向量检索，就得填一个 embedding provider';
      }
      return null;
    },
  };
}

// ============================================================
// 6. 渠道（NapCat）
// ============================================================

export function renderAdapter(host, ctx, state) {
  const a = state.adapter;

  const mode = h('select', {
    onchange: (e) => { a.mode = e.target.value; },
  });
  for (const [value, label] of [
    ['client', 'client（Debata 连 NapCat 的正向 WS）'],
    ['server', 'server（Debata 监听，等 NapCat 反向连入）'],
  ]) {
    mode.append(h('option', { value, selected: a.mode === value }, label));
  }
  const hostInput = h('input', {
    value: a.host, oninput: (e) => { a.host = e.target.value.trim(); },
  });
  const port = h('input', {
    type: 'number', min: '1', max: '65535', value: a.port,
    oninput: (e) => { a.port = Number(e.target.value); },
  });
  const path = h('input', {
    value: a.path, oninput: (e) => { a.path = e.target.value.trim(); },
  });
  const token = h('input', {
    type: 'password', value: a.access_token || '',
    oninput: (e) => { a.access_token = e.target.value.trim(); },
  });
  const manage = h('input', {
    type: 'checkbox', checked: !!a.manage_process,
    onchange: (e) => { a.manage_process = e.target.checked; render(host, ctx, state); },
  });

  const wlMode = h('select', {
    onchange: (e) => { a.whitelist.mode = e.target.value; render(host, ctx, state); },
  });
  for (const [value, label] of [
    ['open', 'open — 对所有人开放（注意 API 费用）'],
    ['verify', 'verify — 陌生人走管理员审核'],
    ['whitelist', 'whitelist — 只有名单里的人能用'],
  ]) {
    wlMode.append(h('option', {
      value, selected: a.whitelist.mode === value,
    }, label));
  }

  const nodes = [
    h('p', { class: 'card-sub' },
      '让 Debata 通过 NapCat 收发 QQ 消息。还没装 NapCat 也可以先跳过，之后在设置里配。'),
    field('连接模式', mode),
    h('div', { class: 'grid grid-3' },
      field('地址', hostInput), field('端口', port), field('路径', path),
    ),
    field('Access Token', token, 'NapCat 里配置的那个，留空表示没设'),
    h('label', { class: 'switch' }, manage, '由 Debata 拉起 NapCat 进程'),
  ];

  if (a.manage_process) {
    const processPath = h('input', {
      value: a.process_path || '', placeholder: 'NapCat 启动脚本的绝对路径',
      oninput: (e) => { a.process_path = e.target.value.trim(); },
    });
    const autoRestart = h('input', {
      type: 'checkbox', checked: a.auto_restart !== false,
      onchange: (e) => { a.auto_restart = e.target.checked; },
    });
    nodes.push(
      field('NapCat 启动路径', processPath),
      h('label', { class: 'switch' }, autoRestart, '断线自动重连'),
    );
  }

  nodes.push(field('入站策略', wlMode));

  if (a.whitelist.mode === 'whitelist') {
    const qq = h('textarea', {
      rows: 2, placeholder: '一行一个，或用逗号/空格/顿号分隔',
      value: (a.whitelist.qq_ids || []).join('\n'),
      oninput: (e) => { a.whitelist.qq_ids = e.target.value; },
    });
    const groups = h('textarea', {
      rows: 2, placeholder: '一行一个，或用逗号/空格/顿号分隔',
      value: (a.whitelist.group_ids || []).join('\n'),
      oninput: (e) => { a.whitelist.group_ids = e.target.value; },
    });
    nodes.push(
      h('div', { class: 'grid grid-2' },
        field('白名单 QQ 号', qq), field('白名单群号', groups),
      ),
      h('div', { class: 'field-help' },
        '别忘了填——名单为空等于谁都进不来。'),
    );
  }

  fill(host, ...nodes);
  return { validate: () => null };
}

// ============================================================
// 7. 角色
// ============================================================

export function renderPersona(host, ctx, state) {
  const nodes = [h('p', { class: 'card-sub' }, '先挑一个角色。之后可以在「角色」页随时新建或换。')];

  const select = h('select', {
    onchange: (e) => { state.persona = e.target.value; },
  });
  if (!state.personas.length) {
    select.append(h('option', { value: 'debata' }, 'debata（内置）'));
  }
  for (const persona of state.personas) {
    select.append(h('option', {
      value: persona.id, selected: state.persona === persona.id,
    }, persona.name));
  }
  nodes.push(field('用哪个角色', select));

  const choices = h('div', { class: 'choice-grid' },
    ...PERSONA_SOURCE.map(([value, title, desc]) => {
      const el = h('button', {
        class: `choice ${state.persona_source === value ? 'active' : ''}`,
        onclick: () => {
          state.persona_source = value;
          render(host, ctx, state);
        },
      },
        h('div', { class: 'choice-title' }, title),
        h('div', { class: 'choice-desc' }, desc),
      );
      return el;
    }),
  );
  nodes.push(h('div', { class: 'stat-label', style: { marginTop: '8px' } }, '或者'), choices);

  if (state.persona_source === 'create') {
    const desc = h('textarea', {
      rows: 3,
      placeholder: '比如：一个住在旧书店二楼的猫娘，说话慢，喜欢在句尾加"喵"',
      value: state.persona_brief || '',
      oninput: (e) => { state.persona_brief = e.target.value; },
    });
    nodes.push(field('想要什么样的角色', desc,
      '先写下来。配置完成后到「角色 → 新建角色」，这段描述会替你填好，'
      + '再让 AI 生成完整人设——那里能预览、能反复重写。'));
  }

  fill(host, ...nodes);
  return { validate: () => null };
}

// ============================================================
// 8. 汇总
// ============================================================

export function renderSummary(host, ctx, state) {
  const rows = [];
  const add = (label, value) => rows.push(
    h('tr', {}, h('td', { class: 'stat-label' }, label), h('td', {}, value)),
  );

  add('路径', state.path === 'custom' ? '自定义' : '推荐');
  add('主模型', `${state.provider.preset || '自定义'} · ${state.provider.model || '（未填）'}`);
  if (state.provider.base_url) add('Base URL', h('span', { class: 'mono' }, state.provider.base_url));
  add('API Key', state.provider.api_key ? '已填写' : '未填（启动后调用会失败）');

  for (const [key, label] of [['proactive', '主动思考'], ['summary', '滚动摘要'],
                              ['persona_gen', '人格生成']]) {
    const cfg = state.agents[key];
    add(label, cfg && cfg.enabled !== false
      ? `启用${cfg.model ? ` · ${cfg.model}` : ' · 跟随主模型'}`
      : '关闭');
  }

  const enabled = ['vision', 'web_search', 'weather', 'tts']
    .filter((k) => (state.features[k] || {}).enabled);
  add('功能', enabled.length ? enabled.join('、') : '都关着');
  add('长期记忆', (state.features.long_term_memory || {}).mode === 'rag'
    ? '向量检索' : '重要记忆文件');
  add('渠道', `${state.adapter.mode}://${state.adapter.host}:${state.adapter.port}`);
  add('入站策略', state.adapter.whitelist.mode);
  add('角色', state.persona);

  const list = h('table', { class: 'table' }, h('tbody', {}, ...rows));

  fill(host,
    h('p', { class: 'card-sub' }, '确认一下。点「完成配置」就会写盘并启动 Runtime。'),
    list,
  );

  return {
    validate() {
      if (!state.provider.api_key) {
        return '还没有填 API Key —— 没有密钥角色无法说话';
      }
      return null;
    },
  };
}

// ============================================================
// 9. 人格生成（PERSONA_CREATE 子流程）
// ============================================================

export function renderPersonaCreate(host, ctx, state) {
  const brief = h('textarea', {
    rows: 4,
    placeholder: '比如：一个住在旧书店二楼的猫娘，说话慢，喜欢在句尾加"喵"',
    value: state.persona_brief || '',
    oninput: (e) => { state.persona_brief = e.target.value; },
  });

  fill(host,
    h('p', { class: 'card-sub' },
      '写几句话描述你想要的角色。这一步先存下来——配置完成后到「角色 → 新建角色」，'
      + '这段描述会替你填好，再让 AI 生成完整人设，那里能预览、能改、能反复重写。'),
    field('角色描述', brief),
    h('div', { class: 'field-help' },
      '现在跳过也没关系，之后随时能在「角色」页新建。'),
  );

  return { validate: () => null };
}

// ============================================================
// 注册表
// ============================================================

export const STEP_VIEWS = {
  welcome: renderWelcome,
  main_model_quick: renderMainModelQuick,
  main_model_custom: renderMainModelCustom,
  other_agents: renderOtherAgents,
  features: renderFeatures,
  embedding: renderEmbedding,
  adapter: renderAdapter,
  persona: renderPersona,
  persona_create: renderPersonaCreate,
  summary: renderSummary,
};

// 供 setup.js 构造 payload
export function buildPayload(state) {
  const provider = {
    id: state.provider.id || 'main_provider',
    preset: state.provider.preset || null,
    display_name: state.provider.display_name || state.provider.id,
    protocol: state.provider.protocol || null,
    base_url: state.provider.base_url || null,
    api_key: state.provider.api_key || '',
    reasoning_style: state.provider.reasoning_style || null,
  };

  const agents = {};
  for (const [key, cfg] of Object.entries(state.agents)) {
    agents[key] = cfg && cfg.enabled !== false
      ? {
          enabled: true,
          provider: cfg.provider || state.provider.id,
          model: cfg.model || state.provider.model,
          temperature: cfg.temperature,
          max_tokens: cfg.max_tokens,
        }
      : { enabled: false };
  }

  const features = {};
  for (const [key, section] of Object.entries(state.features)) {
    if (!section) continue;
    const copy = { ...section };
    // 天气 / TTS 的密钥走各自独立的 secret id，不进 provider
    if (key === 'weather' && copy.api_key) copy.api_key_id = 'weather_key';
    if (key === 'tts' && copy.api_key) copy.api_key_id = 'tts_key';
    if (key === 'vision' && copy.enabled && !copy.api_key_id) {
      copy.api_key_id = `${provider.id}_key`;
    }
    delete copy.api_key;
    features[key] = copy;
  }

  return {
    providers: [provider],
    chat: {
      provider: provider.id,
      model: state.provider.model,
      temperature: state.chat.temperature,
      top_p: state.chat.top_p,
      max_tokens: state.chat.max_tokens,
      reasoning: !!state.chat.reasoning,
      reasoning_budget: state.chat.reasoning_budget || null,
    },
    agents,
    features,
    behavior: { max_context_tokens: state.behavior.max_context_tokens ?? null },
    persona: state.persona,
    adapter: {
      enabled: true,
      mode: state.adapter.mode,
      host: state.adapter.host,
      port: state.adapter.port,
      path: state.adapter.path,
      access_token: state.adapter.access_token || '',
      manage_process: !!state.adapter.manage_process,
      process_path: state.adapter.process_path || '',
      auto_restart: state.adapter.auto_restart !== false,
      whitelist: {
        mode: state.adapter.whitelist.mode,
        qq_ids: state.adapter.whitelist.qq_ids || [],
        group_ids: state.adapter.whitelist.group_ids || [],
      },
    },
    // 不走 provider 的密钥，由后端统一写进密钥库
    secrets: collectSecrets(state),
    // 向导阶段调不了生成器（Runtime 还没起来），把描述存到数据目录，
    // 配置完成后「角色 → 新建角色」会读它预填。不收的话用户就白填了。
    persona_brief: state.persona_source === 'create' && state.persona_brief
      ? { personality: state.persona_brief }
      : null,
  };
}

function collectSecrets(state) {
  const out = {};
  if ((state.features.weather || {}).api_key) out.weather_key = state.features.weather.api_key;
  if ((state.features.tts || {}).api_key) out.tts_key = state.features.tts.api_key;
  return out;
}
