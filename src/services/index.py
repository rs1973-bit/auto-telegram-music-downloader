import asyncio
import re
from typing import Optional
import httpx
from src.utils.config import cfg
from src.utils.logger import logger
from src.database.sql_repo import SQL_REPO


DEEZER_BASE = "https://api.deezer.com"
MAX_RETRIES = 3
_REQ_SEM = asyncio.Semaphore(3)


class MusicIndexer:
    """
    索引器：使用 Deezer API 获取指定歌手的录音室专辑及曲目列表。
    流程: search_artist → artist_id → albums（过滤录音室专辑） → tracks
    """

    # 标题中若包含以下关键词则判定为非录音室专辑
    _EXCLUDE_RE = re.compile(
        r"(live|bbc|anthology|songtrack|naked|rooftop|instrumental|"
        r"cover\b|hollywood|early tapes|past masters|box set|collection|"
        r"bootleg|1962|1967|acoustic (guitar|covers?)|music box|piano|"
        r"^1$|^love$)",  # Cirque du Soleil remix, greatest hits
        re.I,
    )

    # 去重时剥离的版本后缀
    _STRIP_RE = re.compile(
        r"\s*[\(\[][^\)\]]*(remastered|remix|deluxe|super deluxe|"
        r"edition|remaster|mix|anniversary|version|mono|stereo|"
        r"remaster)[^\)\]]*[\)\]]?\s*",
        re.I,
    )

    def __init__(self, sql: SQL_REPO):
        self.result: list[tuple[str, str, str]] = []
        self.sql = sql
        self.done = False

    # ------------------------------------------------------------------ #
    #  HTTP 请求
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _request(url: str) -> Optional[dict]:
        """发送 GET 请求，带重试与并发控制。"""
        for i in range(MAX_RETRIES):
            try:
                async with _REQ_SEM:
                    async with httpx.AsyncClient(timeout=15) as c:
                        resp = await c.get(url)
                        resp.raise_for_status()
                        return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                logger.warning(f"HTTP {e.response.status_code} — {url}")
            except Exception as e:
                logger.warning(f"请求失败(重试 {i + 1}/{MAX_RETRIES}): {url} — {e}")
                if i < MAX_RETRIES - 1:
                    await asyncio.sleep(2**i)
        return None

    # ------------------------------------------------------------------ #
    #  专辑过滤逻辑
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_title(title: str) -> str:
        """剥离版本/重制等后缀，得到基础专辑名"""
        clean = MusicIndexer._STRIP_RE.sub("", title).strip()
        return re.sub(r"\s+", " ", clean)

    @staticmethod
    def _is_studio_album(item: dict) -> bool:
        """通过 Deezer 字段 + 标题启发式判断是否为录音室专辑"""
        if item.get("record_type") != "album":
            return False
        # 用归一化后的标题做排除检测，避免 "(Remastered)" 后缀干扰
        title = MusicIndexer._normalize_title(item.get("title", ""))
        if not title or MusicIndexer._EXCLUDE_RE.search(title):
            return False
        return True

    # ------------------------------------------------------------------ #
    #  数据获取方法
    # ------------------------------------------------------------------ #

    async def get_artist_id(self, name: str) -> Optional[int]:
        """搜索歌手，返回 Deezer artist ID。"""
        from urllib.parse import quote

        url = f"{DEEZER_BASE}/search/artist?q={quote(name)}"
        data = await self._request(url)
        if data and data.get("data"):
            artist = data["data"][0]
            logger.info(f"  → {artist['name']} (Deezer ID={artist['id']})")
            return artist["id"]
        logger.warning(f"未找到歌手: {name}")
        return None

    async def get_albums(self, artist_id: int) -> list[tuple[int, str]]:
        """获取录音室专辑列表，已去重。"""
        seen: set[str] = set()
        albums: list[tuple[int, str]] = []
        url: Optional[str] = f"{DEEZER_BASE}/artist/{artist_id}/albums"

        while url:
            data = await self._request(url)
            if not data:
                break
            for item in data.get("data", []):
                if not self._is_studio_album(item):
                    continue
                base = self._normalize_title(item["title"])
                if base not in seen:
                    seen.add(base)
                    albums.append((item["id"], base))
            url = data.get("next")

        logger.info(f"  → {len(albums)} 张录音室专辑")
        return albums

    async def get_tracks(self, album_id: int, artist: str, album: str) -> None:
        """获取专辑曲目并追加到 result。"""
        tracks: list[str] = []
        url: Optional[str] = f"{DEEZER_BASE}/album/{album_id}/tracks"

        while url:
            data = await self._request(url)
            if not data:
                break
            for item in data.get("data", []):
                title = item.get("title", "").strip()
                if title:
                    tracks.append(title)
            url = data.get("next")

        if tracks:
            self.result.extend((artist, album, t) for t in tracks)
            logger.info(f"    {album}: {len(tracks)} 首")

    # ------------------------------------------------------------------ #
    #  任务编排
    # ------------------------------------------------------------------ #

    async def task(self, artist_name: str):
        """处理单个歌手：搜索 → 专辑 → 曲目"""
        logger.info(f"正在索引: {artist_name}")
        artist_id = await self.get_artist_id(artist_name)
        if not artist_id:
            return

        albums = await self.get_albums(artist_id)
        for alb_id, alb_name in albums:
            await asyncio.sleep(0.3)
            await self.get_tracks(alb_id, artist_name, alb_name)

    # ------------------------------------------------------------------ #
    #  异步入口
    # ------------------------------------------------------------------ #

    async def GET_IDX(self):
        """入口：若 songs 表已有数据则跳过，否则索引所有歌手。"""
        if await self.sql.count_idx_songs() > 0:
            logger.info("索引表 songs 已有数据，跳过索引")
            self.done = True
            return

        tasks = [self.task(a) for a in cfg.author_list]
        if tasks:
            await asyncio.gather(*tasks)

        if self.result:
            await self.sql.insert_for_GET_IDX(self.result)
            logger.info(f"索引完成，共写入 {len(self.result)} 首曲目")
        else:
            logger.warning("索引结果为空，未写入数据库")

        self.done = True
