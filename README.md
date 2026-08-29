# Transcripciones Presidencia CR

**🌐 Hemeroteca Presidencial:** análisis del discurso público de la presidenta — a quiénes culpa, aliados y adversarios, con cada afirmación enlazada a su fuente — publicado en [lapeyus.github.io/presidencial](https://lapeyus.github.io/presidencial/). Se genera con `scripts/discourse_profiles.py` (perfiles por persona desde el grafo + menciones del corpus, motor LLM configurable) y `scripts/build_site.py` (sitio estático en `docs/`).

Script para recolectar transcripciones de videos y transmisiones disponibles en el canal de YouTube de Presidencia de Costa Rica.

Prioridad de extracción:

1. Subtítulos manuales de YouTube.
2. Subtítulos automáticos / CC de YouTube.
3. Descarga de audio y transcripción local con mlx-whisper (activado por defecto; `--no-whisper` para desactivar).

## Uso rápido

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Por defecto el script pasa cookies del perfil de Chrome a `yt-dlp`:

```bash
--cookies-from-browser chrome
```

Para evitar el bloqueo SABR de YouTube al descargar audio, pasa por defecto:

```bash
--extractor-args "youtube:player_client=default,tv_simply"
```

El runtime JavaScript de `yt-dlp` (`--js-runtimes`) esta desactivado por defecto porque no todas las versiones lo soportan; actívalo con `--js-runtimes node` si tu yt-dlp lo acepta.

Para usar otro perfil:

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-05-01 --cookies-from-browser "chrome:Profile 1"
```

Para desactivar cookies del navegador:

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-05-01 --no-browser-cookies
```

Ejecutar con rango de fechas:

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-05-01 --date-before 2026-05-27
```

El rango de fechas se aplica desde el descubrimiento de `/videos` y `/streams`, y se valida de nuevo al procesar cada video.

Extender hacia el pasado:

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-04-01 --date-before 2026-05-27
```

Activar fallback con Whisper cuando YouTube no ofrece subtítulos:

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-05-01 --whisper
```

Cuando se usa Whisper, la descarga de audio y la transcripción imprimen progreso directamente en la terminal. En videos largos, la barra principal puede quedarse en el mismo video hasta que termine Whisper.

Si `yt-dlp` falla por certificados SSL locales, el script reintenta automaticamente con `--no-check-certificates` y mantiene ese modo durante el resto de la corrida. Tambien puedes forzarlo desde el inicio:

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-05-01 --no-check-certificates
```

Usar un modelo específico (repo MLX de Hugging Face):

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-05-01 --whisper --whisper-model mlx-community/whisper-large-v3-turbo
```

La transcripción local usa `mlx-whisper` (acelerado en Apple Silicon) con `whisper-large-v3-turbo` por defecto y `condition_on_previous_text=False`, que evita que Whisper se salte habla de bajo volumen tras silencios largos (típico en intros de transmisiones en vivo).

## Salidas

- `data/presidencia_transcripts.jsonl`: archivo único para consumo por otras apps. Una línea JSON por video, con metadata y transcripción.
- `data/records/*.json`: cache interno de registros procesados.
- `data/state.sqlite`: estado de ejecución para evitar trabajo duplicado.
- `data/audio/`: audios temporales o cacheados para Whisper.
- `data/chroma/`: índice ChromaDB para hacer preguntas a las transcripciones.

El archivo `presidencia_transcripts.jsonl` se reconstruye en cada corrida desde `data/records`, por lo que no acumula duplicados aunque el script se ejecute muchas veces.

## Ejecución constante

El script es idempotente: si un video ya fue procesado con éxito, se omite en corridas posteriores. Para reintentar fallos:

```bash
python scripts/collect_youtube_transcripts.py --retry-failed --date-after 2026-05-01
```

Los videos sin subtítulos se vuelven a revisar después de 24 horas, para cubrir el caso de CC generados más tarde por YouTube. Puedes cambiar esa ventana:

```bash
python scripts/collect_youtube_transcripts.py --date-after 2026-05-01 --retry-no-transcript-after-hours 6
```

Para reprocesar todo lo que caiga en el rango:

```bash
python scripts/collect_youtube_transcripts.py --force --date-after 2026-05-01
```

## Entrevistas en medios (búsqueda + validación)

Además del canal oficial, `scripts/search_interviews.py` busca en YouTube entrevistas de la presidente Laura Fernández dadas a medios (desde 2026-05-01 por defecto) y las pasa por validación manual antes de transcribirlas:

```bash
python scripts/search_interviews.py search
python scripts/search_interviews.py review
python scripts/search_interviews.py process
```

- `search` guarda candidatos como `pending` en `data/interview_candidates.json`. Descarta automáticamente lo ya conocido (records, state.sqlite, búsquedas previas), los videos del propio canal de la Presidencia, lo fuera del rango de fechas y clips menores a 3 minutos (`--min-duration`). Las consultas se personalizan con `--query` (repetible).
- `review` valida de forma interactiva ([a]probar / [r]echazar / Enter salta / [q] salir); `review --list` solo lista, y `approve`/`reject` marcan por `video_id` sin interacción. Nada se transcribe sin aprobación.
- `process` transcribe los aprobados con el mismo pipeline del recolector (subtítulos de YouTube → mlx-whisper), escribe en `data/records/` y reconstruye el JSONL. Reindexa después con `python scripts/rag_transcripts.py index`.

## RAG con ChromaDB

Después de tener transcripciones en `data/presidencia_transcripts.jsonl`, construye el índice:

```bash
ollama pull embeddinggemma:latest
python scripts/rag_transcripts.py index
```

ChromaDB usa embeddings de Ollama con `embeddinggemma:latest` por defecto. Si cambias el modelo de embeddings, reconstruye el índice:

```bash
python scripts/rag_transcripts.py index --embedding-model embeddinggemma:latest
```

Hacer una pregunta con Ollama, proveedor por defecto:

```bash
python scripts/rag_transcripts.py ask "Que dijo la Presidencia sobre universidades?"
```

Extraer todas las menciones exactas de una palabra o frase, sin usar LLM:

```bash
python scripts/rag_transcripts.py mentions "universidades"
python scripts/rag_transcripts.py mentions --whole-word --context-chars 300 "seguridad nacional"
```

La búsqueda exhaustiva deduplica por defecto menciones repetidas por el overlap de chunks. Si necesitas ver los matches crudos:

```bash
python scripts/rag_transcripts.py mentions --no-dedupe "Costa Rica"
```

Escoger proveedor y modelo:

```bash
python scripts/rag_transcripts.py ask --provider ollama --model llama3.1 "Que temas se mencionaron?"
python scripts/rag_transcripts.py ask --provider openai --model gpt-4.1-mini "Resume los anuncios principales"
python scripts/rag_transcripts.py ask --provider gemini --model gemini-2.5-flash "Que fechas se mencionan?"
```

Modo interactivo:

```bash
python scripts/rag_transcripts.py chat --provider ollama --model llama3.1
```

## GUI Streamlit

La interfaz web permite reconstruir el indice ChromaDB, escoger proveedor/modelo y hacer preguntas con fuentes visibles.

```bash
streamlit run streamlit_app.py
```

Es una app multipágina: en la navegación lateral está también **Entrevistas**, la página de validación de candidatos de `search_interviews.py` — muestra cada video con su thumbnail, enlace a YouTube y un checkbox; el botón *Procesar seleccionados* aprueba los marcados y lanza la transcripción en segundo plano (con opción de rechazar el resto). Desde su barra lateral también se ejecutan búsquedas nuevas.

Desde la barra lateral puedes cambiar:

- Archivo `data/presidencia_transcripts.jsonl`.
- Directorio ChromaDB.
- Coleccion.
- Proveedor: Ollama, OpenAI o Gemini.
- Modelo, temperatura y numero de contextos recuperados.
- Modelo de embeddings de Ollama, por defecto `embeddinggemma:latest`.
- Tipo de consulta: RAG o menciones exactas exhaustivas.

## GUI de Analisis Linguistico

La segunda app permite explorar las transcripciones con metricas de linguistica computacional y sociologia politica:

- VAD heuristico: valence, arousal, dominance.
- Riqueza lexical: tokens, vocabulario, TTR, densidad lexical.
- Red semantica de co-ocurrencias.
- Entidades nombradas con SpaCy si hay modelo espanol instalado, o fallback heuristico.
- Marcadores in-group/out-group y populismo.
- Timeline emocional por segmentos con enlaces a timestamps.

```bash
streamlit run streamlit_analysis_app.py --server.port 8502
```

Para mejorar NER en espanol:

```bash
python -m spacy download es_core_news_sm
```

## Grafos de conocimiento (textgraph)

`scripts/knowledge_graph.py` conecta el corpus con [textgraph](https://github.com/lapeyus/textgraph) (instalado en `~/Documents/textgraph`, configurable con `--textgraph-dir` o `TEXTGRAPH_DIR`): selecciona transcripciones, las concatena con encabezados de procedencia y genera un grafo de entidades/relaciones con evidencia literal (JSON + visor HTML autónomo + paquete LLM) en `data/grafos/`.

```bash
python scripts/knowledge_graph.py --last 1                          # el video más reciente
python scripts/knowledge_graph.py --search "100 días" --dry-run     # ver selección y tamaño
python scripts/knowledge_graph.py --videos ID1 ID2 --motor local    # sin modelo, gratis
```

El motor por defecto es `codex-cli` (CLI de OpenAI con sesión; su modelo default es `gpt-5.6-sol`, para textos largos conviene `--modelo gpt-5.6-terra`); también están `gemini-cli`, `antigravity-cli` (CLI `agy`), `ollama` (local, p. ej. `gemma4:12b-mlx`), `claude-cli`, `api` y `local`.

`scripts/batch_graphs.py` genera un grafo por video rotando motores por fases y unifica todo en `data/grafos/grafo_unificado.*` (fusión conservadora por clave); `scripts/refine_unified_graph.py` limpia el unificado con un modelo (corrección de etiquetas, poda de basura, fusión de duplicados con guardas anti-meganodo). Si el grafo unificado existe, el chat RAG, el CLI `ask` y los clips **enriquecen sus respuestas** con las relaciones de las entidades mencionadas en la pregunta. Límite de textgraph: ~400k caracteres por grafo. La página **Grafo** del frontend hace lo mismo con interfaz: selección de videos, estimación de llamadas y visor embebido.

## Cortes de video desde preguntas

`scripts/make_clips.py` une el RAG con los timestamps: el LLM responde citando fuentes `[S#]` más una sección `CITAS:` con citas literales, y cada cita se alinea contra los segmentos finos del transcript para cortar con yt-dlp + ffmpeg el momento exacto en que se dice (MP4 por cita, manifest con score de alineación, `data/clips/<slug>_<fecha>/`). Con `--compilacion` se genera un montaje que une los clips en orden cronológico con rótulos fecha·título (si el ffmpeg local trae `drawtext`).

```bash
python scripts/make_clips.py "¿Qué ha dicho la presidente sobre el fiscal general?"
python scripts/make_clips.py --mentions "fiscal general" --clips 6   # sin LLM, apariciones literales
python scripts/make_clips.py "..." --compilacion --max-seconds 60
```

La página **Clips** del frontend lanza corridas y reproduce los clips con su cita, timestamp y enlace. Nota: `OLLAMA_HOST` estilo `0.0.0.0:11434` se normaliza automáticamente a URL conectable.

Variables útiles:

```bash
export RAG_PROVIDER=ollama
export RAG_MODEL=llama3.1
export RAG_EMBEDDING_MODEL=embeddinggemma:latest
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
export OLLAMA_HOST=http://localhost:11434
```

El índice usa ChromaDB persistente en `data/chroma`. Si cambian las transcripciones, vuelve a ejecutar `python scripts/rag_transcripts.py index`.

## Dependencias externas

- `yt-dlp` para descubrir videos, descargar metadata, subtítulos y audio.
- `ffmpeg` si se usa descarga/transcripción de audio.
- `mlx-whisper` (incluido en requirements.txt; requiere Apple Silicon) si se usa `--whisper`.
- Ollama local si usas `--provider ollama`.
- Python 3.12+ (el venv del proyecto; mlx no soporta 3.9).
