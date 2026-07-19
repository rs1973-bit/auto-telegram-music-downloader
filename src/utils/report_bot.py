from __future__ import annotations
import time
from pyrogram.client import Client
from pyrogram.handlers import MessageHandler
from pyrogram import filters
import psutil
from src.utils.manager import Client_Manager

class ReportBot:
    """汇报机器人：负责向用户推送状态简报和监听 /status 指令。"""

    def __init__(self, bot: Client, manager: Client_Manager, report_id: int):
        """
        Args:
            bot:     已启动的 Pyrogram Client（bot token 模式）
            manager: Client_Manager 实例，用于读取运行时统计
            report_id: 接收消息的用户 ID（通常是 userbot 自身）
        """
        self.bot = bot
        self.manager = manager
        self.report_id = report_id
        self.report_time = time.time()
        self._setup_handlers()

    # ------------------------------------------------------------------ #
    #  指令监听
    # ------------------------------------------------------------------ #

    def _setup_handlers(self) -> None:
        """注册 /status 指令处理器。"""
        async def handle_status(client: Client, message: object) -> None:
            await self.report()

        self.bot.add_handler(
            MessageHandler(handle_status, filters=filters.command("status"))
        )

    # ------------------------------------------------------------------ #
    #  消息发送
    # ------------------------------------------------------------------ #

    async def report(self) -> None:
        """采集设备状态和下载统计并发给用户。"""
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory().percent
        m = self.manager

        text = (
            f"状态/任务简报:\n"
            f"  bot已运行: {(time.time() - self.report_time) / 3600:.2f} 小时\n"
            f"  bot状态: {'激活' if m.can_runs.is_set() else '休眠中'}\n"
            f"  cpu占用: {cpu}%\n"
            f"  内存占用: {mem}%\n"
            f"  已下载: {m.report_size / (1024 ** 3):.2f} GB\n"
            f"  磁盘中已有 {m.files} 首歌曲\n"
            f"  发生错误: {m.error_count}次"
        )
        await self.bot.send_message(self.report_id, text)

    async def send_notice(self, text: str) -> None:
        """发送一条普通通知消息。"""
        await self.bot.send_message(self.report_id, text)
