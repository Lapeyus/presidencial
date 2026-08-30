#!/usr/bin/env python3
"""Aumenta los perfiles con referencias exhaustivas y tono por declaración.

Dos pasadas sobre ``data/perfiles_discurso.json``:

1. **Referencias completas** (sin LLM, solo recuperación): para cada persona
   y tema recolecta TODAS las menciones del corpus (hasta ``--tope``,
   deduplicadas por chunk) y las guarda en ``referencias`` — cita compacta,
   video, fecha, timestamp y URL — actualizando el conteo de menciones para
   que el pie «basado en N menciones» coincida exactamente con la tabla.
2. **Tono del trato** (con LLM, por defecto gemini-cli): etiqueta cada
   declaración de persona como ``positivo``/``negativo``/``neutro`` según el
   trato de la presidenta, lo que habilita las tablas comparativas y los
   balances. ``--sin-tono`` la omite; las declaraciones que fallen quedan
   como ``neutro``.

Uso:
    python scripts/augment_references.py
    python scripts/augment_references.py --sin-tono --tope 1500
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))

from scripts import discourse_profiles as dp  # noqa: E402
from scripts import rag_transcripts as rag  # noqa: E402
from textgraph.llm import ErrorMotor, crear_motor  # noqa: E402

OUTPUT = dp.OUTPUT
TONOS = ("positivo", "negativo", "neutro")

PROMPT_TONO = """Cada línea numerada resume algo que la presidenta de Costa Rica dijo o hizo respecto a \
«{nombre}». Clasifica el TONO DEL TRATO de la presidenta hacia esa persona/colectivo en cada línea:
- "positivo": elogio, agradecimiento, defensa, colaboración, anuncio favorable;
- "negativo": reproche, culpa, confrontación, crítica, exigencia hostil;
- "neutro": mención factual sin carga.

Devuelve UNICAMENTE un JSON: {{"tonos": [{{"i": 1, "tono": "positivo"}}, ...]}} con una entrada por línea.

Líneas:
{lineas}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Referencias exhaustivas y tono por declaración.")
    parser.add_argument("--motor", default="gemini-cli", choices=["gemini-cli", "codex-cli", "antigravity-cli", "claude-cli", "ollama", "api"])
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--tope", type=int, default=1000, help="Máximo de referencias por entidad. Default: 1000.")
    parser.add_argument("--sin-tono", action="store_true")
    parser.add_argument("--sin-referencias", action="store_true")
    parser.add_argument("--max-usd", type=float, default=8.0)
    parser.add_argument("--chroma-dir", type=Path, default=rag.DEFAULT_CHROMA_DIR)
    parser.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    parser.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL))
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def compactar(evidencias: list[dict]) -> list[dict]:
    return [
        {
            "cita": e["cita"][:220],
            "video": e["video"][:80],
            "fecha": e["fecha"],
            "timestamp": e["timestamp"],
            "url": e["url"],
        }
        for e in evidencias
    ]


def referencias(salida: dict, rag_args: argparse.Namespace, tope: int) -> None:
    entidades = [(p, True) for p in salida["perfiles"]] + [(t, False) for t in salida["temas"]]
    for numero, (entidad, es_persona) in enumerate(entidades, start=1):
        alias = entidad.get("alias") or [entidad["nombre"]]
        refs = dp.evidencias_de({"nombre": entidad["nombre"], "alias": alias}, rag_args, tope)
        entidad["referencias"] = compactar(refs)
        if es_persona:
            entidad["menciones_analizadas"] = len(refs)
        else:
            entidad["menciones"] = len(refs)
        log(f"[{numero}/{len(entidades)}] {entidad['nombre']}: {len(refs)} referencias")


def tono(salida: dict, motor, max_usd: float) -> None:
    for perfil in salida["perfiles"]:
        pendientes = [d for d in perfil["declaraciones"] if d.get("tono") not in TONOS]
        if not pendientes:
            continue
        if motor.coste >= max_usd:
            log("tope de gasto del tono alcanzado; el resto queda neutro")
            break
        log(f"tono: {perfil['nombre']} ({len(pendientes)} declaraciones) · ${motor.coste:.2f}")
        for inicio in range(0, len(pendientes), 50):
            lote = pendientes[inicio:inicio + 50]
            lineas = "\n".join(f"{i + 1}. {d['sintesis'][:220]}" for i, d in enumerate(lote))
            try:
                r = motor.json(PROMPT_TONO.format(nombre=perfil["nombre"], lineas=lineas), max_tokens=4000)
            except ErrorMotor as exc:
                log(f"  fallo del motor ({str(exc)[:100]}); lote queda neutro")
                for d in lote:
                    d["tono"] = "neutro"
                continue
            por_indice = {}
            for item in r.get("tonos") or []:
                if isinstance(item, dict) and str(item.get("tono", "")).lower() in TONOS:
                    por_indice[item.get("i")] = str(item["tono"]).lower()
            for i, d in enumerate(lote):
                d["tono"] = por_indice.get(i + 1, "neutro")


def main() -> int:
    args = parse_args()
    salida = json.loads(OUTPUT.read_text(encoding="utf-8"))
    rag_args = argparse.Namespace(
        chroma_dir=args.chroma_dir, collection=args.collection,
        ollama_host=args.ollama_host, embedding_model=args.embedding_model,
    )

    if not args.sin_referencias:
        referencias(salida, rag_args, args.tope)
        OUTPUT.write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log("referencias guardadas")

    if not args.sin_tono:
        motor = crear_motor(args.motor, args.modelo)
        tono(salida, motor, args.max_usd)
        OUTPUT.write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"tonos guardados · coste ${motor.coste:.2f} ({motor.llamadas} llamadas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
