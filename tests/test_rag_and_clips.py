"""Pruebas de la lógica pura del RAG y de los cortes de video.

No construyen índices ni llaman a Ollama, ChromaDB, LLMs, yt-dlp ni ffmpeg:
solo ejercitan el troceo, las menciones literales y la alineación de citas.
"""

from __future__ import annotations

import argparse
import unittest

from scripts import make_clips as clips
from scripts import rag_transcripts as rag


def make_record(segments: list[dict]) -> dict:
    return {
        "video_id": "vid123",
        "title": "Conferencia de prensa",
        "url": "https://www.youtube.com/watch?v=vid123",
        "upload_date": "20260527",
        "duration": 600,
        "was_live": True,
        "transcript": {"source": "youtube_manual", "segments": segments, "text": ""},
    }


class ChunkingTests(unittest.TestCase):
    def test_segments_are_grouped_with_overlap_and_timestamps(self) -> None:
        segments = [{"start": float(i * 10), "end": float(i * 10 + 9), "text": "x" * 40} for i in range(10)]
        chunks = rag.chunks_from_record(make_record(segments), chunk_chars=100, overlap_chars=40)
        self.assertGreater(len(chunks), 1)
        first, second = chunks[0], chunks[1]
        self.assertEqual(first.chunk_id, "vid123:0")
        self.assertEqual(first.metadata["start"], 0.0)
        self.assertEqual(first.metadata["timestamp"], "00:00:00")
        self.assertEqual(first.metadata["timestamp_url"], "https://www.youtube.com/watch?v=vid123&t=0")
        # El último segmento del chunk anterior se repite al inicio del siguiente.
        self.assertLess(second.metadata["start"], first.metadata["end"])
        self.assertEqual(first.metadata["transcript_source"], "youtube_manual")

    def test_record_without_video_id_produces_nothing(self) -> None:
        record = make_record([{"start": 0.0, "end": 1.0, "text": "hola"}])
        record["video_id"] = ""
        self.assertEqual(rag.chunks_from_record(record, 100, 10), [])

    def test_plain_text_fallback_has_no_timestamp(self) -> None:
        record = make_record([])
        record["transcript"]["text"] = "Primera oración. " * 30
        chunks = rag.chunks_from_record(record, chunk_chars=120, overlap_chars=20)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].metadata["start"], -1)
        self.assertEqual(chunks[0].metadata["timestamp"], "")

    def test_split_text_prefers_sentence_boundary(self) -> None:
        text = "a" * 70 + ". " + "b" * 60
        parts = rag.split_text(text, chunk_chars=100, overlap_chars=10)
        self.assertTrue(parts[0].endswith("."))


class HelperTests(unittest.TestCase):
    def test_normalize_ollama_host(self) -> None:
        self.assertEqual(rag.normalize_ollama_host("0.0.0.0:11434"), "http://localhost:11434")
        self.assertEqual(rag.normalize_ollama_host("https://ollama.example/"), "https://ollama.example")
        self.assertEqual(rag.normalize_ollama_host(""), "http://localhost:11434")

    def test_metadata_url(self) -> None:
        self.assertEqual(rag.metadata_url("https://youtu.be/x", 12.9), "https://youtu.be/x?t=12")
        self.assertEqual(rag.metadata_url("https://y.com/watch?v=x", 5), "https://y.com/watch?v=x&t=5")
        self.assertEqual(rag.metadata_url("https://youtu.be/x", -1), "https://youtu.be/x")
        self.assertEqual(rag.metadata_url("", 5), "")

    def test_format_timestamp(self) -> None:
        self.assertEqual(rag.format_timestamp(3723.9), "01:02:03")
        self.assertEqual(rag.format_timestamp(-1), "")
        self.assertEqual(rag.format_timestamp(None), "")


class MentionTests(unittest.TestCase):
    def test_whole_word_pattern(self) -> None:
        pattern = rag.mention_pattern("ellos", case_sensitive=False, whole_word=True)
        self.assertIsNotNone(pattern.search("Ellos dijeron"))
        self.assertIsNone(pattern.search("aquellos sellos"))

    def test_substring_pattern_escapes_regex(self) -> None:
        pattern = rag.mention_pattern("100 días (balance)", case_sensitive=False, whole_word=False)
        self.assertIsNotNone(pattern.search("el discurso de 100 días (balance) de gobierno"))

    def test_context_marks_term(self) -> None:
        text = "uno dos tres cuatro cinco"
        start = text.index("tres")
        self.assertEqual(rag.mention_context(text, start, start + 4, 4), "...dos **tres** cua...")

    def test_dedupe_key_ignores_case_and_punctuation(self) -> None:
        meta = {"video_id": "v", "start": 10}
        self.assertEqual(
            rag.mention_dedupe_key(meta, "Seguridad", "La seguridad, primero."),
            rag.mention_dedupe_key(meta, "seguridad", "la SEGURIDAD primero"),
        )


class ClipAlignmentTests(unittest.TestCase):
    segments = [
        {"start": 0.0, "end": 4.0, "text": "Buenas tardes a todos"},
        {"start": 4.0, "end": 8.0, "text": "hoy vamos a hablar"},
        {"start": 8.0, "end": 12.0, "text": "del fiscal general y su gestión"},
        {"start": 12.0, "end": 16.0, "text": "que ha sido muy cuestionada"},
        {"start": 16.0, "end": 20.0, "text": "pasemos a otro tema"},
    ]

    def test_align_quote_finds_exact_window(self) -> None:
        result = clips.align_quote(self.segments, "del fiscal general y su gestión que ha sido muy cuestionada")
        self.assertIsNotNone(result)
        start, end, score = result
        self.assertEqual((start, end), (8.0, 16.0))
        self.assertGreaterEqual(score, 0.99)

    def test_align_quote_rejects_unrelated_text(self) -> None:
        self.assertIsNone(clips.align_quote(self.segments, "el precio del café en el mercado internacional"))

    def test_clip_bounds_respects_margin_min_and_max(self) -> None:
        args = argparse.Namespace(margin=3.0, min_seconds=10.0, max_seconds=30.0)
        self.assertEqual(clips.clip_bounds(1.0, 2.0, args), (0.0, 10.0))
        self.assertEqual(clips.clip_bounds(100.0, 200.0, args), (97.0, 127.0))

    def test_parse_quotes_and_cited_indexes(self) -> None:
        answer = (
            "La presidenta habló del tema [S2] y luego [S1] [S2].\n"
            "CITAS:\n"
            '[S2] "del fiscal general y su gestión que ha sido muy cuestionada"\n'
            "[S1] corta\n"
        )
        body, quotes = clips.parse_quotes(answer)
        self.assertNotIn("CITAS", body)
        self.assertEqual(quotes, [(2, "del fiscal general y su gestión que ha sido muy cuestionada")])
        self.assertEqual(clips.cited_indexes(answer), [2, 1])

    def test_overlaps_detects_duplicate_cut(self) -> None:
        existing = [{"video_id": "v", "clip_start": 10.0, "clip_end": 40.0}]
        self.assertTrue(clips.overlaps(existing, "v", (15.0, 45.0)))
        self.assertFalse(clips.overlaps(existing, "v", (38.0, 68.0)))
        self.assertFalse(clips.overlaps(existing, "otro", (15.0, 45.0)))

    def test_slug(self) -> None:
        self.assertEqual(clips.slug("¿Qué dijo sobre el Fiscal?"), "que_dijo_sobre_el_fiscal")
        self.assertEqual(clips.slug("¿?"), "clips")


if __name__ == "__main__":
    unittest.main()
