"""workflow — LangGraph courseware pipeline package.

延迟导出：避免 `python -m workflow.runner` 时因包 __init__ 预先导入 runner 而
触发 RuntimeWarning，同时减少 import 开销。
"""

from typing import Any

__all__ = [
    "CoursewareState",
    "GateReport",
    "GateIssue",
    "create_courseware_graph",
    "run_pipeline",
    "resume_pipeline",
    "get_current_state",
    "export_mermaid",
]


def __getattr__(name: str) -> Any:
    if name in ("CoursewareState", "GateReport", "GateIssue"):
        from . import state
        return getattr(state, name)
    if name == "create_courseware_graph":
        from .graph import create_courseware_graph
        return create_courseware_graph
    if name in ("run_pipeline", "resume_pipeline", "get_current_state",
                "export_mermaid"):
        from . import runner
        return getattr(runner, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__():
    return sorted(list(globals()) + __all__)
