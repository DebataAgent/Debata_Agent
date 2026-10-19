// 对话页 —— 对应 ui/dashboard/chats_page.py。
//
// 左侧会话列表 + 右侧 DisplayItem 流。
//
// 「显示项」不是原始记录：后端 webui/chatlog/display_pipeline.py 已经把工具调用
// 与它的返回配成一项、把发送回执去重、把思考单独拆出来。前端只负责按 kind 渲染，
// 不猜记录之间的关系——这样 Web 端和原桌面端看到的内容结构完全一致。
//
// 过滤开关（聊天/系统/工具/思考 + 只看图片文件）与默认展开开关的语义、默认值
// 都照搬原 GUI 的 chats_page.py:556-605。

import { h, fill, empty, formatTime, debounce } from '../dom.js';
import { endpoints } from '../api.js';
import { ensureEmojiNames, renderMessageText } from '../emoji_inline.js';

const DEFAULT_LIMIT = 300;
const LIMIT_STEP = 300;

// 显示项类型 → 气泡样式
const KIND_CLASS = {
  inbound_message: 'user',
  outbound_message: 'assistant',
  assistant_note: 'system',
  system_event: 'system',
  runtime_receipt: 'system',
  reasoning: 'reasoning',
  tool_call: 'tool',
  tool_result: 'tool',
};

const KIND_LABEL = {
  reasoning: '思考',
  tool_call: '工具调用',
  tool_result: '工具结果',
  system_event: '系统',
  runtime_receipt: '回执',
  assistant_note: '备注',
};

