// 角色页 —— 对应 ui/dashboard/personas_page.py。
// 列出 personas/ 下的人格目录，支持切换、导入 zip、导出 zip、删除。

import { h, fill, card, empty } from '../dom.js';
import { icon } from '../icons.js';
import { endpoints } from '../api.js';
import {
  openPersonaCreator,
  openPersonaDuplicate,
  openPersonaRefine,
} from '../persona_dialogs.js';

export async function render(container, ctx) {
  let personas = [];
  let active = '';
  let disposed = false;

  const listBox = h('div', {});

  async function load() {
    try {
      const data = await endpoints.personas();
      personas = data.personas || [];
      active = data.active || '';
      renderList();
    } catch (e) {
      fill(listBox, empty(`读取角色失败：${e.message}`));
    }
  }

  function renderList() {
    if (!personas.length) {
      fill(listBox, empty('personas/ 目录下没有找到任何人设'));
      return;
    }
    fill(listBox, h('table', { class: 'table' },
      h('thead', {}, h('tr', {},
        h('th', {}, '角色'), h('th', {}, '说明'), h('th', {}, '状态'), h('th', {}, '操作'))),
      h('tbody', {}, ...personas.map((p) =>
        h('tr', {},
          h('td', {}, h('strong', {}, p.name)),
          h('td', { class: 'field-help' }, p.description || '—'),
          h('td', {},
            p.active
              ? h('span', { class: 'badge ok' }, '当前使用')
              : (p.builtin ? h('span', { class: 'badge' }, '内置') : '')),
          h('td', {},
            h('div', { class: 'row row-wrap' },
              p.active ? null : h('button', {
                class: 'btn btn-sm',
                onclick: async () => {
                  try {
                    const result = await endpoints.activatePersona(p.name);
                    ctx.toast(result.message, 'ok');
                    await load();
                  } catch (e) {
                    ctx.toast(e.message, 'error');
                  }
                },
              }, '设为当前'),
              h('a', {
                class: 'btn btn-sm',
                href: `/api/personas/${encodeURIComponent(p.name)}/export`,
                download: `${p.name}.zip`,
              }, '导出'),
              h('button', {
                class: 'btn btn-sm',
                onclick: async () => {
                  try {
                    const data = await endpoints.personaPrompt(p.name);
                    showSource(p.name, data);
                  } catch (e) {
                    ctx.toast(e.message, 'error');
                  }
                },
              }, '查看人设'),
              h('button', {
                class: 'btn btn-sm',
                title: '贴一句反馈，让 AI 出一版修改稿',
                onclick: () => openPersonaRefine(ctx, p.name, { onDone: load }),
              }, icon('edit', { size: 14 }), '修改'),
              h('button', {
                class: 'btn btn-sm',
                title: '以它为模板建一个新角色',
                onclick: () => openPersonaDuplicate(ctx, p.name, { onDone: load }),
              }, '复制'),
              (p.builtin || p.active) ? null : h('button', {
                class: 'btn btn-sm btn-danger',
                onclick: async () => {
                  if (!confirm(`删除角色 ${p.name}？该目录会被整体删除，无法撤销。`)) return;
                  try {
                    await endpoints.deletePersona(p.name);
                    ctx.toast('已删除', 'ok');
                    await load();
                  } catch (e) {
                    ctx.toast(e.message, 'error');
                  }
                },
              }, '删除'),
            )),
        ),
      )),
    ));
  }

  function showSource(name, data) {
    const dialog = h('div', { class: 'gate' },
      h('div', { class: 'gate-card gate-card-wide' },
        h('div', { class: 'gate-brand' }, `${name} · persona_prompt.py`),
        h('p', { class: 'gate-hint mono' }, data.path),
        h('pre', {
          style: { maxHeight: '55vh', overflow: 'auto', background: 'var(--bg-primary)',
                   padding: '12px', borderRadius: '8px', margin: '0 0 16px' },
        }, data.source),
        h('button', {
          class: 'btn btn-primary btn-block',
          onclick: () => dialog.remove(),
        }, '关闭'),
      ),
    );
    document.body.append(dialog);
  }

  const fileInput = h('input', {
    type: 'file',
    accept: '.zip',
    style: { display: 'none' },
    onchange: async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      try {
        const result = await endpoints.importPersona(form);
        ctx.toast(result.message, 'ok');
        await load();
      } catch (e) {
        ctx.toast(e.message, 'error');
      } finally {
        event.target.value = '';
      }
    },
  });

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '角色'),
        h('p', { class: 'page-desc' },
          '每套人设是 personas/<名字>/ 下的一个目录。切换后需要重启 Runtime 生效。'),
      ),
      h('div', { class: 'row' },
        fileInput,
        h('button', {
          class: 'btn btn-primary',
          onclick: () => openPersonaCreator(ctx, { onDone: load }),
        }, icon('add', { size: 16 }), '新建角色'),
        h('button', {
          class: 'btn',
          onclick: () => fileInput.click(),
        }, '导入 zip'),
        h('button', { class: 'btn', onclick: load }, '刷新'),
      ),
    ),
    card('', '', listBox),
  );

  await load();
  return { destroy() { disposed = true; void disposed; } };
}
