window.__ModuleLoader__.load({
  id: '@local/competition-courseware',
  factory(require) {
    const React = require('react');
    const { useState, useEffect, useRef, createElement: h } = React;

    const CONTROL_URL = 'http://127.0.0.1:7862';
    const WORKBENCH_URL = 'http://127.0.0.1:7860';

    /**
     * 轮询与探测工作台及控制服务器状态
     */
    function probeWorkbenchStatus(onSettled) {
      fetch(`${CONTROL_URL}/api/status`, { mode: 'cors' })
        .then(res => res.json())
        .then(data => {
          onSettled({
            running: !!data.running,
            url: data.url || WORKBENCH_URL,
            currentWorkspace: data.currentWorkspace || '',
            outputWorkspace: data.outputWorkspace || '',
          });
        })
        .catch(() => {
          // 如果 7862 暂时连不上，直接探测 7860
          fetch(WORKBENCH_URL, { mode: 'no-cors' })
            .then(() => {
              onSettled({
                running: true,
                url: WORKBENCH_URL,
                currentWorkspace: '',
                outputWorkspace: '',
              });
            })
            .catch(() => {
              onSettled({
                running: false,
                url: WORKBENCH_URL,
                currentWorkspace: '',
                outputWorkspace: '',
              });
            });
        });
    }

    /**
     * 打开或启动工作台（带新窗口防弹窗拦截防护）
     */
    function handleOpenOrStart(status, setStatus) {
      if (status.running) {
        window.open(status.url || WORKBENCH_URL, '_blank');
        return;
      }

      // 同步打开过渡页面，避免异步 window.open 被浏览器拦截
      const transitTab = window.open('', '_blank');
      if (transitTab) {
        transitTab.document.write(`
          <!DOCTYPE html>
          <html>
          <head>
            <meta charset="utf-8">
            <title>正在拉起竞赛教研流水线工作台...</title>
            <style>
              body {
                margin: 0; padding: 40px 20px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #f8fafc; color: #1e293b; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 70vh;
              }
              .card {
                background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 32px 40px; text-align: center;
                box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05); max-width: 480px; width: 100%;
              }
              .spinner {
                width: 36px; height: 36px; border: 3px solid #e2e8f0; border-top-color: #2563eb; border-radius: 50%;
                animation: spin 0.8s linear infinite; margin: 0 auto 20px auto;
              }
              @keyframes spin { to { transform: rotate(360deg); } }
              h2 { font-size: 18px; margin: 0 0 10px 0; color: #0f172a; }
              p { font-size: 13px; color: #64748b; margin: 0; line-height: 1.5; }
            </style>
          </head>
          <body>
            <div class="card">
              <div class="spinner"></div>
              <h2>正在启动竞赛教研工作台...</h2>
              <p>系统正在后台预热 LangGraph 状态机与 Gradio 服务，就绪后将立即自动为您呈现。</p>
            </div>
          </body>
          </html>
        `);
      }

      setStatus(prev => ({ ...prev, starting: true }));

      // 发起拉起请求
      fetch(`${CONTROL_URL}/api/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ port: 7860 })
      })
      .then(res => res.json())
      .catch(() => ({}))
      .finally(() => {
        // 轮询最多 15 秒等待工作台就绪
        let attempts = 0;
        const timer = setInterval(() => {
          attempts++;
          fetch(WORKBENCH_URL, { mode: 'no-cors' })
            .then(() => {
              clearInterval(timer);
              setStatus(prev => ({ ...prev, running: true, starting: false }));
              if (transitTab && !transitTab.closed) {
                transitTab.location.href = WORKBENCH_URL;
              } else {
                window.open(WORKBENCH_URL, '_blank');
              }
            })
            .catch(() => {
              if (attempts >= 20) {
                clearInterval(timer);
                setStatus(prev => ({ ...prev, starting: false }));
                if (transitTab && !transitTab.closed) {
                  transitTab.document.body.innerHTML = `
                    <div style="font-family:sans-serif;padding:40px;text-align:center;color:#ef4444;">
                      <h3>工作台拉起超时</h3>
                      <p style="color:#64748b;">请检查端口 7860 是否冲突，或在终端查看 workbench.log 日志。</p>
                    </div>
                  `;
                }
              }
            });
        }, 800);
      });
    }

    /**
     * 1. 顶栏常驻核心组件 (conversation.header.leading)
     * 开始界面（Hero 状态）和会话中均永久置顶可见
     */
    function CoursewareHeaderBar() {
      const [status, setStatus] = useState({
        running: false,
        starting: false,
        url: WORKBENCH_URL,
        currentWorkspace: '',
        outputWorkspace: '',
      });

      useEffect(() => {
        probeWorkbenchStatus(data => {
          setStatus(prev => ({ ...prev, ...data }));
        });
        const interval = setInterval(() => {
          probeWorkbenchStatus(data => {
            setStatus(prev => ({ ...prev, ...data }));
          });
        }, 5000);
        return () => clearInterval(interval);
      }, []);

      const folderName = status.currentWorkspace ? status.currentWorkspace.replace(/\\/g, '/').split('/').filter(Boolean).pop() : '';

      return h('div', {
        style: {
          display: 'inline-flex',
          alignItems: 'center',
          gap: '8px',
          padding: '3px 10px',
          borderRadius: '20px',
          backgroundColor: 'var(--dsw-alias-bg-layer-1, #f8f9fa)',
          border: '1px solid var(--dsw-alias-border-l1, #e2e8f0)',
          fontSize: '12px',
          color: 'var(--dsw-alias-label-primary, #1e293b)',
          boxShadow: '0 1px 2px rgba(0, 0, 0, 0.04)',
        }
      },
        // 标题与图标
        h('span', {
          style: {
            fontWeight: '600',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            color: 'var(--dsw-alias-brand-primary, #2563eb)'
          }
        }, '📐 教研工作台'),

        // 状态徽标
        h('span', {
          style: {
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            padding: '1px 6px',
            borderRadius: '10px',
            fontSize: '11px',
            backgroundColor: status.running
              ? 'rgba(22, 163, 74, 0.1)'
              : (status.starting ? 'rgba(217, 119, 6, 0.1)' : 'var(--dsw-alias-bg-layer-2, #f1f5f9)'),
            color: status.running
              ? 'var(--dsw-alias-state-success-primary, #16a34a)'
              : (status.starting ? 'var(--dsw-alias-state-warn-primary, #d97706)' : 'var(--dsw-alias-label-secondary, #64748b)'),
            border: '1px solid ' + (status.running ? 'rgba(22, 163, 74, 0.25)' : 'var(--dsw-alias-border-l2, #cbd5e1)'),
          }
        },
          h('span', {
            style: {
              width: '6px',
              height: '6px',
              borderRadius: '50%',
              backgroundColor: status.running ? '#16a34a' : (status.starting ? '#d97706' : '#94a3b8'),
              display: 'inline-block',
            }
          }),
          status.running ? '7860 在线' : (status.starting ? '启动中...' : '离线')
        ),

        // 工作目录提示（若有）
        folderName ? h('span', {
          title: `工作目录: ${status.currentWorkspace}\n输出工作区: ${status.outputWorkspace}`,
          style: {
            maxWidth: '120px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontSize: '11px',
            padding: '1px 6px',
            borderRadius: '4px',
            backgroundColor: 'var(--dsw-alias-bg-layer-2, #f1f5f9)',
            color: 'var(--dsw-alias-label-secondary, #475569)',
            border: '1px solid var(--dsw-alias-border-l1, #e2e8f0)',
          }
        }, `📁 ${folderName}`) : null,

        // 快捷启动 / 打开按钮
        h('button', {
          type: 'button',
          disabled: status.starting,
          onClick: () => handleOpenOrStart(status, setStatus),
          title: status.running
            ? '在新标签页打开 Gradio 可视化教研工作台 (http://127.0.0.1:7860)'
            : '一键拉起并在新标签页打开教研工作台',
          style: {
            padding: '3px 10px',
            borderRadius: '12px',
            border: 'none',
            backgroundColor: 'var(--dsw-alias-brand-primary, #2563eb)',
            color: '#ffffff',
            cursor: status.starting ? 'wait' : 'pointer',
            fontSize: '11px',
            fontWeight: '500',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            opacity: status.starting ? 0.7 : 1,
          }
        }, status.running ? '🚀 打开工作台' : (status.starting ? '⏳ 启动中...' : '⚡ 启动工作台'))
      );
    }

    /**
     * 2. 侧边栏底部快捷操作组件 (sidebar.footer.action)
     * 在左下角 Settings 旁边，全局常驻
     */
    function CoursewareSidebarAction(props) {
      const [status, setStatus] = useState({ running: false, starting: false, url: WORKBENCH_URL });

      useEffect(() => {
        probeWorkbenchStatus(data => setStatus(prev => ({ ...prev, ...data })));
        const interval = setInterval(() => {
          probeWorkbenchStatus(data => setStatus(prev => ({ ...prev, ...data })));
        }, 5000);
        return () => clearInterval(interval);
      }, []);

      const isWide = !!props.wide;

      return h('button', {
        type: 'button',
        onClick: () => handleOpenOrStart(status, setStatus),
        title: status.running ? '教研工作台已就绪 (7860) - 点击打开' : '点击启动并打开教研工作台',
        style: {
          display: 'inline-flex',
          alignItems: 'center',
          gap: isWide ? '6px' : '0px',
          padding: isWide ? '4px 10px' : '6px',
          borderRadius: '6px',
          backgroundColor: 'transparent',
          border: '1px solid var(--dsw-alias-border-l1, transparent)',
          color: 'var(--dsw-alias-label-primary, #334155)',
          cursor: 'pointer',
          fontSize: '12px',
          position: 'relative',
        }
      },
        h('span', { style: { fontSize: '15px' } }, '📐'),
        isWide ? h('span', { style: { fontWeight: '500', fontSize: '11px' } }, '教研工作台') : null,
        // 状态小圆点
        h('span', {
          style: {
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            backgroundColor: status.running ? '#16a34a' : (status.starting ? '#d97706' : '#94a3b8'),
            position: isWide ? 'static' : 'absolute',
            top: isWide ? 'auto' : '3px',
            right: isWide ? 'auto' : '3px',
          }
        })
      );
    }

    /**
     * 3. 会话输入栏底部 DockBar (conversation.composer.dock)
     * 会话建立后的快捷命令与工作台操作入口
     */
    function CoursewareDockBar(props) {
      const [status, setStatus] = useState({ running: false, starting: false, url: WORKBENCH_URL });

      useEffect(() => {
        probeWorkbenchStatus(data => setStatus(prev => ({ ...prev, ...data })));
      }, []);

      const setCommand = (cmd) => {
        if (props.inputActions && typeof props.inputActions.setText === 'function') {
          props.inputActions.setText(cmd);
        } else if (navigator.clipboard) {
          navigator.clipboard.writeText(cmd);
        }
      };

      return h('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '8px',
          padding: '6px 12px',
          borderRadius: '8px',
          backgroundColor: 'var(--dsw-alias-bg-layer-1, #f8f9fa)',
          border: '1px solid var(--dsw-alias-border-l1, #e2e8f0)',
          fontSize: '12px',
          color: 'var(--dsw-alias-label-primary, #1e293b)',
          marginBottom: '6px',
        }
      },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: '8px' } },
          h('span', {
            style: {
              fontWeight: '600',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              color: 'var(--dsw-alias-brand-primary, #2563eb)'
            }
          }, '📐 竞赛教研流水线'),
          h('span', {
            style: {
              padding: '2px 8px',
              borderRadius: '12px',
              backgroundColor: 'var(--dsw-alias-bg-layer-2, #f1f5f9)',
              border: '1px solid var(--dsw-alias-border-l2, #cbd5e1)',
              fontSize: '11px',
              color: 'var(--dsw-alias-label-secondary, #64748b)',
            }
          }, 'LangGraph · 零信任门禁')
        ),
        h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
          h('button', {
            type: 'button',
            onClick: () => setCommand('/courseware status'),
            title: '查看当前流水线阶段与检查点 (/courseware status)',
            style: {
              padding: '3px 8px',
              borderRadius: '4px',
              backgroundColor: 'var(--dsw-alias-bg-base, #ffffff)',
              border: '1px solid var(--dsw-alias-border-l1, #cbd5e1)',
              color: 'var(--dsw-alias-label-primary, #334155)',
              cursor: 'pointer',
              fontSize: '11px',
            }
          }, '📊 状态'),
          h('button', {
            type: 'button',
            onClick: () => setCommand('/courseware gates'),
            title: '运行全量教研门禁校验 (/courseware gates)',
            style: {
              padding: '3px 8px',
              borderRadius: '4px',
              backgroundColor: 'var(--dsw-alias-bg-base, #ffffff)',
              border: '1px solid var(--dsw-alias-border-l1, #cbd5e1)',
              color: 'var(--dsw-alias-label-primary, #334155)',
              cursor: 'pointer',
              fontSize: '11px',
            }
          }, '🛡️ 门禁'),
          h('button', {
            type: 'button',
            onClick: () => handleOpenOrStart(status, setStatus),
            title: '在浏览器中打开 Gradio 可视化教研工作台 (http://127.0.0.1:7860)',
            style: {
              padding: '3px 10px',
              borderRadius: '4px',
              backgroundColor: 'var(--dsw-alias-brand-primary, #2563eb)',
              border: 'none',
              color: '#ffffff',
              cursor: 'pointer',
              fontSize: '11px',
              fontWeight: '500',
            }
          }, '🚀 打开工作台')
        )
      );
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        // 1. 注册到顶栏左侧 (在开始界面 Hero 状态与对话流中均永久展示)
        ctx.slots.inject('conversation.header.leading', () => ctx.slots.register({
          name: 'conversation.header.leading',
          id: 'competition-courseware-header',
        }, CoursewareHeaderBar));

        // 2. 注册到侧边栏底部 (Settings 旁边全局常驻)
        ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
          name: 'sidebar.footer.action',
          id: 'competition-courseware-sidebar-action',
          order: 5,
          label: '教研工作台',
        }, CoursewareSidebarAction));

        // 3. 注册到输入栏下方 Dock (进入会话后快捷控制)
        ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register({
          name: 'conversation.composer.dock',
          id: 'competition-courseware-dock',
          order: 2,
        }, CoursewareDockBar));
      },
    };
  },
});
