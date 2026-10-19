// 设置页 —— 对应 ui/dashboard/settings_page.py 的 8 个分页。
//
// 后端 /api/settings 返回一张声明式字段表（webui/settings_spec.py），
// 这里据此自动渲染表单，不再手写每个控件。模型（provider / agent）有独立接口。

import { h, fill, card, empty, field } from '../dom.js';
import { endpoints } from '../api.js';
import { renderModelsPanel } from '../providers.js';

export async function render(container, ctx) {
  let payload;
  try {
    payload = await endpoints.settings();
  } catch (e) {
    fill(container, card('设置', '', empty(`读取配置失败：${e.message}`)));
    return {};
  }

  const groups = payload.groups || [];
  const pathsInfo = payload.paths || {};
  let activeKey = 'models';
  let restartHint = null;

  const nav = h('div', { class: 'settings-nav' });
  const body = h('div', {});
  // 重启提示放在独立容器里：body 每次切分页都会被 fill() 清空，
  // 提示挂在 body 里会被一起冲掉。
  const hintHost = h('div', {});

  const tabs = [
    { key: 'models', title: '模型' },
    ...groups.map((g) => ({ key: g.key, title: g.title })),
    { key: 'paths', title: '路径与诊断' },
  ];

  function markRestart() {
    if (restartHint) return;
    restartHint = card(null, '',
      h('div', { class: 'row' },
        h('span', {}, '有配置需要重启 Runtime 才能生效。'),
        h('div', { class: 'spacer' }),
        h('button', {
          class: 'btn btn-primary',
          onclick: async (event) => {
            event.currentTarget.disabled = true;
            try {
              await endpoints.restart();
              ctx.toast('Runtime 已重启', 'ok');
              clearRestart();
            } catch (e) {
              ctx.toast(e.message, 'error');
              event.currentTarget.disabled = false;
            }
          },
        }, '立即重启'),
      ),
    );
    fill(hintHost, restartHint);
  }

  function clearRestart() {
    restartHint = null;
    fill(hintHost);
  }

  // ============================================================
  // 通用字段渲染
  // ============================================================

  function control(fieldSpec, onSave) {
    const value = fieldSpec.value;
    const kind = fieldSpec.type;

    if (kind === 'bool') {
      const input = h('input', { type: 'checkbox', checked: !!value });
      input.addEventListener('change', () => onSave(input.checked));
      return h('label', { class: 'switch' }, input, fieldSpec.label);
    }

    if (kind === 'enum') {
      const select = h('select', {});
      for (const [val, label] of fieldSpec.options || []) {
        select.append(h('option', { value: val, selected: val === value }, label));
      }
      select.addEventListener('change', () => onSave(select.value));
      return field(fieldSpec.label, select, fieldSpec.help || '');
    }

    if (kind === 'list_int' || kind === 'list_str') {
      const input = h('input', {
        value: (value || []).join(', '),
        placeholder: kind === 'list_int' ? '逗号分隔（中英文均可），留空表示不限制' : '逗号或换行分隔',
      });
      const commit = () => onSave(input.value);
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); commit(); }
      });
      return field(fieldSpec.label, input, fieldSpec.help || '');
    }

    if (kind === 'json') {
      // 结构型配置（字典 / 规则表）声明式表单展开会淹没页面，用一个 JSON 文本框整体编辑。
      // 后端会 json.loads 校验，写错会带着行号报回来。
      const area = h('textarea', {
        rows: 6,
        spellcheck: 'false',
        style: { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-small)' },
        value: value === null || value === undefined
          ? '' : JSON.stringify(value, null, 2),
      });
      const commit = () => onSave(area.value);
      let dirty = false;
      area.addEventListener('input', () => { dirty = true; });
      area.addEventListener('blur', () => { if (dirty) { dirty = false; commit(); } });
      area.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          dirty = false;
          commit();
        }
      });
      return field(fieldSpec.label, area,
        (fieldSpec.help || '') + '（JSON；Ctrl+Enter 保存）');
    }

    if (kind === 'secret_ref') {
      const input = h('input', {
        type: 'password',
        placeholder: value ? '已保存（留空则不修改）' : '未设置',
      });
      const button = h('button', {
        class: 'btn',
        onclick: async () => {
          const text = input.value.trim();
          if (!text && !confirm('清空这个密钥？')) return;
          try {
            await endpoints.putSecret(fieldSpec.secret_id, text);
            ctx.toast(text ? '密钥已保存' : '密钥已清空', 'ok');
            input.value = '';
          } catch (e) {
            ctx.toast(e.message, 'error');
          }
        },
      }, '保存');
      return field(fieldSpec.label, h('div', { class: 'row' }, input, button),
        fieldSpec.help || '密钥只写入加密的 secrets.enc，不落 config.yaml');
    }

    const type = (kind === 'int' || kind === 'int_or_none') ? 'number'
      : (kind === 'float' ? 'number' : 'text');
    const input = h('input', {
      type,
      value: value === null || value === undefined ? '' : value,
      step: kind === 'float' ? 'any' : null,
      min: fieldSpec.min,
      max: fieldSpec.max,
      placeholder: kind === 'int_or_none' ? '留空 = 自动' : '',
    });
    const commit = () => {
      const raw = input.value.trim();
      if (kind === 'int_or_none' && raw === '') return onSave(null);
      onSave(raw);
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); commit(); }
    });
    return field(fieldSpec.label, input, fieldSpec.help || '');
  }

  async function saveField(fieldSpec, value) {
    try {
      const result = await endpoints.patchSetting(fieldSpec.path, value);
      fieldSpec.value = result.value;
      ctx.toast(`${fieldSpec.label} 已保存`, 'ok');
      if (result.needs_restart) markRestart();
    } catch (e) {
      ctx.toast(`${fieldSpec.label} 保存失败：${e.message}`, 'error');
    }
  }

  // ============================================================
  // 模型分页
  // ============================================================

  async function renderModels() {
    // 提供商增删改、测试连接、拉取模型列表都在 providers.js 里，
    // 首次向导也复用同一套表单，避免两处各写一份。
    await renderModelsPanel(body, ctx, { markRestart });
  }

  // ============================================================
  // 路径与诊断
  // ============================================================

  function renderPaths() {
    const secrets = payload.secrets_index || [];
    fill(body,
      card('运行路径', '', h('table', { class: 'table' },
        h('tbody', {}, ...Object.entries(pathsInfo).map(([key, value]) =>
          h('tr', {}, h('td', {}, key), h('td', { class: 'mono' }, value)))),
      )),
      card('已保存的密钥 ID', '只列 ID，不显示密钥内容。',
        secrets.length
          ? h('div', { class: 'chips' }, ...secrets.map((id) => h('span', { class: 'chip' }, id)))
          : empty('还没有保存任何密钥')),
      card('危险操作', '',
        h('div', { class: 'row row-wrap' },
          h('button', {
            class: 'btn',
            onclick: async () => {
              try { await endpoints.stopRuntime(); ctx.toast('Runtime 已停止', 'ok'); }
              catch (e) { ctx.toast(e.message, 'error'); }
            },
          }, '停止 Runtime'),
          h('button', {
            class: 'btn',
            onclick: async () => {
              try { await endpoints.startRuntime(); ctx.toast('Runtime 已启动', 'ok'); }
              catch (e) { ctx.toast(e.message, 'error'); }
            },
          }, '启动 Runtime'),
          h('button', {
            class: 'btn btn-danger',
            onclick: async () => {
              if (!confirm('退出 WebUI 进程？Runtime 会一并关闭。')) return;
              try { await endpoints.quit(); } catch { /* 进程正在退出 */ }
              document.body.replaceChildren(
                h('div', { class: 'gate' },
                  h('div', { class: 'gate-card' },
                    h('div', { class: 'gate-brand' }, '已退出'),
                    h('p', { class: 'gate-hint' }, 'WebUI 服务已关闭，可以关闭此页面。'))));
            },
          }, '退出 WebUI'),
        ),
      ),
    );
  }

  // ============================================================
  // 分页切换
  // ============================================================

  async function show(key) {
    activeKey = key;
    for (const btn of nav.querySelectorAll('button')) {
      btn.classList.toggle('active', btn.dataset.key === key);
    }
    if (key === 'models') return renderModels();
    if (key === 'paths') return renderPaths();

    const group = groups.find((g) => g.key === key);
    if (!group) return fill(body, empty('未知分页'));

    const nodes = [h('p', { class: 'card-sub' }, group.desc || '')];
    if (!group.fields.length) {
      nodes.push(empty('这个分页没有可视化字段，请直接编辑 config.yaml'));
    } else {
      const section = h('div', { class: 'card' });
      for (const spec of group.fields) {
        section.append(control(spec, (value) => saveField(spec, value)));
      }
      nodes.push(section);
    }
    fill(body, ...nodes);
  }

  fill(nav, ...tabs.map((t) =>
    h('button', { dataset: { key: t.key }, onclick: () => show(t.key) }, t.title)));

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '设置'),
        h('p', { class: 'page-desc' }, '改动即时写入 config.yaml；需要重启的会给出提示。'),
      ),
    ),
    hintHost,
    h('div', { class: 'settings-layout' }, nav, body),
  );

  await show('models');
  return {};
}
