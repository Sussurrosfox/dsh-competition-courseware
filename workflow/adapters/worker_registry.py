"""worker_registry.py — Process-wide registry for the cognitive worker callable.

The graph itself never talks to an LLM. Cognitive stages (architect plan,
student tasks, research re-derivation, targeted repair) are *dispatched* to a
worker that must be injected by the host process (Web UI, CLI, or a test):

    from workflow.adapters.worker_registry import worker_registry
    worker_registry.set(my_llm_callable)                # 默认 worker
    worker_registry.set_role("probe", my_probe_worker)  # 按角色注册

When no worker is registered, `dispatch` falls back to a LangGraph
`interrupt()` so a human (or an Antigravity session driving the CLI/UI) can
produce the artifacts and resume the run. Either way the graph *waits* for the
artifacts instead of pretending the work happened.

关键契约：每次 call 必须是全新会话，host 侧不得复用上下文。这是"图内子
agent"隔离语义的全部实现——图本身只负责派发与校验，会话隔离由注入的
worker 保证（例如 probe 与 solver 必须使用互不相同的会话）。
"""

from typing import Callable, Dict, Any, Optional, List


class WorkerRegistry:
    def __init__(self) -> None:
        self._model_callable: Optional[Callable[[str, str], str]] = None
        self._worker_name: Optional[str] = None
        self._role_callables: Dict[str, Callable[[str, str], str]] = {}
        self._role_names: Dict[str, str] = {}

    def set(self, fn: Callable[[str, str], str], name: str = "external-llm") -> None:
        """Register a callable (system_prompt, user_content) -> response_text."""
        if not callable(fn):
            raise TypeError("worker callable must be callable")
        self._model_callable = fn
        self._worker_name = name

    def set_role(self, role: str, fn: Callable[[str, str], str],
                 name: Optional[str] = None) -> None:
        """Register a role-specific callable (system_prompt, user_content) -> response_text."""
        if not callable(fn):
            raise TypeError("worker callable must be callable")
        self._role_callables[role] = fn
        self._role_names[role] = name or role

    def clear(self) -> None:
        self._model_callable = None
        self._worker_name = None
        self._role_callables = {}
        self._role_names = {}

    @property
    def available(self) -> bool:
        """默认 worker 或任一角色 worker 存在即为可用。"""
        return self._model_callable is not None or bool(self._role_callables)

    def available_for_role(self, role: str) -> bool:
        """该角色有专属 worker、或存在默认 worker 时，可以就地派发。"""
        return role in self._role_callables or self._model_callable is not None

    @property
    def name(self) -> str:
        return self._worker_name or "none"

    @property
    def roles(self) -> List[str]:
        return sorted(self._role_callables)

    def call(self, system_prompt: str, user_content: str) -> str:
        if self._model_callable is None:
            raise RuntimeError("no cognitive worker registered")
        return self._model_callable(system_prompt, user_content)

    def call_for_role(self, role: str, system_prompt: str, user_content: str) -> str:
        """优先角色 worker，回退默认 worker；都没有则抛错。"""
        fn = self._role_callables.get(role) or self._model_callable
        if fn is None:
            raise RuntimeError("no cognitive worker registered for role %s" % role)
        return fn(system_prompt, user_content)

    def describe(self) -> Dict[str, Any]:
        return {
            "worker_available": self.available,
            "worker_name": self.name,
            "roles": self.roles,
        }


# 进程内单例：UI / CLI / 测试共用同一注入点
worker_registry = WorkerRegistry()
