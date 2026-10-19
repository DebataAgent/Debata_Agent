// 记忆页 —— 对应 ui/dashboard/memory_page.py。
// 重要记忆的增删改 + RAG 索引浏览。

import { h, fill, card, empty, field, formatTime } from '../dom.js';
import { endpoints } from '../api.js';

export async function render(container, ctx) {
  let mode = 'file';
  let items = [];
  let tab = 'important';
  let disposed = false;

  const listBox = h('div', {});
  const newText = h('textarea', {
    placeholder: '记一条重要记忆，例如「用户说他养了一只叫豆豆的猫」',
    rows: 2,
  });

  async function add() {
    const content = newText.value.trim();
    if (!content) return;
    try {
      const result = await endpoints.addMemory({ content, pinned: false });
      ctx.toast(result.message, result.ok ? 'ok' : 'warn');
      newText.value = '';
      await load();
    } catch (e) {
      ctx.toast(e.message, 'error');
    }
  }

  async function load() {
    try {
      const data = await endpoints.memory();
      mode = data.mode;
      items = data.items || [];
      renderList();
    } catch (e) {
      fill(listBox, empty(`读取记忆失败：${e.message}`));
    }
  }

  function renderList() {
    if (!items.length) {
      fill(listBox, empty('还没有保存任何重要记忆'));
      return;
    }
    fill(listBox, h('table', { class: 'table' },
      h('thead', {}, h('tr', {},
        h('th', {}, '内容'), h('th', {}, '范围'), h('th', {}, '时间'),
        h('th', {}, '置顶'), h('th', {}, '操作'))),
      h('tbody', {}, ...items.map((item) =>
        h('tr', {},
          h('td', {}, item.content || ''),
          h('td', { class: 'mono' }, item.scope || '全局'),
          h('td', { class: 'field-help' },
            item.timestamp ? formatTime(item.timestamp) : '—'),
          h('td', {},
            h('input', {
              type: 'checkbox',
              checked: !!item.pinned,
              onchange: async (event) => {
                try {
                  await endpoints.updateMemory(item.id, { pinned: event.target.checked });
                  ctx.toast('已更新', 'ok');
                } catch (e) {
                  ctx.toast(e.message, 'error');
                  event.target.checked = !event.target.checked;
                }
              },
            })),
          h('td', {},
            h('button', {
              class: 'btn btn-sm btn-danger',
              onclick: async () => {
                if (!confirm('删除这条记忆？')) return;
                try {
                  await endpoints.deleteMemory(item.id);
                  ctx.toast('已删除', 'ok');
                  await load();
                } catch (e) {
                  ctx.toast(e.message, 'error');
                }
              },
            }, '删除')),
        ),
      )),
    ));
  }

  async function renderRag() {
    fill(listBox, empty('读取 RAG 索引…'));
    try {
      const data = await endpoints.rag(300);
      if (!data.available) {
        fill(listBox, empty(
          data.error || 'RAG 未启用。把「设置 → 记忆 → 长期记忆模式」改成 rag 并重启即可启用。'));
        return;
      }
      if (!data.entries.length) {
        fill(listBox, empty('RAG 索引里还没有条目'));
        return;
      }
      fill(listBox, h('div', {},
        h('p', { class: 'card-sub' },
          `共 ${data.count} 条，向量服务${data.embedding_ready ? '已就绪' : '未就绪'}。`),
        h('table', { class: 'table' },
          h('thead', {}, h('tr', {},
            h('th', {}, '内容'), h('th', {}, '会话'), h('th', {}, '时间'))),
          h('tbody', {}, ...data.entries.map((entry) =>
            h('tr', {},
              h('td', {}, entry.content || ''),
              h('td', { class: 'mono' }, entry.conversation_id || '—'),
              h('td', { class: 'field-help' }, entry.timestamp || '—'),
            )))),
      ));
    } catch (e) {
      fill(listBox, empty(`读取 RAG 索引失败：${e.message}`));
    }
  }

  const tabBar = h('div', { class: 'tabs' },
    h('button', { class: 'tab active', dataset: { tab: 'important' },
      onclick: (e) => switchTab('important', e.target) }, '重要记忆'),
    h('button', { class: 'tab', dataset: { tab: 'rag' },
      onclick: (e) => switchTab('rag', e.target) }, 'RAG 历史索引'),
  );

  async function switchTab(key, btn) {
    tab = key;
    for (const el of tabBar.querySelectorAll('.tab')) el.classList.remove('active');
    btn.classList.add('active');
    if (key === 'rag') await renderRag();
    else await load();
  }

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '记忆'),
        h('p', { class: 'page-desc' }, '跨会话长期保留的事实。置顶的记忆每次都进上下文。'),
      ),
      h('div', { class: 'row' },
        h('span', { class: 'badge' }, `模式：${mode}`),
        h('button', { class: 'btn', onclick: load }, '刷新')),
    ),
    tabBar,
    card('新增记忆', '写一句话即可，重复内容不会被重复保存。',
      newText,
      h('div', { style: { marginTop: '10px' } },
        h('button', { class: 'btn btn-primary', onclick: add }, '记下来'))),
    listBox,
  );

  await load();
  return { destroy() { disposed = true; void disposed; } };
}
