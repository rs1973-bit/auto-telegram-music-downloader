import json
import os
from pathlib import Path


class config:
    """应用配置，从 config.json 加载。"""

    def __init__(self):
        with open(Path(__file__).parent.parent.parent / "config.json") as f:
            conf = json.load(f)

        def _get(d: dict, *keys: str, default=None):
            v = d
            for k in keys:
                try:
                    v = v[k]
                except (KeyError, TypeError, IndexError):
                    return default
            return v

        # ── Telegram ────────────────────────────────────────────── #
        self.api_id: str | None = _get(conf, "telegram", "api_id")
        self.api_hash: str | None = _get(conf, "telegram", "api_hash")
        self.bot_token: str | None = _get(conf, "telegram", "bot_token")
        self.session_name: str = str(_get(conf, "telegram", "session_name", default="rs1973"))
        self.dl_workers: int = int(_get(conf, "telegram", "workers", default=2))

        # ── Monitoring ──────────────────────────────────────────── #
        self.targets: list[int] = list(_get(conf, "monitoring", "target_channels", default=[]))
        self.author_list: list[str] = list(_get(conf, "telegram", "author_list", default=[]))
        self.exclude_list: list[str] = list(_get(conf, "monitoring", "exclude", default=[]))
        self.allowed_extensions: list[str] = list(
            _get(conf, "monitoring", "allowed_extensions", default=[])
        )

        # ── Paths ───────────────────────────────────────────────── #
        self.temp: Path = Path(str(_get(conf, "paths", "temp", default="temp")))
        self.result: Path = Path(str(_get(conf, "paths", "result", default="result")))
        self.log: str = str(_get(conf, "paths", "log", default="bot_running.log"))

        # ── Audio ───────────────────────────────────────────────── #
        self.sample_rate: str = str(_get(conf, "audio", "sample_rate", default=44100))
        self.format: str = str(_get(conf, "audio", "format", default="flac")).lstrip(".")
        self.codec: str = str(_get(conf, "audio", "codec", default="flac"))

        raw_depth = _get(conf, "audio", "bit_depth", default="s16")
        self.bit_depth: str = self._normalize_bit_depth(raw_depth)

        # ── Runtime state ───────────────────────────────────────── #
        self.collected_ids: set[int] = set()

        # ── Ensure directories ──────────────────────────────────── #
        self.temp.mkdir(parents=True, exist_ok=True)
        self.result.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  兼容别名（供外部引用）
    # ------------------------------------------------------------------ #

    @property
    def bot_name(self) -> str:
        return self.session_name

    @property
    def temp_path(self) -> str:
        return str(self.temp)

    @property
    def save_root(self) -> str:
        return str(self.result)

    @property
    def conv(self) -> str:
        return self.codec

    @property
    def target_ext(self) -> str:
        return self.format

    @property
    def workers(self) -> int:
        return self.dl_workers

    @property
    def max_workers(self) -> int:
        return self.dl_workers

    @property
    def save_path(self) -> str:
        return str(self.result)

    # ------------------------------------------------------------------ #
    #  辅助方法
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_bit_depth(v: object) -> str:
        """将 bit depth 统一为 ffmpeg sample_fmt 格式。"""
        if not v:
            return "s16"
        s = str(v).lower().strip()
        if s.isdigit():
            return "u8" if s == "8" else f"s{s}"
        if s.endswith("bit") and s[:-3].isdigit():
            b = s[:-3]
            return "u8" if b == "8" else f"s{b}"
        return {"s16le": "s16", "s24le": "s24", "s32le": "s32", "float": "flt"}.get(s, s)


cfg = config()