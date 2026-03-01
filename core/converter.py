import os
import json
import ffmpeg
from concurrent.futures import ThreadPoolExecutor

# 加载配置
with open("config.json", 'r', encoding='utf-8') as f:
    conf = json.load(f)


sample_rate = str(conf["audio_settings"]["sample_rate"])
bit_depth = conf["audio_settings"]["bit_depth"]
target_ext = conf["audio_settings"]["target_ext"]
temp_path = conf["paths"]["temp_memory"]
conv = conf["audio_settings"]["convertor"]

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

def converter():
    """
    扫描并匹配任务
    """
    if not os.path.exists(temp_path):
        return

    all_files = os.listdir(temp_path)
    
    audio_extensions = ('.dsf', '.dff', '.wav', '.m4a')
    audio_files = [f for f in all_files if f.lower().endswith(audio_extensions)]

    if not audio_files:
        return

    with ThreadPoolExecutor(max_workers=1) as t:
        for audio_name in audio_files:
            source_full_path = os.path.join(temp_path, audio_name)
            
            signal_name = audio_name + ".txt"
            signal_full_path = os.path.join(temp_path, signal_name)

            if os.path.exists(signal_full_path):
                try:
                    with open(signal_full_path, 'r', encoding='utf-8') as s:
                        save_root_path = s.read().strip()
                    
                    if not os.path.exists(save_root_path):
                        os.makedirs(save_root_path, exist_ok=True)

                    name_without_ext = os.path.splitext(audio_name)[0]
                    final_tar_path = os.path.join(save_root_path, f"{name_without_ext}.{target_ext}")

                    # 3. 提交任务：显式传入信号文件路径
                    t.submit(to_tar_ext, source_full_path, final_tar_path, signal_full_path)
                except Exception as e:
                    print(f"解析信号文件失败: {signal_name}, {e}")
            else:
                continue
