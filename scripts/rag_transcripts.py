#!/usr/bin/env python3
"""ChromaDB RAG over collected YouTube transcripts."""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
import requests
from tqdm import tqdm


DEFAULT_TRANSCRIPTS = Path("data/presidencia_transcripts.jsonl")
DEFAULT_CHROMA_DIR = Path("data/chroma")
DEFAULT_COLLECTION = "presidencia_transcripts"
DEFAULT_EMBEDDING_MODEL = "embeddinggemma:latest"
DEFAULT_TOP_K = 8
DEFAULT_CHUNK_CHARS = 2600
DEFAULT_OVERLAP_CHARS = 350


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    metadata: dict[str, str | int | float | bool]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG con ChromaDB para preguntar a las transcripciones.")
    parser.add_argument("--chroma-dir", type=Path, default=DEFAULT_CHROMA_DIR)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Construir o reconstruir el indice ChromaDB.")
    index.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    index.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    index.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    index.add_argument("--batch-size", type=int, default=128)
    index.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    index.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))

    ask = subparsers.add_parser("ask", help="Hacer una pregunta.")
    add_llm_args(ask)
    ask.add_argument("question", nargs="+")

    mentions = subparsers.add_parser("mentions", help="Extraer menciones exactas de una palabra o frase.")
    mentions.add_argument("--case-sensitive", action="store_true")
    mentions.add_argument("--whole-word", action="store_true")
    mentions.add_argument("--no-dedupe", action="store_true", help="No deduplicar menciones causadas por overlap de chunks.")
    mentions.add_argument("--context-chars", type=int, default=220)
    mentions.add_argument("--limit", type=int, default=500)
    mentions.add_argument("term", nargs="+")

    chat = subparsers.add_parser("chat", help="Modo interactivo.")
    add_llm_args(chat)

    return parser.parse_args()


def add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=("openai", "gemini", "ollama"), default=os.getenv("RAG_PROVIDER", "ollama"))
    parser.add_argument("--model", default=os.getenv("RAG_MODEL"))
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--show-context", action="store_true")


def chroma_client(path: Path) -> chromadb.PersistentClient:
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


class OllamaEmbeddingFunction:
    def __init__(self, host: str, model: str) -> None:
        self.host = host.rstrip("/")
        self.model = model

    def __call__(self, input: list[str]) -> list[list[float]]:
        return ollama_embed(self.host, self.model, input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def name(self) -> str:
        return f"ollama:{self.model}"


def normalize_ollama_host(host: str) -> str:
    """Acepta valores como '0.0.0.0:11434' (tipicos de OLLAMA_HOST, que es
    direccion de escucha) y los convierte en una URL conectable."""
    host = (host or "http://localhost:11434").strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.replace("://0.0.0.0", "://localhost")


def ollama_embed(host: str, model: str, texts: list[str]) -> list[list[float]]:
    host = normalize_ollama_host(host)
    response = requests.post(
        f"{host.rstrip('/')}/api/embed",
        json={"model": model, "input": texts},
        timeout=600,
    )
    if response.status_code == 404:
        return [ollama_embed_one(host, model, text) for text in texts]
    response.raise_for_status()
    payload = response.json()
    embeddings = payload.get("embeddings")
    if not embeddings:
        raise RuntimeError(f"Ollama no devolvio embeddings para {model}.")
    return embeddings


def ollama_embed_one(host: str, model: str, text: str) -> list[float]:
    host = normalize_ollama_host(host)
    response = requests.post(
        f"{host.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=600,
    )
    response.raise_for_status()
    embedding = response.json().get("embedding")
    if not embedding:
        raise RuntimeError(f"Ollama no devolvio embedding para {model}.")
    return embedding


def embedding_function(args: argparse.Namespace) -> OllamaEmbeddingFunction:
    return OllamaEmbeddingFunction(args.ollama_host, args.embedding_model)


def rebuild_index(args: argparse.Namespace) -> int:
    if not args.transcripts.exists():
        raise SystemExit(f"No existe {args.transcripts}. Primero ejecuta collect_youtube_transcripts.py.")

    client = chroma_client(args.chroma_dir)
    try:
        client.delete_collection(args.collection)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name=args.collection,
        embedding_function=embedding_function(args),
        metadata={
            "source": str(args.transcripts),
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "embedding_provider": "ollama",
            "embedding_model": args.embedding_model,
        },
    )

    total = 0
    batch: list[Chunk] = []
    with args.transcripts.open("r", encoding="utf-8") as handle:
        for line in tqdm(handle, desc="indexando en ChromaDB", unit="video"):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            for chunk in chunks_from_record(record, args.chunk_chars, args.overlap_chars):
                batch.append(chunk)
                if len(batch) >= args.batch_size:
                    add_batch(collection, batch)
                    total += len(batch)
                    batch = []

    if batch:
        add_batch(collection, batch)
        total += len(batch)

    return total


