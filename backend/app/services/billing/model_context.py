from contextlib import contextmanager
from contextvars import ContextVar

_model_code = ContextVar('bs_model_code', default=None)


def current_model_code():
    return _model_code.get()


@contextmanager
def use_model(code):
    token = _model_code.set(code)
    try:
        yield
    finally:
        _model_code.reset(token)
