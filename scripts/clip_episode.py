#!/usr/bin/env python3
"""Montaje exhaustivo de un episodio: todas las veces que la presidenta lo menciona.

Dado un episodio descrito en lenguaje natural y unos términos de búsqueda,
(1) reúne TODAS las menciones candidatas del corpus (varios términos,
deduplicadas por momento), (2) un modelo confirma cuáles refieren realmente
al episodio — aunque no usen los mismos términos —, (3) cada mención
confirmada se alinea al segundo contra los segmentos del transcript y se
corta de YouTube, y (4) los cortes se unen en un montaje cronológico.

Salida en ``data/clips/<nombre>_<fecha>/`` (clips, ``compilacion.mp4``,
``manifest.json`` y ``respuesta.md``), visible en la página Clips.

Uso:
    python scripts/clip_episode.py \\
        --nombre episodio_microfono \\
        --descripcion "la magistrada Patricia Solano le arrebató/quitó un micrófono durante una conferencia de prensa tras una reunión con el Poder Judicial" \\
        --terminos "micrófono" "arrebat" "Patricia Solano"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))

from scripts import make_clips as mc  # noqa: E402
from scripts import rag_transcripts as rag  # noqa: E402
from textgraph.llm import ErrorMotor, crear_motor  # noqa: E402

PROMPT_FILTRO = """La presidenta de Costa Rica Laura Fernández se ha referido varias veces a este episodio:
«{descripcion}»

Abajo hay fragmentos numerados de transcripciones (de sus conferencias, cadenas y entrevistas).
Indica cuáles fragmentos SÍ se refieren a ese episodio (relatándolo, comentándolo o respondiendo
sobre él, aunque no usen las mismas palabras), y cuáles no tienen relación.

Devuelve UNICAMENTE: {{"episodio": [números de los fragmentos que SÍ refieren al episodio]}}

Fragmentos:
{fragmentos}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Montaje de todas las menciones de un episodio.")
    parser.add_argument("--nombre", required=True, help="Nombre corto para la carpeta de salida.")
    parser.add_argument("--descripcion", required=True, help="Descripción del episodio en lenguaje natural.")
    parser.add_argument("--terminos", nargs="+", required=True, help="Términos de búsqueda para reunir candidatos.")
    parser.add_argument("--motor", default="gemini-cli", choices=["gemini-cli", "codex-cli", "antigravity-cli", "claude-cli", "ollama", "api"])
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--margin", type=float, default=3.0)
    parser.add_argument("--min-seconds", type=float, default=10.0)
    parser.add_argument("--max-seconds", type=float, default=75.0)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--sin-montaje", action="store_true")
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def candidatos(terminos: list[str]) -> list[dict]:
    rag_args = argparse.Namespace(
        chroma_dir=rag.DEFAULT_CHROMA_DIR, collection=rag.DEFAULT_COLLECTION,
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL),
    )
    vistos: set[tuple[str, str]] = set()
    lista: list[dict] = []
    for termino in terminos:
        for match in rag.search_mentions(rag_args, termino, context_chars=340, limit=400):
            metadata = match.get("metadata") or {}
            clave = (str(metadata.get("video_id")), str(metadata.get("start")))
            if clave in vistos:
                continue
            vistos.add(clave)
            lista.append(
                {
                    "n": len(lista) + 1,
                    "contexto": re.sub(r"\*\*", "", str(match.get("context") or "")).strip(),
                    "metadata": metadata,
                }
            )
    return lista


def confirmar(lista: list[dict], descripcion: str, motor) -> list[dict]:
    confirmados: list[dict] = []
    for inicio in range(0, len(lista), 25):
        lote = lista[inicio:inicio + 25]
        fragmentos = "\n".join(
            f"{c['n']}. ({c['metadata'].get('title', '')[:45]}, {rag.format_upload_date(str(c['metadata'].get('upload_date', '')))}) «{c['contexto'][:300]}»"
            for c in lote
        )
        try:
            r = motor.json(PROMPT_FILTRO.format(descripcion=descripcion, fragmentos=fragmentos), max_tokens=2000)
        except ErrorMotor as exc:
            log(f"  lote {inicio // 25 + 1}: fallo del motor ({str(exc)[:100]}); lote omitido")
            continue
        numeros = {int(x) for x in (r.get("episodio") or []) if str(x).isdigit() or isinstance(x, int)}
        confirmados.extend(c for c in lote if c["n"] in numeros)
        log(f"  lote {inicio // 25 + 1}/{(len(lista) - 1) // 25 + 1}: {len([c for c in lote if c['n'] in numeros])} confirmados")
    return confirmados


