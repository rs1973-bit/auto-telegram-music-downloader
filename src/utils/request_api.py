import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


async def request_api(
    func: Callable[..., Any] | Callable[..., Coroutine[Any, Any, Any]],
    sleep_time: int,
    *args: Any,
    **kwargs: Any,
) -> Any:
    for i in range(5):
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)  # type: ignore
            else:
                result = func(*args, **kwargs)
            await asyncio.sleep(sleep_time)
            return result
        except Exception as e:
            name = getattr(func, "__name__", "task")
            print(f'[尝试 {i}] {name} 出错: {e}')
            await asyncio.sleep(2 ** i)
    return None