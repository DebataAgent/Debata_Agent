// 极简 DOM 构建工具。整个前端不引入任何框架和构建步骤，
// 保证在 ARM 板子上打开浏览器就能用。

import { icon } from './icons.js';

/**
 * 创建元素。
 *   h('div', {class: 'card'}, h('p', {}, '正文'))
 *   h('button', {onclick: fn, disabled: true}, '点我')
 * 值为 null / undefined / false 的子节点会被跳过。
 */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class' || key === 'className') el.className = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'value' || key === 'checked' || key === 'disabled'
               || key === 'selected' || key === 'placeholder') {
      el[key] = value;
    } else {
      el.setAttribute(key, value === true ? '' : String(value));
    }
  }

  append(el, children);
  return el;
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

/** 清空并重新填充容器。 */
export function fill(parent, ...children) {
  parent.replaceChildren();
  append(parent, children);
  return parent;
}

export function qs(selector, root = document) {
  return root.querySelector(selector);
}

/** 防抖：输入框实时搜索用。 */
export function debounce(fn, wait = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/** 把 ISO / unix 时间戳统一成 HH:MM:SS。 */
export function formatTime(value) {
  if (!value) return '';
  let date;
  if (typeof value === 'number') date = new Date(value * 1000);
  else {
    const asNumber = Number(value);
    date = Number.isFinite(asNumber) && String(value).length >= 10
      ? new Date(asNumber * 1000)
      : new Date(value);
  }
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分`;
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

export function formatNumber(value) {
  const num = Number(value) || 0;
  if (num >= 1e9) return `${(num / 1e9).toFixed(2)}B`;
  if (num >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
  if (num >= 1e4) return `${(num / 1e4).toFixed(2)}万`;
  return String(num);
}

/** 卡片外壳。 */
export function card(title, subtitle, ...children) {
  return h('section', { class: 'card' },
    title ? h('h2', { class: 'card-title' }, title) : null,
    subtitle ? h('p', { class: 'card-sub' }, subtitle) : null,
    ...children,
  );
}

export function statCard(label, value, extra = null) {
  return h('div', { class: 'card' },
    h('div', { class: 'stat-label' }, label),
    h('div', { class: 'stat-value' }, value),
    extra,
  );
}

/**
 * 空状态。docs/ui_style_guide.md §八：给一句静的话，不要喋喋不休。
 *
 *   empty('日志为空')                                  单行（旧用法，保持兼容）
 *   empty('暂无对话', {subtitle: '…', iconName: 'chats'})  图标 + 标题 + 副标题
 */
export function empty(message, { subtitle = '', iconName = '' } = {}) {
  if (!subtitle && !iconName) return h('div', { class: 'empty' }, message);
  return h('div', { class: 'empty' },
    iconName ? icon(iconName, { size: 32, className: 'empty-icon' }) : null,
    h('p', { class: 'empty-title' }, message),
    subtitle ? h('p', { class: 'empty-sub' }, subtitle) : null,
  );
}

/**
 * 弹窗。返回 { el, close }，调用方负责把它挂到 body 上并在需要时移除。
 *
 * 遮罩点击 = 关闭；Esc 由调用方决定是否绑定（比如表单未保存时不该关）。
 */
export function modal({ title, subtitle = '', body, footer = null, onClose = null, wide = false }) {
  const backdrop = h('div', { class: 'modal-backdrop' });
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  const onKey = (event) => {
    if (event.key === 'Escape') close();
  };

  const box = h('div', { class: wide ? 'modal modal-wide' : 'modal' },
    h('div', { class: 'modal-head' },
      h('h2', { class: 'modal-title' }, title),
      subtitle ? h('p', { class: 'modal-sub' }, subtitle) : null,
    ),
    h('div', { class: 'modal-body' }, body),
    footer ? h('div', { class: 'modal-foot' }, footer) : null,
  );

  backdrop.append(box);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });
  document.addEventListener('keydown', onKey);
  return { el: backdrop, close };
}

/** 带标题和说明的表单字段。 */
export function field(label, control, help = '') {
  return h('label', { class: 'field' },
    h('span', { class: 'field-label' }, label),
    control,
    help ? h('div', { class: 'field-help' }, help) : null,
  );
}

export function table(headers, rows) {
  return h('table', { class: 'table' },
    h('thead', {}, h('tr', {}, ...headers.map((x) => h('th', {}, x)))),
    h('tbody', {}, ...rows),
  );
}

/** Tailwind 风格的状态色映射，跟 theme.py 的 success/warning/error 对齐。 */
export function statusClass(kind) {
  return { ok: 'ok', healthy: 'ok', success: 'ok',
           degraded: 'warn', warning: 'warn',
           error: 'bad', unreadable: 'bad' }[kind] || '';
}
