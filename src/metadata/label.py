from __future__ import annotations
from typing import Any
from urllib.parse import quote
import httpx
from fake_useragent import UserAgent
from src.meta import AlbumTask, SongTask

class Label:
    """查询 iTunes 专辑/单曲信息并填充 AlbumTask 和 SongTask 的缺失字段。

    用法::

        label = Label()
        await label.fill_album(album)          # 填充 country, year, type
        await label.fill_song(song)             # 填充 idx 等
        await label.fill_all(song, album)       # 同时填充
    """

    BASE = "https://itunes.apple.com/search?"

    def __init__(self) -> None:
        self.ua: str = str(UserAgent().google)

    # ------------------------------------------------------------------
    #  公开方法
    # ------------------------------------------------------------------

    async def fill_album(self, album: AlbumTask) -> AlbumTask:
        """查询 iTunes 专辑信息，填充 AlbumTask 的 country / year / type 字段。

        iTunes 返回格式参照 ``album.txt``::

            {
              "resultCount": 1,
              "results": [{
                "wrapperType": "collection",
                "collectionType": "Album",
                "artistName": "Pink Floyd",
                "collectionName": "The Wall",
                "country": "USA",
                "releaseDate": "1979-11-30T08:00:00Z",
                "primaryGenreName": "Rock",
                ...
              }]
            }
        """
        raw = await self._search(artist=album.band, title=album.name, entity="album")
        if not raw:
            return album
        item: dict[str, Any] = raw["results"][0]
        album.country = album.country or item.get("country")
        album.year = album.year or self._extract_year(item.get("releaseDate"))
        album.type = album.type or item.get("primaryGenreName")
        return album

    async def fill_song(self, song: SongTask) -> SongTask:
        """查询 iTunes 单曲信息，填充 SongTask 的 idx 以及内嵌 AlbumTask 的缺失字段。 """
        raw = await self._search(artist=song.band, title=song.song, entity="song")
        if not raw:
            return song
        item: dict[str, Any] = raw["results"][0]
        song.idx = song.idx or item.get("trackNumber", 0)
        # 从单曲结果也能拿到专辑级元数据，一并回填到内嵌的 AlbumTask
        if isinstance(song.album, AlbumTask):
            alb = song.album
            alb.name = alb.name or item.get("collectionName", "")
            alb.country = alb.country or item.get("country")
            alb.year = alb.year or self._extract_year(item.get("releaseDate"))
            alb.type = alb.type or item.get("primaryGenreName")
        return song

    async def fill_all(self, song: SongTask, album: AlbumTask) -> tuple[SongTask, AlbumTask]:
        """同时填充歌曲和专辑信息。"""
        return (await self.fill_song(song), await self.fill_album(album))

    # ------------------------------------------------------------------
    #  内部方法
    # ------------------------------------------------------------------

    async def _search(self, artist: str, title: str, entity: str) -> dict[str, Any]:
        """执行 iTunes Search API 请求，返回 JSON 体；无结果 / 失败时返回空 dict。"""
        term = quote(f"{artist} {title}")
        url = f"{self.BASE}term={term}&entity={entity}&limit=1"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers={"User-Agent": self.ua})
        if resp.status_code != 200:
            return {}
        data = resp.json()
        if data.get("resultCount", 0) == 0:
            return {}
        return data

    @staticmethod
    def _extract_year(release_date: str | None) -> str | None:
        """从 iTunes ISO 日期字符串 ``1979-11-30T08:00:00Z`` 中提取前四位年份。"""
        if not release_date:
            return None
        return release_date[:4]