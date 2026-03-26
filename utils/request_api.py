import asyncio

async def request_api(func, sleep_time: int, *args, **kwargs):
    for i in range(5):
        try:
            # 兼容异步和同步函数
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            await asyncio.sleep(sleep_time)
            return result
        except Exception as e:
            print(f'[尝试{i}] {func.__name__ if hasattr(func, "__name__") else "task"} 出错: {e}')
            await asyncio.sleep(2 ** i)
    return None