#!/usr/bin/env python3
"""Incorpora grafos por video al grafo unificado SIN perder el trabajo de limpieza.

``batch_graphs.py --solo-unificar`` reconstruye el unificado desde cero a
partir de ``data/grafos/por_video/``, lo que descarta las correcciones de
etiquetas, la poda de basura y las fusiones de duplicados ya aplicadas. Este
script hace lo contrario: parte del unificado **limpio** y le suma solo los
videos indicados, con las mismas guardas anti-meganodo.

Por defecto toma como base el unificado limpio más reciente del store de
textgraph (el que tenga ``meta.limpieza``) y fusiona los videos que aún no
figuren en ``meta.videos_incorporados``.

Uso:
    python scripts/merge_into_unified.py --videos ID1 ID2
    python scripts/merge_into_unified.py --base data/grafos/grafo_unificado.json --videos ID
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textgraph import analysis, exporters, normalize, store  # noqa: E402
from textgraph.model import Edge, Graph, Node  # noqa: E402

POR_VIDEO = Path("data/grafos/por_video")
UNIFIED_DIR = Path("data/grafos")
STORE_DIR = TEXTGRAPH_DIR / "data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge incremental al grafo unificado limpio.")
    parser.add_argument("--videos", nargs="+", required=True, help="video_id de los grafos por video a incorporar.")
    parser.add_argument("--base", type=Path, help="Grafo base; por defecto el unificado limpio más reciente del store.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def base_limpia() -> Path:
    candidatos = []
    for p in STORE_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (data.get("meta") or {}).get("limpieza") and "unificado" in p.name:
            candidatos.append((p.stat().st_mtime, len(data.get("nodes", [])), p))
    if not candidatos:
        raise SystemExit("No se encontró un unificado con limpieza en el store de textgraph.")
    candidatos.sort()
    return candidatos[-1][2]


def fusion_segura(a, b) -> bool:
    if len(a.aliases) > 12 or len(b.aliases) > 12:
        return False
    if len(a.label) > 60 or len(b.label) > 60:
        return False
    if a.type != b.type and "OTRO" not in (a.type, b.type):
        return False
    return True


def main() -> int:
    args = parse_args()
    ruta_base = args.base or base_limpia()
    data = json.loads(ruta_base.read_text(encoding="utf-8"))
    grafo = Graph.from_dict(data)
    meta = dict(data.get("meta") or {})
    ya = set(meta.get("videos_incorporados") or [])
    log(f"base: {ruta_base.name} · {len(grafo.nodes)} nodos · {len(grafo.edges)} relaciones")

    pendientes = [v for v in args.videos if v not in ya]
    if not pendientes:
        log("todos los videos indicados ya estaban incorporados; nada que hacer")
        return 0

    antes = (len(grafo.nodes), len(grafo.edges))
    incorporados = []
    for video_id in pendientes:
        archivos = sorted((POR_VIDEO / video_id).glob("*.json")) if (POR_VIDEO / video_id).is_dir() else []
        if not archivos:
            log(f"  ! {video_id}: sin grafo por video; omitido")
            continue
        d = json.loads(archivos[0].read_text(encoding="utf-8"))
        nuevos_nodos = 0
        for nd in d.get("nodes", []):
            node = Node.from_dict(nd)
            node.mentions = []
            node.metrics = {}
            if node.id not in grafo.nodes:
                nuevos_nodos += 1
            grafo.add_node(node)
        for ed in d.get("edges", []):
            grafo.add_edge(Edge.from_dict(ed))
        incorporados.append(video_id)
        log(f"  + {video_id}: {len(d.get('nodes', []))} nodos del video ({nuevos_nodos} entidades nuevas) · {d.get('title', '')[:48]}")

    # fusión conservadora por clave normalizada, con guardas
    fusiones = 0
    por_clave: dict[str, str] = {}
    for node in list(grafo.nodes.values()):
        k = normalize.clave(node.label)
        previo = por_clave.get(k)
        if previo is None or previo not in grafo.nodes:
            por_clave[k] = node.id
            continue
        canon, absorbido = normalize.canonico(grafo.nodes[previo], node)
        if not fusion_segura(canon, absorbido):
            continue
        normalize.fusionar(grafo, canon.id, absorbido.id)
        por_clave[k] = canon.id
        fusiones += 1

    analysis.calcular(grafo)
    meta.update({
        "videos": int(meta.get("videos") or 0) + len(incorporados),
        "videos_incorporados": sorted(ya | set(incorporados)),
        "ultima_incorporacion": datetime.now(timezone.utc).isoformat(),
        "base_incremental": ruta_base.name,
    })
    grafo.meta = meta
    log(f"resultado: {antes[0]} → {len(grafo.nodes)} nodos · {antes[1]} → {len(grafo.edges)} relaciones · {fusiones} fusiones por clave")

    if args.dry_run:
        log("dry-run: no se escribió nada")
        return 0

    UNIFIED_DIR.mkdir(parents=True, exist_ok=True)
    web_dir = str(TEXTGRAPH_DIR / "web")
    for formato in ("json", "html", "llm"):
        datos, _mime, ext = exporters.exportar(grafo, formato, web_dir)
        destino = UNIFIED_DIR / f"grafo_unificado{ext}"
        destino.write_bytes(datos if isinstance(datos, bytes) else datos.encode("utf-8"))
        log(f"escrito {destino}")
    try:
        gid = store.guardar(grafo)
        log(f"registrado en el store como {gid}")
    except Exception as exc:
        log(f"no se pudo registrar en el store ({exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