def add_batch(collection: Any, batch: list[Chunk]) -> None:
    collection.add(
        ids=[chunk.chunk_id for chunk in batch],
        documents=[chunk.text for chunk in batch],
        metadatas=[chunk.metadata for chunk in batch],
    )


def chunks_from_record(record: dict[str, Any], chunk_chars: int, overlap_chars: int) -> list[Chunk]:
    transcript = record.get("transcript") or {}
    segments = transcript.get("segments") or []
    video_id = str(record.get("video_id") or "")
    if not video_id:
        return []

    base_metadata = clean_metadata(
        {
            "video_id": video_id,
            "title": record.get("title") or "",
            "url": record.get("url") or "",
            "upload_date": record.get("upload_date") or "",
            "transcript_source": transcript.get("source") or "",
            "duration": record.get("duration") or 0,
            "was_live": bool(record.get("was_live")),
        }
    )

    if segments:
        return chunk_segments(video_id, base_metadata, segments, chunk_chars, overlap_chars)

    text = clean_text(transcript.get("text", ""))
    chunks = []
    for index, part in enumerate(split_text(text, chunk_chars, overlap_chars)):
        if not part:
            continue
        metadata = dict(base_metadata)
        metadata.update(
            {
                "chunk_index": index,
                "start": -1,
                "end": -1,
                "timestamp": "",
                "timestamp_url": metadata_url(base_metadata.get("url"), None),
                "chunk_seconds": 0,
            }
        )
        chunks.append(Chunk(chunk_id=f"{video_id}:{index}", text=part, metadata=metadata))
    return chunks


