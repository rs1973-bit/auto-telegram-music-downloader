import asyncio
import sys
import os
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

with patch.dict(sys.modules, {
    "src.utils.logger": MagicMock(logger=logging.getLogger("test")),
}):
    from src.services.searcher import Search_in_TG
    from src.utils.config import cfg


REVOLVER_SONGS = [
    "Taxman", "Eleanor Rigby", "I'm Only Sleeping",
    "Love You To", "Here, There and Everywhere",
    "Yellow Submarine", "She Said She Said",
    "Good Day Sunshine", "And Your Bird Can Sing",
    "For No One", "Doctor Robert",
    "I Want to Tell You", "Got to Get You into My Life",
    "Tomorrow Never Knows",
]


def _audio_msg(mid, name):
    m = MagicMock()
    m.id = mid; m.text = None; m.caption = None; m.document = None
    a = MagicMock(); a.file_name = name; m.audio = a
    return m


def _text_msg(mid, text):
    m = MagicMock()
    m.id = mid; m.text = text; m.caption = None
    m.document = None; m.audio = None
    return m


@pytest.fixture
def mock_sql():
    s = MagicMock()
    s.get_bands_from_IDX = AsyncMock(return_value=["The Beatles"])
    s.get_albums_from_IDX = AsyncMock(return_value=["Revolver"])
    async def _get_songs(band, album):
        return list(REVOLVER_SONGS) if album == 'Revolver' else []
    s.get_songs_from_IDX = _get_songs
    s.is_song_exists = AsyncMock(return_value=False)
    s.get_status_from_DATA = AsyncMock(return_value=0)
    s.get_statuses_in_album = AsyncMock(return_value={})  # 默认无已下载
    s.insert_for_DATA = AsyncMock()
    s.get_pending_tasks = AsyncMock(return_value=[])
    return s


@pytest.fixture
def revolver_msgs():
    msgs = {5000: _text_msg(5000, "The Beatles - Revolver (1966) [FLAC]")}
    for i, song in enumerate(REVOLVER_SONGS):
        msgs[5001 + i] = _audio_msg(5001 + i, f"{i+1:02d} - {song}.flac")
    return -1001234567890, msgs


@pytest.fixture
def mock_app(revolver_msgs):
    chat_id, msgs = revolver_msgs
    app = MagicMock()
    app.get_chat = AsyncMock(return_value=MagicMock(id=chat_id))

    async def _search(chat_id, filter=None, query=None, limit=100, **_):
        q = query.replace("The Beatles ", "")
        for mid in sorted(msgs):
            m = msgs[mid]
            # text search (no filter): match text/caption
            if filter is None:
                txt = (m.text or m.caption or "").lower()
                if q.lower() in txt:
                    yield m
            # audio/doc search: match file name
            else:
                f = m.audio or m.document
                if f and f.file_name and q.lower() in f.file_name.lower():
                    yield m

    async def _history(chat_id, offset_id, limit, **_):
        lo = offset_id - limit
        for mid in sorted(msgs, reverse=True):
            if lo <= mid < offset_id:
                yield msgs[mid]

    app.search_messages = _search
    app.get_chat_history = _history
    return app


@pytest.fixture
def searcher(mock_app, mock_sql):
    q = asyncio.Queue()
    cfg.targets = [-1001234567890]
    cfg.collected_ids = set()
    cfg.exclude_list = []
    mgr = MagicMock()
    mgr.can_runs = asyncio.Event(); mgr.can_runs.set()
    mgr.need_stop = MagicMock(return_value=False)
    return Search_in_TG(mock_app, mgr, q, mock_sql)


@pytest.fixture(autouse=True)
def _reset_cfg():
    cfg.targets = [-1001234567890]
    cfg.collected_ids = set()
    cfg.exclude_list = []


