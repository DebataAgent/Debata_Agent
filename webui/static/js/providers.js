// 模型提供商面板：列表 + 添加/编辑/删除 + 测试连接 + 拉取模型列表。
//
// 对应原 GUI 的 ui/dashboard/settings/model.py（那是 900 行的 Qt 表单）。
// 这里做成两个可复用面板，设置页「模型」分页直接用；
// 首次向导也复用 providerForm()，避免两处各写一份 provider 表单。
//
// 设计要点：
//   - 密钥输入框只写不读：后端只回 has_key，编辑时留空表示"不改"
//   - 中转站（自定义 OpenAI 兼容端点）= 不选预设 + 填 Base URL，
//     此时思考参数风格默认取 none，避免给不认识的网关发厂商私有字段
//   - 模型 ID 保留下拉 + 手填两条路：拉取模型列表未必成功（很多网关没有 /models）

import { h, fill, card, empty, field, modal, table } from './dom.js';
import { icon } from './icons.js';
import { endpoints } from './api.js';

const AGENT_LABEL = {
  chat: '主聊天',
  proactive: '主动思考',
  summary: '滚动摘要',
  persona_gen: '人格生成',
};

const REASONING_STYLES = [
  ['', '跟随预设（自定义则不发任何字段）'],
  ['thinking_extra_body', 'thinking_extra_body — DeepSeek / GLM / MiMo 风格'],
  ['qwen_enable_thinking', 'qwen_enable_thinking — 通义千问'],
  ['openai_native', 'openai_native — OpenAI 官方（不发字段）'],
  ['none', 'none — 什么都不发（不认识的中转站用这个）'],
];

const CUSTOM = '__custom__';

// ============================================================
// 设置页「模型」分页
// ============================================================

export async function renderModelsPanel(host, ctx, { markRestart } = {}) {
  let data;
  try {
    data = await endpoints.providers();
  } catch (e) {
    fill(host, empty('模型配置没能读出来', { subtitle: e.message, iconName: 'models' }));
    return;
  }

  const providerCard = card(
    '模型提供商',
    '密钥只写进加密的密钥库，不会出现在 config.yaml，也不会回显。改动需要重启 Runtime 生效。',
    providerTable(data.providers || [], reload),
    h('div', { class: 'row', style: { marginTop: '12px' } },
      h('button', {
        class: 'btn btn-primary',
        onclick: () => openProviderDialog(null, reload),
      }, icon('add', { size: 16 }), '添加提供商'),
    ),
  );

  const agentCards = Object.entries(data.agents || {}).map(([name, agent]) =>
    agentCard(name, agent, data.providers || [], ctx, markRestart, reload),
  );

  fill(host, providerCard, h('div', { class: 'grid grid-2' }, ...agentCards));

  async function reload() {
    await renderModelsPanel(host, ctx, { markRestart });
  }
}

// ============================================================
// 提供商列表
// ============================================================

function providerTable(providers, onChanged) {
  if (!providers.length) {
    return empty('还没有配置提供商', {
      subtitle: '点下面的「添加提供商」，选一个预设或填中转站地址',
      iconName: 'models',
    });
  }
  const rows = providers.map((p) =>
    h('tr', {},
      h('td', {},
        h('div', { class: 'mono' }, p.id),
        p.used_by && p.used_by.length
          ? h('div', { class: 'field-help' }, `用于 ${p.used_by.join('、')}`)
          : null,
      ),
      h('td', {}, p.display_name,
        h('div', { class: 'field-help' }, p.preset ? `预设 ${p.preset}` : '自定义')),
      h('td', { class: 'mono', style: { wordBreak: 'break-all' } }, p.base_url || '—'),
      h('td', {}, keyBadge(p)),
      h('td', {}, healthBadge(p.health)),
      h('td', {},
        h('div', { class: 'row', style: { gap: '4px' } },
          h('button', {
            class: 'btn btn-sm',
            title: '测试连接',
            onclick: (event) => runTest(event.currentTarget, p, onChanged),
          }, icon('plug', { size: 14 })),
          h('button', {
            class: 'btn btn-sm',
            title: '编辑',
            onclick: () => openProviderDialog(p, onChanged),
          }, icon('edit', { size: 14 })),
          h('button', {
            class: 'btn btn-sm btn-danger',
            title: '删除',
            onclick: () => removeProvider(p, onChanged),
          }, icon('delete', { size: 14 })),
        ),
      ),
    ),
  );
  return table(['ID', '名称', 'Base URL', '密钥', '状态', '操作'], rows);
}

