from __future__ import annotations
from typing import Optional
from mediafile import MediaFile, Image, FileTypeError
from src.meta import AlbumTask, SongTask
from src.metadata.cover import Get_Cover
from src.metadata.lyr import Get_lry
from src.metadata.label import Label 


class INSERT_METADATA:
    def __init__(self, file_path: str, song: SongTask, album: AlbumTask) -> None:
        self.file_path = file_path
        self.song = song
        self.album = album
        self.cover_path: Optional[str] = None
        self.lry_path: Optional[str] = None

    async def _fetch(self) -> None:
        self.cover_path = await Get_Cover().get_cover(self.album)
        self.lry_path = await Get_lry().get_lyrics(self.song)
        

    async def insert(self) -> None:
        await self._fetch()

        try:
            audio = MediaFile(self.file_path)
            audio.title = self.song.song
            audio.artist = self.song.band
            audio.album = self.album.name or ""
            audio.country = self.album.country or ""
            try:
                audio.year = int(self.album.year) if self.album.year else None
            except (ValueError, TypeError):
                audio.year = None
            audio.track = self.song.idx

            if self.lry_path:
                try:
                    with open(self.lry_path, "r", encoding="utf-8") as l:
                        audio.lyrics = l.read()
                except OSError as e:
                    print(f"Failed reading lyrics: {e}")

            if self.cover_path:
                try:
                    with open(self.cover_path, "rb") as c:
                        audio.images = [Image(c.read())]
                except OSError as e:
                    print(f"Failed reading cover: {e}")

            audio.save()

        except FileTypeError:
            print(f"Unsupported audio format: {self.file_path}")
        except Exception as e:
            print(f"Metadata insertion failed: {self.file_path}, error: {e}")