import { execFile, spawn } from 'node:child_process';
import * as path from 'node:path';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as net from 'node:net';
import * as crypto from 'node:crypto';
import * as os from 'node:os';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const workspaceRoot = __dirname;

let workbenchProcess = null;
let workbenchPort = 7860;
let workbenchHost = '127.0.0.1';
let controlServer = null;

/**
 * 判断目录是否"看起来像"一个竞赛教研项目根（含 teaching/ 或 research/ 或 config.yaml）
 */
function looksLikeProjectRoot(dir) {
  try {
    if (!dir || !fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return false;
    if (fs.existsSync(path.join(dir, 'teaching'))) return true;
    if (fs.existsSync(path.join(dir, 'research'))) return true;
    if (fs.existsSync(path.join(dir, 'config.yaml'))) return true;
    if (fs.existsSync(path.join(dir, 'config.yml'))) return true;
    return false;
  } catch (_) {
    return false;
  }
}

/**
 * 动态探测当前活跃的项目工作目录。
 *
 * 优先级（B2 修复：显式 project_root 曾被静默丢弃，导致跨项目污染）：
 *   1. 显式 project_root 参数（绝对路径，或相对 process.cwd() 解析）——尊重调用方意图，
 *      即使目录不存在也如实返回，由下游给出明确错误，绝不静默回退到 registry[0]；
 *   2. request_ref 为绝对路径时向上回推项目根（典型布局 <root>/teaching/agent-requests/<id>.json）；
 *   3. workspaceRegistry[0].path；
 *   4. process.cwd()。
 */
function resolveCurrentProjectDir(ctx, explicitRoot, requestRef) {
  const explicit = String(explicitRoot || '').trim();
  if (explicit) {
    return path.resolve(explicit);
  }
  const ref = String(requestRef || '').trim();
  if (ref && path.isAbsolute(ref)) {
    const reqDir = path.dirname(path.resolve(ref));
    const candidates = [
      path.resolve(reqDir, '..', '..'),
      path.resolve(reqDir, '..'),
      reqDir,
    ];
    for (const cand of candidates) {
      if (looksLikeProjectRoot(cand)) return cand;
    }
    return candidates[0];
  }
  let projectDir = process.cwd();
  try {
    const registry = ctx?.get?.('workspaceRegistry') || ctx?.workspaceRegistry;
    if (registry && typeof registry.list === 'function') {
      const list = registry.list();
      if (list && list.length > 0 && list[0]?.path) {
        projectDir = list[0].path;
      }
    }
  } catch (_) {}
  return projectDir;
}

/**
 * 探测指定端口是否在监听
 */
function probePort(port, host = '127.0.0.1', timeoutMs = 800) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;
    socket.setTimeout(timeoutMs);
    socket.on('connect', () => {
      settled = true;
      socket.destroy();
      resolve(true);
    });
    socket.on('timeout', () => {
      if (!settled) {
        settled = true;
        socket.destroy();
        resolve(false);
      }
    });
    socket.on('error', () => {
      if (!settled) {
        settled = true;
        socket.destroy();
        resolve(false);
      }
    });
    socket.connect(port, host);
  });
}

/**
 * 安全确保工作台运行中，支持超时等待与自动目录注入
 */
async function ensureWorkbenchRunning({ port = workbenchPort, host = workbenchHost, input_dir = '', output_dir = '' } = {}, pythonBin = 'python', fallbackInputDir = '') {
  workbenchPort = port;
  workbenchHost = host;

  const targetInput = input_dir || fallbackInputDir || '';
  const targetOutput = output_dir || (targetInput ? path.join(targetInput, 'self') : '');

  // 1. 如果端口已经在监听，直接判定为运行就绪
  const isOnline = await probePort(port, host);
  if (isOnline) {
    return {
      running: true,
      url: `http://${host}:${port}`,
      message: `工作台已在线: http://${host}:${port}`,
    };
  }

  // 2. 如果之前拉起过且进程尚在，等待其完成冷启动
  if (workbenchProcess && !workbenchProcess.killed) {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 500));
      if (await probePort(port, host)) {
        return {
          running: true,
          url: `http://${host}:${port}`,
          message: `工作台已就绪: http://${host}:${port}`,
        };
      }
    }
  }

  // 3. 启动新进程并记录日志
  const cliArgs = ['launcher.py', '--port', String(port), '--host', host, '--no-browser'];
  if (targetInput) {
    cliArgs.push('--input', targetInput);
  }
  if (targetOutput) {
    cliArgs.push('--output', targetOutput);
  }

  const logPath = path.join(workspaceRoot, 'workbench.log');
  let logFd = null;
  try {
    logFd = fs.openSync(logPath, 'a');
  } catch (_) {}

  const cp = spawn(
    pythonBin,
    cliArgs,
    {
      cwd: workspaceRoot,
      stdio: logFd ? ['ignore', logFd, logFd] : 'ignore',
      detached: false,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
      },
    }
  );

  workbenchProcess = cp;
  cp.on('exit', () => {
    if (workbenchProcess === cp) {
      workbenchProcess = null;
    }
  });

  // 4. 轮询最多 15 秒等待端口进入 Listen 状态
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await probePort(port, host)) {
      return {
        running: true,
        url: `http://${host}:${port}`,
        message: `工作台已成功拉起并就绪: http://${host}:${port}`,
      };
    }
    if (workbenchProcess && workbenchProcess.killed) {
      break;
    }
  }

  return {
    running: false,
    url: `http://${host}:${port}`,
    message: '工作台启动超时或未能正常绑定端口，请查看 workbench.log 日志',
  };
}

/**
 * 停止工作台
 */
async function stopWorkbenchService() {
  if (workbenchProcess && !workbenchProcess.killed) {
    try {
      workbenchProcess.kill();
    } catch (_) {}
    workbenchProcess = null;
  }
  return {
    running: false,
    url: '',
    message: '已请求停止可视化工作台',
  };
}

/**
 * 启动轻量本地 Loopback HTTP 控制服务 (端口 7862)
 * 为 Web 前端与开始界面提供状态探测、跨域一键拉起与停止能力
 */
