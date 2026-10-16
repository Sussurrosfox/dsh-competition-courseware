import { execFile, spawn } from 'node:child_process';
import * as path from 'node:path';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as net from 'node:net';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const workspaceRoot = __dirname;

let workbenchProcess = null;
let workbenchPort = 7860;
let workbenchHost = '127.0.0.1';
let controlServer = null;

/**
 * 动态探测当前活跃的项目工作目录
 */
function resolveCurrentProjectDir(ctx) {
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
      try {
        fs.appendFileSync(path.join(workspaceRoot, 'apply.log'), `\ncontrolServer listening on ${port}`);
      } catch (_) {}
    });

    controlServer.on('error', (err) => {
      try {
        fs.appendFileSync(path.join(workspaceRoot, 'apply.log'), `\ncontrolServer error: ${err.message}`);
      } catch (_) {}
    });
  } catch (err) {
    try {
      fs.appendFileSync(path.join(workspaceRoot, 'apply.log'), `\nstartControlServer catch: ${err.message}`);
    } catch (_) {}
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

export const inject = ['tools'];

export function apply(ctx, config = {}) {
  try {
    fs.writeFileSync(path.join(workspaceRoot, 'apply.log'), 'apply called at ' + new Date().toISOString() + '\nconfig: ' + JSON.stringify(config));
  } catch (_) {}
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
    description: '运行竞赛教研流水线质量与安全门禁校验（probe, solution, architect, student_visibility, section_ownership, numbering, teaching 或 all）。',
    parameters: {
      type: 'object',
      properties: {
        gate: {
          type: 'string',
          enum: [
            'all',
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
      const proj = args.target_dir || workspaceRoot;
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

  // 4. Register Human Command: /courseware
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

  // 5. Inject Agent Prompt Context
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
