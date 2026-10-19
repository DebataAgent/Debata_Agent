// 与 /ws/events 的连接，带自动重连。
// 一条连接同时收三类事件：log（日志行）、chat（QQ 消息）、status（运行状态）。

const MAX_BACKOFF = 15000;

export class EventStream {
  constructor() {
    this.socket = null;
    this.handlers = new Set();
    this.backoff = 1000;
    this.closedByUser = false;
    this.connected = false;
    this.onConnectionChange = () => {};
  }

  connect() {
    this.closedByUser = false;
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${scheme}://${location.host}/ws/events`;

    let socket;
    try {
      socket = new WebSocket(url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.connected = true;
      this.backoff = 1000;
      this.onConnectionChange(true);
    };

    socket.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      for (const handler of this.handlers) {
        try {
          handler(payload);
        } catch (e) {
          console.error('事件处理失败', e);
        }
      }
    };

    socket.onclose = () => {
      this.connected = false;
      this.onConnectionChange(false);
      if (!this.closedByUser) this._scheduleReconnect();
    };

    socket.onerror = () => {
      // onclose 紧随其后，重连逻辑统一放在那里。
      socket.close();
    };
  }

  _scheduleReconnect() {
    setTimeout(() => {
      if (!this.closedByUser) this.connect();
    }, this.backoff);
    this.backoff = Math.min(this.backoff * 1.7, MAX_BACKOFF);
  }

  subscribe(handler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  close() {
    this.closedByUser = true;
    if (this.socket) this.socket.close();
  }
}
