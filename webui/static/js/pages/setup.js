// 首次配置向导（多步版）。
//
// 对应原桌面端的 ui/wizard/window.py。步骤流转**由后端决定**
// （POST /api/setup/step → webui/wizard/flow.py），前端只渲染当前步、做基础校验、
// 把填好的数据攒在 state 里，最后一次性提交。
//
// 为什么不让前端判断下一步：两条路径（推荐 7 步 / 自定义 8 步）加上
// PERSONA_CREATE 分支，规则写两遍必然漂；flow.py 是原桌面端逐字搬过来的，可信。

import { h, fill, empty } from '../dom.js';
import { icon } from '../icons.js';
import { endpoints } from '../api.js';
import { STEP_VIEWS, buildPayload } from '../setup/steps.js';

export function render(container, ctx) {
  const state = {
    path: 'recommended',
    current: 'welcome',
    isLast: false,
    progress: [0, 1],
    stepsMeta: {},
    presets: [],
    personas: [],
    // 收集中的配置
    provider: {
      id: '', preset: null, display_name: '', protocol: 'openai_compat',
      base_url: '', model: '', api_key: '', reasoning_style: '', timeout_seconds: 120,
      has_key: false,
    },
    chat: { temperature: 0.6, top_p: 1.0, max_tokens: 16384, reasoning: false },
    agents: {},
    features: {},
    behavior: {},
    adapter: {
      mode: 'client', host: '127.0.0.1', port: 3000, path: '/',
      access_token: '', manage_process: false, process_path: '', auto_restart: true,
      whitelist: { mode: 'verify', qq_ids: [], group_ids: [] },
    },
    persona: 'debata',
    persona_source: 'builtin',
    persona_brief: '',
    // UI
    busy: false,
    error: '',
  };

  const progressBar = h('div', { class: 'wizard-progress-bar' });
  const progressText = h('span', { class: 'field-help' }, '');
  const titleEl = h('h2', { class: 'modal-title' }, '');
  const subtitleEl = h('p', { class: 'modal-sub' }, '');
  const stepHost = h('div', { class: 'wizard-body' });
  const errorBox = h('div', { class: 'gate-error', role: 'alert' });
  const backBtn = h('button', { class: 'btn' }, '上一步');
  const skipBtn = h('button', { class: 'btn btn-text' }, '跳过');
  const nextBtn = h('button', { class: 'btn btn-primary' }, '下一步');

  // 文案表来自 webui/wizard/copy.py（原桌面端逐字复制），按钮措辞与桌面端一致。
  // 拉不到就回落到上面写死的中文——文案接口挂掉不该让用户配不了。
  let COPY = {};

  let view = null;

  fill(container,
    h('div', { class: 'wizard' },
      h('div', { class: 'wizard-head' },
        progressBar,
        progressText,
        titleEl,
        subtitleEl,
      ),
      stepHost,
      errorBox,
      h('div', { class: 'wizard-foot' },
        backBtn,
        h('div', { class: 'spacer' }),
        skipBtn,
        nextBtn,
      ),
    ),
  );

  function applyCopy() {
    const text = (key, fallback) => COPY[key] || fallback;
    backBtn.textContent = text('button.back', '上一步');
    skipBtn.textContent = text('button.skip', '跳过');
    if (!state.isLast) nextBtn.textContent = text('button.next', '下一步');
  }

  async function boot() {
    stepHost.replaceChildren(h('div', { class: 'field-help' }, '正在准备…'));
    try {
      const options = await endpoints.setupOptions();
      state.presets = options.presets || [];
      state.personas = options.personas || [];
      for (const step of options.steps || []) state.stepsMeta[step.id] = step;
      const defaults = options.defaults || {};
      state.persona = defaults.persona || 'debata';
      state.provider.timeout_seconds = 120;
      if (defaults.preset) {
        state.provider.preset = defaults.preset;
        state.provider.id = `${defaults.preset}_main`;
      }
      if (defaults.model) state.provider.model = defaults.model;
      const adapter = defaults.adapter || {};
      Object.assign(state.adapter, {
        mode: adapter.mode || 'client',
        host: adapter.host || '127.0.0.1',
        port: adapter.port || 3000,
        path: adapter.path || '/',
      });
      state.adapter.whitelist.mode = adapter.whitelist_mode || 'verify';
    } catch (e) {
      fill(stepHost, empty('读不到向导选项', { subtitle: e.message, iconName: 'settings' }));
      return;
    }

    try {
      const tables = await endpoints.setupCopy();
      COPY = (tables.wizard && tables.wizard.COPY) || {};
    } catch {
      // 文案拉不到不是阻塞问题，按钮用回落的默认中文
    }
    applyCopy();

    await showStep(state.current);
  }

  async function askFlow(direction) {
    try {
      const result = await endpoints.setupStep({
        current: state.current,
        path: state.path,
        direction,
        context: { persona_source: state.persona_source },
      });
      return result;
    } catch (e) {
      state.error = e.message;
      renderError();
      return null;
    }
  }

  function renderError() {
    errorBox.textContent = state.error || '';
    nextBtn.disabled = state.busy || !!state.error;
  }

  async function showStep(stepId) {
    if (typeof view?.destroy === 'function') {
      try { view.destroy(); } catch (e) { console.error(e); }
    }

    state.current = stepId;
    state.error = '';

    const meta = state.stepsMeta[stepId] || { title: stepId, subtitle: '' };
    titleEl.textContent = meta.title;
    subtitleEl.textContent = meta.subtitle || '';
    skipBtn.classList.toggle('hidden', !meta.can_skip);

    const renderer = STEP_VIEWS[stepId];
    if (!renderer) {
      fill(stepHost, empty(`这一步还没实现：${stepId}`, {
        subtitle: '可以在配置生成后到设置页调整',
        iconName: 'settings',
      }));
      view = { validate: () => null };
    } else {
      view = renderer(stepHost, ctx, state) || { validate: () => null };
    }

    await refreshProgress();
    renderError();
    backBtn.disabled = state.current === 'welcome';
  }

  async function refreshProgress() {
    try {
      const flow = await endpoints.setupStep({
        current: state.current,
        path: state.path,
        direction: 'next',
        context: { persona_source: state.persona_source },
      });
      const [index, total] = flow.progress || [0, 1];
      state.isLast = !!flow.is_last;
      progressText.textContent = `第 ${index} / ${total} 步`;
      fill(progressBar, ...Array.from({ length: total }, (_, i) =>
        h('span', { class: `wizard-dot ${i < index ? 'done' : ''}` })));
      nextBtn.textContent = state.isLast
        ? (COPY['summary.start_button'] || '完成配置')
        : (COPY['button.next'] || '下一步');
      skipBtn.classList.toggle('hidden', !(state.stepsMeta[state.current] || {}).can_skip);
    } catch (e) {
      state.error = e.message;
      renderError();
    }
  }

  async function advance() {
    const problem = view?.validate ? view.validate() : null;
    if (problem) {
      state.error = problem;
      renderError();
      return;
    }
    state.error = '';

    if (state.isLast) {
      await submit();
      return;
    }

    const flow = await askFlow('next');
    if (!flow) return;
    if (!flow.next) {
      await submit();
      return;
    }
    await showStep(flow.next);
  }

  async function goBack() {
    const flow = await askFlow('prev');
    if (!flow || !flow.prev) return;
    await showStep(flow.prev);
  }

  async function skip() {
    const flow = await askFlow('next');
    if (!flow) return;
    if (!flow.next) {
      await submit();
      return;
    }
    await showStep(flow.next);
  }

  async function submit() {
    state.busy = true;
    nextBtn.textContent = '正在写入…';
    renderError();
    try {
      const payload = buildPayload(state);
      const result = await endpoints.setupApply(payload);
      ctx.toast(result.message || '配置已写入', result.started ? 'ok' : 'warn');
      await ctx.onDone();
    } catch (e) {
      state.error = e.message;
      state.busy = false;
      nextBtn.textContent = '完成配置';
      renderError();
    }
  }

  backBtn.onclick = goBack;
  skipBtn.onclick = skip;
  nextBtn.onclick = advance;

  boot();

  return {
    destroy() {
      if (typeof view?.destroy === 'function') view.destroy();
    },
  };
}
