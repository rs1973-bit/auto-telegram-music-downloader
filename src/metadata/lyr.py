import httpx
import difflib
from typing import Optional
from pathlib import Path

from src.utils.config import cfg
from src.meta import SongTask

LYR = Path(cfg.temp_path) / "lyr"
LYR.mkdir(parents=True, exist_ok=True)


class Get_lry:
    def __init__(self) -> None:
        self.base_url = "https://lrclib.net/api"
        self.headers = {"User-Agent": "HiFi-Lyrics-Tool/1.0"}

    def _calculate_match_score(
        self, target_artist: str, target_title: str, target_album: str, result: dict
    ) -> int:
        """
        计算搜索结果的匹配得分
        target: 我们想要的内容
        result: API 返回的一条记录
        """
        artist_matcher = difflib.SequenceMatcher(
            None, target_artist.lower(), result.get("artistName", "").lower()
        )
        title_matcher = difflib.SequenceMatcher(
            None, target_title.lower(), result.get("trackName", "").lower()
        )
        score = int(artist_matcher.ratio() * 50 + title_matcher.ratio() * 40)

        if target_album:
            result_album = result.get("albumName", "")
            if result_album:
                album_matcher = difflib.SequenceMatcher(
                    None, target_album.lower(), result_album.lower()
                )
                score += int(album_matcher.ratio() * 30)
        return score

    def _save(self, artist: str, album: str, title: str, lyrics: str) -> str:
        """
        保存歌词文件。
        文件名格式：[Artist] [Album] [Title].lrc
        返回完整路径，失败返回空字符串。
        """
        try:
            file_name = f"{artist} {album} {title}.lrc"
            file_path = str(LYR / file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(lyrics)
            print(f"歌词已保存: {file_name}")
            return file_path
        except Exception as e:
            print(f"文件写入失败: {e}")
            return ""

    async def get_lyrics(self, song: SongTask) -> Optional[str]:
        """
        先尝试精准搜索（/get），失败则进行模糊搜索（/search）并评分筛选。
        """
        artist = song.band
        title = song.song
        album_name = song.album.name if song.album else ""

        async with httpx.AsyncClient(headers=self.headers, timeout=10.0) as client:
            try:
                # ---- 精准搜索 ----
                params = {"artist_name": artist, "track_name": title}
                if album_name:
                    params["album_name"] = album_name

                resp = await client.get(f"{self.base_url}/get", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    lyrics = data.get("syncedLyrics") or data.get("plainLyrics")
                    if lyrics:
                        return self._save(artist, album_name, title, lyrics)

                # ---- 兜底模糊搜索 ----
                search_params = {"q": f"{artist} {title}"}
                search_resp = await client.get(
                    f"{self.base_url}/search", params=search_params
                )
                if search_resp.status_code != 200:
                    return ""

                results = search_resp.json()
                best_match = None
                highest_score = 0
                for item in results:
                    score = self._calculate_match_score(
                        artist, title, album_name, item
                    )
                    if score > highest_score:
                        highest_score = score
                        best_match = item

                if best_match and highest_score > 50:
                    lyrics = (
                        best_match.get("syncedLyrics")
                        or best_match.get("plainLyrics")
                        or ""
                    )
                    if lyrics:
                        return self._save(
                            artist, album_name, title, lyrics
                        )

            except Exception as e:
                print(f"获取歌词异常: {e}")
        return ""