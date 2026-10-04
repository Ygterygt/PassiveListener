import tempfile
import unittest
import wave
from pathlib import Path
from benchmarks.harness import fixtures, normalize, score, percentile, measure, summarize


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

    def test_delayed_probe_excluded_and_pre_onset_still_rejected(self):
        class Clock:
            now = 0
            def __call__(self):
                return self.now
        clock = Clock()
        def probe():
            clock.now += 5_000_000_000
            return 42
        class Adapter:
            def stream(self, path, emit):
                clock.now += 200_000_000
                emit('final', 'synthetic')
                clock.now += 800_000_000
        result, _ = measure(Adapter(), 'unused', .1, clock=clock,
                            cpu_clock=lambda: 0, resource_probe=probe)
        self.assertEqual(result['final_ms'], 100)
        self.assertEqual(result['wall_ms'], 1000)
        with self.assertRaisesRegex(ValueError, 'before annotated'):
            measure(Adapter(), 'unused', .3, clock=clock,
                    cpu_clock=lambda: 0, resource_probe=probe)

    def test_summary_missing_counts(self):
        missing = dict(partial_ms=None, final_ms=None, cpu_percent_one_core=2)
        success = dict(partial_ms=10, final_ms=20, cpu_percent_one_core=3)
        for rows, observed, rate in (([missing, success], 1, .5),
                                     ([missing, missing], 0, 0)):
            report = summarize(rows)
            self.assertEqual(report['n'], 2)
            for metric in ('partial_ms', 'final_ms'):
                self.assertEqual(report[metric + '_observed'], observed)
                self.assertEqual(report[metric + '_missing'], 2 - observed)
                self.assertEqual(report[metric + '_completion_rate'], rate)
            self.assertEqual(report['final_ms_p95'], 20 if observed else None)
        self.assertIsNone(summarize([])['final_ms_completion_rate'])


if __name__ == '__main__':
    unittest.main()