function startControlServer(port = 7862, pythonBin = 'python', ctx = null) {
  if (controlServer) return;
  try {
    controlServer = http.createServer(async (req, res) => {
      // 允许 Web GUI (如 http://127.0.0.1:3080) 跨域调用
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

      if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
      }

      let parsedUrl;
      try {
        parsedUrl = new URL(req.url, `http://${req.headers.host || '127.0.0.1'}`);
      } catch (_) {
        parsedUrl = { pathname: req.url || '/' };
      }
      const pathname = parsedUrl.pathname;

      const currentWorkspace = resolveCurrentProjectDir(ctx);

      if (pathname === '/api/status') {
        const isOnline = await probePort(workbenchPort, workbenchHost);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          running: isOnline,
          port: workbenchPort,
          host: workbenchHost,
          url: `http://${workbenchHost}:${workbenchPort}`,
          currentWorkspace,
          outputWorkspace: path.join(currentWorkspace, 'self'),
          workspaceRoot,
        }));
        return;
      }

      if (pathname === '/api/start' && (req.method === 'POST' || req.method === 'GET')) {
        let body = '';
        req.on('data', chunk => { body += chunk; });
        req.on('end', async () => {
          let parsed = {};
          if (body) {
            try { parsed = JSON.parse(body); } catch (_) {}
          }
          const targetInput = parsed.input_dir || currentWorkspace;
          const targetOutput = parsed.output_dir || (targetInput ? path.join(targetInput, 'self') : '');
          const result = await ensureWorkbenchRunning({
            port: parsed.port || workbenchPort,
            host: parsed.host || workbenchHost,
            input_dir: targetInput,
            output_dir: targetOutput,
          }, pythonBin, currentWorkspace);
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify(result));
        });
        return;
      }

      if (pathname === '/api/stop' && (req.method === 'POST' || req.method === 'GET')) {
        const result = await stopWorkbenchService();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(result));
        return;
      }

      res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: 'Endpoint not found' }));
    });

    controlServer.listen(port, '127.0.0.1', () => {
      // 端口占用/监听失败静默降级：控制服务仅为工作台辅助通道，
      // 不影响 courseware_dispatch / courseware_gate 主链路（不写插件目录日志）。
    });

    controlServer.on('error', (err) => {
      if (err && err.code === 'EADDRINUSE') return;
      console.warn(`[competition-courseware] 控制服务错误: ${err.message}`);
    });
  } catch (err) {
    console.warn(`[competition-courseware] 控制服务启动失败: ${err.message}`);
  }
}

/**
 * Execute python CLI helper.
 */
function runPython(args, cwd = workspaceRoot, timeoutMs = 180000, pythonBin = 'python') {
  return new Promise((resolve) => {
    const env = {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
    };
    execFile(
      pythonBin,
      args,
      { cwd, env, timeout: timeoutMs, maxBuffer: 10 * 1024 * 1024, encoding: 'utf-8' },
      (err, stdout, stderr) => {
        resolve({
          success: !err,
          code: err ? (err.code ?? 1) : 0,
          stdout: stdout ? stdout.trim() : '',
          stderr: stderr ? stderr.trim() : '',
          error: err ? err.message : null,
        });
      }
    );
  });
}

// ---- courseware_dispatch 辅助（host-subagent-v1 证据链） --------------------

function canonicalJson(obj) {
  if (obj === null || typeof obj !== 'object') {
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    return '[' + obj.map(canonicalJson).join(',') + ']';
  }
  const keys = Object.keys(obj).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(obj[k])).join(',') + '}';
}

function cdReadJson(absPath) {
  try {
    let text = fs.readFileSync(absPath, 'utf8');
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1); // 剥离 UTF-8 BOM
    return JSON.parse(text);
  } catch (_) {
    return null;
  }
}

function cdSafeJoin(projectRoot, rel) {
  if (typeof rel !== 'string' || !rel.trim()) return '';
  const norm = rel.replace(/\\/g, '/');
  if (path.isAbsolute(norm) || norm.split('/').includes('..')) return '';
  const normRoot = path.resolve(projectRoot);
  const abs = path.resolve(normRoot, norm);
  if (abs !== normRoot && !abs.startsWith(normRoot + path.sep)) return '';
  return abs;
}

function cdSha256File(absPath) {
  try {
    return crypto.createHash('sha256').update(fs.readFileSync(absPath)).digest('hex');
  } catch (_) {
    return null;
  }
}

