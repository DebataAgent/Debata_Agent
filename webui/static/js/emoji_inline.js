// 把聊天记录里的 [表情包: 名字] 标签渲染成内联缩略图。
//
// 后端在时间线里存的是纯文本标签（core/pipeline_tool_context.py 记的是 send 动作的
// label），图片要靠名称反查，所以这里先取一次「名称 → 文件名」表，
// 避免每条消息都发一次请求。
//
// 安全：图片 URL 只用**服务端返回的文件名**拼，消息正文里的名字只用来查表。
// 正文是用户内容，直接拿去拼路径等于把注入的口子留给对方。

import { endpoints } from './api.js';

// 与 tools/message_builder.py 的 f"[表情包: {name}]" 对应。
// 兼容全角冒号与多余空格，因为历史记录里两种都可能存在。
const EMOJI_LABEL_RE = /\[表情包\s*[:：]\s*([^\]]+?)\s*\]/g;

/** 名称（小写）→ 文件名。null 表示还没取过。 */
let nameToFile = null;
let inflight = null;

/** 取（并缓存）表情包索引。失败时置空表，不让整页聊天记录渲染不出来。 */
export async function ensureEmojiNames({ force = false } = {}) {
  if (force) invalidateEmojiNames();
  if (nameToFile) return nameToFile;
  if (!inflight) {
    inflight = endpoints.emojiNames()
      .then((data) => {
        nameToFile = new Map(
          (data.items || []).map((x) => [x.name.toLowerCase(), x.file]),
        );
        return nameToFile;
      })
      .catch(() => {
        nameToFile = new Map();
        return nameToFile;
      })
      .finally(() => { inflight = null; });
  }
  return inflight;
}

/** 上传/改名/删除后调用，让下次渲染重新取。 */
export function invalidateEmojiNames() {
  nameToFile = null;
  inflight = null;
}

/**
 * 把一段文本转成 DocumentFragment，表情包标签替换成 <img>。
 *
 * 普通文本一律走 textContent —— 正文是用户内容，绝不能当 HTML 解析。
 *
 * @param {string} text
 * @param {{index?: Map<string,string>|null}} [options] 不传则用模块缓存的索引
 */
export function renderMessageText(text, { index = null } = {}) {
  const fragment = document.createDocumentFragment();
  const source = String(text ?? '');
  const table = index || nameToFile;

  if (!table || table.size === 0 || !source.includes('[表情包')) {
    fragment.append(document.createTextNode(source));
    return fragment;
  }

  let cursor = 0;
  EMOJI_LABEL_RE.lastIndex = 0;
  let match;
  while ((match = EMOJI_LABEL_RE.exec(source)) !== null) {
    if (match.index > cursor) {
      fragment.append(document.createTextNode(source.slice(cursor, match.index)));
    }
    const name = match[1].trim();
    const file = table.get(name.toLowerCase());
    if (file) {
      fragment.append(emojiImage(name, file));
    } else {
      // 名字对不上（表情包被删了或改过名）就原样显示标签，不假装图还在
      fragment.append(document.createTextNode(match[0]));
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) {
    fragment.append(document.createTextNode(source.slice(cursor)));
  }
  return fragment;
}

function emojiImage(name, file) {
  const img = document.createElement('img');
  img.className = 'emoji-inline';
  img.alt = `[表情包: ${name}]`;
  img.title = name;
  img.loading = 'lazy';
  img.src = `/api/emoji/file/${encodeURIComponent(file)}`;
  return img;
}
