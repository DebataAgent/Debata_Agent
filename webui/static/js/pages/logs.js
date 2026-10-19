// 日志页 —— 对应 ui/dashboard/logs_page.py。
// 首屏拉历史，之后靠 /ws/events 的 log 事件实时追加。

import { h, fill, card, empty, debounce } from '../dom.js';
import { endpoints } from '../api.js';

const LEVELS = [['', '全部'], ['DEBUG', '调试以上'], ['INFO', '信息以上'],
                ['WARNING', '留意以上'], ['ERROR', '出错']];
const MAX_LINES = 2000;

export async function render(container, ctx) {
  let level = 'INFO';
  let module = '';
  let keyword = '';
  let paused = false;
  let disposed = false;

  const list = h('div', { class: 'log-list' });
  const countLabel = h('span', { class: 'field-help' });

  const levelSelect = h('select', { style: { maxWidth: '130px' } },
    ...LEVELS.map(([value, label]) =>
      h('option', { value, selected: value === level }, label)));
  levelSelect.addEventListener('change', () => {
    level = levelSelect.value;
    reload();
  });

  const moduleInput = h('input', {
    placeholder: '模块前缀，如 core.runtime',
    style: { maxWidth: '220px' },
    oninput: debounce((event) => {
      module = event.target.value.trim();
      reload();
    }, 250),
  });

  const searchInput = h('input', {
    type: 'search',
    placeholder: '搜索正文…',
    style: { maxWidth: '200px' },
    oninput: debounce((event) => {
      keyword = event.target.value.trim();
      reload();
    }, 250),
  });

  const pauseBox = h('input', { type: 'checkbox' });
  pauseBox.addEventListener('change', () => { paused = pauseBox.checked; });

  function matches(record) {
    if (module && !String(record.logger || '').toLowerCase().includes(module.toLowerCase())) {
      return false;
    }
    if (keyword) {
      const needle = keyword.toLowerCase();
      if (!String(record.message || '').toLowerCase().includes(needle)
          && !String(record.logger || '').toLowerCase().includes(needle)) {
        return false;
      }
    }
    return true;
  }

  function lineNode(record) {
    const node = h('div', { class: 'log-line' },
      h('span', { class: 'log-time' }, record.time_text || ''),
      h('span', { class: `log-level ${record.level}` }, record.level),
      h('span', { class: 'log-logger' }, record.logger || ''),
      h('span', {}, record.message || ''),
    );
    if (record.exc) {
      node.append(h('pre', { style: { margin: '4px 0 0', whiteSpace: 'pre-wrap' } }, record.exc));
    }
    return node;
  }

  function append(record) {
    if (paused || !matches(record)) return;
    list.append(lineNode(record));
    while (list.childElementCount > MAX_LINES) list.firstElementChild.remove();
    list.scrollTop = list.scrollHeight;
    countLabel.textContent = `显示 ${list.childElementCount} 行（缓冲上限 ${MAX_LINES}）`;
  }

  async function reload() {
    list.replaceChildren();
    try {
      const data = await endpoints.logs({ limit: 500, level, module, q: keyword });
      if (disposed) return;
      const records = data.records || [];
      for (const record of records) list.append(lineNode(record));
      list.scrollTop = list.scrollHeight;
      countLabel.textContent = records.length
        ? `显示最近 ${records.length} 行（缓冲共 ${data.buffer_size || 0} 行）`
        : '没有符合条件的历史日志，等新的日志进来…';
    } catch (e) {
      fill(list, empty(`读取日志失败：${e.message}`));
    }
  }

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '日志'),
        h('p', { class: 'page-desc' }, '实时跟随 root logger 输出，等级 / 模块 / 关键字三重过滤。'),
      ),
      h('div', { class: 'row row-wrap' },
        levelSelect, moduleInput, searchInput,
        h('label', { class: 'switch' }, pauseBox, '暂停滚动'),
        h('button', { class: 'btn', onclick: () => list.replaceChildren() }, '清屏'),
        h('button', { class: 'btn', onclick: reload }, '重新加载'),
      ),
    ),
    card('', '', countLabel, list),
  );

  await reload();

  const unsubscribe = ctx.onEvent((event) => {
    if (event.type === 'log') append(event.data);
  });

  return {
    destroy() {
      disposed = true;
      unsubscribe();
    },
  };
}
