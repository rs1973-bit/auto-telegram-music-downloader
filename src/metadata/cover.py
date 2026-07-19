from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx
from fake_useragent import UserAgent

from src.meta import AlbumTask
from src.utils.config import cfg

COVER = Path(cfg.temp_path) / "cover"
COVER.mkdir(parents=True, exist_ok=True)


class Get_Cover:
    """封面渠道为 iTunes Search API。"""

    BASE = "https://itunes.apple.com/search?"

    def __init__(self) -> None:
        self.ua: str = str(UserAgent().google)

    async def _search(self, album: AlbumTask) -> dict:
        """查询 iTunes 专辑信息，返回 JSON 结果（失败返回空 dict）。"""
        term = quote(f"{album.band} {album.name}")
        url = f"{self.BASE}term={term}&entity=album&limit=1"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers={"User-Agent": self.ua})
            return resp.json() if resp.status_code == 200 else {}

    async def _download(self, raw: dict) -> str:
        """下载封面图片并保存，返回保存的图片名称；失败返回空字符串。"""
        results = raw.get("results", [])
        if not results:
            return ""
        item = results[0]
        img_url: str = item.get("artworkUrl60", "")
        if not img_url:
            return ""
        img_url = img_url.replace("60x60", "9999x9999")
        img_name = f'{item["artistName"]} {item["collectionName"]}'
        async with httpx.AsyncClient() as client:
            resp = await client.get(img_url, headers={"User-Agent": self.ua})
            if resp.status_code != 200:
                return ""
            save_path = COVER / f"{img_name}.png"
            save_path.write_bytes(resp.content)
            return str(save_path)
        return ""

    async def get_cover(self, albumtask: AlbumTask | None = None) -> str | None:
        if not albumtask:
            raise ValueError("albumtask is required")
        raw = await self._search(albumtask)
        if not raw:
            return None
        return await self._download(raw) or None