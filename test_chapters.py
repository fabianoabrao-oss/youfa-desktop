import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import AudioApp


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.app = AudioApp.__new__(AudioApp)
        self.app._track_metadata = {}
        self.app._split_outputs = set()
        self.app.cancel_event = threading.Event()
        self.events = []
        self.app._emit = lambda *event: self.events.append(event)

    def test_real_video_timestamps_and_medley_titles(self):
        info = json.loads((Path(__file__).parent / 'tests/data/rockbeats-chapters.json').read_text(encoding='utf-8'))
        self.app._duration = lambda *_: info['duration']
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'mix [EVD8fMVzWRU].mp3'
            source.write_bytes(b'original')
            calls = []
            def cut(args, **kwargs):
                calls.append(args)
                Path(args[-1]).write_bytes(b'track')
            with patch('app.subprocess.run', side_effect=cut):
                tracks = self.app._split_chapters(source, Path('ffmpeg'), info)
            self.assertEqual(len(tracks), 39)
            for track, args, chapter in zip(tracks, calls, info['chapters']):
                self.assertEqual(float(args[args.index('-ss') + 1]), chapter['start_time'])
                self.assertEqual(float(args[args.index('-t') + 1]), chapter['end_time'] - chapter['start_time'])
                self.assertEqual(self.app._free_metadata(track, info), ('', chapter['title']))
            self.assertIn('Medley Sitting', tracks[2].name)
            self.assertFalse(source.exists())

    def test_acoustic_tracks_never_inherit_chapter_or_mix_names(self):
        path = Path('01 - faixa.mp3')
        self.app._split_outputs.add(path)
        self.assertIsNone(self.app._free_metadata(path, {'title': 'Band - Mix', 'chapters': [{'title': 'Wrong - Song'}]}))

    def test_invalid_chapters_and_failed_cut_preserve_original(self):
        self.app._duration = lambda *_: 200
        for chapters in ([{'start_time': 10, 'end_time': 100}, {'start_time': 100, 'end_time': 200}],
                         [{'start_time': 0, 'end_time': 100}, {'start_time': 100, 'end_time': 200}]):
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / 'mix.mp3'
                source.write_bytes(b'original')
                with patch('app.subprocess.run', side_effect=RuntimeError('cut failed')):
                    self.assertEqual(self.app._split_chapters(source, Path('ffmpeg'), {'chapters': chapters}), [])
                self.assertEqual(source.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
