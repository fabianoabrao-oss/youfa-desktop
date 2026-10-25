import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from app import AudioApp


class PauseTests(unittest.TestCase):
    def test_real_recording_keeps_first_song_and_long_song_whole(self):
        case = json.loads((Path(__file__).parent / 'tests/data/classic-rock-pauses.json').read_text())
        cuts = AudioApp._pause_boundaries(case['log'], case['duration'])
        self.assertAlmostEqual(cuts[0], 228.7548645)
        self.assertAlmostEqual(cuts[1], 751.135708)
        self.assertGreater(cuts[1] - cuts[0], 390)
        self.assertEqual(cuts, sorted(set(cuts)))

    def test_continuous_audio_never_gets_forced_cuts(self):
        self.assertEqual(AudioApp._pause_boundaries('', 3600), [])

    def test_short_pauses_and_edge_silence_are_not_cuts(self):
        log = 'silence_start: 0\nsilence_end: 2\nsilence_start: 200\nsilence_end: 200.5\nsilence_start: 999\nsilence_end: 1000'
        self.assertEqual(AudioApp._pause_boundaries(log, 1000), [])

    def test_strongest_nearby_pause_wins(self):
        log = 'silence_start: 100\nsilence_end: 101\nsilence_start: 120\nsilence_end: 125'
        self.assertEqual(AudioApp._pause_boundaries(log, 500), [122.5])

    def test_all_audio_including_fractional_tail_is_kept(self):
        app = AudioApp.__new__(AudioApp)
        app.cancel_event = threading.Event()
        app._emit = lambda *_: None
        app._duration = lambda *_: 333.75
        log = 'silence_start: 200\nsilence_end: 202'
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'mix.mp3'
            source.write_bytes(b'original')
            calls = []
            def cut(args, **kwargs):
                calls.append(args)
                Path(args[-1]).write_bytes(b'track')
            with patch('app.subprocess.Popen') as process, patch('app.subprocess.run', side_effect=cut):
                process.return_value.communicate.return_value = (None, log)
                process.return_value.returncode = 0
                tracks = app._split_acoustically(source, Path('ffmpeg'))
            self.assertEqual(len(tracks), 2)
            spans = [(float(a[a.index('-ss')+1]), float(a[a.index('-t')+1])) for a in calls]
            self.assertEqual(spans, [(0, 201), (201, 132.75)])
            self.assertEqual(source.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
