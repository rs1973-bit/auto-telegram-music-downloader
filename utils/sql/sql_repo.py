import aiosqlite
import os
from aiosqlite import Cursor
from typing import Optional
from sql.meta import BaseMuiscRepo, SongTask

IDX_SQL_PATH = os.path.join('database', "songs.db")
IDX_SQL_NAME = "songs"
DATA_SQL_PATH = os.path.join('database', "data.db")
DATA_SQL_NAME = "data"
STATUS = {0:'未下载',
          1:'下载成功',
         -1:'下载失败'}

class SQL_MANAGE:
    """SQL的辅助上下文管理类"""
    def __init__(self, path):
        self.path = path
        self.conn:Optional[aiosqlite.Connection] = None

    async def __aenter__(self)->Cursor:
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.execute("PRAGMA journal_mode=WAL;")
        return await self.conn.cursor()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            await self.conn.commit()
        else:
            print(f'{exc_type} {exc_val} {exc_tb}')
            await self.conn.rollback()
        await self.conn.close()


class SQL_REPO(BaseMuiscRepo):
    """封装所有SQL操作的类"""
    async def inital(self):
        await self.__init_sql()

    async def __init_sql(self):
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DATA_SQL_NAME}(
                    idx INTEGER PRIMARY KEY AUTOINCREMENT,
                    band TEXT NOT NULL,
                    album TEXT NOT NULL,
                    song TEXT NOT NULL,
                    chat_id INTEGER DEFAULT -1,
                    msg_id INTEGER DEFAULT 0,
                    status INTEGER DEFAULT 0 
                        )
            """)
        
        async with SQL_MANAGE(IDX_SQL_PATH) as cur:
            await cur.execute("""
            CREATE TABLE IF NOT EXISTS songs(
                    idx INTEGER PRIMARY KEY AUTOINCREMENT,
                    band TEXT NOT NULL,
                    album TEXT NOT NULL,
                    song TEXT NOT NULL
                        )
            """)

    @staticmethod
    async def __gather(sql_path, sql, data:list[tuple]):
        """聚合输入信息"""
        if not data: return
        async with SQL_MANAGE(sql_path) as cur:
            await cur.executemany(sql, data)
            
    async def insert_for_GET_IDX(self, data:list[tuple]):
        """建立idx索引数据库, 配合get_idxs.py使用"""
        sql = f'INSERT OR IGNORE INTO {IDX_SQL_NAME} (band, album, song) VALUES (?, ?, ?)'
        await self.__gather(IDX_SQL_PATH, sql, data)

    async def insert_for_DATA(self, data:list[SongTask]):
        tuples = [(s.band, s.album, s.song, s.chat_id, s.msg_id, s.status) for s in data]
        sql = f"""INSERT OR IGNORE INTO {DATA_SQL_NAME} 
        (band, album, song, chat_id, msg_id, status) VALUES (?, ?, ?, ?, ?, ?)"""
        await self.__gather(DATA_SQL_PATH, sql, tuples)

    async def update_DATA_status(self, data:list[tuple], status:int):
        """
        data:(band, album, song)
        更新data数据库中某首歌曲的状态
        """
        sql = f"""
        UPDATE {DATA_SQL_NAME}
        SET status = {status}
        WHERE band = ? AND album = ? AND song = ?"""
        await self.__gather(DATA_SQL_PATH, sql, data)

    async def update_DATA_ids(self, band, album, chat_id, msg_start, msg_end):
        """
        更新专辑的chat_id和msg_id范围
        """
        # 获取该专辑的歌曲列表
        songs = await self.get_songs_from_IDX(band, album)
        # 假设msg_id从msg_start开始递增
        for i, song in enumerate(songs):
            msg_id = msg_start + i
            sql = f"""
            UPDATE {DATA_SQL_NAME}
            SET chat_id = ?, msg_id = ?
            WHERE band = ? AND album = ? AND song = ?"""
            async with SQL_MANAGE(DATA_SQL_PATH) as cur:
                await cur.execute(sql, (chat_id, msg_id, band, album, song))

    async def get_albums_from_IDX(self, band:str)->list[str]:
        """查询一个作者的全部专辑"""
        async with SQL_MANAGE(IDX_SQL_PATH) as cur:
            await cur.execute(f"SELECT DISTINCT album FROM {IDX_SQL_NAME} WHERE band = ?", (band, ))
            result = []
            async for row in cur:
                result.append(row[0])
            return result
        
    async def get_songs_from_IDX(self, band:str, album:str) -> list[str]:
        """查询一张专辑的所有歌"""
        async with SQL_MANAGE(IDX_SQL_PATH) as cur:
            await cur.execute(f"""SELECT DISTINCT song FROM {IDX_SQL_NAME} 
                            WHERE band = ? AND album = ?""", (band, album))
            result = []
            async for row in cur:
                result.append(row[0])
            return result
            
    async def is_song_exists(self, band, album, song) -> bool:
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute(f"""SELECT 1 FROM {DATA_SQL_NAME} WHERE 
                        band = ? AND album = ? AND song = ?""", (band, album, song))
            return await cur.fetchone() is not None
        
    async def get_status_from_DATA(self, band, album, song) -> int:
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute(f"""SELECT DISTINCT status FROM {DATA_SQL_NAME} 
                        WHERE band = ? AND album = ? AND song = ?""", (band, album, song))
            result = await cur.fetchone()
            return result[0] if result else None
        
    async def get_album_range_from_DATA(self, band, album) -> tuple:
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute(f"""SELECT * FROM data WHERE band = ? AND album = ?""", (band, album))
            result = await cur.fetchall()
            return (result[0][4], result[-1][4])

    async def get_failed_songs_in_DATA(self)->list[tuple]:
        """
        返回data数据库中所有状态为-1的歌
        """
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute("SELECT * FROM data WHERE status = -1")
            result = []
            async for row in cur:
                result.append(row)
            return result