class TestSearchAlbum:
    @pytest.mark.asyncio
    async def test_finds_album(self, searcher):
        ok = await searcher._search_album("The Beatles", "Revolver")
        assert ok
        tasks = []
        while not searcher.queue.empty():
            tasks.append(await searcher.queue.get())
        assert len(tasks) == 14
        names = {t.song for t in tasks}
        assert "Taxman" in names
        assert "Tomorrow Never Knows" in names

    @pytest.mark.asyncio
    async def test_not_found(self, searcher):
        assert not await searcher._search_album("The Beatles", "FakeAlbum")

    @pytest.mark.asyncio
    async def test_track_numbers(self, searcher):
        await searcher._search_album("The Beatles", "Revolver")
        tasks = []
        while not searcher.queue.empty():
            tasks.append(await searcher.queue.get())
        t = [t for t in tasks if t.song == "Taxman"]
        assert t[0].idx == 1

    @pytest.mark.asyncio
    async def test_dsf_dff(self, mock_app, revolver_msgs, mock_sql):
        _, msgs = revolver_msgs
        msgs[5001] = _audio_msg(5001, "01 - Taxman.dsf")
        msgs[5002] = _audio_msg(5002, "02 - Eleanor Rigby.dff")
        q = asyncio.Queue()
        cfg.targets = [-1001234567890]
        cfg.collected_ids = set(); cfg.exclude_list = []
        mgr = MagicMock()
        mgr.can_runs = asyncio.Event(); mgr.can_runs.set()
        mgr.need_stop = MagicMock(return_value=False)
        s = Search_in_TG(mock_app, mgr, q, mock_sql)
        assert await s._search_album("The Beatles", "Revolver")

    @pytest.mark.asyncio
    async def test_scan_around_anchor_discovers_all(self, searcher, revolver_msgs):
        """双向扫描能覆盖连续曲目区间（无交错时）。"""
        cid, _ = revolver_msgs
        track_map = await searcher._scan_around_anchor(
            cid, 5007, "The Beatles", "Revolver", 14
        )
        assert len(track_map) == 14
        assert "Taxman" in track_map
        assert "Tomorrow Never Knows" in track_map

    @pytest.mark.asyncio
    async def test_build_track_map_all_songs(self, searcher, revolver_msgs):
        """_build_track_map 正确映射曲目到 msg_id。"""
        cid, msgs = revolver_msgs
        msg_list = []
        # 用消息 ID 5000-5014 (公告+14首歌)
        for mid in sorted(msgs):
            if 5000 <= mid <= 5014:
                msg_list.append(msgs[mid])
        track_map = await searcher._build_track_map("The Beatles", "Revolver", msg_list)
        assert len(track_map) == 14
        assert track_map.get("Taxman") == 5001
        assert track_map.get("Tomorrow Never Knows") == 5014

    @pytest.mark.asyncio
    async def test_build_track_map_skips_non_audio(self, searcher, revolver_msgs):
        """非音频消息不会被 _build_track_map 计入。"""
        cid, msgs = revolver_msgs
        msg_list = [msgs[5000]]  # 只有文字公告
        track_map = await searcher._build_track_map("The Beatles", "Revolver", msg_list)
        assert len(track_map) == 0

    @pytest.mark.asyncio
    async def test_skips_downloaded(self, mock_app, revolver_msgs, mock_sql):
        # 模拟所有曲目均已下载
        mock_sql.get_statuses_in_album = AsyncMock(
            return_value={s: 1 for s in REVOLVER_SONGS}
        )
        q = asyncio.Queue()
        cfg.targets = [-1001234567890]
        cfg.collected_ids = set(); cfg.exclude_list = []
        mgr = MagicMock()
        mgr.can_runs = asyncio.Event(); mgr.can_runs.set()
        mgr.need_stop = MagicMock(return_value=False)
        s = Search_in_TG(mock_app, mgr, q, mock_sql)
        ok = await s._search_album("The Beatles", "Revolver")
        assert ok  # 全已下载也算"搜索成功"
        assert q.empty()

    @pytest.mark.asyncio
    async def test_msg_id_matches_actual_file(self, searcher, revolver_msgs):
        """_build_track_map 返回的 msg_id 对应的是实际文件消息。"""
        cid, msgs = revolver_msgs
        msg_list = [msgs[mid] for mid in sorted(msgs) if 5001 <= mid <= 5014]
        track_map = await searcher._build_track_map("The Beatles", "Revolver", msg_list)
        # 验证 Taxman 被映射到实际包含它的消息
        taxman_msgs = [m for m in msg_list if "taxman" in (m.audio.file_name or "").lower()]
        assert track_map.get("Taxman") == taxman_msgs[0].id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])