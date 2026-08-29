#!/usr/bin/env python3
"""Construye grafos de conocimiento de las transcripciones usando textgraph.

Puente entre este corpus y la herramienta textgraph
(https://github.com/lapeyus/textgraph, instalada en ``--textgraph-dir``):
selecciona transcripciones del JSONL, las concatena con un encabezado de
procedencia por video (título, fecha, URL) y las pasa por ``cli.py`` de
textgraph, que extrae entidades y relaciones con evidencia literal y escribe
en ``data/grafos/`` el grafo en JSON, el visor HTML autónomo y el paquete
para LLM.

Selección de videos (combinables):
    --videos ID [ID...]      por video_id
    --date-after / --date-before   por rango de fecha de subida
    --search "término"       por substring del título (insensible a mayúsculas)
    --last N                 los N más recientes de la selección

Motores de textgraph: ``codex-cli`` (default aquí; CLI de OpenAI Codex con
sesión iniciada), ``gemini-cli`` (CLI de Google Gemini), ``claude-cli``,
``api``, ``auto`` o ``local`` (heurístico, gratis, menor calidad). Para
transcripciones largas conviene un modelo medio (``gpt-5.6-terra`` en codex,
``gemini-3-flash-preview`` en gemini, ``claude-sonnet-5`` en claude),
siguiendo el criterio del README de textgraph.

Ejemplos:
    python scripts/knowledge_graph.py --last 1
    python scripts/knowledge_graph.py --search "100 días" --titulo "Los 100 días"
    python scripts/knowledge_graph.py --videos kpgqIWb_9_k --motor local
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
DEFAULT_TRANSCRIPTS = Path("data/presidencia_transcripts.jsonl")
DEFAULT_OUTPUT_DIR = Path("data/grafos")
MAX_CHARS = 398_000  # textgraph admite 400k por extracción (los encabezados ya están contados)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grafo de conocimiento de transcripciones con textgraph.")
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--textgraph-dir", type=Path, default=DEFAULT_TEXTGRAPH_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--videos", nargs="+", help="video_id(s) a incluir.")
    parser.add_argument("--date-after", help="Fecha mínima YYYY-MM-DD.")
    parser.add_argument("--date-before", help="Fecha máxima YYYY-MM-DD.")
    parser.add_argument("--search", help="Substring del título, insensible a mayúsculas.")
    parser.add_argument("--last", type=int, help="Limitar a los N videos más recientes de la selección.")
    parser.add_argument("--titulo", default="", help="Título del grafo; por defecto se deriva de la selección.")
    parser.add_argument("--motor", default="codex-cli", choices=["auto", "claude-cli", "codex-cli", "gemini-cli", "antigravity-cli", "ollama", "api", "local"])
    parser.add_argument("--modelo", default=None, help="Modelo del motor; si se omite, textgraph usa su default por motor (codex-cli: gpt-5.6-sol; gemini-cli: gemini-3-pro-preview). Ignorado por el motor local.")
    parser.add_argument("--fragmento", type=int, default=6000, help="Caracteres por fragmento. Default: 6000.")
    parser.add_argument("--paralelo", type=int, default=2)
    parser.add_argument("--sin-exhaustividad", action="store_true")
    parser.add_argument("--no-guardar", action="store_true", help="No registrar el grafo en el store de textgraph.")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar la selección y el tamaño, sin extraer.")
    return parser.parse_args()


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"No existe {path}. Ejecuta primero la cosecha.")
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def iso_date(value: str | None) -> str:
    value = value or ""
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else "¿fecha?"


def select_records(records: list[dict], args: argparse.Namespace) -> list[dict]:
    chosen = records
    if args.videos:
        wanted = set(args.videos)
        chosen = [r for r in chosen if r.get("video_id") in wanted]
    if args.date_after:
        limit = args.date_after.replace("-", "")
        chosen = [r for r in chosen if (r.get("upload_date") or "") >= limit]
    if args.date_before:
        limit = args.date_before.replace("-", "")
        chosen = [r for r in chosen if (r.get("upload_date") or "") <= limit]
    if args.search:
        needle = args.search.lower()
        chosen = [r for r in chosen if needle in (r.get("title") or "").lower()]
    chosen.sort(key=lambda r: (r.get("upload_date") or "", r.get("video_id") or ""), reverse=True)
    if args.last:
        chosen = chosen[: args.last]
    return chosen


def build_text(selection: list[dict]) -> str:
    blocks = []
    for record in selection:
        transcript = (record.get("transcript") or {}).get("text") or ""
        if not transcript.strip():
            continue
        header = (
            f"=== {record.get('title') or record.get('video_id')} "
            f"({iso_date(record.get('upload_date'))}) — {record.get('url', '')} ==="
        )
        blocks.append(f"{header}\n{transcript.strip()}")
    return "\n\n".join(blocks)


def default_title(selection: list[dict]) -> str:
    if len(selection) == 1:
        record = selection[0]
        return f"{(record.get('title') or record.get('video_id'))[:70]} ({iso_date(record.get('upload_date'))})"
    dates = sorted(iso_date(r.get("upload_date")) for r in selection)
    return f"Corpus Presidencia {dates[0]} a {dates[-1]} ({len(selection)} videos)"


def main() -> int:
    args = parse_args()
    cli = args.textgraph_dir / "cli.py"
    if not cli.exists():
        raise SystemExit(
            f"No se encontró textgraph en {args.textgraph_dir} (falta cli.py). "
            "Indica la ruta con --textgraph-dir o la variable TEXTGRAPH_DIR."
        )

    selection = select_records(load_records(args.transcripts), args)
    if not selection:
        raise SystemExit("La selección no produjo ningún video con transcripción.")
    text = build_text(selection)
    titulo = args.titulo or default_title(selection)

    print(f"Videos seleccionados: {len(selection)}")
    for record in selection:
        print(f"  - {iso_date(record.get('upload_date'))} | {(record.get('title') or '')[:70]}")
    estimated_calls = max(1, len(text) // args.fragmento)
    print(f"Texto combinado: {len(text):,} caracteres (~{estimated_calls} fragmentos/llamadas al modelo)")
    print(f"Título del grafo: {titulo}")
    if len(text) > MAX_CHARS:
        raise SystemExit(
            f"La selección supera el límite de {MAX_CHARS:,} caracteres de textgraph. "
            "Reduce la selección (menos videos, --last N) o sube --fragmento."
        )
    if args.dry_run:
        return 0

    command = [
        sys.executable,
        str(cli),
        "-",
        "-o", str(args.output_dir),
        "-t", titulo,
        "-m", args.motor,
        "--fragmento", str(args.fragmento),
        "--paralelo", str(args.paralelo),
    ]
    if args.modelo and args.motor != "local":
        command.extend(["--modelo", args.modelo])
    if args.sin_exhaustividad:
        command.append("--sin-exhaustividad")
    if args.no_guardar:
        command.append("--no-guardar")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nEjecutando textgraph (motor {args.motor})...\n", flush=True)
    result = subprocess.run(command, input=text, text=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