function keyBadge(p) {
  if (p.has_key) return h('span', { class: 'badge ok' }, '已配置');
  if (p.api_key_id) return h('span', { class: 'badge warn' }, '缺密钥');
  return h('span', { class: 'badge idle' }, '不需要');
}

function healthBadge(health) {
  if (!health) return h('span', { class: 'badge idle' }, '未检测');
  const kind = { healthy: 'ok', degraded: 'warn', error: 'bad' }[health.status] || 'idle';
  return h('span', { class: 'badge ' + kind, title: health.message || '' },
    health.status === 'healthy' ? '已就位' : (health.message || health.status));
}

async function runTest(button, provider, onChanged) {
  button.disabled = true;
  try {
    const result = await endpoints.testProvider(provider.id);
    button.replaceChildren(icon(result.ok ? 'check' : 'close', { size: 14 }));
    button.title = result.message || '';
    // 测试结果会写进后端健康状态，刷新一次让"状态"列同步
    if (onChanged) await onChanged();
  } catch (e) {
    button.title = e.message;
    button.disabled = false;
  }
}

async function removeProvider(provider, onDone) {
  const used = (provider.used_by || []).length;
  const extra = used
    ? `\n\n注意：它还被 ${provider.used_by.join('、')} 使用，需要先改到别的提供商。`
    : '';
  if (!confirm(`删除提供商「${provider.id}」？${extra}\n\n已保存的密钥会保留。`)) return;
  try {
    await endpoints.deleteProvider(provider.id, false);
    if (onDone) await onDone();
  } catch (e) {
    alert(e.message);
  }
}

// ============================================================
// 可复用的 provider 表单
// ============================================================

/**
 * 构造 provider 表单控件，直接读写传入的 state.provider。
 *
 * 设置页的编辑弹窗与首次向导都用它——provider 表单字段多、联动多（选预设带出
 * 协议/地址/模型），两处各写一份必然漂。
 *
 * @returns {{nodes: Array, presetSelect, protocolSelect, baseUrlInput, modelInput,
 *            modelList, keyInput, keyToggle, styleSelect, timeoutInput, statusLine,
 *            setStatus: Function, syncFromPreset: Function}}
 */
