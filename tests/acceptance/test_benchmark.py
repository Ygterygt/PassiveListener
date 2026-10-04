import tempfile
import unittest
import wave
from pathlib import Path
from benchmarks.harness import fixtures, normalize, score, percentile, measure


class BenchmarkTests(unittest.TestCase):
    def test_fixture_repeatability_and_format(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(fixtures(a), fixtures(b))
            with wave.open(str(Path(a) / 'silence.wav')) as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()), (1, 2, 16000, 32000))
                self.assertEqual(set(audio.readframes(32000)), {0})

    def test_turkish_case_and_unicode(self):
        self.assertEqual(normalize('IŞIK İSTANBUL'), 'ışık istanbul')
        self.assertEqual(score('ışık, İstanbul!', 'IŞIK İSTANBUL')['wer'], 0)
        self.assertEqual(score('bir iki üç', 'bir üç')['wer'], 1 / 3)
        self.assertIsNone(score('', 'kelime')['wer'])

    def test_percentiles_nearest_rank(self):
        self.assertEqual(percentile(list(range(1, 101)), .95), 95)
        self.assertIsNone(percentile([], .95))

    def test_no_output_is_missing_not_zero_latency(self):
        class Silent:
            def stream(self, path, emit):
                pass
        result, text = measure(Silent(), 'unused', None)
        self.assertIsNone(result['partial_ms'])
        self.assertIsNone(result['final_ms'])
        self.assertEqual(text, '')

    def test_reject_pre_onset_event(self):
        class Invalid:
            def stream(self, path, emit):
                emit('final', 'synthetic')
        with self.assertRaises(ValueError):
            measure(Invalid(), 'unused', 100)


if __name__ == '__main__':
    unittest.main()
