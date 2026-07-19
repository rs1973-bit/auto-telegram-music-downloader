from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

class BaseMuiscRepo(ABC):
    """
    任何数据库类必须继承这个元类的所有接口
    """
    @abstractmethod
    def insert_for_GET_IDX(self, data: list[tuple]) -> None:
        pass

    @abstractmethod
    def update_DATA_status(self, data: list[tuple], status: int) -> None:
        pass

    @abstractmethod
    def update_DATA_ids(self, auther: str, album: str, chat_id: int, msg_start: int, msg_end: int) -> None:
        pass

    @abstractmethod
    def get_albums_from_IDX(self, auther: str) -> list[str]:
        pass

    @abstractmethod
    def get_songs_from_IDX(self, auther: str, album: str) -> list[str]:
        pass

    @abstractmethod
    def is_song_exists(self, auther: str, album: str, song: str) -> bool:
        pass

    @abstractmethod
    def get_status_from_DATA(self, auther: str, album: str, song: str) -> int:
        pass

    @abstractmethod
    def get_failed_songs_in_DATA(self) -> list[tuple]:
        pass

    @abstractmethod
    def get_pending_conversion_songs(self) -> list[tuple]:
        pass

@dataclass
class AlbumTask:
    """聚合专辑元数据"""
    band:str = None
    country:str = None
    type:str = None
    year:str = None
    name:str = None

    
@dataclass
class SongTask:
    band: str = ""
    album: AlbumTask = field(default_factory=AlbumTask)
    idx: int = 0
    song: str = ""
    chat_id: int = 0
    msg_id: int = 0
    status: int = 0

    def uuid(self) -> tuple[str, str, str]:
        return (self.band, self.album.name, self.song)
    
@dataclass
class ConvTask:
    """转码任务"""
    source_path: str
    final_dir: str
    band: str = ""
    album: str = ""
    song: str = ""
    song_task: SongTask | None = None          # 携带完整 SongTask（含 AlbumTask）供后续元数据写入
    album_task: AlbumTask | None = None        # 冗余字段，方便直接引用