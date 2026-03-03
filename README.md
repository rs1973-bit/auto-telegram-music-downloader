# **全自动telegarm音乐下载器**
## **目录**
* [简介](#简介)
* [准备](#准备)
* [依赖项](#依赖项)
* [快速上手和启动](#快速上手和启动)
* [配置文件](#配置文件)
* [转码器配置](#转码器配置)
* [汇报机器人](#汇报机器人)
* [源码和实现](#源码和实现)
* [常见错误和解决](#常见错误和解决)
* [特别说明](#特别说明)

## **简介**
这是一个基于python-pyrogram以异步生产者-消费者编写的音乐下载器, 利用userbot在telegram的公开频道中批量下载特定歌手的全部作品, 转码成特定格式后以 **作者->专辑->歌曲** 的格式归档在目标文件夹中
## **准备**
在使用该项目之前, 请确保您做了以下准备

1. 拥有telegram的api_id, api_hash和bot_token
2. 在您的设备上下载ffmpeg, 如果没有它转码部分将不能正常运作

关于如何申请telegram的api_id和hash, 请到[https://my.telegram.org/apps](https://my.telegram.org/apps) 登陆并填写相关信息 

bot_token请在telegram中私信@BotFather获取, 详细教程请参阅 [https://core.telegram.org/bots/tutorial](https://core.telegram.org/bots/tutorial)

ffmpeg官网: [https://ffmpeg.org/](https://ffmpeg.org/)

**在安卓环境下下载ffmpeg(termux)请执行以下命令:**

```pkg install ffmpeg```
## **依赖项**
请参阅 [requirements.txt](./requirements.txt)
## **快速上手和启动**

项目的入口在[main.py](main.py), 在参阅完下文的配置文件参数解释并填写后, 让python解释器(项目在3.13.7开发)执行main.py即可启动项目

在第一次启动且songs.json生成后, 程序会生成两个.session文件, 分别是report.session(汇报机器人的session)和另一个由你设置的名字而定的.session用于业务逻辑(详见下文[telegram](#telegram)子栏目), 之后pyrogram会要求你填写手机号和二次验证密码, 如实填写并在telegram获取登陆密钥填入即可
## 配置文件
这是初始的[配置文件](./config.json), 文件内容如下:

```json
{
    "model":{
        "name":"", 
        "token":""
    },
    "telegram": {
        "api_id": "",
        "api_hash": "",
        "bot_token": "",
        "name":"",
        "workers":"2"
    },
    "monitoring": {
        "exclude":["live", "现场", "remake", "remix"],
        "target_channels": [
            -1002321822091,
            -1001356243397,
            -1001235568157,
            -1001277825103
        ],
        "allowed_extensions": [
            ".wav",
            ".flac",
            ".dff",
            ".dsf"
        ],
        "author": []
    },
    "paths": {
        "temp_memory": "temp",
        "final_library": "result",
        "log_file": "bot_running.log"
    },
    "audio_settings": {
        "sample_rate": 88200,
        "bit_depth": "s32",
        "target_ext": "flac",
        "convertor":"flac"
    }
}
```
每个参数的作用和注意事项如下:
### model
利用llm来清洗歌手的数据, 用于匹配频道里可能的资源, 只要有对应的model_name和key, 任何模型都可以 推荐[gemini/gemini-2.5-flash](https://aistudio.google.com), 免费额度非常大, 一次清洗8个歌手的所有作品都绰绰有余
### telegram
填写上文提到的api_key, api_hash和bot_token; workers是并发文件线程, 建议不要超过5;name就是session(Client对象的name参数)的名字, 可以随便写
### monitoring
关于在频道中搜索目标的参数

* exclude: ***排除项***, 当文件名中出现exclude列表中的字符时, 将直接**舍去**而不进行模糊匹配
* target_channels: ***频道池***, 整个程序将在这个列表中的频道里搜索资源, 本项目提供了4个, 但不涵盖所有歌手, 可能需要您填入自己寻找的频道
* allowed_extensions: ***允许的文件类型***, 默认为**无损**格式, 如果您不需要无损音乐(wav和dsf文件一般非常大, 下载它们很耗时), 请自己填写需要的格式
* author: 歌手列表
* mail: 填写你的邮箱, 这会被用于设置musicbrainzngs的user-agent

### path
整个项目的路径和日志的名字
* temp_memory: 当结果不是目标类型时, 将被下载到这里后转码
* final_library: 被转码过后或符合要求的结果所保存的路径
* log_file: 日志的名字, 一般不需要动

***注意: 请确保您有访问temp_memeory和final_library的权限***
### audio_settings
关于转码和ffmpeg的参数

* sample_rate: ***音频采样率***, 建议设置为44.1的倍数
* bit_depth: ***位深***, 默认为**s32**, 即32bit, 如果需要修改, 请改为类似这样的格式
* target_ext: ***目标格式***, 不用带".", 但注意这里不支持mp3, 因为mp3的编码器不支持修改采样率
* convertor: ***音频编码器***, 略微复杂, 详见下文[转码器配置](#转码器配置)

## 转码器配置
不同格式有对应的不同编码器, 下表列出了一些常用的, 请参阅:
|编码器|对应格式|注意事项|
|:--- |:--- | :---|
|aac / libfdk_aac|mp4,m4a|有损
|flac| flac| 无损, 高压缩率, 推荐
|pcm_s16le| wav| 无损, 未压缩16位pcm
|pcm_s24le| wav| 无损, 未压缩24位pcm
|pcm_s32le| wav| 无损, 未压缩32位pcm
|wavpack| wv   | 无损
|alac | m4a | 无损m4a, 对苹果设备支持良好

***注意:若选择 wav 格式，请确保 bit_depth 与编码器匹配（如 s24 对应 pcm_s24le），否则 FFmpeg 可能会报错。***

## 汇报机器人
在使用汇报机器人之前, 请确保在telegram中找到你的机器人并点击"START"

当脚本启动时, 机器人会发送提示, 并每隔2个小时发送一次任务/状态简报, 报告包括cpu, 内存占用, 程序运行时间, 业务机器人状态, 已下载的歌曲数量等

你也可以随时对bot发送"/status"命令来查看当前的进度, 类似这样:
![photo](./docs/Screenshot_20260301-123336_Telegram%20X.png)

***后记:说实话, 我意识到汇报机器人这个功能的可玩性还是挺高的,但目前我并没有想好它还能干什么,也许,之后用户可以对机器人发送命令来修改配置文件或者执行终端命令?如果你有任何想法, 欢迎提交issue***

## 源码和实现
关于源码, 我将程序分成了如下几个部分:索引, 搜索器, 下载器, 转码器和管理

整个项目的流程大概是: 
1. [get_idx.py](./handler/get_idx.py)从**musicbrainzngs**中获取**歌手的所有作品**, 交给llm清洗出纯录音室专辑并生成songs.json
2. [moniter.py](./handler/moniter.py)搜索专辑的id区间并将结果放入异步队列
3. [downloader.py](./core/downloader.py)获取队列的内容并下载 下载已经实现**断点传输**和**进度保存**(已经存在的文件会被跳过下载, 但不能避免被重复搜索)
4. [convertor.py](./core/convertor.py)转码**非目标格式**的文件, 在另一个线程工作, 不会阻塞主线程, 已经是目标格式的文件会被跳过

### 索引
和概述里说的差不多, 但注意程序只获取一次歌曲索引并在handler文件夹下生成一个songs.json文件

正如我在配置文件一栏的[model](#model)栏目说的那样, 因为项目使用了litellm模块, 数据清洗支持任何模型,只要有对应的key即可

对模型写的提示词如下:
```python
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
    response = litellm.completion(model=name, 
                                  messages=msg, 
                                  api_key=token, num_retries=5, 
                                  request_timeout=300)
    print(f'模型完成回复...')
    result = response.choices[0].message.content
    return result.replace('json', '').replace("```", '')
```
你可以随意修改提示词来清洗出你想要的数据, 但请让模型仅回复json

### 搜索器
我考虑到歌曲的名字可能很混乱, 所以引入**RapidFuzz**模块执行模糊匹配, 匹配函数如下:

```python
def is_song_match(query_name: str = "", target_name: str = "", threshold: int = 70) -> bool:
    if not query_name or not target_name: return False
    for word in exclud_list:
        if word in query_name: return False
    q_clean = clean_name(query_name)
    t_clean = clean_name(target_name)
    score = fuzz.token_set_ratio(q_clean, t_clean)
    return score >= threshold
```
原谅我使用了一个全局变量exclud_list, 它实际上就是配置文件里的[exclude](#monitoring), 只要搜索结果中含有排除项中的任何单词, 就会将匹配结果直接设置为False

整个搜索器以专辑为单位进行搜索, 但会尝试匹配专辑里的每一首歌; 搜索一共分两层, 第一层是对单曲进行模糊匹配, 若单曲的匹配结果>=80%, 则根据单曲在专辑中的位置算出整张专辑的区间(如一首歌是专辑的第二首, id为123, 则整张专辑的范围就是[122, 129]) 之后对区间里的每一首音乐做模糊匹配, 计算区间的得分, 得分>=80%的被认为是结果, 结果会被聚合成{歌手, 专辑, 频道id, id区间}并放入队列

***这种上下文匹配非常依赖频道的质量, 如果频道的资源不满足这种上下文关系, 那么搜索器便会失效, 所以请自备高质量资源频道, 如果愿意, 也可以提交issue以文本或者txt文件的形式分享***

### 下载器
因为我下载的资源全来自无损频道, 各种dsd流文件(dsf/dff)或者未压缩pcm都太大了, 加上我的代理不太稳定, 所以我为下载器加上了**断点传输**逻辑, 防止每次失败后都要重新开始下载:
```python
async def robust_download(client, msg, target_path, expected_size, manager):
    file_obj = msg.document or msg.audio
    
    for attempt in range(20):
        await manager.can_runs.wait()
        try:
            logger.info(f'尝试获取最新链接')
            msg = await client.get_messages(msg.chat.id, msg.id)
            current_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
            if current_size == expected_size:
                return True

            # 断点续传对齐逻辑
            offset_index = current_size // (1024 * 1024)
            aligned_size = offset_index * 1024 * 1024
            if offset_index != 0:
                logger.warning(f'已对齐残片: {file_obj.file_name} -> {offset_index}MB')
                with open(target_path, "r+b") as f:
                    f.truncate(aligned_size)
            
            # 开始流式下载
            downloaded_bytes = aligned_size
            with open(target_path, "ab") as f:
                async for chunk in client.stream_media(msg, offset=offset_index):
                    if not manager.can_runs.is_set(): # 如下载中途被全局暂停
                        raise InterruptedError("Global pause triggered")
                    f.write(chunk)
                    downloaded_bytes += len(chunk)

            final_size = os.path.getsize(target_path)
            if final_size < expected_size:
                raise RuntimeError(f"流异常中断: 进度 {final_size}/{expected_size}")
            else:
                logger.info(f"文件{file_obj.file_name} 下载成功")
                manager.files += 1

            manager.report_size += os.path.getsize(target_path) - current_size

            return True

        except (FloodWait, InterruptedError) as e:
            if manager.can_runs.is_set():
                async with dc_auth_lock:
                    if manager.can_runs.is_set(): 
                        wait_time = e.value if hasattr(e, 'value') else 60
                        logger.error(f"触发风控/暂停，静默 {wait_time}s")
                        manager.can_runs.clear()
                        manager.error_count += 1
                        await asyncio.sleep(wait_time)
                        manager.can_runs.set()
            continue

        except RPCError as e:
            # 捕获所有 Telegram 相关的 RPC 错误
            manager.error_count += 1
            logger.error(f"Telegram RPC错误 [尝试 {attempt}]: {e.NAME} - {e.MESSAGE}")
            await asyncio.sleep(random.uniform(5, 10))

        except Exception as e:
            # 兜底所有其他错误（IOError, Runtime等）
            manager.error_count += 1
            logger.error(f"未预期下载异常 [尝试 {attempt}]: {type(e).__name__} - {e}")
            await asyncio.sleep(random.uniform(5, 15))
            
    return False
```
实际上代码里的很多异常处理是摆设, 因为pyrogram根本不会抛出来, 在内部就处理好了, 但我还是选择写上以防万一 

这个函数通过可能的失败文件(即残片)来计算**offset_index**块索引和向下取整文件(即将文件中**aligned_size**以后的内容全部删除给那个位置的块腾位置), 每次拉取一个大小为1mb的文件块来实现断点传输并有**20**次重试, 这相较于直接使用**download_media**更可靠, 但这带来了更多的请求数量, 所以请***不要将并发数和Client对象中的 sleep_threshold设置的太高***

### 转码器
转码实际上非常简略:
```python
def to_tar_ext(source_path: str, tar_path: str, signal_path: str):
    print(f">>> 正在转码: {os.path.basename(source_path)}")
    try:
        (ffmpeg
         .input(source_path)
         .output(
             tar_path,
             acodec=conv,
             ar=sample_rate,
             sample_fmt=bit_depth
         )
         .overwrite_output()
         .run(capture_stdout=True, capture_stderr=True))
        
        print(f"--- 转码成功: {os.path.basename(tar_path)}")
        # 只有成功后才删除源文件和信号文件
        if os.path.exists(source_path): os.remove(source_path)
        if os.path.exists(signal_path): os.remove(signal_path)

    except Exception as e:
        print(f' [!] 转码器报错: {e} \n 文件: {source_path} 转码失败')
        # 记录失败
        with open(f"{tar_path}.FAILED", "w") as f:
            f.write(f"{source_path.upper()} CONVERT FAILED")
        if os.path.exists(signal_path): os.remove(signal_path)
```
这里的"信号文件", 指的是**非目标格式的音频被下载完成后, 会在temp中生成一个同名的.txt文件, 文件内写的是音频的最后的路径**

你可能会问我, 我为什么要这么多此一举:
1. 在项目初期我脑子突然抽经了, 当回过神来时项目已经完成
2. 我可不想看到我的结果里可能出现一堆下了一半的东西

至于转码本身, 我就只是简单的往ffmpeg里传了配置文件里的参数而已, 没什么可说的

## 常见错误和解决
### urlopen error [Errno -3] Temporary failure in name resolution
这是在请求musicbrainz的数据库时dns解析错误导致的 为了尽可能抵消这个问题, 我设置了5次重试, 如果5次重试都失败了, 请检查你的代理/网络设置

### Tgcrpto的whl编译失败
这是在windows下使用高版本python解释器(通常是3.12/13)导致的, 我没有找到在这些解释器下解决这个问题的办法 请降低解释器版本到3.11

###   caused by: <urlopen error [Errno 2] No such file or directory> 或与SSL相关的报错
也是和musicbrainz数据库请求相关的, 在linux下和在windows下都存在, 没有找到解决办法, 可以尝试增加重试次数来解决

### bad requests 429
出现在调用llm清洗数据的时候, 通常是因为使用了被弃用的模型或者模型到限额导致的

***补充:请务必检查模型的[name](#model), 即使写错了一个字母, 程序也会报错***

## 特别说明
这算是我的第一个项目, 开发初期也是为了我自己, 但觉得自己这么久过去了自己也没有像样的作品, 于是就慢慢给这个一次性脚本缝缝补补, 最终呈现出了这个样子

我非常清楚这个脚本仍然拥有的众多问题和不足, 但我会尝试慢慢维护这个项目, 如果您有任何意见和问题, 欢迎提交issue

另外, 这个脚本***不适合古典乐***, 请不要尝试用它下载古典乐


如果你喜欢这个项目, 或者这个项目帮到你了, 请给我一个star⭐, 这对我来说很重要, 谢谢🙏

***此外, 项目欢迎任何形式的pr, 只要你愿意为项目贡献代码***

