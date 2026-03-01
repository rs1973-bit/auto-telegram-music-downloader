import musicbrainzngs
import json
import time
import litellm
from concurrent.futures import ThreadPoolExecutor
import os



with open(r'config.json', 'r') as c:
    conf = json.load(c)

songs = {}
key = conf["model"]["key"]
name = conf["model"]["name"]
artists = conf["monitoring"]["author"]
mail = conf["monitoring"]['mail']

musicbrainzngs.set_useragent("MyHiResCollector", "1.0", mail)
def get_album_list(artist_name):
    print(f"正在获取 {artist_name} 的艺术家 ID...")
    resp = musicbrainzngs.search_artists(artist=artist_name, limit=50)
    artist_id = resp['artist-list'][0]['id']  
    
    # 只取录音室专辑 (Album)，排除 Single, EP 等
    data = musicbrainzngs.browse_release_groups(artist=artist_id, release_type=['album'])
    album_groups = data['release-group-list']
    
    result = {} # 初始化字典
    
    print(f"找到 {len(album_groups)} 个专辑概念组，准备提取单曲...")

    for i, album in enumerate(album_groups):
        album_title = album['title']
        rg_id = album["id"]
        
        if 'secondary-type-list' in album: continue
        
        try:
            # 拿到一个 Release ID
            rel_resp = musicbrainzngs.browse_releases(release_group=rg_id, limit=1)
            if not rel_resp['release-list']: continue
            rel_id = rel_resp['release-list'][0]['id']
            
            # 拿到曲目
            full_rel = musicbrainzngs.get_release_by_id(rel_id, includes=['recordings'])
            
            all_songs = []
            for medium in full_rel['release']['medium-list']:
                for track in medium['track-list']:
                    all_songs.append(track['recording']['title'])
            result[album_title] = all_songs
            songs[artist_name] = result
            time.sleep(1.1) 
            
        except Exception as e:
            print(f"抓取 {album_title} 失败: {e}")
            continue
            
def clean_data(name, token, raw):
    print(f'调用{name}清洗数据...')
    command = (
        f"Convert this dictionary into standard json, "
        f"keep and complete all the author's studio albums, "
        f"remove remastered versions, re-recorded versions, "
        f"live versions, featured collections, and retain ascii characters"
        f"return only json\n{raw}"
    )

    msg = [{'role':'user', "content":command}]
    response = litellm.completion(model=name, messages=msg, 
                                  api_key=token, num_retries=5, 
                                  request_timeout=300)
    print(f'模型完成回复...')
    result = response.choices[0].message.content
    return result.replace('json', '').replace("```", '')

def get_idx():
    j = os.path.join('handler', 'songs.json')
    if not os.path.exists(j):
        print(f'开始建立songs.json')
        with ThreadPoolExecutor(max_workers=4) as t:
            for i in artists:
                t.submit(get_album_list, i)
        with open(j, 'w') as f:
            data = clean_data(name, key, songs)
            f.write(data)
    else:
        print(f'songs.json已经存在...')
        