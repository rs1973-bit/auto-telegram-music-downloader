import asyncio
from collections.abc import Callable, Coroutine
from typing import Any
from pyrogram.errors import FloodWait
    
async def request_api(
    func: Callable[..., Any] | Callable[..., Coroutine[Any, Any, Any]],
    sleep_time: int,
    *args: Any,
    **kwargs: Any,
) -> Any:
    for i in range(5):
        try:
            result = await func(*args, **kwargs)  # type: ignore
            await asyncio.sleep(sleep_time)
            return result
        except FloodWait as e:
            name = getattr(func, "__name__", "task")
            print(f"[Attempt {i}] {name} FloodWait {e.value}s, sleeping...")
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            name = getattr(func, "__name__", "task")
            print(f"[Attempt {i}] {name} error: {e}")
            await asyncio.sleep(2 ** i)
    return None