from __future__ import annotations

import unittest

from scripts import aggressive_language as aggressive


def record(
    texts: list[str], *, date: str = "20260527", step_seconds: float = 10
) -> aggressive.TranscriptRecord:
    segments = [
        {
            "start": float(index * step_seconds),
            "end": float(index * step_seconds + 9),
            "text": text,
        }
        for index, text in enumerate(texts)
    ]
    return aggressive.TranscriptRecord(
        video_id="test-video",
        title="Conferencia de prensa semanal de la Presidente de la República",
        url="https://www.youtube.com/watch?v=test-video",
        upload_date=date,
        transcript_source="test",
        text=" ".join(texts),
        segments=segments,
    )


class AggressiveLanguageTests(unittest.TestCase):
    def test_definition_covers_derived_taxonomy(self) -> None:
        sample = record(
            [
                "Esos señores son ratas.",
                "Esa comisión es un cáncer y está absolutamente atrofiada.",
                "Salieron de acusetas; eso es un berrinche.",
                "Son ambientalistas mentirosos y medios canallas.",
                "Se comportaron como un montón de maleantes.",
                "Son proyectos absurdos, de mentes absurdas y ridículas.",
                "Eso es una pura argolla del Poder Judicial, gente bien enchufada.",
                "Son enemigos del pueblo.",
            ]
        )
        events = aggressive.scan_record(sample)
        categories = {category for event in events for category in event.categories}
        self.assertTrue(
            {
                "animalizacion",
                "suciedad_enfermedad",
                "ridiculizacion_infantilizacion",
                "deshonestidad_deslegitimacion",
                "criminalizacion_condena_moral",
                "incapacidad_irracionalidad",
                "parasitismo_privilegio",
                "enemigo_amenaza",
            }.issubset(categories)
        )

    def test_2026_examples_are_detected(self) -> None:
        examples = [
            "Esos medios son canallas.",
            "Por dicha se archivó ese mamarracho.",
            "Salieron de acusetas.",
            "Lo señalo a usted como una vergüenza nacional.",
            "Parece que viven en Narnia.",
            "Son ambientalistas de caviar.",
            "Son ambientalistas mentirosos.",
            "Ese comunismo que ojalá se desapareciera.",
            "Son los enemigos del pueblo.",
            "Es una estafa política de Liberación Nacional.",
            "Es un invento de ley que se sacaron los comunistas.",
            "Es un juego bajo politiquero.",
            "La misma piedra viene siendo Carlos Díaz.",
            "Es semejante estafa.",
            "Son un montón de maleantes.",
            "Esos vagabundos.",
            "Insisten en que les digamos prensa canalla.",
            "Son diputados irresponsables.",
            "Esos medios canallas.",
            "Eso es una chavacanería.",
            "Eso es un berrinche.",
            "Hay más de un irresponsable en la Asamblea.",
            "Ni pica leña ni presta hacha.",
            "Es una pura argolla del Poder Judicial, gente bien enchufada.",
            "Es alcahuetería y complicidad.",
            "Son chismes al estilo de las viejas de patio.",
            "Siguen chupando de esos manantiales de nutrición.",
            "Es el ridículo de la hablada y siguen chupando el dedito.",
            "Es un princeso, un princesito con corona.",
            "Creen que pueden meterles los dedos en la boca.",
            "Qué poca vergüenza.",
            "La aporrea el piso con un trapo viejo sin credibilidad.",
            "A todo chancho le llega su diciembre.",
            "En realidad esa fue una casa ordeñada.",
            "Qué yeguadas, Jorge.",
            "Es gente tan desgraciada.",
            "El IAFA no sirve para nada.",
        ]
        events = aggressive.scan_record(record(examples, step_seconds=120))
        normalized_phrases = " ".join(aggressive.normalize(event.phrase) for event in events)
        expected_markers = (
            "canallas",
            "mamarracho",
            "acusetas",
            "verguenza nacional",
            "viven en narnia",
            "ambientalistas de caviar",
            "enemigos del pueblo",
            "pura argolla",
            "viejas de patio",
            "princesito con corona",
            "yeguadas",
            "el iafa no sirve para nada",
        )
        for marker in expected_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, normalized_phrases)
        self.assertGreaterEqual(len(events), 34)

    def test_policy_criticism_and_literal_uses_are_not_aggression(self) -> None:
        sample = record(
            [
                "El proyecto necesita correcciones técnicas antes de aprobarse.",
                "El hospital atiende pacientes con cáncer.",
                "En diciembre se consumieron cinco chanchos durante la actividad.",
                "Detuvieron a los aliados de Pecho de Rata.",
                "La fe de ratas se publicó tarde en La Gaceta.",
                "Mi madre venció un cáncer gracias a la atención del hospital.",
                "Tratamos de hacer una gracia y nos jalamos un sapo.",
                "Aquí no hay ticos con corona.",
                "La prensa informó sobre la sesión legislativa.",
            ]
        )
        self.assertEqual(aggressive.scan_record(sample), [])

    def test_multiple_markers_in_one_clause_form_one_event(self) -> None:
        events = aggressive.scan_record(record(["Son ambientalistas mentirosos y ambientalistas de caviar."]))
        self.assertEqual(len(events), 1)
        self.assertEqual(
            set(events[0].categories),
            {"deshonestidad_deslegitimacion", "otro_desprecio"},
        )
        self.assertIn("ambientalistas", events[0].target)

    def test_source_timestamp_and_speaker_hint_are_preserved(self) -> None:
        sample = record(
            [
                "Le doy la palabra a don Rodrigo para que explique.",
                "Esos son princesitos con corona.",
            ]
        )
        event = aggressive.scan_record(sample)[0]
        self.assertEqual(event.speaker, "Rodrigo Chaves")
        self.assertEqual(event.timestamp, "00:00:10")
        self.assertEqual(event.url, "https://www.youtube.com/watch?v=test-video&t=10")


if __name__ == "__main__":
    unittest.main()
