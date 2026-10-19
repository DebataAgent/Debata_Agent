// 模型管理页 —— 对应 ui/dashboard/models_page.py。
// 本地插件（TTS / Embedding）的扫描、状态、安装指引与依赖安装。

import { h, fill, card, empty } from '../dom.js';
import { endpoints } from '../api.js';

const STATUS_CLASS = {
  ENABLED: 'ok',
  INSTALLED: 'ok',
  NOT_INSTALLED: 'warn',
  ERROR: 'bad',
};

export async function render(container, ctx) {
  let models = [];
  let selected = null;
  let disposed = false;

  const listBox = h('div', {});
  const detailBox = h('div', {});

  async function load(rescan = false) {
    fill(listBox, empty(rescan ? '正在重新扫描…' : '读取中…'));
    try {
      const data = rescan ? await endpoints.scanModels() : await endpoints.models();
      models = data.models || [];
      renderList();
      if (selected) {
        const still = models.find((m) => m.name === selected);
        if (still) renderDetail(still);
      }
    } catch (e) {
      fill(listBox, empty(`读取失败：${e.message}`));
    }
  }

  function renderList() {
    if (!models.length) {
      fill(listBox, empty('没有发现本地模型插件'));
      return;
    }
    fill(listBox, h('table', { class: 'table' },
      h('thead', {}, h('tr', {},
        h('th', {}, '模型'), h('th', {}, '类型'), h('th', {}, '状态'), h('th', {}, '依赖'))),
      h('tbody', {}, ...models.map((m) =>
        h('tr', {
          style: { cursor: 'pointer' },
          onclick: () => { selected = m.name; renderDetail(m); },
        },
          h('td', {}, h('strong', {}, m.display_name)),
          h('td', { class: 'mono' }, m.kind || '—'),
          h('td', {}, h('span', { class: `badge ${STATUS_CLASS[m.status] || ''}` },
            m.status_text)),
          h('td', {},
            m.missing_deps && m.missing_deps.length
              ? h('span', { class: 'badge warn' }, `缺 ${m.missing_deps.length} 个`)
              : h('span', { class: 'field-help' }, '齐全')),
        ),
      )),
    ));
  }

  function renderDetail(model) {
    const uploadInput = h('input', {
      type: 'file',
      accept: '.zip',
      style: { display: 'none' },
      onchange: async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        try {
          const result = await endpoints.uploadModel(model.name, form);
          ctx.toast(result.message, 'ok');
          await load(true);
        } catch (e) {
          ctx.toast(e.message, 'error');
        } finally {
          event.target.value = '';
        }
      },
    });

    fill(detailBox,
      card(model.display_name, model.description || '',
        h('table', { class: 'table' },
          h('tbody', {},
            h('tr', {}, h('td', {}, '状态'),
              h('td', {}, h('span', { class: `badge ${STATUS_CLASS[model.status] || ''}` },
                model.status_text))),
            h('tr', {}, h('td', {}, '类型'), h('td', { class: 'mono' }, model.kind || '—')),
            h('tr', {}, h('td', {}, '目标目录'),
              h('td', { class: 'mono' }, model.model_dir || '—')),
            model.download_url
              ? h('tr', {}, h('td', {}, '下载页'),
                  h('td', {}, h('a', { href: model.download_url, target: '_blank',
                    rel: 'noreferrer' }, model.download_url)))
              : null,
            model.error ? h('tr', {}, h('td', {}, '错误'),
              h('td', { style: { color: 'var(--error)' } }, model.error)) : null,
          )),
        h('div', { class: 'row row-wrap', style: { marginTop: '16px' } },
          h('button', {
            class: 'btn',
            onclick: async () => {
              try {
                const data = await endpoints.modelGuide(model.name);
                showGuide(data);
              } catch (e) {
                ctx.toast(e.message, 'error');
              }
            },
          }, '安装指引'),
          h('button', {
            class: 'btn',
            onclick: () => uploadInput.click(),
          }, '上传模型 zip'),
          uploadInput,
          (model.missing_deps && model.missing_deps.length)
            ? h('button', {
                class: 'btn btn-primary',
                onclick: async (event) => {
                  event.currentTarget.disabled = true;
                  try {
                    const result = await endpoints.installModelDeps(model.name, 'tsinghua');
                    ctx.toast(result.message, 'ok');
                  } catch (e) {
                    ctx.toast(e.message, 'error');
                  } finally {
                    event.currentTarget.disabled = false;
                  }
                },
              }, `安装依赖（${model.missing_deps.join(', ')}）`)
            : null,
        ),
      ),
    );
  }

  function showGuide(data) {
    const dialog = h('div', { class: 'gate' },
      h('div', { class: 'gate-card gate-card-wide' },
        h('div', { class: 'gate-brand' }, '安装指引'),
        h('pre', {
          style: { maxHeight: '55vh', overflow: 'auto', background: 'var(--bg-primary)',
                   padding: '14px', borderRadius: '8px', whiteSpace: 'pre-wrap' },
        }, data.markdown),
        h('button', { class: 'btn btn-primary btn-block', onclick: () => dialog.remove() }, '知道了'),
      ),
    );
    document.body.append(dialog);
  }

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '模型管理'),
        h('p', { class: 'page-desc' },
          '本地语音合成 / 向量模型。装依赖或导入模型后点「重新扫描」刷新状态。'),
      ),
      h('button', { class: 'btn', onclick: () => load(true) }, '重新扫描'),
    ),
    card('', '', listBox),
    detailBox,
  );

  await load();
  return { destroy() { disposed = true; void disposed; } };
}
