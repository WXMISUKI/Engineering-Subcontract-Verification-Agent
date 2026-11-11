"""当前任务/会话模型上下文（设计 §8、§2 运行时要求）。

**唯一取值点**：运行期所有「本次调用用哪个目录模型」的兜底都读这里的
``current_model_code()``，不再散读 ``settings.LLM_MODEL``。

为什么用 contextvar 而不是进程级全局变量：同一进程会并发跑多个任务，进程级
变量会让 A 任务改模型影响 B 任务。子进程（OC runner）不共享 contextvar，由调用方
通过显式 env ``SZZG_MODEL_CODE`` 传入（见 ``runner.main``），同样一任务一值。

优先级（与设计 §8.1 一致，由调用方保证）：
    用户显式选择 > 任务/会话绑定 > 全局默认。
本模块只负责「显式选择/绑定」的传递；回退全局默认由计费解析链（registry）完成。
"""

from __future__ import annotations

import os
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from typing import Any, Callable, Iterator, Optional

# 单个请求/任务内的当前模型目录 code；None = 未显式选择，交给解析链回退默认。
_current_model: ContextVar[Optional[str]] = ContextVar("current_model_code", default=None)

# 子进程显式传递用的环境变量名（一个子进程只处理一个任务，因此是「显式参数」，不是全局共享）。
SUBPROCESS_ENV_KEY = "SZZG_MODEL_CODE"

# 只有 OC runner 子进程会置位；普通 Web 进程即使环境里残留 SZZG_MODEL_CODE，
# 也不会被当成任务绑定（避免把进程级 env 误读成某次任务的选择）。
_in_subprocess = False


def mark_subprocess() -> None:
    """由 runner 子进程入口调用：允许 env 作为本进程所有线程的模型兜底。"""
    global _in_subprocess
    _in_subprocess = True


def current_model_code() -> Optional[str]:
    """当前上下文选择的模型 code。

    顺序：contextvar（请求/任务显式设置）> 子进程 env（仅 runner 子进程）。
    线程池不会自动复制 contextvar；OC 子进程内的并发线程靠 env 兜底，
    普通 Web 进程不读该 env。
    """
    code = _current_model.get()
    if code:
        return code
    if _in_subprocess:
        return subprocess_model_code()
    return None


@contextmanager
def use_model(code: Optional[str]) -> Iterator[Optional[str]]:
    """在 with 块内把当前模型设为 ``code``，退出时恢复。

    后台任务/线程/异步生成器必须在自己的执行体内包这一层：contextvar 不会被线程池
    或新任务自动继承（除非显式复制），一任务一个 ``use_model``。
    """
    token = _current_model.set(code if code else None)
    try:
        yield code
    finally:
        _current_model.reset(token)


def subprocess_model_code() -> Optional[str]:
    """读取子进程显式传入的模型 code（env），空则 None。"""
    value = os.environ.get(SUBPROCESS_ENV_KEY)
    return value.strip() or None if value else None


def context_wrapped(fn: Callable[..., Any]) -> Callable[..., Any]:
    """把调用时的 context 固定进可调用对象，供 ``ThreadPoolExecutor.submit`` 使用。

    ``asyncio.to_thread`` 会复制 context；``concurrent.futures.ThreadPoolExecutor``
    不会。凡是在线程池里调 LLM 的路径都要走本包装（或 :func:`submit_with_context`）。
    """
    ctx = copy_context()

    def _call(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)

    return _call


def submit_with_context(executor: Executor, fn: Callable[..., Any], *args, **kwargs) -> Future:
    """在线程池提交任务时携带当前 context（模型选择不随线程丢失）。"""
    return executor.submit(context_wrapped(fn), *args, **kwargs)