def chunk_segments(
    video_id: str,
    base_metadata: dict[str, str | int | float | bool],
    segments: list[dict[str, Any]],
    chunk_chars: int,
    overlap_chars: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[dict[str, Any]] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        text = clean_text(" ".join(str(segment.get("text") or "") for segment in current))
        if text:
            metadata = dict(base_metadata)
            start = safe_number(current[0].get("start"))
            end = safe_number(current[-1].get("end"))
            metadata.update(
                {
                    "chunk_index": len(chunks),
                    "start": start,
                    "end": end,
                    "timestamp": format_timestamp(start),
                    "timestamp_url": metadata_url(base_metadata.get("url"), start),
                    "chunk_seconds": chunk_seconds(start, end),
                }
            )
            chunks.append(Chunk(chunk_id=f"{video_id}:{len(chunks)}", text=text, metadata=metadata))

        overlap: list[dict[str, Any]] = []
        overlap_len = 0
        for segment in reversed(current):
            overlap.insert(0, segment)
            overlap_len += len(str(segment.get("text") or ""))
            if overlap_len >= overlap_chars:
                break
        current = overlap
        current_len = overlap_len

    for segment in segments:
        text = clean_text(str(segment.get("text") or ""))
        if not text:
            continue
        if current and current_len + len(text) > chunk_chars:
            flush()
        current.append(segment)
        current_len += len(text) + 1

    current_len = 0
    flush()
    return chunks


def split_text(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        if end < len(text):
            boundary = text.rfind(". ", start, end)
            if boundary > start + chunk_chars * 0.6:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap_chars)
    return chunks


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    cleaned: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            cleaned[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


def safe_number(value: Any) -> int | float:
    if isinstance(value, (int, float)):
        return value
    return -1


def chunk_seconds(start: Any, end: Any) -> int | float:
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and start >= 0 and end >= start:
        return round(end - start, 3)
    return 0


def metadata_url(url: Any, start: Any) -> str:
    if not url:
        return ""
    url_text = str(url)
    if not isinstance(start, (int, float)) or start < 0:
        return url_text
    separator = "&" if "?" in url_text else "?"
    return f"{url_text}{separator}t={int(start)}"


def retrieve(args: argparse.Namespace, question: str) -> list[dict[str, Any]]:
    client = chroma_client(args.chroma_dir)
    collection = client.get_or_create_collection(
        args.collection,
        embedding_function=embedding_function(args),
    )
    result = collection.query(
        query_texts=[question],
        n_results=args.top_k,
        include=["documents", "metadatas", "distances"],
    )
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    rows = []
    for index, document in enumerate(documents):
        rows.append(
            {
                "text": document,
                "metadata": metadatas[index] or {},
                "distance": distances[index] if index < len(distances) else None,
            }
        )
    return rows


def search_mentions(
    args: argparse.Namespace,
    term: str,
    *,
    case_sensitive: bool = False,
    whole_word: bool = False,
    context_chars: int = 220,
    limit: int = 500,
    dedupe: bool = True,
) -> list[dict[str, Any]]:
    client = chroma_client(args.chroma_dir)
    collection = client.get_or_create_collection(args.collection)
    count = collection.count()
    if count == 0:
        return []

    pattern = mention_pattern(term, case_sensitive, whole_word)
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    page_size = 1000
    for offset in range(0, count, page_size):
        batch = collection.get(
            limit=page_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        documents = batch.get("documents") or []
        metadatas = batch.get("metadatas") or []
        ids = batch.get("ids") or []
        for index, document in enumerate(documents):
            metadata = metadatas[index] or {}
            chunk_id = ids[index] if index < len(ids) else ""
            for match in pattern.finditer(document):
                start, end = match.span()
                context = mention_context(document, start, end, context_chars)
                key = mention_dedupe_key(metadata, match.group(0), context)
                if dedupe and key in seen:
                    continue
                seen.add(key)
                mention = {
                    "chunk_id": chunk_id,
                    "term": match.group(0),
                    "context": context,
                    "match_start": start,
                    "match_end": end,
                    "metadata": metadata,
                }
                matches.append(mention)
                if len(matches) >= limit:
                    return matches
    return matches


def mention_dedupe_key(metadata: dict[str, Any], term: str, context: str) -> tuple[str, str, str, str]:
    normalized_context = re.sub(r"\W+", " ", context).casefold().strip()
    return (
        str(metadata.get("video_id", "")),
        str(metadata.get("start", "")),
        term.casefold(),
        normalized_context,
    )


def mention_pattern(term: str, case_sensitive: bool, whole_word: bool) -> re.Pattern[str]:
    escaped = re.escape(term.strip())
    if whole_word:
        escaped = rf"(?<!\w){escaped}(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(escaped, flags)


def mention_context(text: str, start: int, end: int, context_chars: int) -> str:
    left = max(0, start - context_chars)
    right = min(len(text), end + context_chars)
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(text) else ""
    return f"{prefix}{text[left:start]}**{text[start:end]}**{text[end:right]}{suffix}"


def mention_reference(mention: dict[str, Any]) -> str:
    metadata = mention.get("metadata") or {}
    stamp = format_timestamp(metadata.get("start"))
    stamp_text = f" @ {stamp}" if stamp else ""
    return (
        f"{metadata.get('title', '')}{stamp_text} | "
        f"{metadata.get('timestamp_url') or metadata.get('url', '')}"
    )


DEFAULT_GRAPH_JSON = Path(os.getenv("RAG_GRAPH", "data/grafos/grafo_unificado.json"))
_GRAPH_CACHE: dict[str, Any] = {}
_ORDEN_RELACION = {"explicita": 0, "implicita": 1, "coocurrencia": 2}


def _norm_label(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text)).strip()


def load_graph_index(path: Path | None = None) -> dict[str, Any] | None:
    """Carga (con cache por mtime) el grafo unificado como indice consultable."""
    graph_path = Path(path or DEFAULT_GRAPH_JSON)
    if not graph_path.exists():
        return None
    key = (str(graph_path), graph_path.stat().st_mtime)
    if _GRAPH_CACHE.get("key") == key:
        return _GRAPH_CACHE["index"]
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # archivo a medio reescribir (p. ej. refinamiento en curso): usa la
        # version cacheada si existe, o nada
        return _GRAPH_CACHE.get("index")
    nodes = {n["id"]: n for n in data.get("nodes", []) if n.get("id")}

    def rank(node: dict) -> float:
        try:
            return float((node.get("metrics") or {}).get("pagerank") or 0.0)
        except Exception:
            return 0.0

    alias: dict[str, str] = {}
    for node in nodes.values():
        for candidato in [node.get("label", "")] + list(node.get("aliases") or []):
            norm = _norm_label(candidato)
            if len(norm) < 4:
                continue
            previo = alias.get(norm)
            if previo is None or rank(nodes[previo]) < rank(node):
                alias[norm] = node["id"]

    adjacency: dict[str, list[dict]] = defaultdict(list)
    for edge in data.get("edges", []):
        if edge.get("type") not in _ORDEN_RELACION:
            continue
        adjacency[edge.get("source")].append(edge)
        adjacency[edge.get("target")].append(edge)
    for edges in adjacency.values():
        edges.sort(key=lambda e: (_ORDEN_RELACION.get(e.get("type"), 9), -float(e.get("confidence") or 0)))

    index = {"nodes": nodes, "alias": alias, "adjacency": adjacency, "rank": rank}
    _GRAPH_CACHE.update({"key": key, "index": index})
    return index


def graph_context(
    question: str,
    path: Path | None = None,
    max_entities: int = 4,
    max_relations: int = 6,
    max_chars: int = 2400,
) -> str:
    """Bloque de conocimiento del grafo para las entidades mencionadas en la pregunta.

    Devuelve cadena vacia si no hay grafo o la pregunta no menciona entidades.
    """
    index = load_graph_index(path)
    if index is None:
        return ""
    qnorm = _norm_label(question)
    hits = [a for a in index["alias"] if a in qnorm and re.search(rf"(?<!\w){re.escape(a)}(?!\w)", qnorm)]
    hits.sort(key=len, reverse=True)
    elegidos: list[str] = []
    node_ids: list[str] = []
    for a in hits:
        if any(a in mayor for mayor in elegidos):
            continue
        elegidos.append(a)
        nid = index["alias"][a]
        if nid not in node_ids:
            node_ids.append(nid)
    node_ids.sort(key=lambda nid: index["rank"](index["nodes"][nid]), reverse=True)

    lines: list[str] = []
    total = 0
    for nid in node_ids[:max_entities]:
        node = index["nodes"][nid]
        partes = []
        for pos, edge in enumerate(index["adjacency"].get(nid, [])[:max_relations]):
            otro_id = edge["target"] if edge.get("source") == nid else edge.get("source")
            otro = index["nodes"].get(otro_id)
            if otro is None:
                continue
            flecha = f"{edge.get('label', 'relacionado con')} → {otro.get('label')}" if edge.get("source") == nid \
                else f"{otro.get('label')} → {edge.get('label', 'relacionado con')} → esta entidad"
            if pos < 2 and edge.get("evidence"):
                flecha += f" (\"{str(edge['evidence'])[:150]}\")"
            partes.append(flecha)
        if not partes:
            continue
        line = f"- {node.get('label')} ({node.get('type', 'OTRO')}): " + "; ".join(partes)
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


def build_prompt(question: str, contexts: list[dict[str, Any]], extra_instructions: str = "", conocimiento: str = "") -> str:
    blocks = []
    for index, context in enumerate(contexts, start=1):
        meta = context["metadata"]
        blocks.append(
            f"[S{index}] {meta.get('video_id', '')} @ {format_timestamp(meta.get('start'))}\n"
            f"Titulo: {meta.get('title', '')}\n"
            f"Fecha: {format_upload_date(str(meta.get('upload_date', '')))}\n"
            f"Fuente transcript: {meta.get('transcript_source', '')}\n"
            f"URL: {meta.get('timestamp_url') or meta.get('url', '')}\n"
            f"Texto: {context['text']}"
        )

    extra = f"{extra_instructions}\n" if extra_instructions else ""
    kb = ""
    if conocimiento:
        kb = (
            "Conocimiento estructurado del corpus completo (grafo de entidades y relaciones; "
            "úsalo para conectar hechos y dar contexto, pero cita [S#] para las afirmaciones textuales):\n"
            f"{conocimiento}\n\n"
        )
    return (
        "Eres un asistente que responde preguntas usando exclusivamente transcripciones de videos.\n"
        "Responde en español. Si el contexto no contiene la respuesta, dilo claramente.\n"
        "Cita las fuentes usando etiquetas como [S1], [S2]. No inventes datos fuera del contexto.\n"
        f"{extra}\n"
        f"{kb}"
        f"Pregunta:\n{question}\n\n"
        f"Contexto recuperado:\n{chr(10).join(blocks)}\n\n"
        "Respuesta:"
    )


def answer_with_llm(args: argparse.Namespace, prompt: str) -> str:
    model = args.model or default_model(args.provider)
    if args.provider == "openai":
        return answer_openai(model, prompt, args.temperature)
    if args.provider == "gemini":
        return answer_gemini(model, prompt, args.temperature)
    if args.provider == "ollama":
        return answer_ollama(args.ollama_host, model, prompt, args.temperature)
    raise ValueError(f"Proveedor no soportado: {args.provider}")


def default_model(provider: str) -> str:
    return {
        "openai": "gpt-4.1-mini",
        "gemini": "gemini-2.5-flash",
        "ollama": "gemma4:12b-mlx",
    }[provider]


def answer_openai(model: str, prompt: str, temperature: float) -> str:
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": "Responde con precision y citas."},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


def answer_gemini(model: str, prompt: str, temperature: float) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=temperature),
    )
    return response.text or ""


