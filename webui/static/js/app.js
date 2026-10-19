// Debata_Agent WebUI 外壳：登录 → （首次配置）→ 仪表盘路由。
//
// 导航结构照搬原 PySide6 仪表盘（ui/dashboard/layout.py 的 NAV_ITEMS），
// 顺序、中文标题一致，末尾追加 WebUI 新增的「黑名单」。

import { h, fill, qs, formatDuration, empty } from './dom.js';
import { api, endpoints, setUnauthorizedHandler } from './api.js';
import { icon } from './icons.js';
import { EventStream } from './ws.js';

import * as overview from './pages/overview.js';
import * as chats from './pages/chats.js';
import * as memory from './pages/memory.js';
import * as mind from './pages/mind.js';
import * as logs from './pages/logs.js';
import * as personas from './pages/personas.js';
import * as models from './pages/models.js';
import * as emoji from './pages/emoji.js';
import * as settings from './pages/settings.js';
import * as blacklist from './pages/blacklist.js';
import * as setup from './pages/setup.js';

// icon 字段是 icons.js 里的键名，不用 emoji（docs/ui_style_guide.md §六）。
const NAV_ITEMS = [
  { key: 'overview', title: '总览', icon: 'overview', page: overview },
  { key: 'chats', title: '对话', icon: 'chats', page: chats },
  { key: 'memory', title: '记忆', icon: 'memory', page: memory },
  { key: 'persona_mind', title: '人格后台', icon: 'persona_mind', page: mind },
  { key: 'logs', title: '日志', icon: 'logs', page: logs },
  { key: 'personas', title: '角色', icon: 'personas', page: personas },
  { key: 'models', title: '模型管理', icon: 'models', page: models },
  { key: 'emoji', title: '表情包', icon: 'emoji', page: emoji },
  { key: 'blacklist', title: '黑名单', icon: 'blacklist', page: blacklist },
  { key: 'settings', title: '设置', icon: 'settings', page: settings },
];

const TOAST_MS = 3200;

const state = {
  status: null,
  current: null,
  cleanup: null,
  stream: null,
};

// ============================================================
// 提示条
// ============================================================

function toast(message, kind = '') {
  const host = qs('#toast-host');
  const el = h('div', { class: `toast ${kind}` }, message);
  host.append(el);
  setTimeout(() => el.remove(), TOAST_MS);
}

// ============================================================
// 顶栏 / 侧栏状态
// ============================================================

function applyStatus(status) {
  state.status = status;

  const adapterBadge = qs('#adapter-badge');
  const connected = !!status.adapter_connected;
  const adapter = status.adapter;
  // 徽章四态对齐 docs/ui_style_guide.md §五：
  // ok 苔色绿实心 / warn 栀子黄实心 / bad 朱砂红实心 / idle 描边同色字
  if (status.state !== 'running') {
    const failed = status.state === 'error';
    adapterBadge.textContent = failed ? '启动失败' : '未运行';
    adapterBadge.className = `badge ${failed ? 'bad' : 'idle'}`;
  } else if (!adapter) {
    adapterBadge.textContent = '未配置渠道';
    adapterBadge.className = 'badge warn';
  } else {
    adapterBadge.textContent = connected
      ? `已就位 · ${adapter.mode}://${adapter.host}:${adapter.port}`
      : '渠道未连接';
    adapterBadge.className = `badge ${connected ? 'ok' : 'idle'}`;
  }

  qs('#persona-label').textContent = `当前角色：${status.persona || '—'}`;

  const dot = qs('#side-dot');
  const text = qs('#side-conn-text');
  if (status.state === 'running') {
    dot.className = 'dot ok';
    text.textContent = `运行中 · ${formatDuration(status.uptime_seconds)}`;
  } else if (status.state === 'error') {
    dot.className = 'dot bad';
    text.textContent = '启动失败';
  } else {
    dot.className = 'dot warn';
    text.textContent = 'Runtime 未运行';
  }
}

// ============================================================
// 路由
// ============================================================