function cdAtomicWrite(absPath, content) {
  const dir = path.dirname(absPath);
  if (dir && !fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  const tmp = `${absPath}.tmp-${crypto.randomBytes(4).toString('hex')}`;
  fs.writeFileSync(tmp, content, 'utf8');
  fs.renameSync(tmp, absPath);
}

const CD_PLACEHOLDER_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

function cdRenderTemplate(tpl, values) {
  if (!tpl) return '';
  return tpl.replace(CD_PLACEHOLDER_RE, (m, key) => {
    const v = values[key];
    return v == null ? '' : String(v);
  });
}

function cdParseSubagentFiles(raw) {
  if (!raw) return { files: null, runId: '', error: '子 Agent 无返回' };
  let runId = '';
  let text = '';
  if (typeof raw === 'string') {
    text = raw;
  } else if (typeof raw === 'object') {
    runId = String(raw.runId || raw.id || raw.subagentId || '');
    const blocks = raw.output || (raw.result && raw.result.output) || raw.content;
    if (Array.isArray(blocks)) {
      const textBlocks = blocks.filter((b) => b && typeof b === 'object' && b.type === 'text');
      const targetBlocks = textBlocks.length > 0 ? textBlocks : blocks;
      text = targetBlocks
        .map((b) => {
          if (!b) return '';
          if (typeof b === 'string') return b;
          if (typeof b === 'object') {
            return b.text || b.content || b.value || '';
          }
          return String(b);
        })
        .join('\n');
    } else if (typeof blocks === 'string') {
      text = blocks;
    } else if (raw.text) {
      text = String(raw.text);
    } else if (raw.result && typeof raw.result === 'string') {
      text = raw.result;
    }
  }
  const trimmed = text.trim();
  if (!trimmed) {
    const rawPreview = JSON.stringify(raw || {}).slice(0, 300);
    return { files: null, runId, error: `子 Agent 返回空内容 (raw: ${rawPreview})` };
  }
  let parsed = null;

  // 1. 尝试直接整段解析
  try {
    const p = JSON.parse(trimmed);
    if (p && typeof p === 'object' && p.files && typeof p.files === 'object') {
      parsed = p;
    }
  } catch (_) {}

  // 2. 扫描所有 markdown 代码块，倒序优先（取最后的代码块）
  if (!parsed) {
    const codeBlocks = [...trimmed.matchAll(/```(?:json)?\s*([\s\S]*?)```/gi)];
    for (let i = codeBlocks.length - 1; i >= 0; i--) {
      try {
        const p = JSON.parse(codeBlocks[i][1].trim());
        if (p && typeof p === 'object' && p.files && typeof p.files === 'object') {
          parsed = p;
          break;
        }
      } catch (_) {}
    }
  }

  // 3. 兜底提取最外层含 "files" 的 JSON 块
  if (!parsed) {
    const unfenced = trimmed
      .replace(/^```(?:json)?\s*/i, '')
      .replace(/```\s*$/, '')
      .trim();
    try {
      const p = JSON.parse(unfenced);
      if (p && typeof p === 'object' && p.files && typeof p.files === 'object') {
        parsed = p;
      }
    } catch (_) {
      const m = unfenced.match(/\{[\s\S]*\}/);
      if (m) {
        try {
          const p = JSON.parse(m[0]);
          if (p && typeof p === 'object' && p.files && typeof p.files === 'object') {
            parsed = p;
          }
        } catch (_) {}
      }
    }
  }
  if (!parsed || typeof parsed !== 'object'
      || !parsed.files || typeof parsed.files !== 'object') {
    return {
      files: null,
      runId,
      error: `子 Agent 返回不可解析为 {"files": {...}}：${trimmed.slice(0, 200)}`,
    };
  }
  return { files: parsed.files, runId, error: '' };
}

function readBrokerKey() {
  if (process.env.SKILLS_BROKER_KEY) {
    return Buffer.from(process.env.SKILLS_BROKER_KEY, 'utf8');
  }
  const keyPath = path.join(os.homedir(), '.skills-broker', 'broker.key');
  try {
    if (fs.existsSync(keyPath)) {
      const data = fs.readFileSync(keyPath).toString('utf8').trim();
      if (data) return Buffer.from(data, 'utf8');
    }
  } catch (_) {}
  return Buffer.from('');
}

function verifyDispatchToken(request, brokerKey) {
  if (!request.dispatch_token) {
    // 向后兼容：旧派发单没有下列字段时跳过 token 校验
    return true;
  }
  if (!brokerKey || brokerKey.length === 0) {
    return false;
  }
  const tokenPayload = {
    artifacts: request.artifacts || [],
    before: request.before || {},
    dispatch_id: request.dispatch_id || '',
  };
  const expected = crypto.createHmac('sha256', brokerKey)
    .update(Buffer.from(canonicalJson(tokenPayload), 'utf8'))
    .digest('hex');
  if (typeof request.dispatch_token !== 'string') return false;
  const bufExpected = Buffer.from(expected, 'hex');
  const bufActual = Buffer.from(request.dispatch_token, 'hex');
  if (bufExpected.length !== bufActual.length || bufExpected.length === 0) return false;
  return crypto.timingSafeEqual(bufExpected, bufActual);
}

export const inject = ['tools'];

export function apply(ctx, config = {}) {
  const pythonBin = config.pythonPath || 'python';
  const defaultPort = config.uiPort || 7860;
  const preloadWorkbench = config.preloadWorkbench !== false;

  // 启动本地 Loopback HTTP 控制服务 (端口 7862)
  startControlServer(7862, pythonBin, ctx);

  // 预加载工作台（在开始界面前置运行）
  if (preloadWorkbench) {
    const curDir = resolveCurrentProjectDir(ctx);
    ensureWorkbenchRunning({
      port: defaultPort,
      host: workbenchHost,
      input_dir: curDir,
      output_dir: path.join(curDir, 'self'),
    }, pythonBin, curDir).catch(() => {});
  }

  // 1. Register Tool: courseware_pipeline
  ctx.tools.register({
    name: 'courseware_pipeline',
    description: '竞赛教研与学案制作流水线驱动工具。支持启动流水线(run)、断点恢复(resume)、查询阶段状态(status)与导出计算图(visualize)。',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['status', 'run', 'resume', 'visualize', 'student_solution'],
          description: '流水线动作：status(查询状态) | run(启动) | resume(断点恢复) | visualize(导出Mermaid计算图) | student_solution(生成并编译学生解答版)',
        },
        project_root: {
          type: 'string',
          description: '项目目录根路径（默认为流水线工作区根目录）',
        },
        title: {
          type: 'string',
          description: '学案主标题（run 或 resume 时提供）',
        },
        mode: {
          type: 'string',
          enum: ['guided'],
          description: '教学模式：guided(引导型，唯一模式)，默认为 guided',
        },
        selected_problems: {
          type: 'array',
          items: { type: 'string' },
          description: '选定入选题号列表（run 时使用）',
        },
        thread_id: {
          type: 'string',
          description: '会话线程 ID（默认为 session_1）',
        },
        auth_token: {
          type: 'string',
          description: 'Phase 0 人工授权码（resume 恢复时必须提供真人确认的授权码，严禁 Agent 自行读取）',
        },
        deliver_teacher: {
          type: 'boolean',
          description: '是否交付 teacher.tex 及相应的 teacher.pdf 文件（可选项，默认关闭/false；只有单独声明为 true 时才交付；若为 false 或缺省则不交付，教研全流程完全保留）',
        },
      },
      required: ['action'],
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          action: { type: 'string' },
          success: { type: 'boolean' },
          code: { type: 'number' },
          output: { type: 'string' },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: value.output || (value.success ? '操作执行成功' : '操作执行失败'),
      }],
    },
    async execute(args) {
      const proj = args.project_root || workspaceRoot;
      const threadId = args.thread_id || 'session_1';
      const cliArgs = ['launcher.py', '--cli', '--project', proj, '--thread-id', threadId];

      if (args.deliver_teacher === true) {
        cliArgs.push('--deliver-teacher');
      } else if (args.deliver_teacher === false) {
        cliArgs.push('--no-deliver-teacher');
      }

      if (args.action === 'status') {
        cliArgs.push('--status');
        const res = await runPython(cliArgs, workspaceRoot, 60000, pythonBin);
        const combined = [res.stdout, res.stderr].filter(Boolean).join('\n');
        return {
          action: 'status',
          success: res.success,
          code: res.code,
          output: combined || (res.success ? '流水线无活跃状态' : `查询状态失败: ${res.error}`),
        };
      }

      if (args.action === 'visualize') {
        cliArgs.push('--visualize');
        const res = await runPython(cliArgs, workspaceRoot, 60000, pythonBin);
        return {
          action: 'visualize',
          success: res.success,
          code: res.code,
          output: res.stdout || res.stderr || '无法导出计算图',
        };
      }

      if (args.action === 'run') {
        cliArgs.push('--run');
        if (args.title) cliArgs.push('--title', args.title);
        if (args.mode) cliArgs.push('--mode', args.mode);
        if (args.selected_problems && args.selected_problems.length > 0) {
          cliArgs.push('--select', ...args.selected_problems);
        }
        const res = await runPython(cliArgs, workspaceRoot, 240000, pythonBin);
        const combined = [res.stdout, res.stderr].filter(Boolean).join('\n');
        return {
          action: 'run',
          success: res.success,
          code: res.code,
          output: combined || (res.success ? '流水线启动并执行完成' : `流水线运行出错: ${res.error}`),
        };
      }

      if (args.action === 'resume') {
        cliArgs.push('--resume');
        if (args.title) cliArgs.push('--title', args.title);
        if (args.mode) cliArgs.push('--mode', args.mode);
        if (args.auth_token) cliArgs.push('--auth-token', args.auth_token);
        const res = await runPython(cliArgs, workspaceRoot, 240000, pythonBin);
        const combined = [res.stdout, res.stderr].filter(Boolean).join('\n');
        return {
          action: 'resume',
          success: res.success,
          code: res.code,
          output: combined || (res.success ? '流水线已从断点恢复' : `断点恢复失败: ${res.error}`),
        };
      }

      if (args.action === 'student_solution') {
        const res = await runPython(['scripts/gen_student_solution.py', proj, '--compile'], workspaceRoot, 240000, pythonBin);
        const combined = [res.stdout, res.stderr].filter(Boolean).join('\n');
        return {
          action: 'student_solution',
          success: res.success,
          code: res.code,
          output: combined || (res.success ? '成功生成并编译 student_solution.pdf' : `生成学生解答失败: ${res.error}`),
        };
      }

      return {
        action: args.action,
        success: false,
        code: 1,
        output: `未知 action: ${args.action}`,
      };
    },
  });

  // 2. Register Tool: courseware_gate
  ctx.tools.register({
    name: 'courseware_gate',
    description: '运行竞赛教研流水线质量与安全门禁校验（probe, solution, architect, student_visibility, section_ownership, numbering, teaching, agent_audit 或 all）。',
    parameters: {
      type: 'object',
      properties: {
        gate: {
          type: 'string',
          enum: [
            'all',
            'agent_audit',
            'probe',
            'solution',
            'architect',
            'student_visibility',
            'section_ownership',
            'numbering',
            'teaching',
          ],
          description: '待校验门禁名称，all 表示运行全量门禁检查',
        },
        target_dir: {
          type: 'string',
          description: '检查目标目录（默认为流水线工作区）',
        },
      },
      required: ['gate'],
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          gate: { type: 'string' },
          passed: { type: 'boolean' },
          report: { type: 'string' },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: value.report,
      }],
    },
    async execute(args) {
      const proj = resolveCurrentProjectDir(ctx, args.target_dir);
      const reports = [];
      let allPassed = true;

      // Helper to discover files matching a name in a directory
      const findFiles = (dir, targetName) => {
        const results = [];
        if (!fs.existsSync(dir)) return results;
        const scan = (d) => {
          for (const ent of fs.readdirSync(d, { withFileTypes: true })) {
            const full = path.join(d, ent.name);
            if (ent.isDirectory()) {
              scan(full);
            } else if (ent.name === targetName) {
              results.push(full);
            }
          }
        };
        scan(dir);
        return results;
      };

      const runDirectGate = async (name, scriptRel) => {
        const res = await runPython([scriptRel, proj], workspaceRoot, 60000, pythonBin);
        const pass = res.code === 0;
        if (!pass) allPassed = false;
        const icon = pass ? '✔ PASS' : '✖ FAIL';
        const msg = [res.stdout, res.stderr].filter(Boolean).join('\n') || (pass ? '门禁通过' : '门禁未通过');
        reports.push(`[${icon}] ${name}:\n${msg}`);
      };

      const runFileGate = async (name, scriptRel, targetFileName) => {
        const probDir = path.join(proj, 'research', 'problems');
        const found = findFiles(probDir, targetFileName);
        if (found.length === 0) {
          reports.push(`[⚪ SKIP] ${name}: 未在 research/problems/ 目录下找到 ${targetFileName} 文件`);
          return;
        }
        for (const file of found) {
          const relFile = path.relative(proj, file);
          const res = await runPython([scriptRel, proj, relFile], workspaceRoot, 60000, pythonBin);
          const pass = res.code === 0;
          if (!pass) allPassed = false;
          const icon = pass ? '✔ PASS' : '✖ FAIL';
          const msg = [res.stdout, res.stderr].filter(Boolean).join('\n') || (pass ? '门禁通过' : '门禁未通过');
          reports.push(`[${icon}] ${name} (${relFile}):\n${msg}`);
        }
      };

      const gateHandlers = {
        agent_audit: () => runDirectGate('agent_audit', 'scripts/check_agent_audit.py'),
        research: () => runDirectGate('research', 'scripts/check_research.py'),
        architect: () => runDirectGate('architect', 'scripts/check_architect.py'),
        student_task: () => runDirectGate('student_task', 'scripts/check_student_task.py'),
        student_visibility: () => runDirectGate('student_visibility', 'scripts/check_student_visibility.py'),
        teaching: () => runDirectGate('teaching', 'scripts/check_teaching.py'),
        numbering: () => runDirectGate('numbering', 'scripts/check_numbering.py'),
        probe: () => runFileGate('probe', 'scripts/check_probe.py', 'probe.json'),
        solution: () => runFileGate('solution', 'scripts/check_solution.py', 'solution.json'),
      };

      if (args.gate === 'all') {
        for (const [gName, handler] of Object.entries(gateHandlers)) {
          await handler();
        }
      } else if (gateHandlers[args.gate]) {
        await gateHandlers[args.gate]();
      } else {
        reports.push(`[?] 门禁 ${args.gate}: 未知门禁项`);
        allPassed = false;
      }

      const summary = `=== 门禁检查报告 (${args.gate}) ===\n总体状态: ${allPassed ? '全部通过 (ALL PASS)' : '存在未通过或跳过项'}\n\n` + reports.join('\n\n');
      return {
        gate: args.gate,
        passed: allPassed,
        report: summary,
      };
    },
  });

  // 3. Register Tool: courseware_workbench
  ctx.tools.register({
    name: 'courseware_workbench',
    description: '管理与监控 LangGraph 竞赛教研流水线的可视化 Web UI 工作台 (launcher.py)。支持 start / status / stop。',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['start', 'status', 'stop'],
          description: '操作：start(启动工作台) | status(检查运行状态) | stop(停止工作台)',
        },
        port: {
          type: 'number',
          description: '服务监听端口（默认: 7860）',
        },
        host: {
          type: 'string',
          description: '服务监听主机（默认: 127.0.0.1）',
        },
      },
      required: ['action'],
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          action: { type: 'string' },
          running: { type: 'boolean' },
          url: { type: 'string' },
          message: { type: 'string' },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: `[可视化工作台 ${value.action}]\n状态: ${value.running ? '运行中' : '未运行'}\n地址: ${value.url || 'N/A'}\n${value.message || ''}`,
      }],
    },
    async execute(args) {
      const port = args.port || defaultPort;
      const host = args.host || '127.0.0.1';

      if (args.action === 'status') {
        const isRunning = workbenchProcess !== null && !workbenchProcess.killed;
        return {
          action: 'status',
          running: isRunning,
          url: isRunning ? `http://${workbenchHost}:${workbenchPort}` : '',
          message: isRunning ? `工作台正在 http://${workbenchHost}:${workbenchPort} 运行` : '工作台当前未在运行',
        };
      }

      if (args.action === 'stop') {
        if (workbenchProcess && !workbenchProcess.killed) {
          try {
            workbenchProcess.kill();
          } catch (_) {}
          workbenchProcess = null;
          return {
            action: 'stop',
            running: false,
            url: '',
            message: '已成功停止可视化工作台',
          };
        }
        return {
          action: 'stop',
          running: false,
          url: '',
          message: '工作台原本未在运行',
        };
      }

      if (args.action === 'start') {
        const curDir = resolveCurrentProjectDir(ctx);
        const res = await ensureWorkbenchRunning({
          port,
          host,
          input_dir: curDir,
          output_dir: path.join(curDir, 'self'),
        }, pythonBin, curDir);
        return {
          action: 'start',
          running: res.running,
          url: res.url,
          message: res.message,
        };
      }

      return {
        action: args.action,
        running: false,
        url: '',
        message: `未知操作: ${args.action}`,
      };
    },
  });

  // 4. Register Tool: courseware_dispatch（host-subagent-v1 证据链通道）
  ctx.tools.register({
    name: 'courseware_dispatch',
    description: '把竞赛教研派发单派发给宿主 subagent 子 Agent 执行：渲染派发提示词（prompts/dispatch_prompt.md）→ 子 Agent 生成产物 → 按 request.artifacts 白名单原子写入 teaching/ → 落证据台账 teaching/agent-calls/<dispatch_id>.json（含 worker/provider/model/摘要）。AGENT_DISPATCH 中断恢复后必须先调本工具；全部 dispatch_id 为 ok 后才可 courseware_pipeline action=resume。',
    parameters: {
      type: 'object',
      properties: {
        request_ref: {
          type: 'string',
          description: '单个派发单 JSON 路径（相对 project_root 或绝对路径）；与 batch_id 二选一',
        },
        batch_id: {
          type: 'string',
          description: '批次 ID：派发 teaching/dispatch-plan.json 中该批次全部派发单；与 request_ref 二选一',
        },
        project_root: {
          type: 'string',
          description: '项目目录根路径（缺省自动探测）',
        },
        max_concurrency: {
          type: 'integer',
          description: '子 Agent 并发上限（缺省 2）',
        },
      },
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          success: { type: 'boolean' },
          project_root: { type: 'string' },
          dispatched: { type: 'integer' },
          ok: { type: 'integer' },
          failed: { type: 'integer' },
          results: { type: 'array' },
          report: { type: 'string' },
        },
      },
      render: (_args, value) => [{ type: 'text', text: value.report }],
    },
    async execute(args, exec) {
      const projectRoot = resolveCurrentProjectDir(ctx, args.project_root, args.request_ref);
      const fail = (msg) => ({
        success: false,
        project_root: projectRoot,
        dispatched: 0,
        ok: 0,
        failed: 0,
        results: [],
        report: `❌ ${msg}`,
      });

      // ---- 解析待派发请求 -------------------------------------------------
      const resolveRequests = () => {
        if (args.request_ref) {
          let abs = '';
          if (path.isAbsolute(args.request_ref)) {
            abs = args.request_ref;
          } else {
            const candidates = [
              path.join(projectRoot, args.request_ref),
              path.join(projectRoot, 'teaching', 'agent-requests', args.request_ref),
              path.join(projectRoot, 'teaching', args.request_ref),
            ];
            for (const c of candidates) {
              if (fs.existsSync(c)) {
                abs = c;
                break;
              }
            }
            if (!abs) abs = candidates[0];
          }
          const one = cdReadJson(abs);
          return one ? [one] : [];
        }
        if (args.batch_id) {
          let plan = cdReadJson(path.join(projectRoot, 'teaching', 'dispatch-plan.json'));
          if (!plan) plan = cdReadJson(path.join(projectRoot, 'dispatch-plan.json'));
          const planBatch = String((plan && plan.batch_id) || '');
          const ids = ((plan && Array.isArray(plan.requests)) ? plan.requests : [])
            .filter((r) => String((r || {}).batch_id || planBatch) === String(args.batch_id))
            .map((r) => String((r || {}).dispatch_id || ''));
          const out = [];
          for (const id of ids) {
            let full = cdReadJson(path.join(projectRoot, 'teaching', 'agent-requests', `${id}.json`));
            if (!full) full = cdReadJson(path.join(projectRoot, 'agent-requests', `${id}.json`));
            if (full) out.push(full);
          }
          return out;
        }
        let dir = path.join(projectRoot, 'teaching', 'agent-requests');
        if (!fs.existsSync(dir)) {
          const alt = path.join(projectRoot, 'agent-requests');
          if (fs.existsSync(alt)) dir = alt;
        }
        let names = [];
        try {
          names = fs.readdirSync(dir).filter((f) => f.endsWith('.json')).sort();
        } catch (_) {}
        const out = [];
        for (const name of names) {
          const r = cdReadJson(path.join(dir, name));
          if (r && String(r.status || '') === 'pending') out.push(r);
        }
        return out;
      };

      const requests = resolveRequests();
      if (requests.length === 0) {
        return {
          success: false,
          project_root: projectRoot,
          dispatched: 0,
          ok: 0,
          failed: 0,
          results: [],
          report: '未解析到待派发请求（request_ref/batch_id 无匹配，或 teaching/agent-requests 下无 status=pending 的派发单）。',
        };
      }

      // ---- 派发配置与并发数 ------------------------------------------------
      // 与 Python 状态机同源：agent_evidence.load_dispatch_config(<project_root>)
      // 读取 config.yaml 的 agent_dispatch 块（model_map / max_concurrency / allow_unattested）。
      const readDispatchConfig = async () => {
        const snippet = [
          'import json, sys',
          'sys.path.insert(0, sys.argv[1])',
          'from agent_evidence import load_dispatch_config',
          'print(json.dumps(load_dispatch_config(sys.argv[2]), ensure_ascii=False))',
        ].join('\n');
        try {
          const res = await runPython(
            ['-c', snippet, path.join(workspaceRoot, 'workflow', 'nodes'), projectRoot],
            workspaceRoot, 60000, pythonBin);
          const lines = String(res.stdout || '').trim().split('\n').filter(Boolean);
          if (lines.length) {
            const parsed = JSON.parse(lines[lines.length - 1]);
            if (parsed && typeof parsed === 'object') return parsed;
          }
        } catch (_) {}
        return { max_concurrency: 2, max_retries: 2, model_map: {} };
      };
      const dispatchCfg = await readDispatchConfig();
      const cfgMaxRetries = Number(dispatchCfg.max_retries);
      const maxConcurrency = Math.max(1, Math.min(
        Number(args.max_concurrency) || Number(dispatchCfg.max_concurrency) || 2, 16));
      const modelMap = (dispatchCfg.model_map && typeof dispatchCfg.model_map === 'object')
        ? dispatchCfg.model_map : {};
      const stageRoute = (stage) => {
        const s = String(stage || '').toUpperCase();
        for (const key of [s.split('_')[0], s]) {
          const route = modelMap[key];
          if (route && route.provider && route.model) {
            return { provider: String(route.provider), model: String(route.model) };
          }
        }
        return {};
      };

      // ---- 宿主 subagent 工具解析 -----------------------------------------
      const subagentDef = (() => {
        try {
          const tools = ctx?.tools || ctx?.get?.('tools');
          if (tools && typeof tools.get === 'function') {
            const def = tools.get('subagent');
            if (def && typeof def.execute === 'function') return def;
          }
        } catch (_) {}
        return null;
      })();
      const callSubagent = async (subArgs) => {
        if (subagentDef) return subagentDef.execute(subArgs, exec);
        try {
          const tools = ctx?.tools || ctx?.get?.('tools');
          if (tools && typeof tools.execute === 'function') {
            return tools.execute({
              name: 'subagent',
              arguments: subArgs,
              agent: exec && exec.agent,
              signal: exec && exec.signal,
              callId: exec && exec.callId,
            });
          }
        } catch (_) {}
        const subagents = ctx?.get?.('subagents') || ctx?.subagents;
        if (subagents && typeof subagents.start === 'function') {
          const run = await subagents.start('spawn', {
            label: subArgs.description || 'courseware subagent',
            prompt: [{ type: 'text', text: subArgs.prompt }],
            parent: exec && exec.agent,
            signal: exec && exec.signal,
          });
          const result = await run.result;
          if (typeof run.dispose === 'function') {
            try { run.dispose(); } catch (_) {}
          }
          return result;
        }
        throw new Error('宿主 subagent 工具不可用（ctx.tools.get("subagent") 与 subagents 服务均缺失）');
      };

      // ---- 提示词渲染 ------------------------------------------------------
      const buildPrompt = (request) => {
        const promptFile = String(request.prompt_file || 'prompts/dispatch_prompt.md')
          .replace(/^prompts\//, '');
        let tpl = '';
        try {
          tpl = fs.readFileSync(path.join(workspaceRoot, 'prompts', promptFile), 'utf8');
        } catch (_) {
          try {
            tpl = fs.readFileSync(path.join(workspaceRoot, 'prompts', 'dispatch_prompt.md'), 'utf8');
          } catch (_) {
            tpl = '';
          }
        }
        const ctxObj = (request.context && typeof request.context === 'object') ? request.context : {};
        const artifacts = (Array.isArray(request.artifacts) ? request.artifacts : [])
          .filter((a) => typeof a === 'string' && a);
        const values = {};
        const assign = (key, val) => {
          if (key && val != null && val !== '' && values[key] == null) values[key] = val;
        };
        for (const [k, v] of Object.entries(ctxObj)) {
          if (v == null || typeof v === 'object') continue;
          assign(k, v);
          assign(k.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toUpperCase(), v);
        }
        assign('DISPATCH_ID', request.dispatch_id || '');
        assign('BATCH_ID', request.batch_id || '');
        assign('STAGE', request.stage || '');
        assign('SCOPE', request.scope || '');
        assign('ROLE', request.role || '');
        assign('INSTRUCTION', request.instruction || '');
        assign('PROBLEM_ID', ctxObj.problem_id || request.scope || '');

        let contextDetails = '';
        if (ctxObj.details) {
          contextDetails = typeof ctxObj.details === 'string' ? ctxObj.details : JSON.stringify(ctxObj.details, null, 2);
        } else if (Object.keys(ctxObj).length > 0) {
          contextDetails = '```json\n' + JSON.stringify(ctxObj, null, 2) + '\n```';
        }
        assign('CONTEXT_DETAILS', contextDetails);

        const rendered = cdRenderTemplate(tpl, values);
        const taskBlock = [
          '',
          '## 派发任务书（机器可读）',
          `- dispatch_id: ${request.dispatch_id || ''}`,
          `- batch_id: ${request.batch_id || ''}`,
          `- stage: ${request.stage || ''}`,
          `- scope: ${request.scope || ''}`,
          `- role: ${request.role || ''}`,
          '## 项目根目录 (project_root)',
          projectRoot,
          '## 执行指令',
          String(request.instruction || ''),
          request.command ? `## 提交命令\n${request.command}` : '',
          '## 只允许写这些产物（白名单，越界写入将被拒绝）',
          ...artifacts.map((a) => `- ${a}`),
          '## 返回格式（必须是可解析 JSON，勿包裹多余文字）',
          '{"files": {"<相对路径>": "<完整文件内容>"}}',
          '注意：files 的键只能是上面白名单中的相对路径；内容为完整文件文本。严禁自行调用文件系统工具写盘。',
        ].join('\n');
        return rendered + '\n' + taskBlock;
      };

      // ---- 单派发单执行 ----------------------------------------------------
      const dispatchOne = async (request) => {
        const dispatchId = String(request.dispatch_id || '');
        const artifacts = (Array.isArray(request.artifacts) ? request.artifacts : [])
          .filter((a) => typeof a === 'string' && a);
        const before = (request.before && typeof request.before === 'object')
          ? request.before : {};
        const maxRetries = Number.isFinite(Number(request.max_retries))
          ? Math.max(0, Number(request.max_retries))
          : (Number.isFinite(cfgMaxRetries) ? Math.max(0, cfgMaxRetries) : 2);
        const route = stageRoute(request.stage);

        const bKey = readBrokerKey();
        // 校验 dispatch_token
        if (!verifyDispatchToken(request, bKey)) {
          return {
            dispatch_id: dispatchId,
            scope: request.scope || '',
            stage: request.stage || '',
            status: 'token_invalid',
            attempts: 0,
            evidence: '',
            error: '派发单 dispatch_token 校验失败：可能被篡改或 broker 密钥不匹配',
          };
        }

        const prompt = buildPrompt(request);
        let status = 'worker_error';
        let error = '未执行';
        let evidenceAbs = '';
        let attempts = 0;

        for (let attempt = 1; attempt <= maxRetries + 1; attempt += 1) {
          attempts = attempt;
          const startedAt = new Date();
          let parsed = { files: null, runId: '', error: '' };
          try {
            const raw = await callSubagent({
              description: `courseware ${request.stage || ''} ${request.scope || dispatchId}（${request.role || '子 Agent'}）`,
              prompt,
              run_in_background: false,
              ...(route.provider && route.model
                ? { provider: route.provider, model: route.model } : {}),
            });
            parsed = cdParseSubagentFiles(raw);
          } catch (err) {
            parsed = {
              files: null,
              runId: '',
              error: `子 Agent 调用失败: ${err && err.message ? err.message : String(err)}`,
            };
          }
          const finishedAt = new Date();
          const written = [];
          const outputDigests = {};
          status = 'ok';
          error = '';

          if (!parsed.files) {
            status = 'worker_error';
            error = parsed.error || '子 Agent 未返回可用产物';
          } else {
            const findContent = (rel) => {
              if (rel in parsed.files) return parsed.files[rel];
              const norm1 = rel.replace(/\\/g, '/');
              if (norm1 in parsed.files) return parsed.files[norm1];
              const norm2 = rel.replace(/\//g, '\\');
              if (norm2 in parsed.files) return parsed.files[norm2];
              const base = path.basename(norm1);
              if (base in parsed.files) return parsed.files[base];
              for (const [k, v] of Object.entries(parsed.files)) {
                if (k.replace(/\\/g, '/') === norm1 || path.basename(k.replace(/\\/g, '/')) === base) {
                  return v;
                }
              }
              return undefined;
            };

            for (const rel of artifacts) {
              const content = findContent(rel);
              if (content === undefined) continue;
              const abs = cdSafeJoin(projectRoot, rel);
              if (!abs) {
                status = 'worker_error';
                error = `产物路径非法（越界或绝对路径）: ${rel}`;
                break;
              }
              try {
                let toWrite = '';
                if (typeof content === 'string') {
                  if (rel.toLowerCase().endsWith('.json')) {
                    try {
                      toWrite = JSON.stringify(JSON.parse(content), null, 2);
                    } catch (_) {
                      toWrite = content;
                    }
                  } else {
                    toWrite = content;
                  }
                } else {
                  toWrite = JSON.stringify(content, null, 2);
                }
                cdAtomicWrite(abs, toWrite);
                written.push(rel);
                outputDigests[rel] = cdSha256File(abs);
              } catch (err) {
                status = 'worker_error';
                error = `写入 ${rel} 失败: ${err && err.message ? err.message : String(err)}`;
                break;
              }
            }

            if (status === 'ok') {
              const missing = artifacts.filter((rel) => findContent(rel) === undefined);
              if (written.length === 0) {
                status = 'missing_artifacts';
                error = `子 Agent 未提交任何白名单产物: ${artifacts.join(', ')}`;
              } else if (missing.length > 0) {
                status = 'missing_artifacts';
                error = `部分产物缺失: ${missing.join(', ')}`;
              } else {
                status = 'ok';
                error = '';
              }
            }
          }

          if (dispatchId) {
            const worker = parsed.runId
              ? `subagent:courseware-${parsed.runId}`
              : `subagent:courseware-${dispatchId}`;
            const evidenceRec = {
              schema_version: 1,
              dispatch_id: dispatchId,
              batch_id: request.batch_id || null,
              worker,
              provider: route.provider || 'host-default',
              model: route.model || 'host-default',
              started_at: startedAt.toISOString(),
              finished_at: finishedAt.toISOString(),
              duration_ms: finishedAt.getTime() - startedAt.getTime(),
              artifacts_written: written,
              output_digests: outputDigests,
              status,
              attempts: attempt,
              tool: 'courseware_dispatch',
            };
            if (error) evidenceRec.error = error;

            if (bKey && bKey.length > 0) {
              evidenceRec.signature = crypto.createHmac('sha256', bKey)
                .update(Buffer.from(canonicalJson(evidenceRec), 'utf8'))
                .digest('hex');
            } else {
              evidenceRec.signature = '';
            }

            try {
              const callsDir = path.join(projectRoot, 'teaching', 'agent-calls');
              const absEv = path.join(callsDir, `${dispatchId}.json`);
              cdAtomicWrite(absEv, JSON.stringify(evidenceRec, null, 2));
              evidenceAbs = absEv;
            } catch (_) {}
          }

          if (status === 'ok') break;
        }

        return {
          dispatch_id: dispatchId,
          scope: request.scope || '',
          stage: request.stage || '',
          status,
          attempts,
          evidence: evidenceAbs,
          error: error || null,
        };
      };

      // ---- 并发执行（信号量 ≤ maxConcurrency） -----------------------------
      const results = new Array(requests.length);
      let cursor = 0;
      const runners = [];
      const laneCount = Math.max(1, Math.min(maxConcurrency, requests.length));
      for (let i = 0; i < laneCount; i += 1) {
        runners.push((async () => {
          while (cursor < requests.length) {
            const idx = cursor;
            cursor += 1;
            try {
              results[idx] = await dispatchOne(requests[idx]);
            } catch (err) {
              results[idx] = {
                dispatch_id: String((requests[idx] || {}).dispatch_id || ''),
                scope: String((requests[idx] || {}).scope || ''),
                stage: String((requests[idx] || {}).stage || ''),
                status: 'worker_error',
                attempts: 0,
                evidence: '',
                error: err && err.message ? err.message : String(err),
              };
            }
          }
        })());
      }
      await Promise.all(runners);

      const okCount = results.filter((r) => r.status === 'ok').length;
      const lines = results.map((r) => `- [${r.status === 'ok' ? 'ok' : 'FAIL'}] `
        + `${r.dispatch_id} (${r.scope}) → ${r.status}`
        + (r.error ? `: ${r.error}` : '')
        + (r.evidence ? ` ｜ 证据: ${r.evidence}` : ''));
      const report = [
        `=== courseware_dispatch 派发汇总（project_root: ${projectRoot}）===`,
        `派发单: ${results.length}，ok: ${okCount}，失败: ${results.length - okCount}（并发上限 ${maxConcurrency}）`,
        ...lines,
        okCount === results.length
          ? '全部派发单已由子 Agent 完成并落证据台账；现在可调用 courseware_pipeline action=resume 恢复流水线。'
          : '存在失败派发单：修正后重试本工具或走返修环；切勿由主 Agent 直接撰写产物（将判 ok_unattested 失败并被门禁拒绝）。',
      ].join('\n');

      return {
        success: okCount === results.length,
        project_root: projectRoot,
        dispatched: results.length,
        ok: okCount,
        failed: results.length - okCount,
        results,
        report,
      };
    },
  });

  // 5. Register Human Command: /courseware
  const commandsService = ctx.get('commands');
  if (commandsService) {
    commandsService.register({
      name: 'courseware',
      description: '竞赛教研流水线交互指令 (/courseware status | run | resume | gates | ui | help)',
      input: {
        hint: 'status | run [--title <t>] | resume --title <t> --auth-token <tok> | gates | ui [start|stop]',
      },
      async handler(inv) {
        const raw = (inv.rawInput || '').trim();
        const parts = raw.split(/\s+/).filter(Boolean);
        const sub = parts[0] || 'help';

        if (sub === 'help') {
          return {
            kind: 'success',
            text: [
              '📖 **竞赛教研流水线指令指南**',
              '- `/courseware status` : 查看当前流水线状态与检查点',
              '- `/courseware run [--title <标题>] [--mode guided]` : 启动教研流水线（教学模式固定为 guided 分步引导）',
              '- `/courseware resume --title <标题> --auth-token <授权码>` : 从 Phase 0 断点恢复执行',
              '- `/courseware solution` : 生成并编译学生解答版 (student_solution.tex & pdf)',
              '- `/courseware gates` : 运行全部质量与安全门禁校验',
              '- `/courseware ui [start|status|stop]` : 管理 Gradio 可视化工作台 (http://127.0.0.1:7860)',
            ].join('\n'),
          };
        }

        if (sub === 'solution' || sub === 'student_solution') {
          const res = await runPython(['scripts/gen_student_solution.py', '.', '--compile'], workspaceRoot, 120000, pythonBin);
          return {
            kind: res.success ? 'success' : 'error',
            text: `[学生解答交付产物生成]\n${res.stdout || res.stderr}`,
          };
        }

        if (sub === 'status') {
          const res = await runPython(['launcher.py', '--cli', '--status'], workspaceRoot, 30000, pythonBin);
          return {
            kind: res.success ? 'success' : 'error',
            text: res.stdout || res.stderr || '无活跃流水线状态',
          };
        }

        if (sub === 'gates') {
          const res = await runPython(['scripts/check_probe.py', '.'], workspaceRoot, 30000, pythonBin);
          return {
            kind: res.success ? 'success' : 'error',
            text: `[门禁探针校验]\n${res.stdout || res.stderr}`,
          };
        }

        if (sub === 'ui') {
          const action = parts[1] || 'status';
          if (action === 'start') {
            if (workbenchProcess && !workbenchProcess.killed) {
              return { kind: 'success', text: `工作台已在运行: http://${workbenchHost}:${workbenchPort}` };
            }
            workbenchProcess = spawn(
              pythonBin,
              ['launcher.py', '--port', String(workbenchPort), '--no-browser'],
              { cwd: workspaceRoot, stdio: 'ignore' }
            );
            return { kind: 'success', text: `工作台已拉起: http://${workbenchHost}:${workbenchPort}` };
          }
          if (action === 'stop') {
            if (workbenchProcess) {
              workbenchProcess.kill();
              workbenchProcess = null;
            }
            return { kind: 'success', text: '工作台已停止' };
          }
          const isRunning = workbenchProcess && !workbenchProcess.killed;
          return {
            kind: 'success',
            text: isRunning ? `工作台运行中: http://${workbenchHost}:${workbenchPort}` : '工作台未运行',
          };
        }

        return {
          kind: 'success',
          text: `收到子指令: ${sub}。请使用 /courseware help 查看完整语法。`,
        };
      },
    });
  }

  // 6. Inject Agent Prompt Context
  const systemPromptService = ctx.get('systemPrompt');
  if (systemPromptService && typeof systemPromptService.context === 'function') {
    systemPromptService.context({
      name: 'competition-courseware-context',
      order: 120,
      text: [
        '【竞赛教研流水线 (LangGraph) 协作者规则】',
        '1. 当前工作区配备了完整的数学竞赛教研流水线系统（competition-courseware）。',
        '2. 你可以通过 `courseware_pipeline` 工具启动(run)、恢复(resume)和监控流水线状态。',
        '3. 物理人机断点红线：当流水线在 Phase 0 断点挂起时，绝对禁止自行读取 `.langgraph/phase0_token.txt` 或静默恢复！必须先通过 `ask_user_question` 向真人用户确认学案主标题（教学模式固定为 guided 分步引导），并索取真人提供的授权码，再调用 `courseware_pipeline` action: resume 恢复。',
        '4. 派发与门禁：探针(probe)、解答(solver)、架构(architect)产物严格由零信任门禁校验，可使用 `courseware_gate` 随时进行多维度质量门禁合规检查。',
        '5. 交付产物规范：默认情况下，流水线不交付 teacher.tex 与相应的 teacher.pdf 文件，仅交付 student.pdf（及学生解答版/延迟发放页）；所有教研（题面探针、教师解法与证明、卡点雷达等）照常完整保留。仅在显式单独声明 deliver_teacher 为 true 时才交付教师版文件。',
        '6. Agent 派发红线：收到流水线 AGENT_DISPATCH 中断时，必须调用 `courseware_dispatch` 工具派发独立子 Agent 完成产物；严禁主 Agent 在本会话内直接撰写派发单 artifacts 声明的任何文件。无代理证据的产物在磁盘验证按 ok_unattested 失败处理，交付门禁 FAIL。',
        '7. 派发完成后方可调用 `courseware_pipeline action=resume` 恢复流水线；resume 前先用 `courseware_dispatch` 返回的汇总核对全部 dispatch_id 状态为 ok。',
      ].join('\n'),
    });
  }

  // Teardown cleanup
  ctx.on('dispose', () => {
    if (workbenchProcess && !workbenchProcess.killed) {
      try {
        workbenchProcess.kill();
      } catch (_) {}
      workbenchProcess = null;
    }
    if (controlServer) {
      try {
        controlServer.close();
      } catch (_) {}
      controlServer = null;
    }
  });
}
