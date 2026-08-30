#!/usr/bin/env python3
"""Índice exhaustivo de personas del discurso: todas, con perfil.

Recorre TODOS los nodos PERSONA del grafo unificado (variantes del mismo
nombre agrupadas), y para cada persona construye un perfil básico sin costo
de LLM: descripción del grafo, sus relaciones más confiables (con cita de
evidencia) y **todas sus referencias** en el corpus (menciones con video,
fecha, timestamp y URL; tope ``--tope-referencias``). Los 40 perfiles
completos de ``perfiles_discurso.json`` se marcan como ``destacado``.

Salida progresiva y reanudable: ``data/personas_index.json``.

Uso:
    python scripts/build_people_index.py
    python scripts/build_people_index.py --minimo-rango 200   # prueba corta
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import discourse_profiles as dp  # noqa: E402
from scripts import rag_transcripts as rag  # noqa: E402

GRAPH_JSON = Path("data/grafos/grafo_unificado.json")
PERFILES = Path("data/perfiles_discurso.json")
OUTPUT = Path("data/personas_index.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Índice exhaustivo de personas con perfil básico.")
    parser.add_argument("--tope-referencias", type=int, default=200)
    parser.add_argument("--max-relaciones", type=int, default=8)
    parser.add_argument("--minimo-rango", type=int, help="Procesar solo los primeros N grupos (pruebas).")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--chroma-dir", type=Path, default=rag.DEFAULT_CHROMA_DIR)
    parser.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    parser.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL))
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def grupos_de_personas() -> tuple[list[dict], dict[str, dict]]:
    data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in data["nodes"]}
    personas = [n for n in data["nodes"] if n.get("type") == "PERSONA" and not dp.es_la_presidenta(n.get("label", ""))]
    personas.sort(key=dp._rank, reverse=True)

    grupos: list[dict] = []
    for node in personas:
        toks = dp.tokens_de(node["label"])
        if not toks:
            continue
        destino = next((g for g in grupos if toks <= g["tokens"] or g["tokens"] <= toks), None)
        if destino is None:
            grupos.append({"tokens": toks, "nodos": [node], "rank": dp._rank(node)})
        else:
            destino["nodos"].append(node)
            destino["tokens"] = destino["tokens"] | toks
            destino["rank"] += dp._rank(node)
    grupos.sort(key=lambda g: g["rank"], reverse=True)

    edges_por_nodo: dict[str, list[dict]] = {}
    for e in data.get("edges", []):
        if e.get("type") == "estructural" or not e.get("evidence"):
            continue
        edges_por_nodo.setdefault(e.get("source"), []).append(e)
        edges_por_nodo.setdefault(e.get("target"), []).append(e)
    return grupos, {"nodes": nodes, "edges_por_nodo": edges_por_nodo}


def relaciones_de(grupo: dict, indice: dict, maximo: int) -> list[dict]:
    filas = []
    for node in grupo["nodos"]:
        for e in indice["edges_por_nodo"].get(node["id"], []):
            src = indice["nodes"].get(e["source"], {}).get("label", "?")
            dst = indice["nodes"].get(e["target"], {}).get("label", "?")
            filas.append(
                (
                    float(e.get("confidence") or 0),
                    {
                        "texto": f"{src} → {e.get('label', 'relacionado con')} → {dst}",
                        "evidencia": str(e.get("evidence") or "")[:200],
                    },
                )
            )
    filas.sort(key=lambda x: -x[0])
    vistos, salida = set(), []
    for _, fila in filas:
        if fila["texto"] in vistos:
            continue
        vistos.add(fila["texto"])
        salida.append(fila)
        if len(salida) >= maximo:
            break
    return salida


def main() -> int:
    args = parse_args()
    salida = {"generado": None, "personas": []}
    if OUTPUT.exists() and not args.force:
        salida = json.loads(OUTPUT.read_text(encoding="utf-8"))
    hechas = {p["nombre"] for p in salida["personas"]}

    destacados = set()
    if PERFILES.exists():
        destacados = {p["nombre"] for p in json.loads(PERFILES.read_text(encoding="utf-8"))["perfiles"]}

    rag_args = argparse.Namespace(
        chroma_dir=args.chroma_dir, collection=args.collection,
        ollama_host=args.ollama_host, embedding_model=args.embedding_model,
    )
    grupos, indice = grupos_de_personas()
    if args.minimo_rango:
        grupos = grupos[: args.minimo_rango]
    log(f"grupos de personas: {len(grupos)} · ya indexadas: {len(hechas)} · destacados: {len(destacados)}")

    for numero, grupo in enumerate(grupos, start=1):
        nombre = max((n["label"] for n in grupo["nodos"]), key=len)
        if nombre in hechas:
            continue
        alias = sorted({a for n in grupo["nodos"] for a in [n["label"], *(n.get("aliases") or [])] if len(a) < 50}, key=len)
        descripcion = max((n.get("description") or "" for n in grupo["nodos"]), key=len)
        refs = dp.evidencias_de({"nombre": nombre, "alias": alias[:4]}, rag_args, args.tope_referencias)
        persona = {
            "nombre": nombre,
            "alias": alias[:10],
            "descripcion": descripcion[:400],
            "destacado": nombre in destacados,
            "menciones": len(refs),
            "relaciones": relaciones_de(grupo, indice, args.max_relaciones),
            "referencias": [
                {"cita": e["cita"][:220], "video": e["video"][:80], "fecha": e["fecha"], "timestamp": e["timestamp"], "url": e["url"]}
                for e in refs
            ],
        }
        salida["personas"].append(persona)
        hechas.add(nombre)
        if numero % 25 == 0 or numero == len(grupos):
            salida["generado"] = datetime.now(timezone.utc).isoformat()
            OUTPUT.write_text(json.dumps(salida, ensure_ascii=False) + "\n", encoding="utf-8")
            log(f"[{numero}/{len(grupos)}] {nombre} · {len(refs)} refs · guardado")

    salida["generado"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps(salida, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"listo: {len(salida['personas'])} personas indexadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
