// 线性图标集。
//
// docs/ui_style_guide.md §六要求：线性图标、1.5px 描边、圆角端点，24x24 基准，
// **不使用面性图标**，不用 emoji 当图标。
//
// 这里手写路径而不是引 CDN 或图标字体包——WebUI 刻意没有构建步骤，
// 要能在 ARM 板子这类离线环境里直接刷新浏览器就用。
// 路径风格参考 Phosphor / Lucide 的线性画法（均为 MIT），按本项目的 1.5px 描边重画。

const PATHS = {
  // ===== 导航 =====
  // 总览：四宫格
  overview: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
  // 对话：气泡带小尾巴
  chats: ['M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-5 4z'],
  // 记忆：书页
  memory: ['M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z', 'M5 17a3 3 0 0 1 3-3h11'],
  // 人格后台：头部轮廓 + 脉冲（"后台在跑"的意象）
  persona_mind: [
    'M12 3a6 6 0 0 0-4 10.5V20h8v-6.5A6 6 0 0 0 12 3z',
    'M9 13h1.5l1-2 1.5 4 1-2H16',
  ],
  // 日志：列表
  logs: ['M4 6h2', 'M9 6h11', 'M4 12h2', 'M9 12h11', 'M4 18h2', 'M9 18h11'],
  // 角色：人形
  personas: ['M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M4 20a8 8 0 0 1 16 0'],
  // 模型管理：立方体
  models: ['M12 3l8 4.5v9L12 21l-8-4.5v-9z', 'M4 7.5l8 4.5 8-4.5', 'M12 12v9'],
  // 表情包：笑脸
  emoji: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M9 10h.01', 'M15 10h.01', 'M8.5 14.5a4.5 4.5 0 0 0 7 0'],
  // 黑名单：禁止
  blacklist: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M5.6 5.6l12.8 12.8'],
  // 设置：滑杆（比齿轮更适合线性风格）
  settings: ['M4 7h10', 'M18 7h2', 'M4 17h4', 'M12 17h8', 'M16 7a2 2 0 1 0 0-.01',
             'M10 17a2 2 0 1 0 0-.01'],

  // ===== 顶栏 =====
  // 重启：环形箭头
  restart: ['M20 12a8 8 0 1 1-2.3-5.6', 'M20 4v5h-5'],
  sun: ['M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z', 'M12 2v2', 'M12 20v2',
        'M4.9 4.9l1.4 1.4', 'M17.7 17.7l1.4 1.4', 'M2 12h2', 'M20 12h2',
        'M4.9 19.1l1.4-1.4', 'M17.7 6.3l1.4-1.4'],
  moon: ['M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z'],
  logout: ['M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3', 'M10 16l-4-4 4-4', 'M6 12h10'],

  // ===== 通用操作 =====
  add: ['M12 5v14', 'M5 12h14'],
  edit: ['M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z', 'M14.5 6.5l3 3'],
  delete: ['M5 7h14', 'M9 7V5h6v2', 'M6 7l1 13h10l1-13', 'M10 11v6', 'M14 11v6'],
  refresh: ['M20 12a8 8 0 1 1-2.3-5.6', 'M20 4v5h-5'],
  upload: ['M12 16V4', 'M8 8l4-4 4 4', 'M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  check: ['M5 13l4 4L19 7'],
  image: ['M4 5h16v14H4z', 'M4 16l4.5-4.5 3.5 3.5 3-3L20 16', 'M9.5 10a1.5 1.5 0 1 0 0-.01'],
  // 搜索
  search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.3-4.3'],
  // 测试连接
  plug: ['M9 3v6', 'M15 3v6', 'M6 9h12v3a6 6 0 0 1-12 0z', 'M12 18v3'],
  // 获取模型列表
  download: ['M12 4v12', 'M8 12l4 4 4-4', 'M4 20h16'],
};

/**
 * 生成一个线性 SVG 图标。
 *
 * @param {string} name  PATHS 里的键
 * @param {{size?: number, stroke?: number, className?: string}} [options]
 * @returns {SVGElement}
 */
export function icon(name, { size = 16, stroke = 1.5, className = '' } = {}) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', String(stroke));
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  if (className) svg.classList.add(...className.split(/\s+/).filter(Boolean));

  const paths = PATHS[name];
  if (!paths) {
    // 名字写错时不要静默画空白，控制台留个线索
    console.warn(`icons.js: 未知图标 ${name}`);
    return svg;
  }
  for (const d of paths) {
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

/** 供测试断言用（样式契约测试检查导航引用的名字都存在）。 */
export { PATHS };
