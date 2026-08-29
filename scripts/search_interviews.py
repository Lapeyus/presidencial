#!/usr/bin/env python3
"""Busca en YouTube entrevistas de la presidente Laura Fernández en medios, con validación manual.

Complementa al recolector del canal oficial: busca videos de OTROS canales
(medios de prensa) con entrevistas dadas por la presidente a partir de una
fecha, y los deja en una cola de validación humana antes de transcribirlos.

Flujo en tres pasos:

1. ``search``: ejecuta varias búsquedas de YouTube vía yt-dlp, descarta lo ya
   conocido (records, state.sqlite, candidatos previos), lo que no calza con
   el rango de fechas, lo demasiado corto, lo poco relevante y lo que venga
   del propio canal de la Presidencia. Los candidatos nuevos quedan como
   ``pending`` en ``data/interview_candidates.json``.
2. ``review`` (interactivo) o ``approve``/``reject`` (por lote): el usuario
   valida cada candidato. Nada se procesa sin aprobación explícita.
3. ``process``: los aprobados pasan por el MISMO pipeline del recolector
   (``process_video`` de collect_youtube_transcripts: subtítulos de YouTube
   o mlx-whisper, record JSON, estado en SQLite) y se reconstruye el JSONL.

Uso típico:

    python scripts/search_interviews.py search
    python scripts/search_interviews.py review
    python scripts/search_interviews.py process
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import collect_youtube_transcripts as collector
except ImportError:  # ejecutado como `python scripts/search_interviews.py`
    import collect_youtube_transcripts as collector


DEFAULT_QUERIES = [
    "Laura Fernández entrevista",
    "presidente Laura Fernández entrevista",
    "Laura Fernández Costa Rica entrevista",
]
RELEVANCE_TERMS = ("laura", "fernández", "fernandez", "presidente", "presidenta", "mandataria")
PRESIDENCIA_CHANNEL_ID = "UCLXHie0velori7_dqu8w_bQ"
DEFAULT_CANDIDATES = Path("data/interview_candidates.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Busca entrevistas de la presidente en medios y las procesa tras validación manual."
    )
    parser.add_argument("--data-dir", type=Path, default=collector.DEFAULT_DATA_DIR)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Buscar candidatos nuevos en YouTube.")
    search.add_argument("--query", action="append", help="Consulta de búsqueda; repetible. Default: tres consultas sobre entrevistas de Laura Fernández.")
    search.add_argument("--max-per-query", type=int, default=30, help="Resultados máximos por consulta. Default: 30.")
    search.add_argument("--date-after", default="2026-05-01", help="Fecha mínima de publicación, YYYY-MM-DD. Default: 2026-05-01.")
    search.add_argument("--date-before", help="Fecha máxima de publicación, YYYY-MM-DD.")
    search.add_argument("--min-duration", type=int, default=180, help="Duración mínima en segundos para descartar clips/shorts. Default: 180.")
    add_ytdlp_args(search)

    review = subparsers.add_parser("review", help="Validar candidatos pendientes de forma interactiva.")
    review.add_argument("--list", action="store_true", help="Solo listar pendientes, sin preguntar.")

    approve = subparsers.add_parser("approve", help="Aprobar candidatos por video_id.")
    approve.add_argument("video_ids", nargs="+")

    reject = subparsers.add_parser("reject", help="Rechazar candidatos por video_id.")
    reject.add_argument("video_ids", nargs="+")

    process = subparsers.add_parser("process", help="Transcribir los candidatos aprobados con el pipeline del recolector.")
    process.add_argument(
        "--whisper",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Usar mlx-whisper si no hay subtítulos. Default: activado.",
    )
    process.add_argument("--whisper-model", default="mlx-community/whisper-large-v3-turbo")
    process.add_argument("--keep-audio", action="store_true")
    process.add_argument("--force", action="store_true", help="Reprocesar aunque el video ya tenga estado done.")
    add_ytdlp_args(process)

    return parser.parse_args()


def add_ytdlp_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cookies-from-browser", default="chrome")
    parser.add_argument("--no-browser-cookies", action="store_true")
    parser.add_argument("--js-runtimes", default="")
    parser.add_argument("--extractor-args", default="youtube:player_client=default,tv_simply")
    parser.add_argument("--no-check-certificates", action="store_true")


def load_candidates(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("updated_at", None)
    data.setdefault("candidates", [])
    data.setdefault("discarded", {})
    return data


def save_candidates(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["candidates"].sort(key=lambda c: (c.get("upload_date") or "", c.get("video_id") or ""), reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def known_video_ids(args: argparse.Namespace, data: dict[str, Any]) -> set[str]:
    known = {c["video_id"] for c in data["candidates"]}
    known.update(data.get("discarded", {}))
    known.update(path.stem for path in (args.data_dir / "records").glob("*.json"))
    state_path = args.data_dir / "state.sqlite"
    if state_path.exists():
        db = sqlite3.connect(state_path)
        try:
            known.update(row[0] for row in db.execute("SELECT video_id FROM videos"))
        finally:
            db.close()
    return known


def looks_relevant(title: str) -> bool:
    lowered = title.lower()
    return any(term in lowered for term in RELEVANCE_TERMS)


def format_duration(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "?"
    secs = int(seconds)
    hours, secs = divmod(secs, 3600)
    minutes, secs = divmod(secs, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def candidate_line(cand: dict[str, Any]) -> str:
    date = collector.parse_upload_date(cand.get("upload_date"))
    date_text = date.isoformat() if date else "????-??-??"
    return (
        f"{date_text} | {format_duration(cand.get('duration')):>7} | "
        f"{(cand.get('channel') or '?')[:28]:28} | {(cand.get('title') or '')[:70]}\n"
        f"    {cand.get('url', '')}"
    )


def cmd_search(args: argparse.Namespace) -> int:
    data = load_candidates(args.candidates)
    known = known_video_ids(args, data)
    start_date = collector.parse_iso_date(args.date_after)
    end_date = collector.parse_iso_date(args.date_before)
    queries = args.query or DEFAULT_QUERIES

    hits: dict[str, dict[str, Any]] = {}
    stats = {"listados": 0, "ya_conocidos": 0, "irrelevantes": 0, "muy_cortos": 0}
    for query in queries:
        print(f"Buscando: {query!r} (max {args.max_per_query})")
        try:
            result = collector.run_ytdlp_json(
                args,
                "--flat-playlist",
                "--dump-single-json",
                "--ignore-errors",
                f"ytsearch{args.max_per_query}:{query}",
            )
        except subprocess.CalledProcessError as exc:
            print(f"warning: fallo la búsqueda {query!r}: {collector.command_error_detail(exc)}", file=sys.stderr)
            continue
        for entry in result.get("entries") or []:
            video_id = collector.extract_video_id(entry)
            if not video_id:
                continue
            stats["listados"] += 1
            if video_id in known or video_id in hits:
                stats["ya_conocidos"] += 1
                continue
            title = entry.get("title") or ""
            if title and not looks_relevant(title):
                stats["irrelevantes"] += 1
                continue
            duration = entry.get("duration")
            if isinstance(duration, (int, float)) and duration < args.min_duration:
                stats["muy_cortos"] += 1
                continue
            hits[video_id] = {"query": query, "flat": entry}

    print(f"\nCandidatos a validar con metadata: {len(hits)}")
    added: list[dict[str, Any]] = []
    for video_id, hit in hits.items():
        metadata = collector.fetch_metadata_for_filter(args, video_id)
        if metadata is None:
            data["discarded"][video_id] = "metadata_inaccesible"
            continue
        if metadata.get("channel_id") == PRESIDENCIA_CHANNEL_ID:
            data["discarded"][video_id] = "canal_presidencia"
            continue
        uploaded = collector.parse_upload_date(metadata.get("upload_date"))
        if not collector.date_in_range(uploaded, start_date, end_date):
            data["discarded"][video_id] = f"fuera_de_rango:{metadata.get('upload_date')}"
            continue
        duration = metadata.get("duration")
        if isinstance(duration, (int, float)) and duration < args.min_duration:
            data["discarded"][video_id] = f"muy_corto:{int(duration)}s"
            continue
        candidate = {
            "video_id": video_id,
            "title": metadata.get("title"),
            "channel": metadata.get("channel"),
            "channel_id": metadata.get("channel_id"),
            "upload_date": metadata.get("upload_date"),
            "duration": duration,
            "view_count": metadata.get("view_count"),
            "url": metadata.get("webpage_url") or collector.VIDEO_URL.format(video_id=video_id),
            "status": "pending",
            "found_by_query": hit["query"],
            "found_at": datetime.now(timezone.utc).isoformat(),
        }
        data["candidates"].append(candidate)
        added.append(candidate)
        print(f"  + {candidate_line(candidate)}")

    save_candidates(args.candidates, data)
    pending = sum(1 for c in data["candidates"] if c["status"] == "pending")
    print(f"\nresumen búsqueda: {json.dumps(stats, ensure_ascii=False)}")
    print(f"candidatos nuevos: {len(added)} | pendientes de validar en total: {pending}")
    if pending:
        print("Valida con: python scripts/search_interviews.py review")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    data = load_candidates(args.candidates)
    pending = [c for c in data["candidates"] if c["status"] == "pending"]
    if not pending:
        print("No hay candidatos pendientes de validar.")
        return 0
    if args.list or not sys.stdin.isatty():
        print(f"Candidatos pendientes: {len(pending)}\n")
        for cand in pending:
            print(candidate_line(cand))
            print()
        if not args.list:
            print("(stdin no es interactivo; usa approve/reject por video_id)")
        return 0

    print(f"Candidatos pendientes: {len(pending)}. [a]probar / [r]echazar / Enter=saltar / [q]uitar\n")
    for index, cand in enumerate(pending, start=1):
        print(f"[{index}/{len(pending)}] {candidate_line(cand)}")
        try:
            answer = input("  decisión> ").strip().lower()
        except EOFError:
            break
        if answer == "q":
            break
        if answer == "a":
            cand["status"] = "approved"
        elif answer == "r":
            cand["status"] = "rejected"
        else:
            continue
        cand["decided_at"] = datetime.now(timezone.utc).isoformat()
        save_candidates(args.candidates, data)

    approved = sum(1 for c in data["candidates"] if c["status"] == "approved")
    print(f"\nAprobados listos para procesar: {approved}")
    if approved:
        print("Procesa con: python scripts/search_interviews.py process")
    return 0


def set_status_bulk(args: argparse.Namespace, status: str) -> int:
    data = load_candidates(args.candidates)
    by_id = {c["video_id"]: c for c in data["candidates"]}
    missing = [vid for vid in args.video_ids if vid not in by_id]
    for vid in args.video_ids:
        if vid in by_id:
            by_id[vid]["status"] = status
            by_id[vid]["decided_at"] = datetime.now(timezone.utc).isoformat()
    save_candidates(args.candidates, data)
    print(f"{status}: {len(args.video_ids) - len(missing)} candidato(s).")
    if missing:
        print(f"warning: no encontrados: {', '.join(missing)}", file=sys.stderr)
    return 1 if missing else 0


def cmd_process(args: argparse.Namespace) -> int:
    data = load_candidates(args.candidates)
    approved = [c for c in data["candidates"] if c["status"] == "approved"]
    if not approved:
        print("No hay candidatos aprobados sin procesar. Valida primero con: python scripts/search_interviews.py review")
        return 0

    args.metadata_cache = {}
    args.retry_failed = True
    args.retry_no_transcript_after_hours = 0.0
    args.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.data_dir / "state.sqlite")
    collector.ensure_schema(db)

    counts: dict[str, int] = {}
    for index, cand in enumerate(approved, start=1):
        print(f"\n[{index}/{len(approved)}] {cand.get('channel')} | {cand.get('title')}")
        outcome = collector.process_video(db, cand["video_id"], args, None, None)
        counts[outcome] = counts.get(outcome, 0) + 1
        cand["last_outcome"] = outcome
        if outcome in {"done", "skipped_done"}:
            cand["status"] = "processed"
            cand["processed_at"] = datetime.now(timezone.utc).isoformat()
        save_candidates(args.candidates, data)

    total = collector.rebuild_jsonl(args.data_dir / "records", args.data_dir / "presidencia_transcripts.jsonl")
    print(f"\nresumen: {json.dumps(counts, ensure_ascii=False, sort_keys=True)}")
    print(f"archivo final: {args.data_dir / 'presidencia_transcripts.jsonl'} ({total} registros)")
    print("Recuerda reconstruir el índice: python scripts/rag_transcripts.py index")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "search":
        return cmd_search(args)
    if args.command == "review":
        return cmd_review(args)
    if args.command == "approve":
        return set_status_bulk(args, "approved")
    if args.command == "reject":
        return set_status_bulk(args, "rejected")
    if args.command == "process":
        return cmd_process(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
