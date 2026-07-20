import asyncio
import re
from typing import Optional
from urllib.parse import quote
import httpx
from src.utils.config import cfg
from src.utils.logger import logger
from src.utils.language import has_cjk, has_cyrillic, is_latin, is_native_latin
from src.database.sql_repo import SQL_REPO


DEEZER_BASE = "https://api.deezer.com"
ITUNES_BASE = "https://itunes.apple.com"
MAX_RETRIES = 3
_REQ_SEM = asyncio.Semaphore(3)


def _itunes_countries(name: str) -> list[str]:
    """按艺人名语种返回 iTunes store 地区列表。"""
    if has_cjk(name):
        return ["TW", "JP", "US"]
    if has_cyrillic(name):
        return ["RU", "US"]
    return ["US"]


def _needs_latin_fallback(artist_name: str, tracks: list[str]) -> bool:
    """拉丁语系艺人但曲目含非拉丁 → 可能拿错了语言版本。"""
    if not tracks or not is_latin(artist_name):
        return False
    return not all(is_latin(t) for t in tracks[:3])


# ── 匹配工具 ────────────────────────────────────────────────

def _match(value: str, filter_str: Optional[str]) -> bool:
    """正则匹配。filter_str=None 时通配。"""
    if not filter_str:
        return True
    try:
        return bool(re.search(filter_str, value, re.I))
    except re.error:
        return False