function currentRouteKey() {
  const fromHash = location.hash.replace(/^#\/?/, '');
  return NAV_ITEMS.some((x) => x.key === fromHash) ? fromHash : 'overview';
}

async function navigate(key, { force = false } = {}) {
  const item = NAV_ITEMS.find((x) => x.key === key);
  if (!item) return;
  if (!force && state.current === key) return;

  if (typeof state.cleanup === 'function') {
    try { state.cleanup(); } catch (e) { console.error(e); }
    state.cleanup = null;
  }

  location.hash = `#/${key}`;

  for (const btn of document.querySelectorAll('.nav-item')) {
    btn.classList.toggle('active', btn.dataset.key === key);
  }

  const container = qs('#content');
  fill(container);

  const ctx = {
    toast,
    status: state.status,
    navigate,
    onEvent: (handler) => state.stream.subscribe(handler),
  };

  try {
    const result = await item.page.render(container, ctx);
    // 渲染成功才认账。之前是进函数就写 state.current，一旦某页渲染抛错，
    // state.current 已经等于该页 key，再点同一个导航项会被
    // `state.current === key` 提前 return，用户只能刷新页面才能重试。
    state.current = key;
    state.cleanup = result && result.destroy ? result.destroy : null;
  } catch (e) {
    console.error(e);
    state.current = null;
    state.cleanup = null;
    fill(container, h('div', { class: 'empty' }, `页面加载失败：${e.message}`));
  }
}

function buildNav() {
  const nav = qs('#nav');
  fill(nav, ...NAV_ITEMS.map((item) =>
    h('button', {
      class: 'nav-item',
      dataset: { key: item.key },
      onclick: () => navigate(item.key),
    },
      h('span', { class: 'nav-icon' }, icon(item.icon)),
      h('span', {}, item.title),
    ),
  ));
}

// ============================================================
// 主题
// ============================================================

function applyTheme(theme) {
  const resolved = theme === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;
  document.documentElement.dataset.theme = resolved;
  // 图标改成 SVG 后不能再靠 textContent 表示状态，同时更新 title 兜底
  const btn = qs('#btn-theme');
  fill(btn, icon(resolved === 'dark' ? 'sun' : 'moon', { size: 18 }));
  btn.title = resolved === 'dark' ? '切换到浅色主题' : '切换到深色主题';
  localStorage.setItem('debata-theme', theme);
}

function initTheme() {
  applyTheme(localStorage.getItem('debata-theme') || 'auto');
  window.matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', () => applyTheme(localStorage.getItem('debata-theme') || 'auto'));
}

// ============================================================
// 启动流程
// ============================================================

let loadingHintTimer = null;

async function boot() {
  initTheme();

  // 先把外壳亮出来再问后端。
  // 之前是 await 完 /api/auth/state 和 /api/status 才决定显示哪个界面，
  // 后端只要卡住（探测密钥、换模型、本机代理挂死），两个 await 都不 settle，
  // 三个顶层容器就一直是 hidden —— 页面纯白，且没有任何提示。
  showLoading();

  try {
    const auth = await endpoints.authState();
    if (auth.auth_enabled && !auth.authenticated) {
      showLogin();
      return;
    }
    await enterApp();
  } finally {
    clearTimeout(loadingHintTimer);
  }
}

function showLoading() {
  qs('#login-gate').classList.add('hidden');
  qs('#setup-gate').classList.add('hidden');
  qs('#shell').classList.remove('hidden');

  const content = qs('#content');
  fill(content, h('div', { class: 'page' },
    empty('正在连接 Debata 服务…', {
      subtitle: '启动 Runtime 可能要几秒，稍等一下。',
      iconName: 'overview',
    }),
  ));
  const hint = content.querySelector('.empty-sub');

  // 后端慢到 20 秒还没回应时补一句，让用户知道是后端卡住而不是浏览器死了。
  loadingHintTimer = setTimeout(() => {
    if (hint) hint.textContent = '还在等后端回应。若一直停在这里，请检查启动窗口是否报错。';
  }, 20000);
}

/** 连不上后端时的可见兜底：给出原因和「重试」，而不是留一片白。 */
function showBootError(error) {
  qs('#login-gate').classList.add('hidden');
  qs('#setup-gate').classList.add('hidden');
  qs('#shell').classList.remove('hidden');

  fill(qs('#content'), h('div', { class: 'page' },
    h('div', { class: 'empty' },
      icon('settings', { size: 32, className: 'empty-icon' }),
      h('p', { class: 'empty-title' }, '连不上 Debata 服务'),
      h('p', { class: 'empty-sub' }, error?.message || String(error)),
      h('button', {
        class: 'btn btn-primary',
        style: { marginTop: '20px' },
        onclick: () => location.reload(),
      }, '重试'),
    ),
  ));
}

function showLogin() {
  qs('#login-gate').classList.remove('hidden');
  qs('#shell').classList.add('hidden');
  qs('#setup-gate').classList.add('hidden');
  qs('#login-token').focus();

  qs('#login-form').onsubmit = async (event) => {
    event.preventDefault();
    const errorBox = qs('#login-error');
    errorBox.textContent = '';
    try {
      await endpoints.login(qs('#login-token').value.trim());
      qs('#login-gate').classList.add('hidden');
      await enterApp();
    } catch (e) {
      errorBox.textContent = e.message;
    }
  };
}

function showSetup() {
  qs('#login-gate').classList.add('hidden');
  qs('#shell').classList.add('hidden');
  qs('#setup-gate').classList.remove('hidden');
  setup.render(qs('#setup-body'), {
    toast,
    onDone: async () => {
      qs('#setup-gate').classList.add('hidden');
      await enterApp();
    },
  });
}

async function enterApp() {
  const status = await endpoints.status();

  if (status.setup_required) {
    showSetup();
    return;
  }

  qs('#setup-gate').classList.add('hidden');
  qs('#shell').classList.remove('hidden');

  if (!state.stream) {
    state.stream = new EventStream();
    state.stream.onConnectionChange = (connected) => {
      if (!connected && state.status) {
        qs('#side-dot').className = 'dot bad';
        qs('#side-conn-text').textContent = '控制台连接断开，重连中…';
      }
    };
    // 外壳只关心 status；chat / log 由各页面通过 ctx.onEvent 自行订阅。
    state.stream.subscribe((event) => {
      if (event.type === 'status') applyStatus(event.data);
    });
    state.stream.connect();
  }

  buildNav();
  applyStatus(status);

  if (status.last_error) {
    toast(`Runtime 启动失败：${status.last_error}`, 'error');
  }
  for (const warning of status.warnings || []) {
    toast(warning, 'warn');
  }

  await navigate(currentRouteKey(), { force: true });
}

function wireChrome() {
  window.addEventListener('hashchange', () => navigate(currentRouteKey()));

  // 顶栏图标（index.html 里只留空的 button，图标在这里挂，才能复用 icons.js）
  fill(qs('#btn-restart'), icon('restart', { size: 18 }));
  fill(qs('#btn-logout'), icon('logout', { size: 18 }));
  applyTheme(localStorage.getItem('debata-theme') || 'auto');

  qs('#btn-theme').onclick = () => {
    const order = ['auto', 'light', 'dark'];
    const now = localStorage.getItem('debata-theme') || 'auto';
    const next = order[(order.indexOf(now) + 1) % order.length];
    applyTheme(next);
    // 顺手同步到配置，让同一实例的其它客户端/桌面端保持一致。
    if (state.status && state.status.state === 'running') {
      api.patch('/api/settings', { path: 'app.theme', value: next }).catch(() => {});
    }
  };

  qs('#btn-restart').onclick = async (event) => {
    const btn = event.currentTarget;
    if (!confirm('重启 Runtime？正在处理的对话会被中断。')) return;
    btn.disabled = true;
    try {
      await endpoints.restart();
      toast('Runtime 已重启', 'ok');
      applyStatus(await endpoints.status());
      await navigate(state.current, { force: true });
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  };

  qs('#btn-logout').onclick = async () => {
    try { await endpoints.logout(); } catch { /* 已失效也无所谓 */ }
    if (state.stream) { state.stream.close(); state.stream = null; }
    showLogin();
  };

  setUnauthorizedHandler(() => {
    if (state.stream) { state.stream.close(); state.stream = null; }
    showLogin();
  });
}

wireChrome();
boot().catch((e) => {
  console.error(e);
  // 后端已经在跑了（拿到了响应）但启动流程出错：留在控制台里报错，
  // 不要整页替换掉——那样连侧栏都没了，看着也像白屏。
  showBootError(e);
});
