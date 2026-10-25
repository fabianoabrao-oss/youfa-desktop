import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yt_dlp
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import DownloadCancelled, ExtractorError
from app import AudioApp, QueueLogger


class PlaylistFixtureIE(InfoExtractor):
    _VALID_URL = r'youfa-fixture:(?P<id>playlist|first|missing|last)'

    def _real_extract(self, url):
        video_id = self._match_id(url)
        if video_id == 'playlist':
            return self.playlist_result([self.url_result('youfa-fixture:' + name) for name in ('first', 'missing', 'last')], 'playlist')
        if video_id == 'missing':
            raise ExtractorError('Video unavailable', expected=True)
        return {'id': video_id, 'title': video_id, 'formats': [{'url': 'https://example.invalid/' + video_id + '.mp3', 'ext': 'mp3', 'vcodec': 'none', 'acodec': 'mp3'}]}


class PlaylistTests(unittest.TestCase):
    def make_app(self):
        app = AudioApp.__new__(AudioApp)
        app.cancel_event = threading.Event()
        app.events_seen = []
        app._emit = lambda *event: app.events_seen.append(event)
        return app

    def test_extractor_continues_past_missing_middle_video(self):
        app = self.make_app()
        logger = QueueLogger(app._emit)
        with yt_dlp.YoutubeDL({'ignoreerrors': True, 'logger': logger, 'quiet': True}, auto_init=False) as ydl:
            ydl.add_info_extractor(PlaylistFixtureIE())
            result = ydl.extract_info('youfa-fixture:playlist', download=False)
        self.assertEqual([x['id'] for x in app._flatten_info(result)], ['first', 'last'])
        self.assertTrue(logger.errors)

    def test_download_marks_partial_completion_with_warnings(self):
        app = self.make_app()
        with tempfile.TemporaryDirectory() as folder:
            def download(url, download=True):
                opts = factory.call_args.args[0]
                self.assertTrue(opts['ignoreerrors'])
                opts['logger'].error('missing: Video unavailable')
                for name in ('first', 'last'): (Path(folder) / (name + '.mp3')).write_bytes(b'audio')
                return {'entries': [{'id': 'first'}, None, {'id': 'last'}]}
            with patch('app.resource_path', return_value=Path(__file__)), patch('app.yt_dlp.YoutubeDL') as factory:
                factory.return_value.__enter__.return_value.extract_info.side_effect = download
                app._download('https://www.youtube.com/playlist?list=fixture', Path(folder), 'mp3', 'Alta', 'Arquivo inteiro', True, False)
        self.assertTrue(app._download_warnings)
        self.assertIn(('done', True), app.events_seen)
        self.assertTrue(any(kind == 'log' and 'Concluído com avisos' in message for kind, message in app.events_seen))

    def test_all_missing_is_failure_and_single_video_stays_strict(self):
        for allow_playlist, info in ((True, {'entries': [None]}), (False, None)):
            app = self.make_app()
            with tempfile.TemporaryDirectory() as folder:
                with patch('app.resource_path', return_value=Path(__file__)), patch('app.yt_dlp.YoutubeDL') as factory:
                    factory.return_value.__enter__.return_value.extract_info.return_value = info
                    app._download('https://www.youtube.com/watch?v=fixture', Path(folder), 'mp3', 'Alta', 'Arquivo inteiro', allow_playlist, False)
                    self.assertEqual(factory.call_args.args[0]['ignoreerrors'], allow_playlist)
            self.assertIn(('done', False), app.events_seen)

    def test_cancel_still_stops_playlist(self):
        app = self.make_app()
        app.cancel_event.set()
        with self.assertRaises(DownloadCancelled): app._download_filter({})
        with self.assertRaises(DownloadCancelled): app._progress_hook({})


if __name__ == '__main__':
    unittest.main()
