from musicbrainzngs import *
import time
from concurrent.futures import ThreadPoolExecutor
from utils.manage.setup import cfg
from utils.manage.log import logger
import traceback
from utils.sql.sql_repo import SQL_REPO

class MusicBrainzClient:
    def __init__(self, sql:SQL_REPO):
        set_useragent("MyHiResCollector", "1.0", cfg.mail)
        self.auther_list = cfg.author_list
        self.result = []
        self.sql = sql
        self.done = False

    @staticmethod
    def request_api(func, **kwargs):
        for i in range(5):
            try:
                result = func(**kwargs)
                time.sleep(1)
                return result
            except Exception as e:
                print(f'[尝试{i} 错误: {e}]')
                time.sleep(3)

    @staticmethod
    def is_album_official(rg:dict, artist_country: str | None)->bool:
        if rg.get('primary-type') != "Album":
            return False
        
        if "secondary-type-list" in rg:
            return False
        
        title = rg.get("title", "").lower()
        noise_keywords = ['live', 'remaster', 'deluxe', 'edition', 'expanded']
        if any(word in title for word in noise_keywords):
            return False
        releases = rg.get('release-list', [])

        if releases and artist_country:
            countries = {r.get('country') for r in releases}
            # 如果没有任何一个版本来自艺术家本国，说明是其他市场的编辑版
            if artist_country not in countries and None not in countries:
                return False

        return True
    
    def get_auther_country(self, auther_id: str) -> str | None:
        """获取作者的国籍用于筛选专辑"""
        resp = self.request_api(get_artist_by_id, id=auther_id, includes=[])
        if not resp:
            return None
        artist = resp['artist']
        return artist.get('country')
        
    def get_auther_id(self, auther:str)->int:
        resp:dict = self.request_api(search_artists, artist=auther, limit=1)
        if resp and resp.get('artist-list'):
            return resp['artist-list'][0]['id']
        return None
    
    def get_album_group_ids(self, auther_id:int)->list[tuple[int ,str]]:
        resp = self.request_api(browse_release_groups, artist=auther_id,
                                                        release_type=["album"],
                                                        limit=50,)
        
        albums = resp['release-group-list']
        artist_country = self.get_auther_country(auther_id)
        cleaned_ids = []

        for album in albums:
            if self.is_album_official(album, artist_country):
                cleaned_ids.append((album["id"], album["title"]))
        return cleaned_ids

    def get_release_id(self, album_id:int)->int:
        rel_resp = self.request_api(browse_releases,
                                    release_group=album_id,
                                    limit=1,
                                    release_status=["official"])
        
        return rel_resp['release-list'][0]['id']
    
    def get_songs(self, release_id:int, artist:str, album:str)->None:
        print(f'正在索引:{artist} -- {album}')
        full_rel = self.request_api(get_release_by_id, id=release_id, includes=['recordings'])
        for medium in full_rel['release']['medium-list']:
            for track in medium['track-list']:
                song = track['recording']['title']
                self.result.append((artist, album, song))
        return None
    
    def task(self, auther):
        print(f'获取歌手 {auther} 的数据')
        auther_id = self.get_auther_id(auther=auther)
        if not auther_id:
            return
        
        artist_country = self.get_auther_country(auther_id) 
        album_group = self.get_album_group_ids(auther_id)
        for album in album_group:
            release_id = self.get_release_id(album[0])
            self.get_songs(release_id, auther, album[1])
        print(f"歌手 {auther} 数据获取完毕")


    async def GET_IDX(self):
        print('>>> 使用 get_idxs 生成索引数据库...')
        with ThreadPoolExecutor(max_workers=4) as t:
            t.map(self.task, self.auther_list)
            
        await self.sql.insert_for_GET_IDX(self.result)
        self.done = True
        print('>>> 索引数据库生成完成')
       
