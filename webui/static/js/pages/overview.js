// 总览页 —— 对应 ui/dashboard/overview_page.py 的 4 张统计卡：
// 主模型状态 / 模型健康 / 渠道状态 / 用量统计。

import { h, fill, card, statCard, formatNumber, formatDuration, statusClass } from '../dom.js';
import { endpoints } from '../api.js';

const REFRESH_MS = 2000;
const RANGES = [['today', '今天'], ['7d', '7 天'], ['30d', '30 天'], ['all', '全部']];

const ACTIVITY_TEXT = {
  idle: '空闲',
  thinking: '思考中',
  tool: '调用工具中',
  error: '出错了',
};

export async function render(container, ctx) {
  let range = 'today';
  let timer = null;
  let disposed = false;

  const activityCard = h('div', { class: 'card' });
  const providerCard = h('div', { class: 'card' });
  const adapterCard = h('div', { class: 'card' });
  const usageCard = h('div', { class: 'card' });

  const rangeBar = h('div', { class: 'row' },
    ...RANGES.map(([value, label]) =>
      h('button', {
        class: `btn btn-sm ${value === range ? 'btn-primary' : ''}`,
        dataset: { range: value },
        onclick: (event) => {
          range = value;
          for (const btn of rangeBar.querySelectorAll('button')) {
            btn.classList.toggle('btn-primary', btn.dataset.range === range);
          }
          refresh();
        },
      }, label),
    ),
  );

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '总览'),
        h('p', { class: 'page-desc' }, '运行状态、模型健康与用量。数据每 2 秒自动刷新。'),
      ),
      h('button', {
        class: 'btn',
        onclick: async (event) => {
          event.currentTarget.disabled = true;
          try {
            await endpoints.restart();
            ctx.toast('Runtime 已重启', 'ok');
          } catch (e) {
            ctx.toast(e.message, 'error');
          } finally {
            event.currentTarget.disabled = false;
            refresh();
          }
        },
      }, '重启 Runtime'),
    ),

    h('div', { class: 'grid grid-2' }, activityCard, adapterCard),
    providerCard,
    usageCard,
  );

  function renderActivity(activity) {
    const state = activity.state || 'idle';
    const cls = state === 'error' ? 'bad' : (state === 'idle' ? '' : 'ok');
    fill(activityCard,
      h('div', { class: 'stat-label' }, '主模型状态'),
      h('div', { class: 'stat-value small' },
        h('span', { class: `badge ${cls}` }, ACTIVITY_TEXT[state] || state)),
      h('div', { class: 'field-help' },
        activity.agent ? `${activity.agent} · ${activity.model || '未指定模型'}` : '—'),
      activity.text ? h('div', { class: 'field-help' }, activity.text) : null,
    );
  }

  function renderAdapter(status) {
    const adapter = status.adapter;
    const connected = !!status.adapter_connected;
    fill(adapterCard,
      h('div', { class: 'stat-label' }, '渠道状态'),
      h('div', { class: 'stat-value small' },
        h('span', { class: `badge ${connected ? 'ok' : (status.state === 'running' ? 'bad' : 'warn')}` },
          connected ? '已就位' : (status.state === 'running' ? '未连接' : '未运行'))),
      h('div', { class: 'field-help' },
        adapter
          ? `${adapter.name} · ${adapter.mode} · ${adapter.host}:${adapter.port}${adapter.path}`
          : '未配置渠道'),
      h('div', { class: 'field-help' }, `已运行 ${formatDuration(status.uptime_seconds)}`),
    );
  }

  function renderProviders(providers) {
    if (!providers.length) {
      fill(providerCard,
        h('h2', { class: 'card-title' }, '模型健康'),
        h('div', { class: 'empty' }, '还没有配置任何模型提供商'));
      return;
    }
    fill(providerCard,
      h('h2', { class: 'card-title' }, '模型健康'),
      h('p', { class: 'card-sub' }, '启动时探测的连通性与延迟。'),
      h('table', { class: 'table' },
        h('thead', {}, h('tr', {},
          h('th', {}, 'Provider'), h('th', {}, '状态'), h('th', {}, '延迟'), h('th', {}, '说明'))),
        h('tbody', {}, ...providers.map((p) =>
          h('tr', {},
            h('td', { class: 'mono' }, p.id),
            h('td', {}, h('span', { class: `badge ${statusClass(p.status)}` }, p.text)),
            h('td', {}, p.latency_ms != null ? `${Math.round(p.latency_ms)} ms` : '—'),
            h('td', { class: 'field-help' }, p.message || '—'),
          ),
        )),
      ),
    );
  }

  function renderUsage(usage) {
    if (!usage.available) {
      fill(usageCard,
        h('h2', { class: 'card-title' }, '用量统计'),
        h('div', { class: 'empty' }, usage.error || '暂无用量数据'),
        rangeBar);
      return;
    }
    const hit = Number(usage.cache_hit_rate || 0);
    const estimateSamples = Number(usage.estimate_samples || 0);
    const estimateError = Number(usage.estimate_error_rate || 0);
    const estimateText = estimateSamples > 0
      ? `${(estimateError * 100).toFixed(1)}%`
      : '—';
    fill(usageCard,
      h('h2', { class: 'card-title' }, '用量统计'),
      h('p', { class: 'card-sub' }, 'Token 消耗、缓存命中与估算准确性。'),
      rangeBar,
      h('div', { class: 'grid grid-4', style: { marginTop: '16px' } },
        statCard('请求数', formatNumber(usage.request_count)),
        statCard('总 token', formatNumber(usage.total_tokens)),
        statCard('缓存命中率', `${(hit <= 1 ? hit * 100 : hit).toFixed(1)}%`),
        statCard('输出 token', formatNumber(usage.completion_tokens)),
      ),
      h('div', { class: 'grid grid-4', style: { marginTop: '16px' } },
        statCard('输入 token', formatNumber(usage.prompt_tokens)),
        statCard('未命中输入 token', formatNumber(usage.uncached_prompt_tokens)),
        statCard('命中缓存 token', formatNumber(usage.cached_tokens)),
        statCard('写入缓存 token', formatNumber(usage.cache_creation_tokens)),
      ),
      h('div', { class: 'grid grid-4', style: { marginTop: '16px' } },
        statCard('推理 token', formatNumber(usage.reasoning_tokens)),
        statCard('估算误差', estimateText),
        statCard('估算样本', formatNumber(estimateSamples)),
        statCard('估算输入 token', formatNumber(usage.estimated_prompt_tokens)),
      ),
    );
  }

  async function refresh() {
    if (disposed) return;
    try {
      const data = await endpoints.overview(range);
      if (disposed) return;
      renderActivity(data.activity);
      renderAdapter(data.status);
      renderProviders(data.providers || []);
      renderUsage(data.usage || { available: false });
    } catch (e) {
      if (disposed) return;
      fill(activityCard,
        h('div', { class: 'stat-label' }, '主模型状态'),
        h('div', { class: 'stat-value small' }, h('span', { class: 'badge bad' }, '不可用')),
        h('div', { class: 'field-help' }, e.message),
      );
    }
  }

  await refresh();
  timer = setInterval(refresh, REFRESH_MS);

  // Runtime 状态变化时立刻刷新一次，不用等下一个轮询周期。
  const unsubscribe = ctx.onEvent((event) => {
    if (event.type === 'status') refresh();
  });

  return {
    destroy() {
      disposed = true;
      clearInterval(timer);
      unsubscribe();
    },
  };
}