export function providerFields(state, { includeId = true, idLocked = false, presets = null } = {}) {
  const available = presets || state.presets || [];
  const p = state.provider;

  const idInput = h('input', {
    value: p.id || '',
    placeholder: 'relay01',
    disabled: idLocked,
    oninput: (e) => { p.id = e.target.value.trim(); },
  });
  // 显示名和协议/地址一样会被预设覆盖，切回「自定义」时要还原，所以提前定义。
  const displayNameInput = h('input', {
    value: p.display_name || '',
    oninput: (e) => { p.display_name = e.target.value.trim(); },
  });

  const presetSelect = h('select', {
    onchange: () => {
      const chosen = presetSelect.value;
      p.preset = chosen === CUSTOM ? null : chosen;
      syncFromPreset();
    },
  });
  presetSelect.append(h('option', { value: CUSTOM }, '自定义 OpenAI / Anthropic 兼容端点'));
  for (const preset of available) {
    presetSelect.append(h('option', {
      value: preset.id, selected: p.preset === preset.id,
    }, `${preset.display_name}（${preset.id}）`));
  }
  presetSelect.value = p.preset || CUSTOM;

  const protocolSelect = h('select', {
    onchange: (e) => { p.protocol = e.target.value; },
  });
  for (const [value, label] of [['openai_compat', 'OpenAI 兼容'], ['anthropic', 'Anthropic']]) {
    protocolSelect.append(h('option', { value, selected: p.protocol === value }, label));
  }
  protocolSelect.value = p.protocol || 'openai_compat';

  const baseUrlInput = h('input', {
    value: p.base_url || '',
    placeholder: 'https://relay.example.com/v1',
    oninput: (e) => { p.base_url = e.target.value.trim(); },
  });
  const modelInput = h('input', {
    value: p.model || '',
    placeholder: '模型 ID，如 mimo-v2.5-pro',
    list: 'provider-model-options',
    oninput: (e) => { p.model = e.target.value.trim(); },
  });

  const keyInput = h('input', {
    type: 'password',
    autocomplete: 'new-password',
    value: p.api_key || '',
    placeholder: p.has_key ? '已保存（留空则不改）' : 'sk-…',
    oninput: (e) => { p.api_key = e.target.value.trim(); },
  });
  const keyToggle = h('button', {
    type: 'button',
    class: 'secret-toggle',
    onclick: () => {
      const shown = keyInput.type === 'text';
      keyInput.type = shown ? 'password' : 'text';
      keyToggle.textContent = shown ? '显示' : '隐藏';
    },
  }, '显示');

  const styleSelect = h('select', {
    onchange: (e) => { p.reasoning_style = e.target.value; },
  });
  for (const [value, label] of REASONING_STYLES) {
    styleSelect.append(h('option', {
      value, selected: (p.reasoning_style || '') === value,
    }, label));
  }
  styleSelect.value = p.reasoning_style || '';

  const timeoutInput = h('input', {
    type: 'number', min: '1', step: '1',
    value: p.timeout_seconds || 120,
    oninput: (e) => { p.timeout_seconds = Number(e.target.value); },
  });

  const statusLine = h('div', { class: 'field-help' }, '');
  const setStatus = (text, kind = '') => {
    statusLine.textContent = text;
    statusLine.style.color = kind === 'error' ? 'var(--error)'
      : kind === 'ok' ? 'var(--success)' : '';
  };

  // 用户自己填的那份值。选预设会覆盖表单，切回「自定义」必须原样还回来——
  // 只把协议下拉解禁、值却留着预设的，等于把中转站配成 anthropic：
  // 上游多半只有 OpenAI 协议，一请求就是 403 "does not allow /v1/messages dispatch"。
  const customValues = {
    protocol: p.protocol || 'openai_compat',
    display_name: p.display_name || '',
    base_url: p.base_url || '',
    reasoning_style: p.reasoning_style || '',
  };
  const rememberCustom = () => {
    customValues.protocol = p.protocol || 'openai_compat';
    customValues.display_name = p.display_name || '';
    customValues.base_url = p.base_url || '';
    customValues.reasoning_style = p.reasoning_style || '';
  };
  let lastChosen = presetSelect.value;

  function syncFromPreset() {
    const chosen = presetSelect.value;
    // 只有「从自定义切走」这一刻，表单里才是用户自己填的值，值得存下来。
    if (lastChosen === CUSTOM) rememberCustom();
    lastChosen = chosen;
    if (chosen === CUSTOM) {
      p.protocol = customValues.protocol;
      p.display_name = customValues.display_name;
      p.base_url = customValues.base_url;
      p.reasoning_style = customValues.reasoning_style;
      protocolSelect.value = customValues.protocol;
      protocolSelect.disabled = false;
      displayNameInput.value = customValues.display_name;
      baseUrlInput.value = customValues.base_url;
      styleSelect.value = customValues.reasoning_style;
      return;
    }
    const preset = available.find((x) => x.id === chosen);
    if (!preset) return;
    p.protocol = preset.protocol;
    p.display_name = preset.display_name;
    p.base_url = preset.base_url;
    p.reasoning_style = preset.reasoning_style || '';
    protocolSelect.value = preset.protocol;
    protocolSelect.disabled = true;
    displayNameInput.value = preset.display_name;
    baseUrlInput.value = preset.base_url;
    styleSelect.value = preset.reasoning_style || '';
    if (!p.model && preset.models && preset.models.length) {
      p.model = preset.models[0].id;
      modelInput.value = p.model;
    }
  }

  syncFromPreset();

  const nodes = [];
  if (includeId) {
    nodes.push(h('div', { class: 'grid grid-2' },
      field('提供商 ID', idInput, idLocked ? 'ID 建好后不可改' : '小写字母开头，可含数字、_、-'),
      field('显示名', displayNameInput),
    ));
  }
  nodes.push(
    field('预设', presetSelect),
    h('div', { class: 'grid grid-2' },
      field('协议', protocolSelect, '选预设时由预设决定'),
      field('Base URL', baseUrlInput, '不以 /v1 结尾会自动补上'),
    ),
    field('API Key', h('div', { class: 'secret-field' }, keyInput, keyToggle),
      p.has_key ? '留空表示不改动已保存的密钥' : '只写进密钥库，不会回显'),
    field('思考参数风格', styleSelect,
      '中转站拿不准就选 none —— 不发任何厂商私有字段，最不容易被网关拒绝'),
    h('div', { class: 'grid grid-2' },
      field('超时（秒）', timeoutInput),
      field('模型 ID', h('div', {}, modelInput,
        h('datalist', { id: 'provider-model-options' })), '测试连接要用它发一次真实请求'),
    ),
  );

  return {
    nodes, idInput, presetSelect, protocolSelect, baseUrlInput,
    modelInput, keyInput, keyToggle, styleSelect, timeoutInput, statusLine,
    setStatus, syncFromPreset,
  };
}