def main() -> int:
    args = parse_args()
    lista = candidatos(args.terminos)
    log(f"candidatos únicos: {len(lista)} (términos: {', '.join(args.terminos)})")
    if not lista:
        raise SystemExit("Sin candidatos.")

    motor = crear_motor(args.motor, args.modelo)
    confirmados = confirmar(lista, args.descripcion, motor)
    log(f"menciones confirmadas del episodio: {len(confirmados)} · ${motor.coste:.2f}")
    if not confirmados:
        raise SystemExit("El modelo no confirmó ninguna mención.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path("data/clips") / f"{args.nombre}_{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    manifest_clips: list[dict] = []
    for index, c in enumerate(sorted(confirmados, key=lambda x: (x["metadata"].get("upload_date") or "", mc.slug(str(x["metadata"].get("start"))))), start=1):
        metadata = c["metadata"]
        video_id = str(metadata.get("video_id") or "")
        hint = None
        if isinstance(metadata.get("start"), (int, float)) and metadata["start"] >= 0:
            hint = (float(metadata["start"]), float(metadata.get("end") or metadata["start"]))
        aligned = mc.align_quote(mc.load_segments(video_id), c["contexto"], hint)
        if aligned is None:
            bounds = mc.fallback_bounds(metadata, args)
            score = None
        else:
            bounds = mc.clip_bounds(aligned[0], aligned[1], args)
            score = aligned[2]
        if bounds is None or mc.overlaps(manifest_clips, video_id, bounds):
            continue
        filename = f"{index:02d}_{video_id}_{int(bounds[0])}.mp4"
        log(f"  [{index}] {video_id} {rag.format_timestamp(bounds[0])} → {rag.format_timestamp(bounds[1])}")
        if mc.cut_clip(video_id, bounds, outdir / filename, args.height):
            manifest_clips.append(
                {
                    "file": filename,
                    "video_id": video_id,
                    "title": metadata.get("title", ""),
                    "upload_date": metadata.get("upload_date", ""),
                    "clip_start": round(bounds[0], 1),
                    "clip_end": round(bounds[1], 1),
                    "align_score": round(score, 3) if score is not None else None,
                    "timestamp": rag.format_timestamp(bounds[0]),
                    "timestamp_url": f"https://www.youtube.com/watch?v={video_id}&t={int(bounds[0])}",
                    "quote": c["contexto"][:400],
                }
            )

    compilation = None
    if not args.sin_montaje and len(manifest_clips) > 1:
        log("generando montaje cronológico...")
        ordenados = sorted(manifest_clips, key=lambda x: (x.get("upload_date") or "", x["clip_start"]))
        if mc.build_compilation(ordenados, outdir, outdir / "compilacion.mp4", rotulos=True):
            compilation = "compilacion.mp4"

    manifest = {
        "question": f"Episodio: {args.descripcion}",
        "mode": "episodio",
        "answer": f"Todas las menciones del episodio en el corpus: {len(manifest_clips)} momentos confirmados por el modelo entre {len(lista)} candidatos.",
        "created_at": datetime.now().astimezone().isoformat(),
        "clips": manifest_clips,
        "compilation": compilation,
        "orden": "fecha",
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# Episodio: {args.descripcion}", "", manifest["answer"], ""]
    for clip in manifest_clips:
        lines.append(f"- {clip['fecha'] if 'fecha' in clip else clip['upload_date']} · **{clip['title'][:60]}** @ {clip['timestamp']} — [ver]({clip['timestamp_url']})")
    (outdir / "respuesta.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"listo: {len(manifest_clips)} clips en {outdir}" + (f" · montaje: {compilation}" if compilation else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
