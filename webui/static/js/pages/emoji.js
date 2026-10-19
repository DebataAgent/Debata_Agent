// 表情包管理页。
//
// 对应原 GUI 的 ui/dashboard/emoji_section.py（原先是设置页「功能」里的一块），
// WebUI 独立成页，能力对齐：
//   列表 + 计数、缩略图网格、添加（多选 / 拖放 / 逐个改名 / 重名自动去重）、
//   重命名、删除（二次确认）、刷新。
//
// 与浏览器环境的差异：拖文件夹只能拿到空的 File（浏览器不给内容），
// 所以不做 Qt 那样的目录递归，遇到就明确提示。

import { h, fill, empty, card, modal, debounce } from '../dom.js';
import { icon } from '../icons.js';
import { endpoints } from '../api.js';

const ACCEPT = '.png,.jpg,.jpeg,.gif';

export async function render(container, ctx) {
  const state = {
    items: [],
    query: '',
    dir: '',
    supported: [],
    maxBytes: 0,
    destroyed: false,
  };

  const page = h('div', { class: 'page' });
  container.append(page);

  // 用 objectURL 做本地预览；切页时要全部回收，否则泄漏
  const objectUrls = new Set();
  const trackUrl = (blob) => {
    const url = URL.createObjectURL(blob);
    objectUrls.add(url);
    return url;
  };

  function releaseUrls() {
    for (const url of objectUrls) URL.revokeObjectURL(url);
    objectUrls.clear();
  }

  // ===== 头部 =====
  const searchInput = h('input', {
    type: 'search',
    placeholder: '按名称筛选',
    style: { maxWidth: '220px' },
    oninput: debounce((event) => {
      state.query = event.target.value.trim().toLowerCase();
      renderGrid();
    }, 200),
  });

  const addBtn = h('button', {
    class: 'btn btn-primary',
    onclick: () => openAddDialog(),
  }, icon('add', { size: 16 }), '添加表情包');

  const refreshBtn = h('button', {
    class: 'btn',
    onclick: () => load(),
  }, icon('refresh', { size: 16 }), '刷新');

  const head = h('div', { class: 'page-head' },
    h('div', {},
      h('h1', { class: 'page-title' }, '表情包'),
      h('p', { class: 'page-desc' },
        '角色在聊天中可以发送这些图片。名称就是 AI 调用时用的名字，别用太生僻的字。'),
    ),
    h('div', { class: 'row' }, searchInput, refreshBtn, addBtn),
  );

  const gridHost = h('div', {});
  const infoCard = card('读取中…', '',
    h('div', { class: 'stat-label' }, '存放目录'),
    h('div', { class: 'mono', style: { marginTop: '4px', wordBreak: 'break-all' } }, '—'),
  );
  const dirText = infoCard.querySelector('.mono');

  page.append(head, infoCard, gridHost);

  // ===== 加载列表 =====
  async function load() {
    try {
      const data = await endpoints.emoji();
      if (state.destroyed) return;
      state.items = data.items || [];
      state.dir = data.dir || '';
      state.supported = data.supported_exts || [];
      state.maxBytes = data.max_upload_bytes || 0;
      infoCard.querySelector('.card-title').textContent =
        `共 ${state.items.length} 个表情包`;
      dirText.textContent = state.dir;
      renderGrid();
    } catch (e) {
      if (state.destroyed) return;
      fill(gridHost, empty('表情包没能读出来', { subtitle: e.message, iconName: 'emoji' }));
    }
  }

  // ===== 网格 =====
  function visibleItems() {
    if (!state.query) return state.items;
    return state.items.filter((x) => x.name.toLowerCase().includes(state.query));
  }

  function renderGrid() {
    const items = visibleItems();
    if (!items.length) {
      fill(gridHost, state.items.length
        ? empty('没有匹配的表情包', { subtitle: '换个词试试', iconName: 'search' })
        : empty('还没有表情包', {
            subtitle: '把常用的图片拖进来，角色聊天时就能用了',
            iconName: 'emoji',
          }));
      return;
    }
    fill(gridHost, h('div', { class: 'emoji-grid' }, ...items.map(tile)));
  }

  function tile(item) {
    return h('div', { class: 'emoji-tile' },
      h('img', {
        src: `/api/emoji/file/${encodeURIComponent(item.file)}`,
        alt: item.name,
        loading: 'lazy',
      }),
      h('div', { class: 'emoji-name', title: item.name }, item.name),
      h('div', { class: 'emoji-actions' },
        h('button', {
          class: 'btn btn-sm',
          title: '改名',
          onclick: () => startRename(item),
        }, icon('edit', { size: 14 }), '改名'),
        h('button', {
          class: 'btn btn-sm btn-danger',
          title: '删除',
          onclick: () => removeItem(item),
        }, icon('delete', { size: 14 })),
      ),
    );
  }

  // ===== 改名 =====
  async function startRename(item) {
    const next = prompt(`把「${item.name}」改成：`, item.name);
    if (next === null) return;
    const trimmed = next.trim();
    if (!trimmed || trimmed === item.name) return;
    try {
      const result = await endpoints.renameEmoji(item.name, trimmed);
      ctx.toast(`已改名为 ${result.to}`, 'ok');
      await load();
    } catch (e) {
      ctx.toast(e.message, 'error');
    }
  }

  // ===== 删除 =====
  async function removeItem(item) {
    if (!confirm(`删除「${item.name}」？此操作不可撤销。`)) return;
    try {
      await endpoints.deleteEmoji(item.name);
      ctx.toast('已删除', 'ok');
      await load();
    } catch (e) {
      ctx.toast(e.message, 'error');
    }
  }

  // ===== 添加 =====
  function openAddDialog() {
    // 暂存区：{file, name, url}
    const staged = [];

    const listHost = h('div', { style: { display: 'grid', gap: '8px' } });
    const dropzone = h('div', { class: 'dropzone' },
      icon('upload', { size: 24 }),
      h('div', { style: { marginTop: '6px' } }, '把图片拖到这里，或点击选择'),
      h('div', { class: 'field-help' },
        `支持 ${ACCEPT.replace(/\./g, '').toUpperCase()}，单个不超过 5MB`),
    );
    const fileInput = h('input', {
      type: 'file',
      multiple: true,
      accept: ACCEPT,
      style: { display: 'none' },
      onchange: (event) => {
        stageFiles([...event.target.files]);
        event.target.value = '';
      },
    });

    dropzone.append(fileInput);
    dropzone.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('dragover', (event) => {
      event.preventDefault();
      dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (event) => {
      event.preventDefault();
      dropzone.classList.remove('dragover');
      const dropped = [...(event.dataTransfer?.files || [])];
      if (dropped.some((f) => f.type === '' && f.size === 0)) {
        ctx.toast('浏览器不允许拖入文件夹，请打开文件夹后选中图片再拖', 'warn');
        return;
      }
      stageFiles(dropped);
    });

    function stageFiles(files) {
      for (const file of files) {
        const ext = `.${file.name.split('.').pop().toLowerCase()}`;
        if (!state.supported.includes(ext)) {
          ctx.toast(`${file.name}：不支持的格式`, 'warn');
          continue;
        }
        staged.push({
          file,
          name: file.name.replace(/\.[^.]+$/, ''),
          url: trackUrl(file),
        });
      }
      renderStaged();
    }

    function renderStaged() {
      if (!staged.length) {
        fill(listHost, h('div', { class: 'field-help' }, '还没有选择文件'));
        return;
      }
      fill(listHost, ...staged.map((entry, index) =>
        h('div', { class: 'row', style: { gap: '8px' } },
          h('img', {
            src: entry.url,
            alt: entry.name,
            style: {
              width: '40px', height: '40px', objectFit: 'contain',
              border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
              flex: '0 0 40px',
            },
          }),
          h('input', {
            value: entry.name,
            placeholder: '名称',
            oninput: (event) => { entry.name = event.target.value; },
          }),
          h('button', {
            class: 'btn btn-sm',
            title: '移出列表',
            onclick: () => {
              staged.splice(index, 1);
              renderStaged();
            },
          }, icon('close', { size: 14 })),
        ),
      ));
    }

    const submitBtn = h('button', { class: 'btn btn-primary' }, '添加');
    const cancelBtn = h('button', { class: 'btn' }, '算了');

    const dialog = modal({
      title: '添加表情包',
      subtitle: '可以为每个文件改个名字——AI 就是用这个名字调用的',
      wide: true,
      body: h('div', {}, dropzone, h('div', { style: { marginTop: '16px' } }, listHost)),
      footer: [cancelBtn, submitBtn],
      onClose: () => releaseUrls(),
    });

    cancelBtn.onclick = () => dialog.close();

    submitBtn.onclick = async () => {
      if (!staged.length) {
        ctx.toast('还没有选择文件', 'warn');
        return;
      }
      const form = new FormData();
      const rename = {};
      for (const entry of staged) {
        form.append('files', entry.file, entry.file.name);
        rename[entry.file.name] = entry.name.trim() || entry.file.name;
      }
      form.append('rename', JSON.stringify(rename));

      submitBtn.disabled = true;
      submitBtn.textContent = '正在添加…';
      try {
        const result = await endpoints.uploadEmoji(form);
        const added = result.count || 0;
        const skipped = result.skipped || [];
        const deduped = result.deduped || [];
        ctx.toast(
          skipped.length ? `已添加 ${added} 个，跳过 ${skipped.length} 个` : `已添加 ${added} 个`,
          skipped.length ? 'warn' : 'ok',
        );
        for (const item of skipped) ctx.toast(`${item.file}：${item.reason}`, 'warn');
        // 重名被自动加后缀的要单独说，否则用户找不到自己刚传的图
        for (const item of deduped) {
          ctx.toast(`${item.name} 与已有表情包重名，已存为 ${item.file}`, 'warn');
        }
        dialog.close();
        await load();
      } catch (e) {
        ctx.toast(e.message, 'error');
        submitBtn.disabled = false;
        submitBtn.textContent = '添加';
      }
    };

    document.body.append(dialog.el);
    renderStaged();
  }

  await load();
  ctx.onEvent(() => {}); // 表情包不依赖实时事件，占位保持接口一致

  return {
    destroy() {
      state.destroyed = true;
      releaseUrls();
    },
  };
}
