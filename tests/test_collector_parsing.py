"""Pruebas de la lógica pura del recolector (sin yt-dlp, sin red, sin Whisper)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts import collect_youtube_transcripts as collector

VTT = """WEBVTT
Kind: captions
Language: es

1
00:00:01.000 --> 00:00:03.500 align:start position:0%
Buenos <c>días</c> a todos

2
00:00:03.500 --> 00:00:05.000
Buenos días a todos

3
01:02:03.250 --> 01:02:04.000
y y y seguimos &amp; avanzamos
"""


class VttParsingTests(unittest.TestCase):
    def test_parse_vtt_time_accepts_hours_and_comma(self) -> None:
        self.assertEqual(collector.parse_vtt_time("00:01.500"), 1.5)
        self.assertEqual(collector.parse_vtt_time("01:02:03,250"), 3723.25)
        self.assertIsNone(collector.parse_vtt_time("basura"))

    def test_parse_vtt_cleans_tags_and_drops_consecutive_duplicates(self) -> None:
        segments = collector.parse_vtt(VTT)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0], {"start": 1.0, "end": 3.5, "text": "Buenos días a todos"})
        self.assertEqual(segments[1]["start"], 3723.25)
        self.assertEqual(segments[1]["text"], "y seguimos & avanzamos")

    def test_clean_caption_text_collapses_hallucinated_repetitions(self) -> None:
        self.assertEqual(collector.clean_caption_text("gracias gracias gracias"), "gracias")
        self.assertEqual(collector.clean_caption_text("y, y, y entonces"), "y entonces")
        # Dos repeticiones no se consideran alucinación.
        self.assertEqual(collector.clean_caption_text("no no"), "no no")


class SubtitleChoiceTests(unittest.TestCase):
    def test_manual_spanish_wins_over_automatic(self) -> None:
        metadata = {
            "subtitles": {"es-419": [{}], "live_chat": [{}]},
            "automatic_captions": {"es": [{}]},
        }
        self.assertEqual(collector.choose_subtitle(metadata), ("es-419", "manual", False))

    def test_regional_manual_variant_is_accepted(self) -> None:
        metadata = {"subtitles": {"es-CR": [{}]}, "automatic_captions": {"es": [{}]}}
        self.assertEqual(collector.choose_subtitle(metadata), ("es-CR", "manual", False))

    def test_falls_back_to_automatic_and_ignores_live_chat(self) -> None:
        metadata = {"subtitles": {"live_chat": [{}]}, "automatic_captions": {"es": [{}], "en": [{}]}}
        self.assertEqual(collector.choose_subtitle(metadata), ("es", "automatic", True))

    def test_no_spanish_returns_none(self) -> None:
        metadata = {"subtitles": {"en": [{}]}, "automatic_captions": {"fr": [{}]}}
        self.assertIsNone(collector.choose_subtitle(metadata))


class DateAndLiveTests(unittest.TestCase):
    def test_date_in_range(self) -> None:
        start, end = date(2026, 5, 1), date(2026, 5, 31)
        self.assertTrue(collector.date_in_range(date(2026, 5, 15), start, end))
        self.assertFalse(collector.date_in_range(date(2026, 4, 30), start, end))
        self.assertFalse(collector.date_in_range(date(2026, 6, 1), start, end))
        # Sin fecha conocida, el video pasa el filtro.
        self.assertTrue(collector.date_in_range(None, start, end))

    def test_ytdlp_date_format(self) -> None:
        self.assertEqual(collector.ytdlp_date("2026-05-01"), "20260501")
        self.assertIsNone(collector.ytdlp_date(None))

    def test_live_and_upcoming_are_deferred(self) -> None:
        self.assertTrue(collector.should_skip_live({"live_status": "is_live"}))
        self.assertTrue(collector.should_skip_live({"live_status": "is_upcoming"}))
        self.assertFalse(collector.should_skip_live({"live_status": "was_live"}))


class RebuildJsonlTests(unittest.TestCase):
    def test_rebuild_is_idempotent_and_sorted_by_date_desc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = Path(tmp) / "records"
            output = Path(tmp) / "out.jsonl"
            for video_id, upload in (("a", "20260501"), ("b", "20260510"), ("c", "20260505")):
                collector.write_record({"video_id": video_id, "upload_date": upload}, records)
            self.assertEqual(collector.rebuild_jsonl(records, output), 3)
            self.assertEqual(collector.rebuild_jsonl(records, output), 3)
            lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["video_id"] for r in lines], ["b", "c", "a"])


if __name__ == "__main__":
    unittest.main()
