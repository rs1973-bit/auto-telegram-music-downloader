import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.index import MusicIndexer


class TestMusicIndexer(unittest.TestCase):
    """MusicIndexer 的单元测试（Deezer API）"""

    def setUp(self):
        self.mock_sql = MagicMock()
        self.indexer = MusicIndexer(self.mock_sql)

    def tearDown(self):
        self.indexer = None
        self.mock_sql = None

    # ==================== 测试 _normalize_title ====================

    def test_normalize_title_removes_remastered(self):
        self.assertEqual(
            MusicIndexer._normalize_title("Abbey Road (Remastered)"),
            "Abbey Road",
        )

    def test_normalize_title_removes_super_deluxe(self):
        self.assertEqual(
            MusicIndexer._normalize_title("Revolver (Super Deluxe)"),
            "Revolver",
        )

    def test_normalize_title_unchanged(self):
        self.assertEqual(
            MusicIndexer._normalize_title("Please Please Me"),
            "Please Please Me",
        )

    def test_normalize_title_multiple_suffixes(self):
        self.assertEqual(
            MusicIndexer._normalize_title("Sgt. Pepper (Deluxe Edition)"),
            "Sgt. Pepper",
        )

    # ==================== 测试 _is_studio_album ====================

    def test_is_studio_album_album_type(self):
        self.assertTrue(MusicIndexer._is_studio_album({"record_type": "album", "title": "Abbey Road"}))

    def test_is_studio_album_rejects_single(self):
        self.assertFalse(MusicIndexer._is_studio_album({"record_type": "single", "title": "Hey Jude"}))

    def test_is_studio_album_rejects_live_keyword(self):
        self.assertFalse(MusicIndexer._is_studio_album({"record_type": "album", "title": "Live at the BBC"}))

    def test_is_studio_album_rejects_anthology(self):
        self.assertFalse(MusicIndexer._is_studio_album({"record_type": "album", "title": "Anthology 1"}))

    def test_is_studio_album_rejects_instrumental(self):
        self.assertFalse(MusicIndexer._is_studio_album({"record_type": "album", "title": "Piano Covers"}))

    # ==================== 测试 get_artist_id ====================

    async def _test_get_artist_id(self, mock_data, expected):
        with patch.object(MusicIndexer, "_request", AsyncMock(return_value=mock_data)):
            return await self.indexer.get_artist_id("Test Artist")

    def test_get_artist_id_found(self):
        result = asyncio.run(self._test_get_artist_id(
            {"data": [{"id": 123, "name": "Test Artist"}]}, 123,
        ))
        self.assertEqual(result, 123)

    def test_get_artist_id_not_found(self):
        result = asyncio.run(self._test_get_artist_id({"data": []}, None))
        self.assertIsNone(result)

    def test_get_artist_id_api_failure(self):
        result = asyncio.run(self._test_get_artist_id(None, None))
        self.assertIsNone(result)

    # ==================== 测试 get_albums ====================

    async def _test_get_albums(self, mock_data, expected_count):
        with patch.object(MusicIndexer, "_request", AsyncMock(return_value=mock_data)):
            return await self.indexer.get_albums(123)

    def test_get_albums_single_page(self):
        mock = {
            "data": [
                {"id": 1, "title": "Album 1", "record_type": "album"},
                {"id": 2, "title": "Live Album", "record_type": "album"},
                {"id": 3, "title": "Album 2 (Remastered)", "record_type": "album"},
                {"id": 4, "title": "Single", "record_type": "single"},
            ]
        }
        result = asyncio.run(self._test_get_albums(mock, 2))
        self.assertEqual(len(result), 2)
        self.assertIn((1, "Album 1"), result)
        self.assertIn((3, "Album 2"), result)

    def test_get_albums_empty(self):
        result = asyncio.run(self._test_get_albums({"data": []}, 0))
        self.assertEqual(result, [])

    def test_get_albums_api_failure(self):
        result = asyncio.run(self._test_get_albums(None, 0))
        self.assertEqual(result, [])

    # ==================== 测试 get_tracks ====================

    async def _test_get_tracks(self, mock_data, expected_count):
        with patch.object(MusicIndexer, "_request", AsyncMock(return_value=mock_data)):
            await self.indexer.get_tracks(123, "Artist", "Album")
        return len(self.indexer.result)

    def test_get_tracks_single_page(self):
        mock = {
            "data": [
                {"title": "Song 1", "track_position": 1},
                {"title": "Song 2", "track_position": 2},
            ]
        }
        count = asyncio.run(self._test_get_tracks(mock, 2))
        self.assertEqual(count, 2)
        self.assertIn(("Artist", "Album", "Song 1"), self.indexer.result)
        self.assertIn(("Artist", "Album", "Song 2"), self.indexer.result)

    def test_get_tracks_empty(self):
        count = asyncio.run(self._test_get_tracks({"data": []}, 0))
        self.assertEqual(count, 0)

    # ==================== 测试 task ====================

    def test_task_complete_flow(self):
        async def run():
            with (
                patch.object(self.indexer, "get_artist_id", AsyncMock(return_value=42)),
                patch.object(self.indexer, "get_albums", AsyncMock(return_value=[(1, "Album 1"), (2, "Album 2")])),
                patch.object(self.indexer, "get_tracks", AsyncMock()),
            ):
                await self.indexer.task("Test Artist")
                self.assertEqual(self.indexer.get_artist_id.call_count, 1)
                self.indexer.get_artist_id.assert_called_with("Test Artist")
                self.assertEqual(self.indexer.get_albums.call_count, 1)
                self.assertEqual(self.indexer.get_tracks.call_count, 2)

        asyncio.run(run())

    def test_task_artist_not_found(self):
        async def run():
            with patch.object(self.indexer, "get_artist_id", AsyncMock(return_value=None)):
                await self.indexer.task("Unknown")
                self.assertEqual(len(self.indexer.result), 0)

        asyncio.run(run())

    def test_task_no_albums(self):
        async def run():
            with (
                patch.object(self.indexer, "get_artist_id", AsyncMock(return_value=42)),
                patch.object(self.indexer, "get_albums", AsyncMock(return_value=[])),
            ):
                await self.indexer.task("Test Artist")
                self.assertEqual(len(self.indexer.result), 0)

        asyncio.run(run())

    # ==================== 测试 GET_IDX ====================

    def test_GET_IDX_skips_when_data_exists(self):
        self.mock_sql.count_idx_songs = AsyncMock(return_value=3152)

        asyncio.run(self.indexer.GET_IDX())
        self.assertTrue(self.indexer.done)
        self.assertEqual(len(self.indexer.result), 0)

    def test_GET_IDX_runs_tasks(self):
        import src.utils.config as cfg_mod
        cfg_mod.cfg.author_list = ["Artist1", "Artist2"]
        self.mock_sql.count_idx_songs = AsyncMock(return_value=0)
        self.mock_sql.insert_for_GET_IDX = AsyncMock()

        async def run():
            with patch.object(self.indexer, "task", AsyncMock()):
                await self.indexer.GET_IDX()
                self.assertEqual(self.indexer.task.call_count, 2)
                self.assertTrue(self.indexer.done)

        asyncio.run(run())

    def test_GET_IDX_writes_results(self):
        self.mock_sql.count_idx_songs = AsyncMock(return_value=0)
        self.mock_sql.insert_for_GET_IDX = AsyncMock()
        self.indexer.result = [("A", "B", "C")]

        async def run():
            with patch.object(self.indexer, "task", AsyncMock()):
                await self.indexer.GET_IDX()
                self.mock_sql.insert_for_GET_IDX.assert_called_once_with([("A", "B", "C")])

        asyncio.run(run())


