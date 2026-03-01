import time
from pyrogram.client import Client
from pyrogram.handlers import MessageHandler
from pyrogram import filters
import asyncio
import random
from manage.log import logger
import psutil
import os

class Client_Manager:
    def __init__(self, app:Client, bot:Client, save_path):
        """一个管理全局冷却和汇报bot的类"""
        self.inital_time = time.time()
        self.report_time = self.inital_time
        self.app = app
        self.report_id = app.me.id # bot的汇报对象就是userbot本身
        self.bot = bot
        self.downloaded_size = 0
        self.report_size = self.downloaded_size
        self.can_runs = asyncio.Event() # 全局锁, 当触发冷却阈值时上锁
        self.error_count = 0
        self.can_runs.set()
        self.files = 0
        self.setup_bot_handlers() # 执行bot的会话监听函数, 监控指令

        for _, _, files in os.walk(save_path):
            self.files += len(files)

    def setup_bot_handlers(self):
        """将指令监听器绑定到 Bot 对象"""
        # 定义指令处理逻辑
        async def handle_status(client, message):
            await self.report()

        # 注册处理器
        self.bot.add_handler(
           MessageHandler(
                handle_status, 
                filters=filters.command("status")
            )
        )
            
    async def report(self):
        """让bot发送设备情况概述和进度"""
        cpu_status = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory().percent
        text = (
        f"状态/任务简报:\n"
        f"  bot已运行: {(time.time() - self.report_time) / 3600:.2f} 小时\n"
        f"  bot状态: {"激活" if self.can_runs.is_set() else "休眠中"}\n"
        f"  cpu占用: {cpu_status}%\n"
        f"  内存占用: {memory}%\n"
        f"  已下载: {self.report_size / (1024 ** 3):.2f} GB\n"
        f"  磁盘中已有 {self.files} 首歌曲\n"
        f"  发生错误: {self.error_count}次"
    )
                
        await self.bot.send_message(self.report_id, text)

    def need_stop(self):
        """判断是否需要冷却"""
        now = time.time()
        if now - self.inital_time >= 2 * 60 * 60 or self.downloaded_size >= 10 * (1024 ** 3):
            print(f'准备冷却...')
            return True 
        return False

    async def restart(self):
        """冷却的具体逻辑"""
        stop_time = random.randint(300, 600)
        logger.info(f"会话冷却开始, 冷却{stop_time}秒...")
        self.can_runs.clear()
        await self.report()
        await asyncio.sleep(stop_time)
        logger.info(f'冷却完成...')
        self.can_runs.set()
        self.inital_time = time.time()
        self.downloaded_size = 0