/** 表单校验。返回错误文案，或 null 表示通过。 */
export function readProviderFields(state, fields, { requireKey = true } = {}) {
  const p = state.provider;
  if (!p.id) return '还没填提供商 ID';
  if (!/^[a-z][a-z0-9_-]{0,63}$/.test(p.id)) {
    return '提供商 ID 只能用小写字母开头，后跟小写字母、数字、下划线或短横线';
  }
  if (!p.preset) {
    if (!p.base_url) return '自定义端点要填 Base URL';
    if (!p.protocol) return '自定义端点要选协议';
  }
  if (requireKey && !p.api_key && !p.has_key) return '还没填 API Key';
  if (!p.model) return '还没填模型 ID';
  void fields;
  return null;
}

// ============================================================
// 添加 / 编辑弹窗
// ============================================================

async function openProviderDialog(existing, onDone) {
  let presets = [];
  try {
    presets = (await endpoints.providerPresets()).presets || [];
  } catch {
    // 预设拉不到也能用：自定义端点这条路不依赖预设
  }

  const isEdit = !!existing;
  const state = {
    presets,
    provider: existing
      ? {
          id: existing.id,
          display_name: existing.display_name,
          preset: existing.preset || null,
          protocol: existing.protocol || 'openai_compat',
          base_url: existing.base_url || '',
          model: '',
          reasoning_style: existing.reasoning_style || '',
          timeout_seconds: existing.timeout_seconds || 120,
          api_key: '',
          has_key: !!existing.has_key,
        }
      : {
          id: '',
          display_name: '',
          // 和 CLI、首次向导一致默认 deepseek。原先取 presets[0]，而预设是字母序的，
          // 于是"默认"落到 anthropic——只改 Base URL 接中转站的用户会被静默锁成
          // anthropic 协议，请求打到 /v1/messages 直接 403。
          preset: (presets.find((x) => x.id === 'deepseek') || presets[0] || {}).id || null,
          protocol: 'openai_compat',
          base_url: '',
          model: '',
          reasoning_style: '',
          timeout_seconds: 120,
          api_key: '',
          has_key: false,
        },
  };

  const fields = providerFields(state, { includeId: true, idLocked: isEdit, presets });
  const { setStatus } = fields;

  // 编辑已有 provider 时，模型 ID 从它的预设里带一个出来，好让「测试连接」有东西可用
  if (isEdit) {
    const preset = presets.find((x) => x.id === state.provider.preset);
    if (preset && preset.models && preset.models.length) {
      state.provider.model = preset.models[0].id;
      fields.modelInput.value = state.provider.model;
    }
  }

  function payload({ forProbe = false } = {}) {
    const p = state.provider;
    const out = {
      preset: p.preset || null,
      protocol: p.protocol || null,
      base_url: p.base_url || null,
      display_name: p.display_name || null,
      reasoning_style: p.reasoning_style || null,
      timeout_seconds: p.timeout_seconds || null,
      model: p.model || null,
    };
    if (!isEdit) out.id = p.id;
    if (p.api_key) out.api_key = p.api_key;
    if (forProbe && !p.api_key && existing?.api_key_id) {
      out.api_key_id = existing.api_key_id;
    }
    return out;
  }

  const testBtn = h('button', { class: 'btn' }, icon('plug', { size: 16 }), '测试连接');
  testBtn.onclick = async () => {
    testBtn.disabled = true;
    setStatus('正在测试…');
    try {
      const result = isEdit && !state.provider.api_key
        ? await endpoints.testProvider(existing.id)
        : await endpoints.testProviderConfig(payload({ forProbe: true }));
      setStatus(result.message || (result.ok ? '连接正常' : '未通过'),
        result.ok ? 'ok' : 'error');
    } catch (e) {
      setStatus(e.message, 'error');
    } finally {
      testBtn.disabled = false;
    }
  };

  const fetchBtn = h('button', { class: 'btn' }, icon('download', { size: 16 }), '获取模型列表');
  fetchBtn.onclick = async () => {
    fetchBtn.disabled = true;
    setStatus('正在获取…');
    try {
      const result = await endpoints.fetchProviderModels(payload({ forProbe: true }));
      const models = result.models || [];
      fields.modelInput.setAttribute('list', 'provider-model-options');
      const list = document.getElementById('provider-model-options');
      if (list) {
        list.replaceChildren(...models.map((m) =>
          h('option', { value: m.id }, m.display_name || m.id)));
      }
      if (!state.provider.model && models.length) {
        state.provider.model = models[0].id;
        fields.modelInput.value = models[0].id;
      }
      setStatus(`拿到 ${models.length} 个模型，可在上面输入框里选或手填`, 'ok');
    } catch (e) {
      setStatus(`${e.message}（可以直接手填模型 ID）`, 'error');
    } finally {
      fetchBtn.disabled = false;
    }
  };

  const saveBtn = h('button', { class: 'btn btn-primary' }, isEdit ? '保存' : '添加');
  const cancelBtn = h('button', { class: 'btn' }, '算了');

  const dialog = modal({
    title: isEdit ? `编辑 ${existing.id}` : '添加提供商',
    subtitle: '中转站选「自定义」再填 Base URL；模型 ID 拉不到就手填',
    wide: true,
    body: h('div', {},
      ...fields.nodes,
      h('div', { class: 'row', style: { marginTop: '4px' } }, testBtn, fetchBtn),
      h('div', { style: { marginTop: '8px' } }, fields.statusLine),
    ),
    footer: [cancelBtn, saveBtn],
  });

  cancelBtn.onclick = () => dialog.close();

  saveBtn.onclick = async () => {
    const error = readProviderFields(state, fields, { requireKey: !isEdit });
    if (error) {
      setStatus(error, 'error');
      return;
    }
    saveBtn.disabled = true;
    try {
      if (isEdit) {
        await endpoints.updateProvider(existing.id, payload());
      } else {
        await endpoints.createProvider(payload());
      }
      dialog.close();
      if (onDone) await onDone();
    } catch (e) {
      setStatus(e.message, 'error');
      saveBtn.disabled = false;
    }
  };

  document.body.append(dialog.el);
  if (!isEdit) fields.idInput.focus();
}
// ============================================================
// Agent 卡片
// ============================================================

