import json
import os

# 加载配置文件
class config:
    def __init__(self):
        with open(r'config.json', 'r') as c:
            conf = json.load(c)
        def _get(d, *keys, default=None):
            v = d
            try:
                for k in keys:
                    v = v[k]
                return v
            except Exception:
                return default

        self.songs = {}
        self.bot_name = _get(conf, "telegram", "name", default="auto-bot")
        self.key = _get(conf, "model", "key", default="")
        self.name = _get(conf, "model", "name", default="")
        self.artists = _get(conf, "monitoring", "author", default=[])
        self.mail = _get(conf, "monitoring", "mail", default="")
        self.moniting = _get(conf, "monitoring", default={})
        self.targets = _get(self.moniting, "target_channels", default=[])
        self.author_list = _get(self.moniting, "author", default=self.artists)
        self.exclude_list = _get(self.moniting, "exclude", default=[])
        self.api_id = _get(conf, "telegram", "api_id", default=None)
        self.api_hash = _get(conf, "telegram", "api_hash", default=None)
        self.bot_token = _get(conf, "telegram", "bot_token", default=None)
        self.save_path = _get(conf, "paths", "final_library", default="result")

        # workers
        try:
            self.max_workers = int(_get(conf, "telegram", "workers", default=2))
        except Exception:
            self.max_workers = 2

        # audio settings normalization and validation
        def normalize_sample_rate(v):
            try:
                return str(int(v))
            except Exception:
                print(f"配置警告: 无效的 sample_rate '{v}', 使用默认 44100")
                return "44100"

        def normalize_bit_depth(v):
            if not v:
                return "s16"
            s = str(v).lower().strip()
            # common inputs: '16', '16bit', 's16', 's16le'
            if s.isdigit():
                if s == '8':
                    return 'u8'
                return f's{s}'
            if s.endswith('bit') and s[:-3].isdigit():
                b = s[:-3]
                if b == '8':
                    return 'u8'
                return f's{b}'
            # known sample_fmt aliases
            aliases = {'s16le': 's16', 's24le': 's24', 's32le': 's32', 'float': 'flt'}
            return aliases.get(s, s)

        self.sample_rate = normalize_sample_rate(_get(conf, "audio_settings", "sample_rate", default=44100))
        self.bit_depth = normalize_bit_depth(_get(conf, "audio_settings", "bit_depth", default='s16'))
        self.target_ext = str(_get(conf, "audio_settings", "target_ext", default='flac')).lower().lstrip('.')

        self.temp_path = _get(conf, "paths", "temp_memory", default='temp')
        self.save_root = self.save_path
        self.tar_ext = self.target_ext

        try:
            self.workers = int(_get(conf, "telegram", "workers", default=2))
            if self.workers < 1:
                raise ValueError
        except Exception:
            print(f"配置警告: 无效的 workers 值，使用默认 2")
            self.workers = 2

        # ensure directories exist
        if not os.path.exists(self.temp_path):
            try:
                os.makedirs(self.temp_path, exist_ok=True)
            except Exception as e:
                print(f"创建临时目录失败: {e}")
        if not os.path.exists(self.save_root):
            try:
                os.makedirs(self.save_root, exist_ok=True)
            except Exception as e:
                print(f"创建保存目录失败: {e}")

        self.conv = _get(conf, "audio_settings", "convertor", default='flac')
        if not isinstance(self.conv, str) or not self.conv:
            print(f"配置警告: 无效的 convertor 值，使用 'flac'")
            self.conv = 'flac'

        self.collected_ids = set()

cfg = config()