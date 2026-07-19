import time
import asyncio
import random
import os
from collections.abc import Awaitable, Callable
from pyrogram.client import Client
from src.utils.logger import logger


class Client_Manager:
    """流量控制器 + 运行时统计。

    职责：
    - 维护全局暂停/恢复信号 (can_runs Event)
    - 跟踪下载量、错误数、文件数等统计
    - 判断冷却条件并执行冷却（休眠）
    """

    def __init__(self, app: Client, save_path: str):
        self.app = app
        self.inital_time = time.time()
        self.can_runs = asyncio.Event()
        self.can_runs.set()
        self.error_count = 0
        self.files = 0
        self.downloaded_size = 0
        self.report_size = 0

        for _, _, files in os.walk(save_path):
            self.files += len(files)

    # ------------------------------------------------------------------ #
    #  冷却控制
    # ------------------------------------------------------------------ #

    def need_stop(self) -> bool:
        """判断是否需要冷却（运行 ≥2h 或下载 ≥10GB）。"""
        now = time.time()
        if now - self.inital_time >= 2 * 60 * 60 or self.downloaded_size >= 10 * (1024 ** 3):
            logger.info('准备冷却...')
            return True
        return False

    async def restart(self, on_cooldown: Callable[[], Awaitable[None]] | None = None) -> None:
        """执行冷却：暂停 → 可选回调 → 等待 → 恢复。

        Args:
            on_cooldown: 可选异步回调，暂停后立即调用（如发送状态简报）。
        """
        stop_time = random.randint(300, 600)
        logger.info(f"会话冷却开始, 冷却{stop_time}秒...")
        self.can_runs.clear()
        if on_cooldown:
            await on_cooldown()
        await asyncio.sleep(stop_time)
        logger.info('冷却完成...')
        self.can_runs.set()
        self.inital_time = time.time()
        self.downloaded_size = 0

