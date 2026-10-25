#!/usr/bin/env python3
"""Detección reproducible de frases agresivas en el discurso presidencial.

La definición operacional se deriva del inventario publicado por La Nación
para las conferencias de 2022-2026. Una *frase agresiva* es una cláusula que
caracteriza negativamente a una persona, grupo o institución identificable
mediante al menos uno de estos recursos:

* deshumanización o animalización;
* enfermedad, suciedad o deterioro como metáfora del adversario;
* ridiculización o infantilización;
* imputación de mentira, hipocresía o ilegitimidad;
* criminalización o condena moral;
* atribución de incapacidad o irracionalidad;
* parasitismo, privilegio o apropiación indebida;
* presentación como amenaza o enemigo del colectivo.

No basta una crítica de política pública ni la mención literal de una
enfermedad, animal o delito. La unidad de conteo es la cláusula: varias marcas
agresivas dentro de la misma cláusula producen un solo evento con varias
categorías. Esta decisión evita la ambigüedad del conteo por palabras.

El detector es deliberadamente conservador y no determina si la acusación es
verdadera. ``hablante`` y ``blanco`` son pistas heurísticas porque muchas
transcripciones no contienen diarización. Cada resultado conserva contexto y
URL con timestamp para revisión humana.

Uso::

    python scripts/aggressive_language.py --date-after 20260501
    python scripts/aggressive_language.py --video-id g4-Ymq8IAeo --output -
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Sequence

METHOD_VERSION: Final[str] = "frase-agresiva-ln-ejemplos-v1"
DEFAULT_TRANSCRIPTS: Final[Path] = Path("data/presidencia_transcripts.jsonl")
DEFAULT_OUTPUT: Final[Path] = Path("data/frases_agresivas.json")
DEFAULT_CHROMA_DIR: Final[Path] = Path("data/chroma")
DEFAULT_COLLECTION: Final[str] = "presidencia_transcripts"
DEFAULT_GRAPH: Final[Path] = Path("data/grafos/grafo_unificado.json")

DEFINITION: Final[str] = (
    "Cláusula que caracteriza negativamente a una persona, grupo o institución "
    "identificable mediante deshumanización, metáforas de enfermedad/suciedad, "
    "ridiculización o infantilización, imputación de deshonestidad, criminalización "
    "o condena moral, atribución de incapacidad, parasitismo/privilegio o su "
    "presentación como amenaza o enemigo del colectivo. No incluye por sí sola una "
    "crítica de política pública ni usos literales de animales, enfermedad o delito."
)

# Consultas prototipo para la segunda capa: descubren en Chroma pasajes
# semánticamente cercanos aunque no contengan las expresiones ya conocidas.
# Esos pasajes son candidatos de auditoría y nunca engrosan el conteo automático.
SEMANTIC_QUERIES: Final[dict[str, str]] = {
    "animalizacion": "adversarios o instituciones descritos como ratas, culebras, animales o plagas",
    "suciedad_enfermedad": "personas o instituciones descritas metafóricamente como cáncer, suciedad o deterioro",
    "ridiculizacion_infantilizacion": "adversarios ridiculizados, infantilizados o tratados con apodos humillantes",
    "deshonestidad_deslegitimacion": "grupos acusados de mentir, engañar, manipular o carecer de legitimidad",
    "criminalizacion_condena_moral": "personas o grupos llamados maleantes, canallas, vagabundos o moralmente indignos",
    "incapacidad_irracionalidad": "personas o instituciones calificadas de incapaces, absurdas o irracionales",
    "parasitismo_privilegio": "élites acusadas de parasitar, vivir de privilegios, argollas o apropiarse de recursos",
    "enemigo_amenaza": "adversarios presentados como enemigos del pueblo o amenaza para Costa Rica",
}


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: str
    confidence: float
    label: str


# Las expresiones fuertes pueden operar solas; las débiles están codificadas con
# contexto lingüístico (p. ej. "ese cáncer" y no la palabra médica "cáncer").
RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "animalizacion",
        r"\b(?:ratas|(?:son|esas?|estos?|aquellas?)\s+rata|culebras?|alacran(?:es)?|animales?\s+ponzonosos?)\b",
        0.96,
        "animalización",
    ),
    Rule("animalizacion", r"\b(?:yeguadas?|a\s+todo\s+chancho\s+les?\s+llega\s+su\s+diciembre)\b", 0.91, "animalización"),
    Rule("suciedad_enfermedad", r"\b(?:ese|este)\s+cancer\b", 0.88, "enfermedad/deterioro"),
    Rule("suciedad_enfermedad", r"\b(?:escoria|cuchitriles?|gente\s+tan\s+(?:baja|sucia)|suciedad\s+de\s+sus\s+almas?|mentes?\s+(?:morbosas?|perversas?))\b", 0.93, "suciedad/deterioro"),
    Rule("suciedad_enfermedad", r"\b(?:comision(?:es)?\s+)?(?:absolutamente\s+)?atrofiad[ao]s?\b", 0.86, "deterioro"),
    Rule("ridiculizacion_infantilizacion", r"\b(?:los\s+)?tres\s+chiflados\b", 0.98, "ridiculización"),
    Rule("ridiculizacion_infantilizacion", r"\b(?:mamarracho|mamaracho|bufon|charlatanes?|acusetas?|berrinches?|pataletas?|mentirosillos?)\b", 0.94, "ridiculización/infantilización"),
    Rule("ridiculizacion_infantilizacion", r"\b(?:viejas?\s+de\s+patio|viven?\s+en\s+narnia|chupando\s+(?:el\s+)?dedito)\b", 0.96, "ridiculización/infantilización"),
    Rule("ridiculizacion_infantilizacion", r"\b(?:princesos?|princesitos?)(?:\s+con\s+corona)?\b", 0.95, "ridiculización/infantilización"),
    Rule("ridiculizacion_infantilizacion", r"\b(?:comicirco|circomision|hizo\s+el\s+ridiculo|el\s+ridiculo\s+de\s+la\s+hablada)\b", 0.91, "ridiculización"),
    Rule("ridiculizacion_infantilizacion", r"\baporrea\s+el\s+piso\s+con\s+un\s+trapo\s+viejo\b", 0.95, "ridiculización"),
    Rule("ridiculizacion_infantilizacion", r"\bmeter(?:le|les)?\s+los\s+dedos\s+en\s+la\s+boca\b", 0.91, "ridiculización/infantilización"),
    Rule("ridiculizacion_infantilizacion", r"\b(?:jetones?|mata\s+hari\s+criolla|arbol\s+caido\s+politicamente)\b", 0.90, "ridiculización"),
    Rule("deshonestidad_deslegitimacion", r"\b(?:prensa|medios?)\s+canallas?\b", 0.97, "deslegitimación"),
    Rule("deshonestidad_deslegitimacion", r"\b(?:medio\s+pervertido|voceros?\s+a\s+sueldo|titiriteros?\s+y\s+mentirosos?)\b", 0.94, "deslegitimación"),
    Rule("deshonestidad_deslegitimacion", r"\b(?:ambientalistas?\s+mentirosos?|mentiras?\s+tan\s+patranas?|patrana\s+absurda|falsificaciones?)\b", 0.91, "deshonestidad"),
    Rule("deshonestidad_deslegitimacion", r"\b(?:hipocresia\s+que\s+les\s+caracteriza|hipocriton|demagogo\s+y\s+un\s+mentiroso)\b", 0.92, "deshonestidad"),
    Rule("deshonestidad_deslegitimacion", r"\b(?:s?tafa\s+politica|semejante\s+s?tafa|juego\s+bajo\s+politi(?:co|quero))\b", 0.89, "deslegitimación"),
    Rule("criminalizacion_condena_moral", r"\bun\s+monton\s+de\s+maleantes?\b", 0.95, "criminalización"),
    Rule("criminalizacion_condena_moral", r"\b(?:vagabundos?|canallas?|gente\s+tan\s+desgraciada)\b", 0.93, "condena moral"),
    Rule("criminalizacion_condena_moral", r"\b(?:esbirros?|serviles?|incompetentes?\s+arbitrarios?|sinverguenzadas?|no\s+tiene\s+perdon\s+de\s+dios)\b", 0.89, "condena moral"),
    Rule("criminalizacion_condena_moral", r"\b(?:bajeza\s+y\s+vulgaridad|que\s+poca\s+verguenza|la\s+que\s+no\s+tiene\s+verguenza)\b", 0.91, "condena moral"),
    Rule("criminalizacion_condena_moral", r"\b(?:asesinatos?\s+politicos?.{0,30}cometieron|escupir\s+el\s+pabellon\s+nacional|chanchada)\b", 0.90, "condena moral"),
    Rule("criminalizacion_condena_moral", r"\b(?:senor|usted|fiscal).{0,55}(?:una\s+)?verguenza\s+nacional\b", 0.90, "condena moral"),
    Rule("incapacidad_irracionalidad", r"\b(?:estulticias?|estupideces?|proyectos?\s+absurdos?|mentes?\s+absurdas?\s+y\s+ridiculas?)\b", 0.90, "incapacidad/irracionalidad"),
    Rule("incapacidad_irracionalidad", r"\b(?:las\s+neuronas?.{0,35}no\s+funcionando|sombreros?\s+muy\s+grandes?\s+y\s+cabezas?\s+chiquitas?)\b", 0.94, "incapacidad/irracionalidad"),
    Rule("incapacidad_irracionalidad", r"\b(?:el\s+iafa|esa\s+institucion|esa\s+comision)\s+no\s+sirve\s+para\s+nada\b", 0.91, "incapacidad"),
    Rule("incapacidad_irracionalidad", r"\b(?:diputados?\s+(?:y\s+)?irresponsables?|mas\s+de\s+un\s+irresponsable\s+en\s+la\s+asamblea)\b", 0.86, "incapacidad/irresponsabilidad"),
    Rule("parasitismo_privilegio", r"\b(?:senores?\s+feudales?\s+autocoronados?|ticos?\s+con\s+corona)\b", 0.97, "privilegio"),
    Rule("parasitismo_privilegio", r"\b(?:la\s+ordenan?\s+hasta\s+la\s+ultima\s+gota|(?:ya\s+no\s+)?chupan\s+tanto|chupan(?:do)?\s+(?:de\s+)?(?:esos\s+)?manantiales?\s+de\s+nutricion)\b", 0.95, "parasitismo"),
    Rule("parasitismo_privilegio", r"\b(?:pura\s+argolla|gente.{0,20}bien\s+enchufada|en\s+realidad.{0,30}casa\s+ordenada)\b", 0.89, "privilegio/apropiación"),
    Rule("parasitismo_privilegio", r"\b(?:capo\s+del\s+ecosistema\s+del\s+cambalache|club\s+de\s+amigos\s+de\s+tricolin)\b", 0.94, "privilegio/apropiación"),
    Rule("enemigo_amenaza", r"\benemigos?\s+del\s+pueblo\b", 0.98, "construcción del enemigo"),
    Rule("enemigo_amenaza", r"\b(?:tiranias?\s+de\s+la\s+institucionalidad|tiranias?\s+perfectas?|mafia)\b", 0.88, "amenaza/ilegitimidad"),
    Rule("enemigo_amenaza", r"\b(?:comunismo\s+que\s+ojala\s+se\s+desapareciera|invento\s+de\s+ley.{0,35}comunistas?|comunistas?.{0,25}por\s+definicion.{0,35}(?:hipocritas|mentirosos))\b", 0.91, "enemigo ideológico"),
    Rule("otro_desprecio", r"\b(?:ambientalistas?\s+de\s+caviar|chava\s*caneria|alca[gh]ueteria\s+y\s+complicidad)\b", 0.90, "desprecio/descalificación"),
    Rule("otro_desprecio", r"\b(?:ni\s+pica\s+lena\s+ni\s+presta\s+(?:el\s+)?hacha|la\s+misma\s+piedra\s+viene\s+siendo)\b", 0.87, "descalificación"),
)

COMPILED_RULES: Final[tuple[tuple[Rule, re.Pattern[str]], ...]] = tuple(
    (rule, re.compile(rule.pattern, re.IGNORECASE)) for rule in RULES
)

TARGET_TERMS: Final[tuple[tuple[str, str], ...]] = (
    (r"\bpoder\s+judicial\b", "Poder Judicial"),
    (r"\bcorte\s+(?:suprema|plena)\b", "Corte Suprema de Justicia"),
    (r"\bmagistrad[oa]s?\b", "magistratura"),
    (r"\bfiscal(?:ia|\s+general)?\b", "Fiscalía"),
    (r"\boij\b", "OIJ"),
    (r"\bprensa\b|\bmedios?\s+de\s+comunicacion\b", "prensa/medios"),
    (r"\bperiodistas?\b", "periodistas"),
    (r"\bdiputad[oa]s?\b|\basamblea\s+legislativa\b", "Asamblea/diputaciones"),
    (r"\bfrente\s+amplio\b", "Frente Amplio"),
    (r"\bliberacion\s+nacional\b|\bpln\b", "Liberación Nacional"),
    (r"\bsindicatos?\b|\bgremios?\b", "sindicatos/gremios"),
    (r"\bambientalistas?\b", "ambientalistas"),
    (r"\bmanifestantes?\b", "manifestantes"),
    (r"\bopositores?|\boposicion\b", "oposición"),
    (r"\bfuncionarios?\b|\bjerarcas?\b", "funcionarios/jerarcas"),
    (r"\bla\s+nacion\b", "La Nación"),
    (r"\bcanal\s+7\b", "Canal 7"),
    (r"\bcr\s*hoy\b", "CRHoy"),
)

HANDOFF_TO_CHAVES = re.compile(
    r"(?:le\s+(?:doy|damos)\s+la\s+palabra|por\s+favor|comente|explique).{0,80}(?:don\s+)?rodrigo|"
    r"(?:don\s+)?rodrigo.{0,60}(?:tiene\s+la\s+palabra|nos\s+comente)",
    re.IGNORECASE,
)
HANDOFF_TO_PRIMARY = re.compile(
    r"gracias(?:\s+a\s+usted)?[, ]+(?:don\s+)?rodrigo|continuemos|continuamos",
    re.IGNORECASE,
)

RODRIGO_2026_SIGNATURE = re.compile(
    r"(?:chupando.{0,45}(?:manantiales|dedito)|ridiculo\s+de\s+la\s+hablada|"
    r"princes|aporrea\s+el\s+piso|casa\s+ordenada|yeguad)",
    re.IGNORECASE,
)
LAURA_2026_SIGNATURE = re.compile(
    r"(?:canallas?|mamarracho|mamaracho|acusetas?|verguenza\s+nacional|narnia|"
    r"ambientalistas?|enemigos?\s+del\s+pueblo|vagabundos?|maleantes?|chava\s*caneria|"
    r"berrinches?|irresponsable|pura\s+argolla|enchufada|alca[gh]ueteria|viejas?\s+de\s+patio|"
    r"meter.{0,25}dedos|poca\s+verguenza|chancho|iafa)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TranscriptRecord:
    video_id: str
    title: str
    url: str
    upload_date: str
    transcript_source: str
    text: str
    segments: list[dict[str, Any]]


@dataclass(frozen=True)
class AggressivePhrase:
    video_id: str
    video: str
    date: str
    start: float
    end: float
    timestamp: str
    url: str
    phrase: str
    context: str
    categories: tuple[str, ...]
    labels: tuple[str, ...]
    matched_markers: tuple[str, ...]
    confidence: float
    speaker: str
    speaker_confidence: float
    target: str
    target_confidence: float
    us_them_frame: bool
    method_version: str = METHOD_VERSION


def normalize(value: str) -> str:
    """Normaliza sin cambiar la longitud, para conservar offsets."""
    trans = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    lowered = str(value).lower().translate(trans)
    return re.sub(r"[^a-z0-9]", " ", lowered)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def timestamp(value: float | int) -> str:
    seconds = max(0, int(value))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def timestamp_url(url: str, start: float | int) -> str:
    if not url:
        return ""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}t={max(0, int(start))}"


def _record_value(record: Any, name: str, default: Any = "") -> Any:
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def record_from_json(item: dict[str, Any]) -> TranscriptRecord:
    transcript = item.get("transcript") or {}
    return TranscriptRecord(
        video_id=str(item.get("video_id") or ""),
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        upload_date=str(item.get("upload_date") or ""),
        transcript_source=str(transcript.get("source") or ""),
        text=clean_text(transcript.get("text") or ""),
        segments=list(transcript.get("segments") or []),
    )


def load_records(path: Path) -> list[TranscriptRecord]:
    records: list[TranscriptRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = record_from_json(json.loads(line))
                if record.text:
                    records.append(record)
    return records


def _primary_speaker(record: Any) -> tuple[str, float]:
    date = str(_record_value(record, "upload_date", ""))
    title = normalize(str(_record_value(record, "title", "")))
    if date[:4] in {"2022", "2023", "2024", "2025"}:
        return "Rodrigo Chaves", 0.88
    if "rodrigo chaves" in title and "laura fernandez" not in title:
        return "Rodrigo Chaves", 0.82
    if date.startswith("2026") or "laura fernandez" in title or "presidente de la republica" in title:
        return "Laura Fernández", 0.78
    return "Hablante no determinado", 0.25


def _stream(record: Any) -> tuple[str, list[tuple[int, int, float, float, str, float]]]:
    """Concatena segmentos y devuelve mapa char→tiempo/hablante estimado."""
    segments = list(_record_value(record, "segments", []) or [])
    primary, primary_conf = _primary_speaker(record)
    if not segments:
        text = clean_text(_record_value(record, "text", ""))
        return text, [(0, len(text), 0.0, 0.0, primary, primary_conf)]

    parts: list[str] = []
    spans: list[tuple[int, int, float, float, str, float]] = []
    cursor = 0
    current_speaker, speaker_conf = primary, primary_conf
    for segment in segments:
        part = clean_text(segment.get("text") or "")
        if not part:
            continue
        normalized_part = normalize(part)
        if HANDOFF_TO_CHAVES.search(normalized_part):
            next_speaker = "Rodrigo Chaves"
        else:
            next_speaker = None
        if current_speaker == "Rodrigo Chaves" and HANDOFF_TO_PRIMARY.search(normalized_part):
            next_speaker = primary

        if parts:
            parts.append(" ")
            cursor += 1
        begin = cursor
        parts.append(part)
        cursor += len(part)
        spans.append(
            (
                begin,
                cursor,
                float(segment.get("start") or 0),
                float(segment.get("end") or segment.get("start") or 0),
                current_speaker,
                speaker_conf,
            )
        )
        if next_speaker:
            current_speaker = next_speaker
            speaker_conf = 0.72 if next_speaker == "Rodrigo Chaves" else primary_conf
    return "".join(parts), spans


def _span_for(spans: Sequence[tuple[int, int, float, float, str, float]], position: int) -> tuple[int, int, float, float, str, float]:
    for span in spans:
        if span[0] <= position < span[1]:
            return span
    return spans[-1]


def _clause_bounds(text: str, start: int, end: int, max_chars: int = 230) -> tuple[int, int]:
    separators = ".!?;:\n"
    floor = max(0, start - max_chars)
    left_candidates = [text.rfind(char, floor, start) for char in separators]
    left_valid = [candidate for candidate in left_candidates if candidate >= floor]
    left = max(left_valid) + 1 if left_valid else floor
    if left == floor and left > 0:
        next_space = text.find(" ", left, start)
        if next_space >= 0:
            left = next_space + 1
    right_candidates = [text.find(char, end, min(len(text), end + max_chars)) for char in separators]
    right_valid = [candidate for candidate in right_candidates if candidate >= 0]
    right = min(right_valid) + 1 if right_valid else min(len(text), end + max_chars)
    if not right_valid and right < len(text):
        previous_space = text.rfind(" ", end, right)
        if previous_space > end:
            right = previous_space
    return left, right


def _context(text: str, start: int, end: int, radius: int = 260) -> str:
    left, right = max(0, start - radius), min(len(text), end + radius)
    return clean_text(("…" if left else "") + text[left:right] + ("…" if right < len(text) else ""))


def _targets(phrase: str, context: str) -> tuple[str, float]:
    found: list[str] = []
    for source in (phrase, context):
        normalized = normalize(source)
        for pattern, label in TARGET_TERMS:
            if re.search(pattern, normalized) and label not in found:
                found.append(label)

    # Los tratamientos don/doña suelen identificar personas con bastante precisión.
    for source in (phrase, context):
        for match in re.finditer(r"\b(?:don|dona|doña)\s+([A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ]+){0,2})", source):
            name = clean_text(match.group(1))
            if name.casefold() not in {"laura", "rodrigo"} and name not in found:
                found.append(name)
    if not found:
        return "Blanco no determinado", 0.20
    confidence = 0.72 if len(found) == 1 else 0.58
    return "; ".join(found[:4]), confidence


def _us_them(context: str, target: str) -> bool:
    normalized = normalize(context)
    collective_self = bool(re.search(r"\b(?:nosotros|nuestro|gobierno|pueblo|costarricenses)\b", normalized))
    collective_other = target != "Blanco no determinado" or bool(
        re.search(r"\b(?:ellos|enemigos|oposicion|opositores|prensa|medios|sindicatos|diputados)\b", normalized)
    )
    return collective_self and collective_other


def _event_speaker(
    record: Any,
    span: tuple[int, int, float, float, str, float],
    markers: Sequence[str],
) -> tuple[str, float]:
    """Combina la diarización heurística con firmas verificadas del corpus 2026."""
    date = str(_record_value(record, "upload_date", ""))
    signature = normalize(" ".join(markers))
    if date.startswith("2026") and RODRIGO_2026_SIGNATURE.search(signature):
        return "Rodrigo Chaves", 0.94
    if date.startswith("2026") and LAURA_2026_SIGNATURE.search(signature):
        return "Laura Fernández", 0.90
    return span[4], span[5]


def _marker_signature(markers: Sequence[str]) -> frozenset[str]:
    tokens = re.findall(r"[a-z0-9]+", normalize(" ".join(markers)))
    return frozenset(token[:6] for token in tokens if len(token) >= 5)


def scan_record(record: Any, min_confidence: float = 0.82) -> list[AggressivePhrase]:
    """Detecta frases agresivas en un registro y devuelve eventos deduplicados."""
    text, spans = _stream(record)
    if not text or not spans:
        return []
    normalized = normalize(text)
    matches: list[tuple[int, int, Rule, str]] = []
    for rule, pattern in COMPILED_RULES:
        if rule.confidence < min_confidence:
            continue
        for match in pattern.finditer(normalized):
            prefix = normalized[max(0, match.start() - 24) : match.start()]
            if re.search(r"\b(?:no|ni)(?:\s+(?:hay|son|es))?\s+$", prefix):
                continue
            # Error frecuente de ASR: «fe de erratas» → «fe de ratas».
            if match.group(0).strip() == "ratas" and re.search(r"\bfe\s+de\s+$", prefix):
                continue
            matches.append((match.start(), match.end(), rule, clean_text(text[match.start() : match.end()])))
    matches.sort(key=lambda item: (item[0], item[1]))

    # Agrupa marcas de la misma cláusula. Dos expresiones separadas por más de
    # 90 caracteres se consideran unidades distintas aun sin puntuación ASR.
    groups: list[list[tuple[int, int, Rule, str]]] = []
    for match in matches:
        if groups and match[0] - groups[-1][-1][1] <= 90:
            previous_bounds = _clause_bounds(text, groups[-1][0][0], groups[-1][-1][1])
            current_bounds = _clause_bounds(text, match[0], match[1])
            if previous_bounds == current_bounds:
                groups[-1].append(match)
                continue
        groups.append([match])

    events: list[AggressivePhrase] = []
    seen: set[tuple[int, str]] = set()
    recent_signatures: list[tuple[float, frozenset[str], frozenset[str]]] = []
    for group in groups:
        start, end = group[0][0], group[-1][1]
        clause_start, clause_end = _clause_bounds(text, start, end)
        phrase = clean_text(text[clause_start:clause_end])
        if not phrase:
            continue
        span = _span_for(spans, start)
        dedupe_key = (int(span[2] // 8), normalize(phrase).strip())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        categories = tuple(dict.fromkeys(match[2].category for match in group))
        labels = tuple(dict.fromkeys(match[2].label for match in group))
        markers = tuple(dict.fromkeys(match[3] for match in group))
        category_signature = frozenset(categories)
        marker_signature = _marker_signature(markers)
        if any(
            abs(span[2] - previous_start) <= 75
            and category_signature == previous_categories
            and marker_signature
            and marker_signature.intersection(previous_markers)
            for previous_start, previous_categories, previous_markers in recent_signatures
        ):
            continue
        recent_signatures.append((span[2], category_signature, marker_signature))
        confidence = min(0.99, max(match[2].confidence for match in group) + 0.02 * (len(categories) - 1))
        context = _context(text, clause_start, clause_end)
        target, target_confidence = _targets(phrase, context)
        speaker, speaker_confidence = _event_speaker(record, span, markers)
        video_url = str(_record_value(record, "url", ""))
        events.append(
            AggressivePhrase(
                video_id=str(_record_value(record, "video_id", "")),
                video=str(_record_value(record, "title", "")),
                date=str(_record_value(record, "upload_date", "")),
                start=span[2],
                end=max(span[3], span[2]),
                timestamp=timestamp(span[2]),
                url=timestamp_url(video_url, span[2]),
                phrase=phrase,
                context=context,
                categories=categories,
                labels=labels,
                matched_markers=markers,
                confidence=round(confidence, 3),
                speaker=speaker,
                speaker_confidence=round(speaker_confidence, 3),
                target=target,
                target_confidence=round(target_confidence, 3),
                us_them_frame=_us_them(context, target),
            )
        )
    return events


def scan_records(records: Iterable[Any], min_confidence: float = 0.82) -> list[AggressivePhrase]:
    events: list[AggressivePhrase] = []
    for record in records:
        events.extend(scan_record(record, min_confidence=min_confidence))
    return sorted(events, key=lambda event: (event.date, event.video_id, event.start))


def summarize(records: Sequence[Any], events: Sequence[AggressivePhrase]) -> dict[str, Any]:
    by_category = Counter(category for event in events for category in event.categories)
    by_year = Counter(event.date[:4] or "sin_fecha" for event in events)
    by_speaker = Counter(event.speaker for event in events)
    by_video = Counter(event.video_id for event in events)
    token_count = sum(len(re.findall(r"\b\w+\b", str(_record_value(record, "text", "")))) for record in records)
    return {
        "records": len(records),
        "events": len(events),
        "events_per_record": round(len(events) / len(records), 3) if records else 0.0,
        "events_per_1000_words": round(1000 * len(events) / token_count, 3) if token_count else 0.0,
        "us_them_events": sum(event.us_them_frame for event in events),
        "by_category": dict(by_category.most_common()),
        "by_year": dict(sorted(by_year.items())),
        "by_speaker": dict(by_speaker.most_common()),
        "by_video": dict(by_video.most_common()),
    }


def build_report(records: Sequence[Any], events: Sequence[AggressivePhrase]) -> dict[str, Any]:
    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "method_version": METHOD_VERSION,
            "definition": DEFINITION,
            "unit": "cláusula",
            "caveats": [
                "El detector identifica lenguaje y no determina la veracidad de una acusación.",
                "Hablante y blanco son inferencias cuando la fuente carece de diarización.",
                "Los resultados deben revisarse en el video enlazado antes de publicarse.",
            ],
        },
        "summary": summarize(records, events),
        "events": [asdict(event) for event in events],
    }


def semantic_candidates(
    video_ids: Sequence[str],
    *,
    top_k_per_category: int = 4,
    chroma_dir: Path = DEFAULT_CHROMA_DIR,
    collection_name: str = DEFAULT_COLLECTION,
    graph_path: Path = DEFAULT_GRAPH,
    ollama_host: str | None = None,
    embedding_model: str | None = None,
) -> list[dict[str, Any]]:
    """Recupera candidatos semánticos desde Chroma y los contextualiza con el grafo.

    Esta capa sirve para descubrir formulaciones nuevas. No clasifica ni suma
    eventos: la inclusión en las métricas requiere una regla explícita y una
    prueba de regresión, lo que mantiene comparable la serie histórica.
    """
    if not chroma_dir.exists():
        raise FileNotFoundError(f"No existe la base vectorial: {chroma_dir}")

    # Importación diferida: el detector reproducible y sus pruebas no dependen
    # de que Chroma/Ollama estén disponibles.
    from scripts import rag_transcripts as rag

    client = rag.chroma_client(chroma_dir)
    embedder = rag.OllamaEmbeddingFunction(
        ollama_host or os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        embedding_model or os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL),
    )
    collection = client.get_collection(name=collection_name, embedding_function=embedder)
    selected = sorted({str(video_id) for video_id in video_ids if video_id})
    where = {"video_id": {"$in": selected}} if selected else None
    candidates: dict[str, dict[str, Any]] = {}

    for category, query in SEMANTIC_QUERIES.items():
        kwargs: dict[str, Any] = {
            "query_texts": [f"{DEFINITION} Ejemplo de recurso: {query}."],
            "n_results": max(1, top_k_per_category),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        result = collection.query(**kwargs)
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        ids = (result.get("ids") or [[]])[0]
        for index, document in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            if selected and str((metadata or {}).get("video_id", "")) not in selected:
                continue
            chunk_id = ids[index] if index < len(ids) else f"{category}:{index}"
            row = candidates.setdefault(
                chunk_id,
                {
                    "chunk_id": chunk_id,
                    "categories": [],
                    "text": clean_text(document),
                    "metadata": metadata or {},
                    "distance": None,
                    "graph_context": "",
                },
            )
            if category not in row["categories"]:
                row["categories"].append(category)
            distance = distances[index] if index < len(distances) else None
            if distance is not None and (row["distance"] is None or distance < row["distance"]):
                row["distance"] = float(distance)

    for row in candidates.values():
        row["graph_context"] = rag.graph_context(row["text"], graph_path)
    return sorted(
        candidates.values(),
        key=lambda row: row["distance"] if row["distance"] is not None else float("inf"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detecta frases agresivas con una definición reproducible.")
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Ruta JSON; '-' imprime a stdout.")
    parser.add_argument("--date-after", default="")
    parser.add_argument("--date-before", default="")
    parser.add_argument("--video-id", action="append", default=[])
    parser.add_argument("--min-confidence", type=float, default=0.82)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_records(args.transcripts)
    if args.date_after:
        records = [record for record in records if record.upload_date >= args.date_after.replace("-", "")]
    if args.date_before:
        records = [record for record in records if record.upload_date <= args.date_before.replace("-", "")]
    if args.video_id:
        selected = set(args.video_id)
        records = [record for record in records if record.video_id in selected]
    events = scan_records(records, min_confidence=args.min_confidence)
    report = build_report(records, events)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output == "-":
        print(payload, end="")
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(f"{len(events)} frases agresivas en {len(records)} registros → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
