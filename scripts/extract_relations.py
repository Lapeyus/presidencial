#!/usr/bin/env python3
"""Extrae perfiles de personas y menciones de temas desde transcripciones usando LangExtract.

Lee un archivo JSONL de transcripciones de videos (por defecto
``data/presidencia_transcripts.jsonl``) y un archivo de configuración JSON
(por defecto ``data/extraction_config.json``) con las personas de interés
(``people``, cada una con ``name`` y ``aliases``) y los temas de interés
(``topics``, cada uno con ``name`` y ``keywords``). Divide cada transcripción
en fragmentos (chunks) por cantidad de caracteres y ejecuta LangExtract sobre
cada fragmento con un modelo local de Ollama para extraer:

- ``PersonMention``: menciones a personas de la lista, con su cargo (``role``)
  y un enunciado auto-contenido (``statement``).
- ``ConceptMention``: menciones a temas de la lista, con su enunciado.

Cada extracción se asocia a la persona o tema configurado mediante
coincidencia difusa de alias/keywords, y conserva la trazabilidad al video de
origen: título, fecha, timestamp y URL de YouTube con marca de tiempo.

Produce dos salidas que se sobrescriben tras procesar cada video (guardado
progresivo) y también al recibir Ctrl+C:

- Un JSON de perfiles (por defecto ``data/profiles.json``) con metadatos del
  procesamiento, los perfiles por persona y por tema, y la lista de
  ``processed_video_ids`` que permite reanudar la ejecución sin repetir
  videos ya procesados.
- Un reporte en Markdown (por defecto ``data/profiles_report.md``) con tablas
  de declaraciones por persona y por tema, enlazadas al timestamp del video.

Uso típico:

    python scripts/extract_relations.py \\
        --model-id qwen3.5:9b --model-url http://localhost:11434 \\
        --limit-videos 5 --chunk-chars 3000

Requiere un servidor de Ollama accesible en ``--model-url`` con el modelo
indicado en ``--model-id`` ya descargado.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import langextract as lx
from tqdm import tqdm


def parse_args() -> argparse.Namespace:
    """Define y parsea los argumentos de línea de comandos del script.

    Returns:
        Namespace con: ``config``, ``transcripts``, ``output_json``,
        ``output_md`` (rutas), ``model_id`` y ``model_url`` (Ollama),
        ``limit_videos`` (opcional, para pruebas) y ``chunk_chars``
        (tamaño máximo de cada fragmento, 3000 por defecto).
    """
    parser = argparse.ArgumentParser(
        description="Extrae perfiles de personas y temas utilizando LangExtract."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("data/extraction_config.json"),
        help="Ruta al archivo JSON de configuración con personas y temas.",
    )
    parser.add_argument(
        "--transcripts",
        type=Path,
        default=Path("data/presidencia_transcripts.jsonl"),
        help="Ruta al archivo JSONL de transcripciones.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("data/profiles.json"),
        help="Ruta para guardar los perfiles en formato JSON.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path("data/profiles_report.md"),
        help="Ruta para guardar el reporte en Markdown.",
    )
    parser.add_argument(
        "--model-id",
        default="qwen3.5:9b",
        help="ID del modelo de Ollama a utilizar.",
    )
    parser.add_argument(
        "--model-url",
        default="http://localhost:11434",
        help="URL del host de Ollama.",
    )
    parser.add_argument(
        "--limit-videos",
        type=int,
        help="Límite máximo de videos a procesar (para pruebas rápidas).",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=3000,
        help="Largo máximo de caracteres para cada chunk de transcripción.",
    )
    return parser.parse_args()


def format_timestamp(seconds: float | None) -> str:
    """Convierte segundos a formato ``HH:MM:SS``; devuelve ``00:00:00`` si el valor es None o negativo."""
    if seconds is None or seconds < 0:
        return "00:00:00"
    secs = int(seconds)
    hours, remainder = divmod(secs, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def get_timestamp_url(base_url: str | None, start_seconds: float | None) -> str:
    """Construye la URL del video con marca de tiempo (``?t=Ns`` o ``&t=Ns``).

    Devuelve cadena vacía si no hay URL base, y la URL sin modificar si
    ``start_seconds`` es None o negativo.
    """
    if not base_url:
        return ""
    if start_seconds is None or start_seconds < 0:
        return base_url
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}t={int(start_seconds)}s"


def get_transcript_chunks(record: dict[str, Any], chunk_chars: int) -> list[dict[str, Any]]:
    """Divide la transcripción de un video en fragmentos de texto con metadatos.

    Agrupa los segmentos de ``record["transcript"]["segments"]`` acumulando
    texto hasta alcanzar al menos ``chunk_chars`` caracteres por fragmento;
    los segmentos vacíos se descartan. Si el registro no tiene segmentos,
    usa el texto plano de ``transcript["text"]`` como un único fragmento
    (o devuelve lista vacía si tampoco hay texto).

    Args:
        record: Registro JSONL de un video con ``video_id``, ``title``,
            ``upload_date``, ``url`` y ``transcript``.
        chunk_chars: Umbral de caracteres a partir del cual se cierra un
            fragmento (los fragmentos pueden excederlo ligeramente).

    Returns:
        Lista de dicts, cada uno con el texto del fragmento, los metadatos
        del video y ``start``/``timestamp``/``timestamp_url`` calculados a
        partir del primer segmento del fragmento.
    """
    video_id = record.get("video_id", "")
    title = record.get("title", "")
    upload_date = record.get("upload_date", "")
    url = record.get("url", "")
    
    transcript_data = record.get("transcript") or {}
    segments = transcript_data.get("segments") or []
    
    if not segments:
        text = transcript_data.get("text") or ""
        if not text.strip():
            return []
        return [{
            "video_id": video_id,
            "title": title,
            "upload_date": upload_date,
            "url": url,
            "text": text,
            "start": 0,
            "timestamp": "00:00:00",
            "timestamp_url": url
        }]
    
    chunks = []
    current_segments = []
    current_len = 0
    
    for seg in segments:
        seg_text = seg.get("text", "").strip()
        if not seg_text:
            continue
        current_segments.append(seg)
        current_len += len(seg_text) + 1
        
        if current_len >= chunk_chars:
            text = " ".join(s.get("text", "") for s in current_segments)
            start = current_segments[0].get("start", 0)
            chunks.append({
                "video_id": video_id,
                "title": title,
                "upload_date": upload_date,
                "url": url,
                "text": text,
                "start": start,
                "timestamp": format_timestamp(start),
                "timestamp_url": get_timestamp_url(url, start)
            })
            current_segments = []
            current_len = 0
            
    if current_segments:
        text = " ".join(s.get("text", "") for s in current_segments)
        start = current_segments[0].get("start", 0)
        chunks.append({
            "video_id": video_id,
            "title": title,
            "upload_date": upload_date,
            "url": url,
            "text": text,
            "start": start,
            "timestamp": format_timestamp(start),
            "timestamp_url": get_timestamp_url(url, start)
        })
        
    return chunks


def find_matching_person(extracted_text: str, target_people: list[dict[str, Any]]) -> str | None:
    """Asocia un texto extraído por el modelo con una persona configurada.

    Aplica dos estrategias en orden: (1) igualdad o inclusión (substring en
    cualquier dirección) contra los ``aliases`` de cada persona, exigiendo al
    menos 4 caracteres en ambos lados para la inclusión; (2) coincidencia de
    palabras significativas (más de 3 letras, excluyendo stopwords y títulos
    como "señor", "presidente" o "ministro") entre el texto y los alias.
    La comparación es insensible a mayúsculas.

    Returns:
        El ``name`` canónico de la primera persona que coincide, o None si
        ninguna coincide.
    """
    ext_lower = extracted_text.lower().strip()
    
    # 1. Coincidencia exacta o substring con los alias
    for person in target_people:
        name = person["name"]
        aliases = person["aliases"]
        for alias in aliases:
            alias_lower = alias.lower().strip()
            if ext_lower == alias_lower:
                return name
            if alias_lower in ext_lower or ext_lower in alias_lower:
                if len(ext_lower) >= 4 and len(alias_lower) >= 4:
                    return name
                    
    # 2. Coincidencia de palabras clave significativas (evitando stop words comunes)
    stopwords = {"señor", "doña", "don", "presidente", "ministro", "director", "la", "el", "de", "del"}
    ext_words = [w for w in ext_lower.split() if len(w) > 3 and w not in stopwords]
    for person in target_people:
        name = person["name"]
        key_words = set()
        for alias in person["aliases"]:
            for w in alias.lower().split():
                if len(w) > 3 and w not in stopwords:
                    key_words.add(w)
        for w in ext_words:
            if w in key_words:
                return name
    return None


def find_matching_topic(extracted_text: str, target_topics: list[dict[str, Any]]) -> str | None:
    """Asocia un texto extraído por el modelo con un tema configurado.

    Misma lógica difusa que ``find_matching_person`` pero contra las
    ``keywords`` de cada tema y con una lista de stopwords de artículos y
    preposiciones en español: primero igualdad/inclusión (mínimo 4
    caracteres en ambos lados para la inclusión) y luego coincidencia de
    palabras significativas de más de 3 letras.

    Returns:
        El ``name`` del primer tema que coincide, o None si ninguno coincide.
    """
    ext_lower = extracted_text.lower().strip()
    
    # 1. Coincidencia exacta o substring en keywords
    for topic in target_topics:
        name = topic["name"]
        keywords = topic["keywords"]
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if ext_lower == kw_lower:
                return name
            if kw_lower in ext_lower or ext_lower in kw_lower:
                if len(ext_lower) >= 4 and len(kw_lower) >= 4:
                    return name
                    
    # 2. Coincidencia por palabra clave significativa
    stopwords = {"la", "el", "de", "del", "en", "para", "por", "un", "una", "con", "y", "o"}
    ext_words = [w for w in ext_lower.split() if len(w) > 3 and w not in stopwords]
    for topic in target_topics:
        name = topic["name"]
        key_words = set()
        for kw in topic["keywords"]:
            for w in kw.lower().split():
                if len(w) > 3 and w not in stopwords:
                    key_words.add(w)
        for w in ext_words:
            if w in key_words:
                return name
    return None


def format_iso_date(value: str) -> str:
    """Convierte una fecha compacta ``YYYYMMDD`` a ``YYYY-MM-DD``; devuelve el valor intacto si no tiene 8 caracteres."""
    if len(value) == 8:
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def save_results(
    args: argparse.Namespace,
    model_id: str,
    total_videos: int,
    total_chunks: int,
    extractions_count: int,
    people_profiles: dict[str, list[dict[str, Any]]],
    topic_profiles: dict[str, list[dict[str, Any]]],
    processed_video_ids: set[str],
) -> None:
    output_data = {
        "metadata": {
            "processed_at": datetime.now().isoformat(),
            "model_used": model_id,
            "total_videos": total_videos,
            "total_chunks": total_chunks,
            "total_extractions": extractions_count,
            "processed_video_ids": list(processed_video_ids)
        },
        "people": people_profiles,
        "topics": topic_profiles
    }
    
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
        
    generate_markdown_report(output_data, args.output_md)


def main() -> int:
    args = parse_args()
    
    # 1. Cargar Configuración
    if not args.config.exists():
        print(f"Error: No existe el archivo de configuración {args.config}", file=sys.stderr)
        return 1
        
    with args.config.open("r", encoding="utf-8") as f:
        config = json.load(f)
        
    target_people = config.get("people", [])
    target_topics = config.get("topics", [])
    
    print(f"Cargadas {len(target_people)} personas y {len(target_topics)} temas para extraer.")
    
    # 2. Cargar Transcripciones
    if not args.transcripts.exists():
        print(f"Error: No existe el archivo de transcripciones {args.transcripts}", file=sys.stderr)
        return 1
        
    records = []
    with args.transcripts.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
                
    # Ordenar por fecha descentente
    records.sort(key=lambda item: item.get("upload_date") or "", reverse=True)
    
    if args.limit_videos:
        records = records[:args.limit_videos]
        print(f"Límite activado: se procesarán únicamente los últimos {len(records)} videos.")
        
    # Inicializar estructuras para acumular resultados
    people_profiles: dict[str, list[dict[str, Any]]] = {p["name"]: [] for p in target_people}
    topic_profiles: dict[str, list[dict[str, Any]]] = {t["name"]: [] for t in target_topics}
    
    total_chunks_processed = 0
    extractions_count = 0
    processed_video_ids = set()
    total_videos_count = len(records)
    
    # 3. Cargar resultados existentes si los hay para poder resumir / continuar
    if args.output_json.exists():
        try:
            with args.output_json.open("r", encoding="utf-8") as f:
                existing_data = json.load(f)
            meta = existing_data.get("metadata", {})
            processed_video_ids = set(meta.get("processed_video_ids", []))
            total_chunks_processed = meta.get("total_chunks", 0)
            extractions_count = meta.get("total_extractions", 0)
            
            existing_people = existing_data.get("people", {})
            for p in target_people:
                name = p["name"]
                if name in existing_people:
                    people_profiles[name] = existing_people[name]
                    
            existing_topics = existing_data.get("topics", {})
            for t in target_topics:
                name = t["name"]
                if name in existing_topics:
                    topic_profiles[name] = existing_topics[name]
            print(f"Resultados previos cargados. Encontrados {len(processed_video_ids)} videos ya procesados.")
        except Exception as e:
            print(f"Advertencia: No se pudieron cargar los resultados previos ({e}). Iniciando desde cero.")

    # Filtrar videos ya procesados
    records = [r for r in records if r.get("video_id") not in processed_video_ids]
    if len(records) < total_videos_count:
        print(f"Saltando {total_videos_count - len(records)} videos ya procesados. Quedan {len(records)} videos por procesar.")
        
    if not records:
        print("Todos los videos configurados ya han sido procesados de forma exitosa.")
        # Guardar nuevamente para refrescar reportes/fechas
        save_results(
            args,
            args.model_id,
            total_videos_count,
            total_chunks_processed,
            extractions_count,
            people_profiles,
            topic_profiles,
            processed_video_ids
        )
        return 0

    # 4. Definir ejemplos few-shot y prompt para LangExtract
    examples = [
        lx.core.data.ExampleData(
            text="El ministro de obras públicas Efraín Célezón anunció obras en el aeropuerto de Liberia.",
            extractions=[
                lx.core.data.Extraction(
                    extraction_class="PersonMention",
                    extraction_text="Efraín Célezón",
                    attributes={
                        "role": "ministro de obras públicas",
                        "statement": "Efraín Célezón anunció obras de infraestructura en el aeropuerto de Liberia."
                    }
                ),
                lx.core.data.Extraction(
                    extraction_class="ConceptMention",
                    extraction_text="obras",
                    attributes={
                        "statement": "El gobierno anunció la realización de obras públicas en Guanacaste."
                    }
                ),
                lx.core.data.Extraction(
                    extraction_class="ConceptMention",
                    extraction_text="aeropuerto de Liberia",
                    attributes={
                        "statement": "El aeropuerto de Liberia recibirá mejoras en su infraestructura."
                    }
                )
            ]
        )
    ]
    
    # Construir listado de personas y alias para el prompt
    people_lines = []
    for p in target_people:
        aliases_str = ", ".join(p["aliases"])
        people_lines.append(f"- {p['name']} (referenciado como: {aliases_str})")
    people_prompt_str = "\n".join(people_lines)
    
    # Construir listado de temas y keywords para el prompt
    topics_lines = []
    for t in target_topics:
        keywords_str = ", ".join(t["keywords"])
        topics_lines.append(f"- {t['name']} (referenciado como: {keywords_str})")
    topics_prompt_str = "\n".join(topics_lines)
    
    prompt = f"""
    Extrae de forma precisa menciones a las personas y conceptos de interés a partir del fragmento de texto proporcionado.
    
    Personas de interés:
    {people_prompt_str}
    
    Conceptos de interés:
    {topics_prompt_str}
    
    Reglas de Extracción:
    1. Si se menciona a alguna de las personas de la lista (ya sea por su nombre, apellido, apodo, cargo o título indicado entre sus formas de referencia):
       - Extrae el nombre o la forma en que se le refiere como aparece en el fragmento (PersonMention).
       - En 'attributes', añade:
         * 'role': El cargo o puesto que se le menciona en el texto (ej: "Presidente", "Ministro", "Director"). Si no se indica en este fragmento, usa "n/a".
         * 'statement': Un enunciado autónomo, escrito en tercera persona y completamente comprensible sin contexto, que resuma lo que hace o declara esa persona. Usa nombres explícitos y nunca pronombres ambiguos (ej: "Laura Fernández defendió la inversión pública").
    2. Si se menciona alguno de los conceptos de la lista (o palabras y términos relacionados indicados en sus formas de referencia):
       - Extrae el término o concepto exacto que refiere al tema (ConceptMention).
       - En 'attributes', añade:
         * 'statement': Un enunciado completo y comprensible fuera de contexto que resuma la idea, política, acción o decisión asociada a este tema en el fragmento de texto.
    """
    
    # Instanciar el modelo de Ollama directamente para evitar problemas de registro de patrones de nombres
    from langextract.providers.ollama import OllamaLanguageModel
    try:
        model = OllamaLanguageModel(
            model_id=args.model_id,
            model_url=args.model_url,
        )
    except Exception as e:
        print(f"Error al inicializar el modelo de Ollama: {e}", file=sys.stderr)
        return 1

    print(f"\nIniciando extracción con LangExtract usando modelo: {args.model_id}...")
    
    try:
        for v_idx, record in enumerate(records, start=1):
            video_id = record.get("video_id", "")
            chunks = get_transcript_chunks(record, args.chunk_chars)
            if not chunks:
                processed_video_ids.add(video_id)
                continue
                
            video_title = record.get("title", "")
            video_date = format_iso_date(record.get("upload_date", ""))
            
            print(f"\n--- [{v_idx}/{len(records)}] Procesando video: {video_title} ({video_date}) ---", flush=True)
            print(f"Total fragmentos a analizar: {len(chunks)}", flush=True)
            
            for idx, chunk in enumerate(chunks, start=1):
                print(f"  [{idx}/{len(chunks)}] Analizando fragmento en timestamp: {chunk['timestamp']}...", flush=True)
                try:
                    # Ejecutar LangExtract en el fragmento de texto utilizando el modelo instanciado
                    result = lx.extract(
                        text_or_documents=chunk["text"],
                        prompt_description=prompt,
                        examples=examples,
                        model=model,
                        show_progress=False
                    )
                    
                    total_chunks_processed += 1
                    
                    # Procesar extracciones obtenidas
                    found_any = False
                    for ext in getattr(result, "extractions", []):
                        cls = ext.extraction_class
                        text = ext.extraction_text
                        attrs = ext.attributes or {}
                        statement = attrs.get("statement")
                        
                        if not statement:
                            continue
                            
                        source_info = {
                            "statement": statement,
                            "extracted_text": text,
                            "video_id": chunk["video_id"],
                            "video_title": video_title,
                            "video_date": video_date,
                            "timestamp": chunk["timestamp"],
                            "timestamp_url": chunk["timestamp_url"]
                        }
                        
                        if cls == "PersonMention":
                            matched_person = find_matching_person(text, target_people)
                            if matched_person:
                                source_info["role"] = attrs.get("role", "n/a")
                                people_profiles[matched_person].append(source_info)
                                extractions_count += 1
                                found_any = True
                                print(f"    + Extraído: {matched_person} -> \"{statement}\"", flush=True)
                                
                        elif cls == "ConceptMention":
                            matched_topic = find_matching_topic(text, target_topics)
                            if matched_topic:
                                topic_profiles[matched_topic].append(source_info)
                                extractions_count += 1
                                found_any = True
                                print(f"    + Extraído: Tema \"{matched_topic}\" -> \"{statement}\"", flush=True)
                    
                    if not found_any:
                        print("    (No se detectaron personas o temas de interés en este fragmento)", flush=True)
                                
                except Exception as e:
                    # Imprimir error sin romper el loop de procesamiento
                    print(f"    ! Error en fragmento {chunk['timestamp']}: {e}", flush=True)
                    
            # Registrar el video actual como completamente procesado
            processed_video_ids.add(video_id)
            
            # Guardado progresivo tras terminar cada video
            save_results(
                args,
                args.model_id,
                total_videos_count,
                total_chunks_processed,
                extractions_count,
                people_profiles,
                topic_profiles,
                processed_video_ids
            )
            print(f"-> Progreso guardado para: {video_title}", flush=True)
            
    except KeyboardInterrupt:
        print("\n\n[Proceso Interrumpido] Guardando estado actual antes de salir...")
        save_results(
            args,
            args.model_id,
            total_videos_count,
            total_chunks_processed,
            extractions_count,
            people_profiles,
            topic_profiles,
            processed_video_ids
        )
        print("Estado actual guardado de forma segura.")
        return 130
        
    print(f"\nProcesamiento terminado: {total_chunks_processed} fragmentos analizados.")
    print(f"Se realizaron {extractions_count} extracciones exitosas.")
    
    # Guardar resultados finales
    save_results(
        args,
        args.model_id,
        total_videos_count,
        total_chunks_processed,
        extractions_count,
        people_profiles,
        topic_profiles,
        processed_video_ids
    )
    print("Resultados finales actualizados exitosamente.")
    return 0



def generate_markdown_report(data: dict[str, Any], output_path: Path) -> None:
    meta = data["metadata"]
    people = data["people"]
    topics = data["topics"]
    
    md_content = []
    md_content.append("# 📊 Reporte de Perfiles y Relaciones en Discursos Presidenciales")
    md_content.append(f"\nGenerado automáticamente el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} utilizando **{meta['model_used']}** a través de **LangExtract**.")
    
    md_content.append("\n## 🔍 Resumen del Análisis")
    md_content.append(f"- **Videos analizados:** {meta['total_videos']}")
    md_content.append(f"- **Fragmentos procesados:** {meta['total_chunks']}")
    md_content.append(f"- **Declaraciones/Menciones extraídas:** {meta['total_extractions']}")
    
    md_content.append("\n---")
    
    md_content.append("\n## 👤 Perfiles de Personas")
    md_content.append("A continuación se presentan los enunciados auto-contenidos asociados a cada persona configurada en el análisis:")
    
    for person, extractions in people.items():
        md_content.append(f"\n### {person}")
        if not extractions:
            md_content.append("_No se encontraron declaraciones o menciones específicas sobre esta persona en los discursos analizados._")
            continue
            
        # Agrupar por rol si hay roles distintos
        roles = set(e.get("role", "n/a") for e in extractions if e.get("role") != "n/a")
        if roles:
            md_content.append(f"**Cargos/Roles identificados:** {', '.join(roles)}")
            
        md_content.append("\n| Declaración / Hecho Extraído | Video / Speeches | Fecha | Timestamp |")
        md_content.append("| :--- | :--- | :--- | :--- |")
        
        for ext in extractions:
            title = ext['video_title']
            # Truncar título largo para que la tabla sea legible
            if len(title) > 40:
                title = title[:37] + "..."
            timestamp_link = f"[{ext['timestamp']}]({ext['timestamp_url']})"
            md_content.append(f"| {ext['statement']} | {title} | {ext['video_date']} | {timestamp_link} |")
            
    md_content.append("\n---")
    
    md_content.append("\n## 🎯 Ideas y Declaraciones por Temas Clave")
    md_content.append("Colección de ideas, políticas y decisiones organizadas por conceptos importantes:")
    
    for topic, extractions in topics.items():
        md_content.append(f"\n### Concepto: {topic.capitalize()}")
        if not extractions:
            md_content.append("_No se encontraron ideas asociadas a este tema en los discursos analizados._")
            continue
            
        md_content.append("\n| Idea / Decisión / Política | Video / Speeches | Fecha | Timestamp |")
        md_content.append("| :--- | :--- | :--- | :--- |")
        
        for ext in extractions:
            title = ext['video_title']
            if len(title) > 40:
                title = title[:37] + "..."
            timestamp_link = f"[{ext['timestamp']}]({ext['timestamp_url']})"
            md_content.append(f"| {ext['statement']} | {title} | {ext['video_date']} | {timestamp_link} |")
            
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(md_content), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
