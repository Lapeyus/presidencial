# Pruebas

## Estrategia

- **Qué se prueba:** la lógica pura y determinista del pipeline: parseo de subtítulos y elección de pista,
  filtros de fecha, consolidación idempotente del JSONL, troceo para el índice, búsqueda literal de menciones,
  alineación de citas para cortes de video y el detector de lenguaje agresivo (reglas versionadas con ejemplos
  reales y contraejemplos).
- **Qué no se prueba automáticamente:** todo lo que depende de servicios o binarios externos: YouTube / `yt-dlp`,
  `mlx-whisper`, `ffmpeg`, Ollama, ChromaDB con embeddings reales, LLMs (SDK o CLI), textgraph y las apps
  Streamlit. Esas piezas se verifican a mano (ver §4).
- **Cómo:** `unittest` de la biblioteca estándar. Las pruebas importan los módulos de `scripts/` como paquete
  (`from scripts import …`) y se ejecutan desde la raíz del repo. Ninguna prueba abre red, lee `data/` ni requiere
  claves; las que escriben archivos usan `tempfile.TemporaryDirectory`.

## Cómo correrlas

```bash
cd presidencial
.venv/bin/python -m unittest discover -s tests -v
```

Requiere el entorno del repo (`pip install -r requirements.txt`), porque `rag_transcripts.py` importa `chromadb`,
`requests` y `tqdm` al cargarse (no se conectan a nada durante las pruebas).

**Resultado al 2026-10-25** (Python 3.12.13, `.venv` del repo): `Ran 33 tests … OK`.

## Inventario de suites

| Suite | Pruebas | Cubre |
|---|---|---|
| `tests/test_aggressive_language.py` | 5 | Taxonomía de ocho familias; ejemplos de 2026 detectados; críticas de política y usos literales no cuentan; varias marcas en una cláusula = un evento; se conservan fuente, timestamp y pista de hablante. |
| `tests/test_collector_parsing.py` | 11 | `parse_vtt_time` (horas, coma decimal, entrada inválida); `parse_vtt` (etiquetas, entidades HTML, cues duplicados); limpieza de repeticiones alucinadas; `choose_subtitle` (manual > automático, variantes regionales, `live_chat` ignorado, sin español); `date_in_range`, `ytdlp_date`, `should_skip_live`; `rebuild_jsonl` idempotente y ordenado. |
| `tests/test_rag_and_clips.py` | 17 | Troceo con solapamiento y `timestamp_url`; record sin `video_id`; respaldo por texto plano; corte en límite de oración; `normalize_ollama_host`, `metadata_url`, `format_timestamp`; patrones de mención (palabra completa, escape de regex), contexto marcado y clave de deduplicación; `align_quote` (acierto y rechazo), `clip_bounds`, `parse_quotes`, `cited_indexes`, `overlaps`, `slug`. |

## Datos y simulaciones

- Los registros y segmentos de prueba se construyen en memoria (helpers `record()` y `make_record()`); el VTT de
  prueba es una cadena literal.
- No se simulan servicios: las funciones probadas son puras y no los invocan. Las que sí los invocan
  (`transcript_from_youtube`, `transcript_from_whisper`, `ollama_embed`, `answer_*`, `cut_clip`,
  `build_compilation`) quedan fuera de la suite.

## Verificación manual recomendada

| Cambio en… | Verificación |
|---|---|
| Recolector | `python scripts/collect_youtube_transcripts.py --date-after <ayer> --no-force --limit 2` y revisar `data/records/` y `state.sqlite`. |
| RAG | `python scripts/rag_transcripts.py index` y luego `mentions` con un término conocido (no usa LLM). |
| Grafos | `python scripts/knowledge_graph.py --last 1 --dry-run` (no llama motores); `--motor local` genera un grafo sin modelo. |
| Clips | `python scripts/make_clips.py --mentions "<término>" --clips 1` (sin LLM). |
| Sitio | `python scripts/build_site.py` y abrir `docs/index.html` localmente. |
| Detector agresivo | Agregar el ejemplo como prueba de regresión en `tests/test_aggressive_language.py` antes de cambiar reglas. |

## Brechas conocidas

- Sin pruebas para el estado en SQLite (`set_status`, reintentos por ventana de horas), el filtro de fechas con
  metadata real ni el corte tras 15 videos viejos consecutivos.
- Sin pruebas del enriquecimiento con grafo (`load_graph_index`, `graph_context`); se podrían cubrir con un grafo
  JSON pequeño en un directorio temporal.
- Sin pruebas del dashboard (`streamlit_analysis_app.py`): las funciones de métricas están mezcladas con la UI.
  Los defectos documentados (NER vacío con spaCy, palabras duplicadas en el léxico VAD, conteo por subcadena de
  `ellos`) no tienen prueba que los fije.
- Sin pruebas de los scripts de perfiles, desambiguación, fusión manual y generación del sitio.
- No hay integración continua configurada: las pruebas se corren localmente.
