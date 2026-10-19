// 黑名单页 —— WebUI 新增功能（原 GUI 只有白名单）。
//
// 与白名单的分工：
//   白名单 = 决定「谁能用」（收紧整体开放策略）
//   黑名单 = 决定「谁永远不能用」（点名拉黑，不影响其它人）
// 命中黑名单的消息在 pipeline 入站处直接丢弃：不回复、不入库、不计费。
// 保存后立即生效（pipeline.apply_blacklist），无需重启 Runtime。

import { h, fill, card, empty, formatNumber } from '../dom.js';
import { endpoints } from '../api.js';

export async function render(container, ctx) {
  let data = { enabled: false, qq_ids: [], group_ids: [] };
  let dirty = false;

  const qqInput = h('input', { placeholder: '输入 QQ 号后回车，可一次粘贴多个（逗号、空格、顿号、分号均可）' });
  const groupInput = h('input', { placeholder: '输入群号后回车，可一次粘贴多个' });
  const qqChips = h('div', { class: 'chips' });
  const groupChips = h('div', { class: 'chips' });
  const enableBox = h('input', { type: 'checkbox' });
  const statusLine = h('div', { class: 'field-help' });
  const saveBtn = h('button', { class: 'btn btn-primary', onclick: save }, '保存并立即生效');

  // ---------- 渲染 ----------

  function renderChips() {
    fill(qqChips, ...(data.qq_ids.length
      ? data.qq_ids.map((id) => chip(id, () => {
          data.qq_ids = data.qq_ids.filter((x) => x !== id);
          dirty = true;
          renderChips();
        }))
      : [h('span', { class: 'field-help' }, '还没有拉黑任何 QQ 号')]));

    fill(groupChips, ...(data.group_ids.length
      ? data.group_ids.map((id) => chip(id, () => {
          data.group_ids = data.group_ids.filter((x) => x !== id);
          dirty = true;
          renderChips();
        }))
      : [h('span', { class: 'field-help' }, '还没有拉黑任何群')]));
  }

  function chip(id, onRemove) {
    return h('span', { class: 'chip' }, String(id),
      h('button', { title: '移出黑名单', onclick: onRemove }, '×'));
  }

  function parseIds(raw) {
    return String(raw)
      .replace(/[，、;；\s]+/g, ',')
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean)
      .map((x) => Number(x))
      .filter((x) => Number.isFinite(x) && x > 0);
  }

  function addFrom(input, key) {
    const ids = parseIds(input.value);
    if (!ids.length) return;
    const merged = new Set([...(data[key] || []), ...ids]);
    data[key] = [...merged].sort((a, b) => a - b);
    input.value = '';
    dirty = true;
    renderChips();
  }

  qqInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addFrom(qqInput, 'qq_ids');
  });
  groupInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addFrom(groupInput, 'group_ids');
  });

  function renderStatus() {
    const total = (data.qq_ids?.length || 0) + (data.group_ids?.length || 0);
    if (!data.enabled) {
      statusLine.textContent = total
        ? `名单里有 ${total} 项，但开关关着 —— 当前不拦截任何人。`
        : '黑名单关闭中。';
      return;
    }
    const activeQq = (data.active_qq_ids || []).length;
    const activeGroup = (data.active_group_ids || []).length;
    statusLine.textContent = `生效中：拦截 ${formatNumber(activeQq)} 个 QQ 号、`
      + `${formatNumber(activeGroup)} 个群。保存后立即生效，无需重启。`;
  }

  // ---------- 保存 ----------

  async function save() {
    saveBtn.disabled = true;
    try {
      const result = await endpoints.saveBlacklist({
        enabled: enableBox.checked,
        qq_ids: data.qq_ids || [],
        group_ids: data.group_ids || [],
      });
      data = { ...data, ...result };
      dirty = false;
      renderStatus();
      ctx.toast(result.message || '已保存', 'ok');
    } catch (e) {
      ctx.toast(e.message, 'error');
    } finally {
      saveBtn.disabled = false;
    }
  }

  // ---------- 首次加载 ----------

  try {
    data = await endpoints.blacklist();
  } catch (e) {
    fill(container, card('黑名单', '', empty(`读取失败：${e.message}`)));
    return {};
  }

  enableBox.checked = !!data.enabled;
  enableBox.addEventListener('change', () => { dirty = true; renderStatus(); });
  renderChips();
  renderStatus();

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '黑名单'),
        h('p', { class: 'page-desc' },
          '点名拉黑 QQ 号或群。命中后消息在入站处直接丢弃——不回复、不入库、不消耗 API 费用。'),
      ),
      h('div', { class: 'row' }, saveBtn),
    ),

    card('总开关', '',
      h('label', { class: 'switch' }, enableBox, '启用黑名单'),
      h('div', { class: 'field-help', style: { marginTop: '8px' } },
        '关闭时名单内容保留但不生效，适合临时放行。'),
      h('div', { style: { marginTop: '10px' } }, statusLine),
    ),

    h('div', { class: 'grid grid-2' },
      card('拉黑的 QQ 号', '私聊直接丢弃；群聊中该成员的发言也不触发回复。',
        h('div', { class: 'row' },
          qqInput,
          h('button', { class: 'btn', onclick: () => addFrom(qqInput, 'qq_ids') }, '添加'),
        ),
        h('div', { style: { marginTop: '12px' } }, qqChips),
      ),
      card('拉黑的群', '该群所有消息一律丢弃。',
        h('div', { class: 'row' },
          groupInput,
          h('button', { class: 'btn', onclick: () => addFrom(groupInput, 'group_ids') }, '添加'),
        ),
        h('div', { style: { marginTop: '12px' } }, groupChips),
      ),
    ),

    card('与白名单的关系', '',
      h('ul', { style: { margin: 0, paddingLeft: '20px', color: 'var(--text-secondary)' } },
        h('li', {}, '黑名单优先级更高：先查黑名单，命中即丢弃，不再走白名单与审核。'),
        h('li', {}, '白名单模式设为 open（对所有人开放）时，黑名单依然拦得住被拉黑的对象。'),
        h('li', {}, '两者都在「设置 → 渠道」里，白名单改完需要重启，黑名单即时生效。'),
      ),
    ),
  );

  // 离开页面时如果有未保存改动，提示一下。
  window.addEventListener('beforeunload', (event) => {
    if (dirty) event.preventDefault();
  });

  return {};
}
