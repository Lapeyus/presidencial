#!/usr/bin/env python3
"""Corta clips de video con las respuestas a una pregunta sobre el corpus.

Une el RAG con los timestamps de las transcripciones. En modo pregunta, el
LLM responde citando fuentes ``[S#]`` y además devuelve una sección
``CITAS:`` con las citas literales que respaldan la respuesta; cada cita se
**alinea contra los segmentos finos del transcript** (2-8 s cada uno) del
video correspondiente para cortar el momento exacto en que se dice — no el
inicio del chunk recuperado, que puede quedar minutos antes. En modo
``--mentions`` se alinea el término literal de la misma forma, sin LLM.

Los cortes se hacen con ``yt-dlp --download-sections`` + ffmpeg. Con
``--compilacion`` los clips se unen en un **montaje**: re-encodeados a
formato uniforme, en orden cronológico (``--orden fecha``, default) o de
relevancia, con un rótulo sobreimpreso (fecha · título del video) que se
desactiva con ``--no-rotulos``.

Salidas por corrida, en ``data/clips/<slug>_<fecha>/``: un MP4 por cita,
``compilacion.mp4`` opcional, ``manifest.json`` (pregunta, respuesta, y por
clip su cita, tiempos, score de alineación y URL con timestamp) y
``respuesta.md``.

Ejemplos:
    python scripts/make_clips.py "¿Qué ha dicho la presidente sobre el fiscal general?" --compilacion
    python scripts/make_clips.py --mentions "fiscal general" --clips 6
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import rag_transcripts as rag  # noqa: E402

DEFAULT_OUTPUT_ROOT = Path("data/clips")
RECORDS_DIR = Path("data/records")
YTDLP_EXTRACTOR_ARGS = "youtube:player_client=default,tv_simply"
MIN_ALIGN_SCORE = 0.45
FONT_FILE = "/System/Library/Fonts/Helvetica.ttc"
QUOTE_INSTRUCTIONS = (
    "Al final de tu respuesta agrega una seccion que empiece exactamente con 'CITAS:' seguida de las citas "
    "literales del contexto que respaldan tu respuesta, una por linea, con el formato: [S1] \"cita textual "
    "copiada exactamente del contexto\". Incluye de 2 a 6 citas, de fuentes distintas cuando sea posible, y "
    "elige oraciones completas y textuales, sin parafrasear."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Corta clips de video que respaldan la respuesta a una pregunta.")
    parser.add_argument("question", nargs="*", help="Pregunta para el RAG (o término si se usa --mentions).")
    parser.add_argument("--mentions", help="Modo menciones exactas: corta las apariciones literales de este término, sin LLM.")
    parser.add_argument("--clips", type=int, default=4, help="Máximo de clips a cortar. Default: 4.")
    parser.add_argument("--margin", type=float, default=3.0, help="Segundos extra antes y después de cada cita. Default: 3.")
    parser.add_argument("--min-seconds", type=float, default=10.0, help="Duración mínima por clip. Default: 10.")
    parser.add_argument("--max-seconds", type=float, default=120.0, help="Duración máxima por clip. Default: 120.")
    parser.add_argument("--height", type=int, default=720, help="Resolución máxima del video descargado. Default: 720.")
    parser.add_argument("--no-llm", action="store_true", help="En modo pregunta, no llamar al LLM: cortar los top contextos recuperados.")
    parser.add_argument("--compilacion", action="store_true", help="Unir los clips en un montaje (compilacion.mp4).")
    parser.add_argument("--orden", choices=["fecha", "relevancia"], default="fecha", help="Orden del montaje. Default: fecha.")
    parser.add_argument("--rotulos", action=argparse.BooleanOptionalAction, default=True, help="Rótulo fecha · título sobre cada clip del montaje.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--provider", default=os.getenv("RAG_PROVIDER", "ollama"), choices=["ollama", "openai", "gemini"])
    parser.add_argument("--model", default=os.getenv("RAG_MODEL"))
    parser.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--top-k", type=int, default=rag.DEFAULT_TOP_K)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--chroma-dir", type=Path, default=rag.DEFAULT_CHROMA_DIR)
    parser.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    return parser.parse_args()


def slug(value: str, limit: int = 50) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value[:limit] or "clips"


def iso_date(value: str) -> str:
    value = value or ""
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else ""


def normalize_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", str(text).lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", text)


def load_segments(video_id: str) -> list[dict]:
    path = RECORDS_DIR / f"{video_id}.json"
    if not path.exists():
        return []
    record = json.loads(path.read_text(encoding="utf-8"))
    segments = (record.get("transcript") or {}).get("segments") or []
    return [s for s in segments if isinstance(s.get("start"), (int, float)) and (s.get("text") or "").strip()]


def align_quote(
    segments: list[dict],
    quote: str,
    hint: tuple[float, float] | None = None,
) -> tuple[float, float, float] | None:
    """Localiza la cita en los segmentos y devuelve (start, end, score).

    Puntúa ventanas contiguas de segmentos por contención de las palabras de
    la cita (normalizadas, como multiconjunto) y prefiere la ventana más
    corta entre las de puntuación similar. Si hay ``hint`` (rango del chunk
    de origen), la búsqueda se limita a ±90 s de ese rango.
    """
    quote_words = normalize_words(quote)
    if not quote_words or not segments:
        return None
    quote_count = Counter(quote_words)
    quote_len = len(quote_words)

    indexes = list(range(len(segments)))
    if hint is not None:
        low, high = hint[0] - 90, hint[1] + 90
        hinted = [i for i in indexes if low <= segments[i]["start"] <= high]
        if hinted:
            indexes = hinted

    segment_words = {i: normalize_words(segments[i]["text"]) for i in indexes}
    best: tuple[float, float, int, int] | None = None
    for position, start_index in enumerate(indexes):
        window_count: Counter = Counter()
        window_len = 0
        for end_position in range(position, len(indexes)):
            end_index = indexes[end_position]
            window_count.update(segment_words[end_index])
            window_len += len(segment_words[end_index])
            if window_len >= quote_len * 0.6:
                matched = sum(min(count, window_count[word]) for word, count in quote_count.items())
                score = matched / quote_len
                adjusted = score - 0.02 * (end_position - position)
                if best is None or adjusted > best[0]:
                    best = (adjusted, score, start_index, end_index)
            if window_len >= quote_len * 3:
                break

    if best is None or best[1] < MIN_ALIGN_SCORE:
        return None
    _, score, start_index, end_index = best
    start = float(segments[start_index]["start"])
    end = float(segments[end_index].get("end") or segments[end_index]["start"])
    return start, end, score


def clip_bounds(
    start: float,
    end: float,
    args: argparse.Namespace,
) -> tuple[float, float]:
    clip_start = max(0.0, start - args.margin)
    clip_end = end + args.margin
    if clip_end - clip_start < args.min_seconds:
        clip_end = clip_start + args.min_seconds
    if clip_end - clip_start > args.max_seconds:
        clip_end = clip_start + args.max_seconds
    return clip_start, clip_end


def fallback_bounds(metadata: dict, args: argparse.Namespace) -> tuple[float, float] | None:
    """Sin cita alineable: recorte centrado en el chunk (mejor prior que su inicio)."""
    start = metadata.get("start")
    end = metadata.get("end")
    if not isinstance(start, (int, float)) or start < 0:
        return None
    if not isinstance(end, (int, float)) or end <= start:
        end = start + 60
    duration = end - start
    if duration > args.max_seconds:
        center = start + duration / 2
        start = center - args.max_seconds / 2
        end = center + args.max_seconds / 2
    return clip_bounds(float(start), float(end), args)


def parse_quotes(answer: str) -> tuple[str, list[tuple[int, str]]]:
    """Separa la respuesta del bloque CITAS: y devuelve (respuesta, [(indice, cita)])."""
    match = re.search(r"^\s*CITAS?\s*:\s*$", answer, flags=re.MULTILINE | re.IGNORECASE)
    if match:
        body, tail = answer[: match.start()], answer[match.end():]
    else:
        alt = re.search(r"CITAS?\s*:", answer, flags=re.IGNORECASE)
        if not alt:
            return answer.strip(), []
        body, tail = answer[: alt.start()], answer[alt.end():]
    quotes = []
    for source, text in re.findall(r"\[S(\d+)\]\s*[\"“«]?([^\"”»\n]{15,})[\"”»]?", tail):
        quotes.append((int(source), text.strip().rstrip('".')))
    return body.strip(), quotes


def cited_indexes(answer: str) -> list[int]:
    seen: list[int] = []
    for match in re.finditer(r"\[S(\d+)\]", answer):
        index = int(match.group(1))
        if index not in seen:
            seen.append(index)
    return seen


def overlaps(existing: list[dict], video_id: str, bounds: tuple[float, float]) -> bool:
    for clip in existing:
        if clip["video_id"] != video_id:
            continue
        overlap = min(clip["clip_end"], bounds[1]) - max(clip["clip_start"], bounds[0])
        if overlap > 0.5 * (bounds[1] - bounds[0]):
            return True
    return False


def cut_clip(video_id: str, bounds: tuple[float, float], destination: Path, height: int) -> bool:
    url = f"https://www.youtube.com/watch?v={video_id}"
    section = f"*{bounds[0]:.1f}-{bounds[1]:.1f}"
    command = [
        "yt-dlp",
        "--extractor-args", YTDLP_EXTRACTOR_ARGS,
        "--download-sections", section,
        "--force-keyframes-at-cuts",
        "-f", f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(destination),
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not destination.exists():
        tail = "\n".join((result.stderr or result.stdout or "").splitlines()[-5:])
        print(f"  ! fallo el corte de {video_id} [{section}]:\n{tail}", file=sys.stderr)
        return False
    return True


def drawtext_available() -> bool:
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
        return "drawtext" in result.stdout
    except Exception:
        return False


def build_compilation(clips: list[dict], rundir: Path, destination: Path, rotulos: bool) -> bool:
    """Re-encodea los clips a formato uniforme (con rótulo opcional) y los concatena."""
    workdir = rundir / ".compilacion_tmp"
    workdir.mkdir(exist_ok=True)
    uniform: list[Path] = []
    use_font = rotulos and Path(FONT_FILE).exists() and drawtext_available()
    if rotulos and not use_font:
        print("  (ffmpeg sin filtro drawtext o sin fuente; el montaje sale sin rótulos)", flush=True)
    try:
        for index, clip in enumerate(clips):
            source = rundir / clip["file"]
            out = workdir / f"{index:02d}.mp4"
            filters = "scale=-2:720,fps=30"
            if use_font:
                label = " · ".join(part for part in (iso_date(clip.get("upload_date", "")), (clip.get("title") or "")[:70]) if part)
                textfile = workdir / f"{index:02d}.txt"
                textfile.write_text(label, encoding="utf-8")
                filters += (
                    f",drawtext=textfile={textfile}:fontfile={FONT_FILE}:fontsize=24:fontcolor=white:"
                    "x=24:y=h-th-24:box=1:boxcolor=black@0.55:boxborderw=12"
                )
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                 "-vf", filters, "-c:v", "libx264", "-preset", "veryfast",
                 "-c:a", "aac", "-ar", "44100", str(out)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"  ! fallo re-encodeando {source.name}: {result.stderr[-300:]}", file=sys.stderr)
                return False
            uniform.append(out)
        concat_list = workdir / "list.txt"
        concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in uniform), encoding="utf-8")
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(destination)],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and destination.exists()
    finally:
        for path in workdir.iterdir():
            path.unlink(missing_ok=True)
        workdir.rmdir()


def rag_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        chroma_dir=args.chroma_dir,
        collection=args.collection,
        provider=args.provider,
        model=args.model or rag.default_model(args.provider),
        embedding_model=args.embedding_model,
        top_k=args.top_k,
        temperature=args.temperature,
        ollama_host=args.ollama_host,
        show_context=False,
    )


def plan_segments(args: argparse.Namespace, rag_args: argparse.Namespace) -> tuple[str, str, list[dict]]:
    """Devuelve (pregunta, respuesta, plan de cortes ya alineados).

    Cada elemento del plan: {metadata, quote, source, bounds, score}.
    """
    plan: list[dict] = []

    if args.mentions:
        term = args.mentions
        matches = rag.search_mentions(rag_args, term, whole_word=False, context_chars=160, limit=300)
        seen: set[tuple[str, int]] = set()
        for match in matches:
            metadata = match.get("metadata") or {}
            video_id = str(metadata.get("video_id") or "")
            chunk_start = metadata.get("start")
            hint = None
            if isinstance(chunk_start, (int, float)) and chunk_start >= 0:
                hint = (float(chunk_start), float(metadata.get("end") or chunk_start))
            aligned = align_quote(load_segments(video_id), term, hint)
            if aligned is None:
                continue
            key = (video_id, int(aligned[0] // 20))
            if key in seen:
                continue
            seen.add(key)
            plan.append(
                {
                    "metadata": metadata,
                    "quote": re.sub(r"\*\*", "", str(match.get("context") or ""))[:300],
                    "source": None,
                    "aligned": aligned,
                }
            )
            if len(plan) >= args.clips:
                break
        answer = f"Menciones exactas de «{term}»: se cortan {len(plan)} apariciones alineadas al segundo."
        return term, answer, plan

    question = " ".join(args.question).strip()
    if not question:
        raise SystemExit("Falta la pregunta (o usa --mentions TERMINO).")
    contexts = rag.retrieve(rag_args, question)
    if not contexts:
        raise SystemExit("El índice no devolvió contexto. Construye el índice: python scripts/rag_transcripts.py index")

    if args.no_llm:
        answer = "(sin LLM: se cortan los contextos más relevantes recuperados por el índice)"
        for context in contexts[: args.clips]:
            plan.append({"metadata": context.get("metadata") or {}, "quote": (context.get("text") or "")[:300], "source": None, "aligned": None})
        return question, answer, plan

    print(f"Consultando {rag_args.provider} / {rag_args.model}...", flush=True)
    conocimiento = rag.graph_context(question)
    raw_answer = rag.answer_with_llm(
        rag_args, rag.build_prompt(question, contexts, QUOTE_INSTRUCTIONS, conocimiento)
    ).strip()
    answer, quotes = parse_quotes(raw_answer)

    if quotes:
        for source, quote in quotes:
            if not (1 <= source <= len(contexts)):
                continue
            metadata = contexts[source - 1].get("metadata") or {}
            video_id = str(metadata.get("video_id") or "")
            chunk_start = metadata.get("start")
            hint = None
            if isinstance(chunk_start, (int, float)) and chunk_start >= 0:
                hint = (float(chunk_start), float(metadata.get("end") or chunk_start))
            aligned = align_quote(load_segments(video_id), quote, hint)
            plan.append({"metadata": metadata, "quote": quote, "source": f"S{source}", "aligned": aligned})
    else:
        print("  (el modelo no devolvió CITAS:; se usan los contextos citados en el texto)", flush=True)
        order = [i for i in cited_indexes(answer) if 1 <= i <= len(contexts)] or list(range(1, len(contexts) + 1))
        for index in order:
            context = contexts[index - 1]
            plan.append({"metadata": context.get("metadata") or {}, "quote": (context.get("text") or "")[:300], "source": f"S{index}", "aligned": None})

    return question, answer, plan[: args.clips]


def main() -> int:
    args = parse_args()
    rag_args = rag_namespace(args)
    question, answer, plan = plan_segments(args, rag_args)

    print(f"\nPregunta: {question}")
    print(f"Respuesta:\n{answer}\n")
    if not plan:
        raise SystemExit("No hay segmentos con timestamp para cortar.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = args.output_root / f"{slug(question)}_{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    manifest_clips: list[dict] = []
    for index, item in enumerate(plan, start=1):
        metadata = item["metadata"]
        video_id = str(metadata.get("video_id") or "")
        if not video_id:
            continue
        if item["aligned"] is not None:
            start, end, score = item["aligned"]
            bounds = clip_bounds(start, end, args)
        else:
            score = None
            bounds = fallback_bounds(metadata, args)
        if bounds is None or overlaps(manifest_clips, video_id, bounds):
            continue
        filename = f"{index:02d}_{video_id}_{int(bounds[0])}.mp4"
        precision = f"cita alineada (score {score:.2f})" if score is not None else "chunk centrado (sin alineación)"
        print(f"  - [{index}] {video_id} {rag.format_timestamp(bounds[0])} → {rag.format_timestamp(bounds[1])} · {precision}", flush=True)
        if cut_clip(video_id, bounds, outdir / filename, args.height):
            manifest_clips.append(
                {
                    "file": filename,
                    "video_id": video_id,
                    "title": metadata.get("title", ""),
                    "upload_date": metadata.get("upload_date", ""),
                    "source": item.get("source"),
                    "clip_start": round(bounds[0], 1),
                    "clip_end": round(bounds[1], 1),
                    "align_score": round(score, 3) if score is not None else None,
                    "timestamp": rag.format_timestamp(bounds[0]),
                    "timestamp_url": f"https://www.youtube.com/watch?v={video_id}&t={int(bounds[0])}",
                    "quote": (item.get("quote") or "")[:400],
                }
            )

    compilation = None
    if args.compilacion and len(manifest_clips) > 1:
        ordered = manifest_clips
        if args.orden == "fecha":
            ordered = sorted(manifest_clips, key=lambda c: (c.get("upload_date") or "", c["clip_start"]))
        print("Generando montaje...", flush=True)
        target = outdir / "compilacion.mp4"
        if build_compilation(ordered, outdir, target, args.rotulos):
            compilation = target.name

    manifest = {
        "question": question,
        "mode": "mentions" if args.mentions else ("retrieval" if args.no_llm else "rag"),
        "answer": answer,
        "provider": rag_args.provider if not args.mentions else None,
        "model": rag_args.model if not args.mentions else None,
        "created_at": datetime.now().astimezone().isoformat(),
        "clips": manifest_clips,
        "compilation": compilation,
        "orden": args.orden,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [f"# {question}", "", answer, "", "## Clips", ""]
    for clip in manifest_clips:
        lines.append(f"- **{clip['file']}** — {clip['title']} @ {clip['timestamp']} — [ver en YouTube]({clip['timestamp_url']})")
        lines.append(f"  > {clip['quote'][:250]}")
    if compilation:
        lines.extend(["", f"**Montaje:** {compilation} (orden: {args.orden})"])
    (outdir / "respuesta.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nListo: {len(manifest_clips)} clip(s) en {outdir}" + (f" · montaje: {compilation}" if compilation else ""))
    return 0 if manifest_clips else 1


if __name__ == "__main__":
    raise SystemExit(main())