function agentCard(name, agent, providers, ctx, markRestart, onSaved) {
  const title = AGENT_LABEL[name] || name;
  if (!agent) {
    return card(title, '未启用',
      h('div', { class: 'field-help' }, '这个 Agent 当前关闭，不需要配置模型。'));
  }

  const providerSelect = h('select', {});
  for (const p of providers) {
    providerSelect.append(h('option', { value: p.id },
      `${p.display_name}（${p.id}）`));
  }
  if (!providers.some((p) => p.id === agent.provider)) {
    // 引用的 provider 已不存在——不能装作正常，直接显出来
    providerSelect.append(h('option', { value: agent.provider },
      `${agent.provider}（已不存在）`));
  }
  providerSelect.value = agent.provider;

  const modelInput = h('input', { value: agent.model });
  const tempInput = h('input', {
    type: 'number', step: '0.1', min: '0', max: '2', value: agent.temperature,
  });
  const topPInput = h('input', {
    type: 'number', step: '0.05', min: '0', max: '1', value: agent.top_p,
  });
  const maxInput = h('input', { type: 'number', min: '1', value: agent.max_tokens });
  const reasoning = h('input', { type: 'checkbox', checked: !!agent.reasoning });

  const commit = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await endpoints.patchAgent(name, {
        provider: providerSelect.value,
        model: modelInput.value.trim(),
        temperature: Number(tempInput.value),
        top_p: Number(topPInput.value),
        max_tokens: Number(maxInput.value),
        reasoning: reasoning.checked,
      });
      ctx.toast(result.message, 'ok');
      if (markRestart) markRestart();
      if (onSaved) await onSaved();
    } catch (e) {
      ctx.toast(e.message, 'error');
      button.disabled = false;
    }
  };

  return card(title, `当前：${agent.provider}`,
    field('提供商', providerSelect),
    field('模型 ID', modelInput),
    h('div', { class: 'grid grid-2' },
      field('温度', tempInput),
      field('top_p', topPInput),
    ),
    h('div', { class: 'grid grid-2' },
      field('最大输出 token', maxInput),
      field('', h('label', { class: 'switch' }, reasoning, '启用思考模式')),
    ),
    h('div', { style: { marginTop: '12px' } },
      h('button', { class: 'btn btn-primary', onclick: commit }, '保存')),
  );
}