class MusicIndexer:
    """
    Deezer（主源，record_type 精准）+ iTunes（副源，覆盖盲区 + 语言纠正）。
    流程: Deezer 搜索 → 专辑 → 曲目；遇缺漏自动切 iTunes。
    """

    # ── 专辑过滤（通用）─────────────────────────────────────
    _EXCLUDE_RE = re.compile(
        r"(live|bbc|anthology|songtrack|naked|rooftop|instrumental|"
        r"cover\b|hollywood|early tapes|past masters|box\s*set|collection|"
        r"bootleg|1962|1967|acoustic (guitar|covers?)|music box|piano|"
        r"^1$|^love$|soundtrack|documentary|"
        r"single|ep\b|remixes|remix\b|mixes|"
        r"albums$|singles$|hits$)",
        re.I,
    )
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

    # ── HTTP ────────────────────────────────────────────────

    @staticmethod
    async def _request(url: str) -> Optional[dict]:
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

    # ── 标题处理 ────────────────────────────────────────────

    @staticmethod
    def _normalize_title(title: str) -> str:
        clean = MusicIndexer._STRIP_RE.sub("", title).strip()
        return re.sub(r"\s+", " ", clean)

    # ── Deezer ──────────────────────────────────────────────

    async def _deezer_artist(self, name: str) -> Optional[int]:
        url = f"{DEEZER_BASE}/search/artist?q={quote(name)}"
        data = await self._request(url)
        if data and data.get("data"):
            artist = data["data"][0]
            logger.info(f"  [Deezer] {artist['name']} (ID={artist['id']})")
            return artist["id"]
        return None

    async def _deezer_albums(self, artist_id: int) -> list[tuple[int, str]]:
        seen: dict[str, int] = {}
        url: Optional[str] = f"{DEEZER_BASE}/artist/{artist_id}/albums"
        while url:
            data = await self._request(url)
            if not data:
                break
            for item in data.get("data", []):
                if item.get("record_type") != "album":
                    continue
                title = item.get("title", "")
                base = self._normalize_title(title)
                if not base or self._EXCLUDE_RE.search(base):
                    continue
                if base not in seen:
                    seen[base] = item["id"]
            url = data.get("next")
        return [(aid, base) for base, aid in seen.items()]

    @staticmethod
    async def _deezer_tracks(album_id: int) -> list[str]:
        tracks: list[str] = []
        url: Optional[str] = f"{DEEZER_BASE}/album/{album_id}/tracks"
        while url:
            resp = await MusicIndexer._request(url)
            if not resp:
                break
            for item in resp.get("data", []):
                t = item.get("title", "").strip()
                if t:
                    tracks.append(t)
            url = resp.get("next")
        return tracks

    # ── iTunes ──────────────────────────────────────────────

    async def _itunes_artist(self, name: str) -> tuple[Optional[int], str]:
        url = f"{ITUNES_BASE}/search?term={quote(name)}&entity=musicArtist&country=US&limit=5"
        data = await self._request(url)
        if data and data.get("results"):
            artist = data["results"][0]
            return artist["artistId"], artist["artistName"]
        return None, name

    async def _itunes_albums(self, artist_id: int) -> list[tuple[int, str]]:
        seen: dict[str, int] = {}
        url = f"{ITUNES_BASE}/lookup?id={artist_id}&entity=album&country=US&limit=200"
        data = await self._request(url)
        if not data:
            return []
        for item in data.get("results", []):
            if item.get("wrapperType") != "collection":
                continue
            name = item.get("collectionName", "")
            if not name or self._EXCLUDE_RE.search(name):
                continue
            title = self._normalize_title(name)
            if not title or self._EXCLUDE_RE.search(title):
                continue
            if title not in seen:
                seen[title] = item["collectionId"]
        return [(aid, base) for base, aid in seen.items()]

    @staticmethod
    async def _itunes_tracks(album_id: int, countries: list[str]) -> Optional[list[str]]:
        for country in countries:
            url = f"{ITUNES_BASE}/lookup?id={album_id}&entity=song&country={country}"
            data = await MusicIndexer._request(url)
            if not data:
                continue
            tracks = [
                item["trackName"].strip()
                for item in data.get("results", [])
                if item.get("wrapperType") == "track"
                and item.get("kind") == "song"
                and item.get("trackName", "").strip()
            ]
            if tracks:
                return tracks
        return None

    @staticmethod
    async def _itunes_search_album(album: str, artist: str, countries: list[str]) -> Optional[list[str]]:
        """在 iTunes 按专辑名+艺人名搜索，返回曲目列表（用于语言纠正）。"""
        q = quote(f"{album} {artist}")
        for country in countries:
            url = f"{ITUNES_BASE}/search?term={q}&entity=album&country={country}&limit=3"
            data = await MusicIndexer._request(url)
            if not data:
                continue
            for r in data.get("results", []):
                if r.get("wrapperType") != "collection":
                    continue
                tracks = await MusicIndexer._itunes_tracks(r["collectionId"], [country])
                if tracks:
                    return tracks
        return None

    # ── 编排 ────────────────────────────────────────────────

    async def _index_from_deezer(
        self,
        artist: str,
        deezer_id: int,
        itunes_id: Optional[int],
        itunes_name: str,
        itunes_countries: list[str],
        album_filter: Optional[str] = None,
        track_filter: Optional[str] = None,
    ) -> bool:
        """用 Deezer 索引；返回 True 表示至少拿到了一些数据。"""
        albums = await self._deezer_albums(deezer_id)
        if not albums:
            return False

        for alb_id, alb_name in albums:
            if not _match(alb_name, album_filter):
                continue
            await asyncio.sleep(0.3)
            tracks = await self._deezer_tracks(alb_id)

            if tracks and _needs_latin_fallback(artist, tracks) and itunes_id:
                if is_native_latin(itunes_name):
                    itunes_tracks = await self._itunes_search_album(alb_name, itunes_name, itunes_countries)
                    if itunes_tracks:
                        tracks = itunes_tracks
                        logger.info(f"    {alb_name}: {len(tracks)} 首 (iTunes 纠正)")

            if tracks:
                matched = [(artist, alb_name, t) for t in tracks if _match(t, track_filter)]
                if matched:
                    self.result.extend(matched)
                    logger.info(f"    {alb_name}: {len(matched)} 首")
                else:
                    logger.warning(f"    {alb_name}: 无匹配曲目")

        return True

    async def _index_from_itunes(
        self,
        artist: str,
        itunes_id: Optional[int],
        itunes_countries: list[str],
        album_filter: Optional[str] = None,
        track_filter: Optional[str] = None,
    ):
        if not itunes_id:
            return
        albums = await self._itunes_albums(itunes_id)
        if not albums:
            return
        for col_id, alb_name in albums:
            if not _match(alb_name, album_filter):
                continue
            await asyncio.sleep(0.3)
            tracks = await self._itunes_tracks(col_id, itunes_countries)
            if tracks:
                matched = [(artist, alb_name, t) for t in tracks if _match(t, track_filter)]
                if matched:
                    self.result.extend(matched)
                    logger.info(f"    {alb_name}: {len(matched)} 首")
                else:
                    logger.warning(f"    {alb_name}: 无匹配曲目")
            else:
                logger.warning(f"    {alb_name}: 无曲目")

    async def task(self, input_str: str):
        """索引入口。

        支持输入格式：
          "艺人"                → 索引全部专辑曲目
          "艺人 / 专辑"         → 只索引指定专辑
          "艺人 / 专辑 / 曲名"  → 只索引指定曲目
          "艺人 / * / 曲名"     → 在所有专辑中搜指定曲目
        """
        parts = [p.strip() for p in input_str.split("/")]
        artist_name = parts[0]
        album_filter = parts[1] if len(parts) > 1 and parts[1] != "*" else None
        track_filter = parts[2] if len(parts) > 2 else None

        detail = artist_name
        if album_filter:
            detail += f" / {album_filter}"
        elif len(parts) > 1:
            detail += " / *"
        if track_filter:
            detail += f" / {track_filter}"
        logger.info(f"正在索引: {detail}")

        itunes_id, itunes_name = await self._itunes_artist(artist_name)
        itunes_countries = _itunes_countries(itunes_name)
        non_latin = not is_native_latin(itunes_name)

        if non_latin:
            if itunes_id:
                await self._index_from_itunes(
                    artist_name, itunes_id, itunes_countries,
                    album_filter=album_filter, track_filter=track_filter,
                )
            else:
                logger.warning(f"iTunes 未找到: {artist_name}")
            return

        deezer_id = await self._deezer_artist(artist_name)
        if deezer_id:
            ok = await self._index_from_deezer(
                artist_name, deezer_id, itunes_id, itunes_name, itunes_countries,
                album_filter=album_filter, track_filter=track_filter,
            )
            if not ok and itunes_id:
                logger.info("  → Deezer 无录音室专辑，切 iTunes")
                await self._index_from_itunes(
                    artist_name, itunes_id, itunes_countries,
                    album_filter=album_filter, track_filter=track_filter,
                )
        elif itunes_id:
            logger.info("  → Deezer 未找到，切 iTunes")
            await self._index_from_itunes(
                artist_name, itunes_id, itunes_countries,
                album_filter=album_filter, track_filter=track_filter,
            )
        else:
            logger.warning(f"Deezer 和 iTunes 均未找到: {artist_name}")

    # ── 入口 ────────────────────────────────────────────────
    async def GET_IDX(self):
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
