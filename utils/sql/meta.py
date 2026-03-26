from abc import ABC, abstractmethod
from dataclasses import dataclass

class BaseMuiscRepo(ABC):
    """
    任何数据库类必须继承这个元类的所有接口
    """
    @abstractmethod
    def insert_for_GET_IDX(self, data:list[tuple])->None:
        pass

    @abstractmethod
    def update_DATA_status(self, data:list[tuple], status:int)->None:
        pass
    
    @abstractmethod
    def update_DATA_ids(self, auther, album, chat_id, msg_start, msg_end)->None:
        pass

    @abstractmethod
    def get_albums_from_IDX(self, auther:str)->list[str]:
        pass

    @abstractmethod
    def get_songs_from_IDX(self, auther:str, album:str) -> list[str]:
        pass
    
    @abstractmethod
    def is_song_exists(self, auther, album, song) -> bool:
        pass
    
    @abstractmethod
    def get_status_from_DATA(self, auther, album, song) -> int:
        pass

    @abstractmethod
    def get_failed_songs_in_DATA(self)->list[tuple]:
        pass

@dataclass
class SongTask:
    band:str = None
    album:str = None
    song:str = None
    chat_id:int = None
    msg_id:int = None
    status:int = 0

    def uuid(self) -> tuple[str, str, str]:
        return (self.band, self.album, self.song)

@dataclass
class AlbumTask:
    band:str = None
    album:str = None
    chat_id:int = None
    msg_start:int = None
    msg_end:int = None

    def gather(self) -> tuple[str, str, int, int, int]:
        return (self.band, self.album, self.chat_id, self.msg_start, self.msg_end)
    