import aiosqlite
import os
from aiosqlite import Cursor
from typing import Optional, Any
from src.meta import BaseMuiscRepo, SongTask, AlbumTask

IDX_SQL_PATH = os.path.join('src', 'database', "songs.db")
IDX_SQL_NAME = "songs"
DATA_SQL_PATH = os.path.join('src', 'database', "data.db")
DATA_SQL_NAME = "data"
STATUS = {0:'未下载',
          1:'下载成功',
          2:'待转码',
         -1:'下载失败'}

class SQL_MANAGE:
    """SQL的辅助上下文管理类"""
    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None

    async def __aenter__(self) -> Cursor:
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.execute("PRAGMA journal_mode=WAL;")
        return await self.conn.cursor()

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
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

    async def __init_sql(self) -> None:
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
            # 若索引表已存在且有数据则跳过创建
            await cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='songs'")
            exists = (await cur.fetchone())[0]
            if exists:
                await cur.execute("SELECT count(*) FROM songs")
                row_count = (await cur.fetchone())[0]
                if row_count > 0:
                    print(f"  ℹ️  Index table songs has {row_count} rows, skipping")
                    return

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
        """建立索引数据库, 配合index.py使用"""
        sql = f'INSERT OR IGNORE INTO {IDX_SQL_NAME} (band, album, song) VALUES (?, ?, ?)'
        await self.__gather(IDX_SQL_PATH, sql, data)

    async def insert_for_DATA(self, data: list[SongTask]):
        tuples = [(s.band, s.album.name, s.song, s.chat_id, s.msg_id, s.status) for s in data]
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

    async def update_DATA_ids(self, band: str, album: str, chat_id: int, msg_start: int, msg_end: int) -> None:
        """
        更新专辑的chat_id和msg_id范围
        """
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
        
    async def count_idx_songs(self) -> int:
        """返回索引 songs 表中的总行数"""
        async with SQL_MANAGE(IDX_SQL_PATH) as cur:
            await cur.execute("SELECT count(*) FROM songs")
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_songs_from_IDX(self, band:str, album:str) -> list[str]:
        """查询一张专辑的所有歌（按 idx 排序，保证与音轨顺序一致）"""
        async with SQL_MANAGE(IDX_SQL_PATH) as cur:
            await cur.execute(f"""SELECT DISTINCT song FROM {IDX_SQL_NAME} 
                            WHERE band = ? AND album = ?
                            ORDER BY idx""", (band, album))
            result = []
            async for row in cur:
                result.append(row[0])
            return result
            

    async def get_track_num(self, band: str, album: str, song: str) -> int:
        """从 INDEX 表获取某首歌的轨道号（1-based）。"""
        try:
            songs = await self.get_songs_from_IDX(band, album)
            return songs.index(song) + 1
        except ValueError:
            return 0
    async def is_song_exists(self, band: str, album: str, song: str) -> bool:
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute(f"""SELECT 1 FROM {DATA_SQL_NAME} WHERE 
                        band = ? AND album = ? AND song = ?""", (band, album, song))
            return await cur.fetchone() is not None

    async def get_status_from_DATA(self, band: str, album: str, song: str) -> int | None:
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute(f"""SELECT DISTINCT status FROM {DATA_SQL_NAME}
                        WHERE band = ? AND album = ? AND song = ?""", (band, album, song))
            result = await cur.fetchone()
            return result[0] if result else None

    async def get_album_range_from_DATA(self, band: str, album: str) -> tuple[int, int]:
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute(f"""SELECT * FROM data WHERE band = ? AND album = ?""", (band, album))
            result = await cur.fetchall()
            if not result:
                return (-1, -1)
            return (result[0][4], result[-1][4])  # type: ignore

    async def get_failed_songs_in_DATA(self) -> list[tuple]:
        """返回所有下载/转码失败（status = -1）的歌曲。"""
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute("SELECT * FROM data WHERE status = -1")
            result = []
            async for row in cur:
                result.append(row)
            return result

    async def get_pending_conversion_songs(self) -> list[tuple]:
        """返回已下载待转码（status = 2）的歌曲。"""
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute("SELECT * FROM data WHERE status = 2")
            result = []
            async for row in cur:
                result.append(row)
            return result

    async def get_pending_tasks(self) -> list[SongTask]:
        """返回已分配 chat_id/msg_id 但未下载（status = 0）的任务，用于重启时重入队列。"""
        async with SQL_MANAGE(DATA_SQL_PATH) as cur:
            await cur.execute("SELECT * FROM data WHERE status = 0 AND chat_id != -1 AND msg_id != 0")
            tasks = []
            async for row in cur:
                band, album, song = row[1], row[2], row[3]
                idx = await self.get_track_num(band, album, song)
                tasks.append(SongTask(
                    band=band, album=AlbumTask(name=album, band=band),
                    song=song, chat_id=row[4], msg_id=row[5], status=row[6],
                    idx=idx,
                ))
            return tasks