def answer_ollama(host: str, model: str, prompt: str, temperature: float) -> str:
    host = normalize_ollama_host(host)
    response = requests.post(
        f"{host.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=600,
    )
    response.raise_for_status()
    return response.json().get("response", "")


def run_question(args: argparse.Namespace, question: str) -> None:
    contexts = retrieve(args, question)
    if not contexts:
        raise SystemExit("No encontre contexto. Ejecuta primero: python scripts/rag_transcripts.py index")
    if args.show_context:
        print_contexts(contexts)

    conocimiento = graph_context(question)
    if conocimiento and args.show_context:
        print(f"\nConocimiento del grafo:\n{conocimiento}\n")

    print(answer_with_llm(args, build_prompt(question, contexts, conocimiento=conocimiento)).strip())
    print("\nFuentes:")
    for index, context in enumerate(contexts, start=1):
        meta = context["metadata"]
        stamp = format_timestamp(meta.get("start"))
        stamp_text = f" @ {stamp}" if stamp else ""
        print(f"- [S{index}] {meta.get('title', '')}{stamp_text} {meta.get('timestamp_url') or meta.get('url', '')}")


def run_mentions(args: argparse.Namespace, term: str) -> None:
    matches = search_mentions(
        args,
        term,
        case_sensitive=args.case_sensitive,
        whole_word=args.whole_word,
        context_chars=args.context_chars,
        limit=args.limit,
        dedupe=not args.no_dedupe,
    )
    print(f"menciones encontradas: {len(matches)}")
    for index, match in enumerate(matches, start=1):
        print(f"\n[M{index}] {mention_reference(match)}")
        print(match["context"])


def print_contexts(contexts: list[dict[str, Any]]) -> None:
    print("Contexto recuperado:")
    for index, context in enumerate(contexts, start=1):
        meta = context["metadata"]
        print(f"\n[S{index}] {meta.get('title', '')} ({meta.get('video_id', '')})")
        print(context["text"][:1200])


def format_upload_date(value: str) -> str:
    if len(value) == 8:
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def format_timestamp(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    if value < 0:
        return ""
    seconds = int(value)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def main() -> int:
    args = parse_args()
    if args.command == "index":
        total = rebuild_index(args)
        print(f"indice ChromaDB listo: {args.chroma_dir} / {args.collection} ({total} chunks)")
        return 0

    if args.command == "ask":
        run_question(args, " ".join(args.question))
        return 0

    if args.command == "mentions":
        run_mentions(args, " ".join(args.term))
        return 0

    if args.command == "chat":
        print("Modo chat. Escribe 'salir' para terminar.")
        while True:
            try:
                question = input("\nPregunta> ").strip()
            except EOFError:
                break
            if not question or question.lower() in {"salir", "exit", "quit"}:
                break
            run_question(args, question)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