class TestMusicIndexerIntegration(unittest.TestCase):
    """集成测试 - 模拟 Deezer API 完整工作流"""

    def setUp(self):
        self.mock_sql = AsyncMock()
        self.indexer = MusicIndexer(self.mock_sql)

    def _mock_request(self, side_effect):
        return patch.object(MusicIndexer, "_request", AsyncMock(side_effect=side_effect))

    def test_full_artist_workflow(self):
        """模拟 Deezer API 响应的完整索引流程"""
        artist_search = {"data": [{"id": 42, "name": "Taylor Swift"}]}
        albums_resp = {
            "data": [
                {"id": 100, "title": "Folklore", "record_type": "album"},
                {"id": 200, "title": "Evermore", "record_type": "album"},
            ]
        }
        folklore_tracks = {"data": [{"title": "Willow"}, {"title": "Cardigan"}]}
        evermore_tracks = {"data": [{"title": "No Time to Die"}]}

        async def run():
            with self._mock_request(side_effect=[
                artist_search, albums_resp, folklore_tracks, evermore_tracks,
            ]):
                await self.indexer.task("Taylor Swift")

                self.assertEqual(len(self.indexer.result), 3)
                self.assertIn(("Taylor Swift", "Folklore", "Willow"), self.indexer.result)
                self.assertIn(("Taylor Swift", "Folklore", "Cardigan"), self.indexer.result)
                self.assertIn(("Taylor Swift", "Evermore", "No Time to Die"), self.indexer.result)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
