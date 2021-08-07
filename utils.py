import contextlib
from typing import Callable, Coroutine, Type


def async_suppress(*exceptions: Type[BaseException]):
    def decorator(async_function: Callable[..., Coroutine]):
        async def wrapped(*args, **kwargs):
            with contextlib.suppress(*exceptions):
                await async_function(*args, **kwargs)
        return wrapped
    return decorator
