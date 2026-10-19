// HTTP 封装：统一处理鉴权失效、错误提示和 JSON 解析。

let onUnauthorized = () => {};

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  constructor(status, detail) {
    const message = typeof detail === 'string'
      ? detail
      : (detail && (detail.message || detail.reason)) || `HTTP ${status}`;
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

// 单次请求的上限。没有这个超时，后端一旦卡住（换模型、探测密钥、代理挂死），
// fetch 会一直挂着，调用方的 await 永远不 settle，启动流程就停在白屏上。
const REQUEST_TIMEOUT_MS = 15000;
// 对话页首次加载需要汇总历史/事件/归档，在 ARM 设备上可能超过 15 秒；
// 给这组接口单独放宽超时，后端有缓存，后续请求会很快。
const CHAT_REQUEST_TIMEOUT_MS = 60000;

async function request(method, path, body, options = {}) {
  const init = {
    method,
    credentials: 'same-origin',
    headers: {},
  };

  if (body !== undefined && !(body instanceof FormData)) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  } else if (body instanceof FormData) {
    init.body = body;
  }

  const timeoutMs = options.timeoutMs ?? REQUEST_TIMEOUT_MS;
  let timer = null;
  if (timeoutMs > 0 && typeof AbortController === 'function') {
    const controller = new AbortController();
    init.signal = controller.signal;
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }

  let response;
  try {
    response = await fetch(path, init);
  } catch (e) {
    // abort 和真正的网络错误要分开报，否则用户看到 "The user aborted a request"
    // 完全不知道发生了什么。
    const message = e && e.name === 'AbortError'
      ? `请求超时（超过 ${Math.round(timeoutMs / 1000)} 秒）：${path}`
      : `无法连接到 Debata 服务：${e.message}`;
    throw new ApiError(0, message);
  } finally {
    if (timer) clearTimeout(timer);
  }

  if (response.status === 401) {
    onUnauthorized();
    throw new ApiError(401, '登录已失效，请重新输入令牌');
  }

  let payload = null;
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) {
    payload = await response.json().catch(() => null);
  } else if (!options.raw) {
    payload = await response.text().catch(() => '');
  }

  if (!response.ok) {
    const detail = payload && payload.detail !== undefined ? payload.detail : payload;
    throw new ApiError(response.status, detail || response.statusText);
  }
  return options.raw ? response : payload;
}

export const api = {
  get: (path, options) => request('GET', path, undefined, options),
  post: (path, body) => request('POST', path, body),
  patch: (path, body) => request('PATCH', path, body),
  put: (path, body) => request('PUT', path, body),
  del: (path) => request('DELETE', path),
  raw: (path) => request('GET', path, undefined, { raw: true }),
  upload: (path, formData) => request('POST', path, formData),
};

