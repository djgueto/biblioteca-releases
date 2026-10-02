"""
Tests para core/services/quality.py: el esquema de calidad genérico
(resolución + codec + HDR) usado por el arbitraje multi-proveedor.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.services.quality import (
    Hdr,
    Quality,
    compare_quality,
    is_upgrade,
    parse_quality_text,
    quality_from_local_episode,
)


class TestQualityOrdering(unittest.TestCase):
    def test_resolution_beats_codec_and_hdr(self):
        higher_res = Quality(1080, 'x264', Hdr.UNKNOWN)
        lower_res_better_everything_else = Quality(720, 'x265', Hdr.DOLBY_VISION)
        self.assertEqual(compare_quality(higher_res, lower_res_better_everything_else), 1)

    def test_hdr_beats_codec_at_same_resolution(self):
        hdr_x264 = Quality(2160, 'x264', Hdr.HDR10)
        sdr_x265 = Quality(2160, 'x265', Hdr.UNKNOWN)
        self.assertEqual(compare_quality(hdr_x264, sdr_x265), 1)

    def test_codec_is_weakest_tiebreak(self):
        x265 = Quality(1080, 'x265', Hdr.UNKNOWN)
        x264 = Quality(1080, 'x264', Hdr.UNKNOWN)
        self.assertEqual(compare_quality(x265, x264), 1)

    def test_equal_quality_is_equal(self):
        a = Quality(1080, 'x265', Hdr.SDR)
        b = Quality(1080, 'x265', Hdr.SDR)
        self.assertEqual(compare_quality(a, b), 0)

    def test_unknown_resolution_ranks_lowest(self):
        unknown = Quality()
        known = Quality(480, None, Hdr.UNKNOWN)
        self.assertEqual(compare_quality(known, unknown), 1)

    def test_is_upgrade(self):
        current = Quality(1080, 'x264', Hdr.UNKNOWN)
        candidate_4k = Quality(2160, 'x264', Hdr.UNKNOWN)
        same = Quality(1080, 'x264', Hdr.UNKNOWN)
        self.assertTrue(is_upgrade(current, candidate_4k))
        self.assertFalse(is_upgrade(current, same))
        self.assertFalse(is_upgrade(candidate_4k, current))


class TestParseQualityText(unittest.TestCase):
    def test_2160p_x265_hdr(self):
        q = parse_quality_text("2160p x265 HDR10")
        self.assertEqual(q.resolution_height, 2160)
        self.assertEqual(q.codec, 'x265')
        self.assertEqual(q.hdr, Hdr.HDR10)

    def test_1080p_x264_plain(self):
        q = parse_quality_text("1080p x264")
        self.assertEqual(q.resolution_height, 1080)
        self.assertEqual(q.codec, 'x264')
        self.assertEqual(q.hdr, Hdr.UNKNOWN)

    def test_4k_keyword_without_p_suffix(self):
        q = parse_quality_text("Movie Title [4K UHD][Esp]")
        self.assertEqual(q.resolution_height, 2160)

    def test_dolby_vision_detected(self):
        q = parse_quality_text("2160p HEVC Dolby Vision")
        self.assertEqual(q.hdr, Hdr.DOLBY_VISION)
        self.assertEqual(q.codec, 'x265')

    def test_dv_token_detected(self):
        q = parse_quality_text("2160p x265 DV")
        self.assertEqual(q.hdr, Hdr.DOLBY_VISION)

    def test_empty_text(self):
        q = parse_quality_text("")
        self.assertIsNone(q.resolution_height)
        self.assertIsNone(q.codec)
        self.assertEqual(q.hdr, Hdr.UNKNOWN)

    def test_none_text(self):
        q = parse_quality_text(None)
        self.assertIsNone(q.resolution_height)

    def test_domain_containing_4k_does_not_false_positive_on_unrelated_text(self):
        # No debe confundir "480p" con "4k" por contener el dígito 4.
        q = parse_quality_text("576p x264")
        self.assertEqual(q.resolution_height, 480)


class TestQualityFromLocalEpisode(unittest.TestCase):
    def test_maps_fields_hdr_always_unknown(self):
        row = {'resolution_height': 1080, 'codec': 'x265'}
        q = quality_from_local_episode(row)
        self.assertEqual(q.resolution_height, 1080)
        self.assertEqual(q.codec, 'x265')
        self.assertEqual(q.hdr, Hdr.UNKNOWN)

    def test_handles_none_row(self):
        q = quality_from_local_episode(None)
        self.assertIsNone(q.resolution_height)
        self.assertIsNone(q.codec)
        self.assertEqual(q.hdr, Hdr.UNKNOWN)


if __name__ == '__main__':
    unittest.main()
