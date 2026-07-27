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
    s.get_albums_from_IDX = AsyncMock(return_value=["Revolver"])
    async def _get_songs(band, album):
        return list(REVOLVER_SONGS) if album == 'Revolver' else []
    s.get_songs_from_IDX = _get_songs
    s.is_song_exists = AsyncMock(return_value=False)
    s.get_status_from_DATA = AsyncMock(return_value=0)
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
    cfg.author_list = ["The Beatles"]
    cfg.targets = [-1001234567890]
    cfg.collected_ids = set()
    cfg.exclude_list = []
    mgr = MagicMock()
    mgr.can_runs = asyncio.Event(); mgr.can_runs.set()
    mgr.need_stop = MagicMock(return_value=False)
    return Search_in_TG(mock_app, mgr, q, mock_sql)



@pytest.fixture(autouse=True)
def _reset_cfg():
    cfg.author_list = ["The Beatles"]
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
        cfg.author_list = ["The Beatles"]
        cfg.targets = [-1001234567890]
        cfg.collected_ids = set(); cfg.exclude_list = []
        mgr = MagicMock()
        mgr.can_runs = asyncio.Event(); mgr.can_runs.set()
        mgr.need_stop = MagicMock(return_value=False)
        s = Search_in_TG(mock_app, mgr, q, mock_sql)
        assert await s._search_album("The Beatles", "Revolver")

    @pytest.mark.asyncio
    async def test_progressive_expansion(self, searcher, revolver_msgs):
        cid, _ = revolver_msgs
        rate, details = await searcher._validate_hit_rate(
            "The Beatles", "Revolver", cid, 5001, 5014
        )
        assert rate > 0.7
        assert len(details) == 14

    @pytest.mark.asyncio
    async def test_skips_downloaded(self, mock_app, revolver_msgs, mock_sql):
        mock_sql.is_song_exists = AsyncMock(return_value=True)
        mock_sql.get_status_from_DATA = AsyncMock(return_value=1)
        q = asyncio.Queue()
        cfg.author_list = ["The Beatles"]
        cfg.targets = [-1001234567890]
        cfg.collected_ids = set(); cfg.exclude_list = []
        mgr = MagicMock()
        mgr.can_runs = asyncio.Event(); mgr.can_runs.set()
        mgr.need_stop = MagicMock(return_value=False)
        s = Search_in_TG(mock_app, mgr, q, mock_sql)
        await s._search_album("The Beatles", "Revolver")
        assert q.empty()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])