export const endpoints = {
  authState: () => api.get('/api/auth/state'),
  login: (token) => api.post('/api/auth/login', { token }),
  logout: () => api.post('/api/auth/logout'),

  status: () => api.get('/api/status'),
  restart: () => api.post('/api/system/restart'),
  stopRuntime: () => api.post('/api/system/stop'),
  startRuntime: () => api.post('/api/system/start'),
  quit: () => api.post('/api/system/quit'),

  setupOptions: () => api.get('/api/setup/options'),
  setupStep: (payload) => api.post('/api/setup/step', payload),
  setupCopy: () => api.get('/api/setup/copy'),
  setupApply: (payload) => api.post('/api/setup/apply', payload),

  overview: (range = 'today') => api.get(`/api/overview?range=${encodeURIComponent(range)}`),

  conversations: () =>
    api.get('/api/chats/conversations', { timeoutMs: CHAT_REQUEST_TIMEOUT_MS }),
  conversation: (id, limit = 300) =>
    api.get(
      `/api/chats/conversation?conversation_id=${encodeURIComponent(id)}&limit=${limit}`,
      { timeoutMs: CHAT_REQUEST_TIMEOUT_MS },
    ),
  // 结构化显示项（工具调用↔结果已配对、回执已去重），q 直接是查询串
  display: (query) =>
    api.get(`/api/chats/display?${query}`, { timeoutMs: CHAT_REQUEST_TIMEOUT_MS }),
  timeline: (id = '', limit = 100) =>
    api.get(
      `/api/chats/timeline?conversation_id=${encodeURIComponent(id)}&limit=${limit}`,
      { timeoutMs: CHAT_REQUEST_TIMEOUT_MS },
    ),
  inject: (payload) => api.post('/api/chats/inject', payload),
  send: (payload) => api.post('/api/chats/send', payload),

  memory: () => api.get('/api/memory'),
  addMemory: (payload) => api.post('/api/memory', payload),
  updateMemory: (id, payload) => api.patch(`/api/memory/${encodeURIComponent(id)}`, payload),
  deleteMemory: (id) => api.del(`/api/memory/${encodeURIComponent(id)}`),
  rag: (limit = 200) => api.get(`/api/memory/rag?limit=${limit}`),

  mindState: () => api.get('/api/mind/state'),
  mindTab: (key, limit = 50) => api.get(`/api/mind/tab/${key}?limit=${limit}`),

  logs: (params = {}) => {
    const q = new URLSearchParams();
    if (params.limit) q.set('limit', params.limit);
    if (params.level) q.set('level', params.level);
    if (params.module) q.set('module', params.module);
    if (params.q) q.set('q', params.q);
    return api.get(`/api/logs?${q.toString()}`);
  },

  personas: () => api.get('/api/personas'),
  activatePersona: (name) => api.post(`/api/personas/${encodeURIComponent(name)}/activate`),
  deletePersona: (name) => api.del(`/api/personas/${encodeURIComponent(name)}`),
  personaPrompt: (name) => api.get(`/api/personas/${encodeURIComponent(name)}/prompt`),
  importPersona: (formData) => api.upload('/api/personas/import', formData),

  // 角色生成 / 改写 / 复制
  personaDraft: (brief) => api.post('/api/personas/draft', brief),
  personaRefine: (payload) => api.post('/api/personas/refine', payload),
  savePersona: (payload) => api.post('/api/personas', payload),
  personaSource: (name) => api.get(`/api/personas/${encodeURIComponent(name)}/source`),
  personaBrief: () => api.get('/api/personas/brief'),
  clearPersonaBrief: () => api.del('/api/personas/brief'),
  duplicatePersona: (name, newName) =>
    api.post(`/api/personas/${encodeURIComponent(name)}/duplicate`, { new_name: newName }),

  emoji: () => api.get('/api/emoji'),
  emojiNames: () => api.get('/api/emoji/names'),
  uploadEmoji: (formData) => api.upload('/api/emoji/upload', formData),
  renameEmoji: (name, newName) =>
    api.patch(`/api/emoji/${encodeURIComponent(name)}`, { new_name: newName }),
  deleteEmoji: (name) => api.del(`/api/emoji/${encodeURIComponent(name)}`),

  models: (rescan = false) => api.get(`/api/models${rescan ? '?rescan=true' : ''}`),
  scanModels: () => api.post('/api/models/scan'),
  modelGuide: (name) => api.get(`/api/models/${encodeURIComponent(name)}/guide`),
  installModelDeps: (name, source) =>
    api.post(`/api/models/${encodeURIComponent(name)}/install-deps`, { source }),
  uploadModel: (name, formData) =>
    api.upload(`/api/models/${encodeURIComponent(name)}/upload`, formData),

  settings: () => api.get('/api/settings'),
  patchSetting: (path, value) => api.patch('/api/settings', { path, value }),
  providers: () => api.get('/api/settings/providers'),
  patchAgent: (name, payload) => api.patch(`/api/settings/agents/${name}`, payload),
  putSecret: (keyId, value) => api.put('/api/settings/secrets', { key_id: keyId, value }),

  // 提供商增删改（写操作走 /api/providers，settings 那条只读）
  providerPresets: () => api.get('/api/providers/presets'),
  createProvider: (payload) => api.post('/api/providers', payload),
  updateProvider: (id, payload) =>
    api.patch(`/api/providers/${encodeURIComponent(id)}`, payload),
  deleteProvider: (id, dropKey = false) =>
    api.del(`/api/providers/${encodeURIComponent(id)}?drop_key=${dropKey ? 'true' : 'false'}`),
  testProvider: (id) => api.post(`/api/providers/${encodeURIComponent(id)}/test`),
  testProviderConfig: (payload) => api.post('/api/providers/test', payload),
  fetchProviderModels: (payload) => api.post('/api/providers/fetch-models', payload),

  blacklist: () => api.get('/api/settings/blacklist'),
  saveBlacklist: (payload) => api.put('/api/settings/blacklist', payload),
};
