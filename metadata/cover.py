import asyncio
import httpx
import PIL
from utils.sql.meta import AlbumTask
from fake_useragent import UserAgent
from utils.request_api import request_api
import json

class Get_Cover:
    def __init__(self):
        self.itunes:str = "https://itunes.apple.com/search?"
        self.AudiodB:str = "https://www.theaudiodb.com/api/v1/json/123/searchalbum.php?"
        self.ua:str = None

    def get_ua(self):
        self.ua = UserAgent.random()

    async def request_api(self, func:function, *arg, **kwargs):
        pass



    