export async function render(container, ctx) {
  const state = {
    conversations: [],
    activeId: '',
    items: [],
    total: 0,
    filteredTotal: 0,
    truncated: false,
    limit: DEFAULT_LIMIT,
    filterText: '',
    searchText: '',
    showChat: true,
    showSystem: true,
    showTools: true,
    showReasoning: true,
    mediaOnly: false,
    expandReasoning: false,
    expandSystem: false,
    expandToolCall: false,
    expandToolResult: false,
    // 用户对单条的展开/收起覆盖，优先于上面 4 个默认值
    expanded: new Set(),
    collapsed: new Set(),
    disposed: false,
  };

  // 表情包索引先拉一次，之后 renderMessageText 直接用缓存查表
  ensureEmojiNames().catch(() => {});

  const listBox = h('div', { class: 'conv-list' });
  const msgList = h('div', { class: 'msg-list' });
  const msgHead = h('div', { class: 'msg-pane-head' });
  const pagerBar = h('div', { class: 'msg-pager' });

  const convSearch = h('input', {
    type: 'search',
    placeholder: '搜索会话…',
    oninput: debounce((event) => {
      state.filterText = event.target.value.trim().toLowerCase();
      renderConversations();
    }, 200),
  });

  const recordSearch = h('input', {
    type: 'search',
    placeholder: '在记录里搜索…',
    oninput: debounce((event) => {
      state.searchText = event.target.value.trim();
      state.limit = DEFAULT_LIMIT;
      loadItems();
    }, 250),
  });

  function filterSwitch(label, key, title = '') {
    const input = h('input', {
      type: 'checkbox',
      checked: state[key],
      onchange: (event) => {
        state[key] = event.target.checked;
        state.limit = DEFAULT_LIMIT;
        loadItems();
      },
    });
    return h('label', { class: 'switch', title }, input, label);
  }

  function expandSwitch(label, key) {
    const input = h('input', {
      type: 'checkbox',
      checked: state[key],
      onchange: (event) => {
        state[key] = event.target.checked;
        renderItems();
      },
    });
    return h('label', { class: 'switch' }, input, label);
  }

  const filterRow = h('div', { class: 'filter-row' },
    recordSearch,
    filterSwitch('聊天', 'showChat'),
    filterSwitch('系统', 'showSystem'),
    filterSwitch('工具', 'showTools'),
    filterSwitch('思考', 'showReasoning'),
    filterSwitch('只看图片/文件', 'mediaOnly', '只显示含图片、文件或表情包的记录'),
  );

  const expandRow = h('div', { class: 'filter-row' },
    h('span', { class: 'field-help' }, '默认展开'),
    expandSwitch('展开思考', 'expandReasoning'),
    expandSwitch('展开系统', 'expandSystem'),
    expandSwitch('展开工具调用', 'expandToolCall'),
    expandSwitch('展开工具返回/结果', 'expandToolResult'),
  );

  // ---- 试跑输入框：不接 QQ 也能验证人格 ----
  const injectText = h('textarea', {
    placeholder: '输入一条模拟消息，回车发送（Shift+Enter 换行）',
    rows: 1,
    onkeydown: (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        doInject();
      }
    },
  });
  const injectWho = h('input', {
    style: { maxWidth: '110px' },
    value: '10001',
    title: '发送者 QQ 号',
  });

  async function doInject() {
    const text = injectText.value.trim();
    if (!text) return;
    injectText.value = '';
    try {
      const result = await endpoints.inject({
        text,
        user_id: injectWho.value.trim() || '10001',
        nickname: '测试用户',
        scope: 'private',
      });
      ctx.toast(result.message, result.blacklisted ? 'warn' : 'ok');
      if (result.conversation_id) {
        await loadConversations();
        state.activeId = '';       // 强制重选，触发一次完整加载
        await selectConversation(result.conversation_id);
      }
    } catch (e) {
      ctx.toast(e.message, 'error');
    }
  }

  const composer = h('div', { class: 'composer' },
    injectWho, injectText,
    h('button', { class: 'btn btn-primary', onclick: doInject }, '发送'),
  );

  const msgPane = h('div', { class: 'msg-pane' },
    msgHead, filterRow, expandRow, msgList, pagerBar, composer);

  fill(container,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { class: 'page-title' }, '对话'),
        h('p', { class: 'page-desc' },
          '与 QQ 完全一致的收发记录。底部输入框可在不接 NapCat 的情况下直接试跑人格。'),
      ),
      h('div', { class: 'row' },
        convSearch,
        h('button', { class: 'btn', onclick: () => loadConversations() }, '刷新'),
      ),
    ),
    h('div', { class: 'chats-layout' }, listBox, msgPane),
  );

  // ============================================================
  // 会话列表
  // ============================================================

  function renderConversations() {
    const visible = state.conversations.filter((conv) =>
      !state.filterText || conv.label.toLowerCase().includes(state.filterText)
      || (conv.preview || '').toLowerCase().includes(state.filterText));

    if (!visible.length) {
      fill(listBox, empty(state.conversations.length ? '没有匹配的会话' : '暂无对话', {
        subtitle: state.conversations.length ? '换个词试试' : '机器人就位后，往来的消息会显示在这里',
        iconName: 'chats',
      }));
      return;
    }

    fill(listBox, ...visible.map((conv) =>
      h('button', {
        class: `conv-item ${conv.key === state.activeId ? 'active' : ''}`,
        onclick: () => selectConversation(conv.key),
      },
        h('div', { class: 'conv-label' }, conv.label || conv.key),
        h('div', { class: 'conv-meta' },
          `${conv.record_count} 条 · ${conv.preview || '（无文本）'}`),
      ),
    ));
  }

  async function loadConversations() {
    try {
      const data = await endpoints.conversations();
      state.conversations = data.conversations || [];
      renderConversations();
    } catch (e) {
      fill(listBox, empty('会话列表没能读出来', { subtitle: e.message, iconName: 'chats' }));
    }
  }

  // ============================================================
  // 显示项
  // ============================================================

  function queryString() {
    const q = new URLSearchParams();
    q.set('conversation_id', state.activeId);
    q.set('limit', String(state.limit));
    q.set('show_chat', state.showChat ? '1' : '0');
    q.set('show_system', state.showSystem ? '1' : '0');
    q.set('show_tools', state.showTools ? '1' : '0');
    q.set('show_reasoning', state.showReasoning ? '1' : '0');
    q.set('media_only', state.mediaOnly ? '1' : '0');
    if (state.searchText) q.set('q', state.searchText);
    return q.toString();
  }

  async function loadItems() {
    if (!state.activeId) return;
    const id = state.activeId;
    try {
      const data = await endpoints.display(queryString());
      if (state.disposed || state.activeId !== id) return;
      state.items = data.items || [];
      state.total = data.total || 0;
      state.filteredTotal = data.filtered_total || 0;
      state.truncated = !!data.truncated;
      renderItems();
    } catch (e) {
      fill(msgList, empty('记录没能读出来', { subtitle: e.message, iconName: 'chats' }));
    }
  }

  /** 单条是否展开：用户的显式操作优先于"默认展开"开关。 */
  function isExpanded(item) {
    if (state.expanded.has(item.id)) return true;
    if (state.collapsed.has(item.id)) return false;
    switch (item.kind) {
      case 'reasoning': return state.expandReasoning;
      case 'system_event':
      case 'runtime_receipt':
      case 'assistant_note': return state.expandSystem;
      case 'tool_call': return state.expandToolCall;
      case 'tool_result': return state.expandToolResult;
      default: return false;
    }
  }

  function toggle(item) {
    const open = isExpanded(item);
    if (open) {
      state.expanded.delete(item.id);
      state.collapsed.add(item.id);
    } else {
      state.collapsed.delete(item.id);
      state.expanded.add(item.id);
    }
    renderItems();
  }

  /** 可折叠的项：头部一行摘要，点开看正文。 */
  function collapsibleNode(item, extraClass = '') {
    const open = isExpanded(item);
    const head = h('button', {
      class: 'item-head',
      onclick: () => toggle(item),
    },
      h('span', { class: 'item-caret' }, open ? '▾' : '▸'),
      h('span', {}, KIND_LABEL[item.kind] || item.kind),
      item.speaker_label ? h('span', { class: 'field-help' }, ` · ${item.speaker_label}`) : null,
      item.timestamp ? h('span', { class: 'field-help' }, ` · ${formatTime(item.timestamp)}`) : null,
      h('span', { class: 'item-summary' }, item.summary || ''),
    );
    return h('div', {
      class: `msg ${extraClass} ${open ? 'open' : 'collapsed'}`,
      dataset: { kind: item.kind, severity: item.severity },
    },
      head,
      open ? h('div', { class: 'msg-body' }, renderMessageText(item.text)) : null,
    );
  }

  function itemNode(item) {
    switch (item.kind) {
      case 'inbound_message':
      case 'outbound_message': {
        const who = item.speaker_label || (item.kind === 'inbound_message' ? '用户' : '角色');
        return h('div', { class: `msg ${KIND_CLASS[item.kind]}`, dataset: { kind: item.kind } },
          h('div', { class: 'msg-head' },
            h('span', { class: 'msg-role' }, who),
            item.timestamp ? ` · ${formatTime(item.timestamp)}` : '',
            item.summary ? ` · ${item.summary}` : '',
          ),
          h('div', { class: 'msg-body' }, renderMessageText(item.text || '（空）')),
        );
      }
      case 'reasoning':
      case 'assistant_note':
      case 'system_event':
      case 'runtime_receipt':
        return collapsibleNode(item, KIND_CLASS[item.kind]);
      case 'tool_call': {
        // 工具调用与它的返回配成一项：返回挂在下面，各自可折叠
        const node = collapsibleNode(item, 'tool');
        if (item.tool_results && item.tool_results.length) {
          node.append(h('div', { class: 'tool-results' },
            ...item.tool_results.map((result) => collapsibleNode(result, 'tool'))));
        }
        return node;
      }
      case 'tool_result':
        // 已被配到某个 tool_call 下的不会走到这里（后端已挂到 tool_results）
        return collapsibleNode(item, 'tool');
      default:
        return collapsibleNode(item, 'system');
    }
  }

  function renderItems() {
    if (!state.activeId) {
      fill(msgHead);
      fill(pagerBar);
      fill(msgList, empty('选一个会话', {
        subtitle: '或在下面输入一条消息开始试跑',
        iconName: 'chats',
      }));
      return;
    }
    const conv = state.conversations.find((c) => c.key === state.activeId);
    fill(msgHead,
      h('strong', {}, conv ? conv.label : state.activeId),
      h('span', { class: 'field-help' },
        `${state.filteredTotal} / ${state.total} 条`),
    );

    if (!state.items.length) {
      const filteredOut = state.filteredTotal === 0 && state.total > 0;
      fill(msgList, filteredOut
        ? empty('当前过滤条件下没有内容', {
            subtitle: '试试勾上「聊天」或「系统」，或清空搜索词',
            iconName: 'search',
          })
        : empty('这个会话还没有可显示的记录', {
            subtitle: '收到消息后会自动出现在这里',
            iconName: 'chats',
          }));
      fill(pagerBar);
      return;
    }

    fill(msgList, ...state.items.map(itemNode));

    // 分页条：照搬原 GUI 的「加载更早（N）/ 显示全部 / 已显示全部」
    const hidden = state.filteredTotal - state.items.length;
    if (hidden > 0) {
      fill(pagerBar,
        h('span', { class: 'field-help' },
          `还有 ${hidden} 条更早的记录未显示`),
        h('div', { class: 'spacer' }),
        h('button', {
          class: 'btn btn-sm',
          onclick: () => { state.limit += LIMIT_STEP; loadItems(); },
        }, `加载更早（${Math.min(hidden, LIMIT_STEP)}）`),
        h('button', {
          class: 'btn btn-sm',
          onclick: () => { state.limit = 0; loadItems(); },
        }, '显示全部'),
      );
    } else if (state.truncated) {
      fill(pagerBar, h('span', { class: 'field-help' }, '已显示全部'));
    } else {
      fill(pagerBar);
    }
  }

  async function selectConversation(id) {
    if (id === state.activeId) return;
    state.activeId = id;
    state.items = [];
    state.limit = DEFAULT_LIMIT;
    state.expanded.clear();
    state.collapsed.clear();
    renderConversations();
    fill(msgList, h('div', { class: 'field-help' }, '正在准备…'));
    await loadItems();
  }

  // ============================================================
  // 实时：新消息到达时重新拉一次当前会话
  // ============================================================

  const unsubscribe = ctx.onEvent(async (event) => {
    if (event.type !== 'chat') return;
    const msg = event.data;
    if (msg.conversation_id !== state.activeId) {
      await loadConversations();
      return;
    }
    await loadItems();
    msgList.scrollTop = msgList.scrollHeight;
    await loadConversations();
  });

  await loadConversations();
  const first = state.conversations.find((c) => !c.key.startsWith('system:'));
  if (first) await selectConversation(first.key);

  return {
    destroy() {
      state.disposed = true;
      unsubscribe();
    },
  };
}
