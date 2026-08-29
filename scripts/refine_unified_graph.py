#!/usr/bin/env python3
"""Limpia y mejora el grafo unificado con ayuda de un modelo (codex por defecto).

Tres pasadas sobre ``data/grafos/grafo_unificado.json`` (con respaldo previo
en ``grafo_unificado_pre_limpieza.*``):

1. **Corrección de etiquetas**: en lotes priorizados por PageRank, el modelo
   corrige errores evidentes de transcripción (nombres propios mal oídos por
   Whisper, tildes, mayúsculas institucionales) y señala entidades basura
   (muletillas, fragmentos sin sentido). Las correcciones se aplican como
   renombre conservando la etiqueta vieja como alias; si la etiqueta corregida
   ya existe, ambos nodos se fusionan. La poda de basura tiene tope de
   seguridad por lote (si el modelo marca demasiado, se ignora ese lote).
2. **Fusión de duplicados**: una pasada por reglas de textgraph y luego
   rondas de ``normalize.resolver(usar_llm=True)`` (40 parejas dudosas por
   ronda) hasta que no haya cambios o se agote el presupuesto.
3. **Recalcular y exportar**: métricas de red, JSON + visor HTML + paquete
   LLM, y registro de la versión limpia en el store de textgraph.

El gasto (coste equivalente que reporta el motor) se corta en ``--max-usd``.
Si la cuota muere a media pasada, lo aplicado hasta ahí se exporta igual.

Uso:
    python scripts/refine_unified_graph.py
    python scripts/refine_unified_graph.py --max-usd 10 --batch 60
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))

from textgraph import analysis, exporters, normalize, store  # noqa: E402
from textgraph.llm import ErrorMotor, crear_motor  # noqa: E402
from textgraph.model import Graph  # noqa: E402

UNIFIED_JSON = Path("data/grafos/grafo_unificado.json")
UNIFIED_DIR = Path("data/grafos")
BACKUP_STEM = "grafo_unificado_pre_limpieza"
MAX_BASURA_POR_LOTE = 0.2

PROMPT_CORRECCION = """Estas revisando etiquetas de entidades extraidas de transcripciones de audio \
(conferencias de prensa y entrevistas del gobierno de Costa Rica, 2026). Las transcripciones \
vienen de reconocimiento de voz, asi que hay nombres propios mal oidos, tildes perdidas y \
fragmentos sin sentido.

Devuelve UNICAMENTE un objeto JSON con dos claves:
- "correcciones": lista de objetos {{"antes": "etiqueta actual", "despues": "etiqueta corregida"}} \
SOLO para errores evidentes: nombres propios mal transcritos (personas, instituciones, lugares de \
Costa Rica), tildes faltantes en nombres, siglas mal escritas. NO cambies etiquetas que ya estan \
bien, NO traduzcas, NO reformules.
- "basura": lista de etiquetas que NO son entidades reales (muletillas, pedazos de frase cortados, \
ruido de transcripcion). Se conservador: ante la duda, no la marques.

Etiquetas (formato: etiqueta | tipo):
{listado}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Limpieza asistida del grafo unificado.")
    parser.add_argument("--motor", default="codex-cli", choices=["codex-cli", "gemini-cli", "antigravity-cli", "claude-cli", "ollama", "api"])
    parser.add_argument("--modelo", default=None, help="Default: el modelo propio de cada motor (codex: gpt-5.6-sol; antigravity: gemini-3.7-flash-high).")
    parser.add_argument("--max-usd", type=float, default=15.0, help="Tope de gasto (coste equivalente). Default: 15.")
    parser.add_argument("--batch", type=int, default=90, help="Etiquetas por llamada en la pasada de corrección. Default: 90.")
    parser.add_argument("--rondas-fusion", type=int, default=8, help="Rondas máximas de fusión asistida (40 parejas c/u). Default: 8.")
    parser.add_argument("--solo-fusion", action="store_true", help="Saltar la corrección de etiquetas y correr solo barridos de fusión paginados hasta converger.")
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def backup_unified() -> None:
    for suffix in (".json", ".html", ".llm.md"):
        origen = UNIFIED_DIR / f"grafo_unificado{suffix}"
        if origen.exists():
            shutil.copy2(origen, UNIFIED_DIR / f"{BACKUP_STEM}{suffix}")
    log(f"respaldo escrito como {BACKUP_STEM}.*")


def pagerank_de(node) -> float:
    try:
        return float((node.metrics or {}).get("pagerank") or 0.0)
    except Exception:
        return 0.0


def aplicar_correcciones(grafo: Graph, correcciones: list[dict], stats: dict) -> None:
    for item in correcciones:
        antes = (item.get("antes") or "").strip()
        despues = (item.get("despues") or "").strip()
        if not antes or not despues or antes == despues:
            continue
        nodo = grafo.node_by_label(antes)
        if nodo is None or nodo.label != antes:
            continue
        existente = grafo.node_by_label(despues)
        if existente is not None and existente.id != nodo.id:
            normalize.fusionar(grafo, existente.id, nodo.id)
            stats["fusiones_por_correccion"] += 1
        else:
            nodo.add_alias(nodo.label)
            nodo.label = despues
            stats["renombres"] += 1


def aplicar_basura(grafo: Graph, basura: list[str], tam_lote: int, stats: dict) -> None:
    if not basura:
        return
    if len(basura) > max(3, int(tam_lote * MAX_BASURA_POR_LOTE)):
        log(f"  lote marcó {len(basura)} etiquetas como basura (> tope); se ignora la poda de este lote")
        return
    for label in basura:
        nodo = grafo.node_by_label(str(label))
        if nodo is not None and nodo.label == str(label):
            grafo.remove_node(nodo.id)
            stats["basura_eliminada"] += 1


def pasada_correccion(grafo: Graph, motor, args: argparse.Namespace, stats: dict) -> None:
    nodos = sorted(grafo.nodes.values(), key=pagerank_de, reverse=True)
    lotes = [nodos[i:i + args.batch] for i in range(0, len(nodos), args.batch)]
    log(f"pasada 1: {len(nodos)} etiquetas en {len(lotes)} lotes de ~{args.batch}")
    fallos_seguidos = 0
    for numero, lote in enumerate(lotes, start=1):
        if motor.coste >= args.max_usd:
            log(f"pasada 1: tope de ${args.max_usd:.2f} alcanzado en el lote {numero}/{len(lotes)}; se detiene")
            break
        vivos = [n for n in lote if n.id in grafo.nodes]
        listado = "\n".join(f"{n.label} | {n.type}" for n in vivos)
        try:
            respuesta = motor.json(PROMPT_CORRECCION.format(listado=listado), max_tokens=8000)
            fallos_seguidos = 0
        except ErrorMotor as exc:
            fallos_seguidos += 1
            log(f"  lote {numero}: fallo del motor ({str(exc)[:120]})")
            if fallos_seguidos >= 3:
                log("pasada 1: 3 fallos seguidos (¿cuota?); se detiene la pasada")
                break
            continue
        correcciones = respuesta.get("correcciones") or []
        basura = respuesta.get("basura") or []
        aplicar_correcciones(grafo, correcciones, stats)
        aplicar_basura(grafo, [str(b) for b in basura], len(vivos), stats)
        log(
            f"  lote {numero}/{len(lotes)}: {len(correcciones)} correcciones, {len(basura)} basura propuesta "
            f"· acumulado ${motor.coste:.2f}"
        )


def _fusion_segura(a, b) -> bool:
    """Guardas anti-meganodo: nunca fusionar candidatos propensos a encadenarse."""
    if len(a.aliases) > 12 or len(b.aliases) > 12:
        return False
    if len(a.label) > 60 or len(b.label) > 60:
        return False
    if a.type != b.type and "OTRO" not in (a.type, b.type):
        return False
    return True


def fusion_por_clave(grafo: Graph) -> int:
    """Fusiona solo nodos con la misma clave normalizada (seguro y determinista)."""
    por_clave: dict[str, str] = {}
    cambios = 0
    for node in list(grafo.nodes.values()):
        k = normalize.clave(node.label)
        previo = por_clave.get(k)
        if previo is None or previo not in grafo.nodes:
            por_clave[k] = node.id
            continue
        canon, absorbido = normalize.canonico(grafo.nodes[previo], node)
        normalize.fusionar(grafo, canon.id, absorbido.id)
        por_clave[k] = canon.id
        cambios += 1
    return cambios


def pasada_fusion_paginada(grafo: Graph, motor, args: argparse.Namespace, stats: dict, max_barridos: int = 6) -> None:
    """Barre TODAS las parejas dudosas seguras hasta converger.

    Cada barrido: fusión por clave normalizada y luego el catálogo de parejas
    candidatas que pasan las guardas anti-meganodo, paginado de a 40 hacia el
    modelo. Termina cuando un barrido no produce fusiones o se agota el tope.
    """
    for barrido in range(1, max_barridos + 1):
        fusion_por_clave(grafo)
        dudosas = [
            (a, b, motivo)
            for a, b, motivo, _conf in normalize.parejas_candidatas(list(grafo.nodes.values()))
            if _fusion_segura(a, b)
        ]
        log(f"barrido {barrido}: {len(grafo.nodes)} nodos · {len(dudosas)} parejas dudosas por evaluar")
        fusiones_barrido = 0
        fallos_seguidos = 0
        for inicio in range(0, len(dudosas), 40):
            if motor.coste >= args.max_usd:
                log(f"barrido {barrido}: tope de ${args.max_usd:.2f} alcanzado; se detiene")
                break
            pagina = [
                (a, b, motivo) for a, b, motivo in dudosas[inicio:inicio + 40]
                if a.id in grafo.nodes and b.id in grafo.nodes
            ]
            if not pagina:
                continue
            listado = "\n".join(
                "%d. «%s» (%s) vs «%s» (%s) — indicio: %s"
                % (i + 1, a.label, a.type, b.label, b.type, motivo)
                for i, (a, b, motivo) in enumerate(pagina)
            )
            try:
                fusiones = motor.fusiones(listado).get("fusiones", [])
                fallos_seguidos = 0
            except ErrorMotor as exc:
                fallos_seguidos += 1
                log(f"  página {inicio // 40 + 1}: fallo del motor ({str(exc)[:100]})")
                if fallos_seguidos >= 3:
                    log("  3 fallos seguidos; se detiene el barrido")
                    break
                continue
            for f in fusiones:
                if not f.get("misma"):
                    continue
                na, nb = grafo.node_by_label(f.get("a")), grafo.node_by_label(f.get("b"))
                if not na or not nb or na.id == nb.id or not _fusion_segura(na, nb):
                    continue
                pref = grafo.node_by_label(f.get("canonico") or na.label) or na
                otro = nb if pref.id == na.id else na
                normalize.fusionar(grafo, pref.id, otro.id)
                fusiones_barrido += 1
        stats["fusiones_llm"] += fusiones_barrido
        log(f"barrido {barrido} completo: {fusiones_barrido} fusiones del modelo · {len(grafo.nodes)} nodos · ${motor.coste:.2f}")
        if fusiones_barrido == 0 or motor.coste >= args.max_usd:
            break


def exportar(grafo: Graph, stats: dict, motor) -> None:
    grafo.meta["limpieza"] = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "motor": f"{motor.nombre}/{motor.modelo}",
        "coste_usd": round(motor.coste, 3),
        **stats,
    }
    analysis.calcular(grafo)
    web_dir = str(TEXTGRAPH_DIR / "web")
    for formato in ("json", "html", "llm"):
        datos, _mime, ext = exporters.exportar(grafo, formato, web_dir)
        destino = UNIFIED_DIR / f"grafo_unificado{ext}"
        destino.write_bytes(datos if isinstance(datos, bytes) else datos.encode("utf-8"))
        log(f"escrito {destino}")
    try:
        gid = store.guardar(grafo)
        log(f"versión limpia registrada en el store como {gid}")
    except Exception as exc:
        log(f"no se pudo registrar en el store ({exc})")


def main() -> int:
    args = parse_args()
    if not UNIFIED_JSON.exists():
        raise SystemExit(f"No existe {UNIFIED_JSON}; genera primero el grafo unificado.")
    if not args.solo_fusion:
        backup_unified()
    grafo = Graph.from_dict(json.loads(UNIFIED_JSON.read_text(encoding="utf-8")))
    grafo.title = "Grafo unificado del corpus Presidencia CR (limpio)"
    inicial = (len(grafo.nodes), len(grafo.edges))
    log(f"grafo cargado: {inicial[0]} nodos · {inicial[1]} relaciones")

    motor = crear_motor(args.motor, args.modelo)
    stats = {"renombres": 0, "fusiones_por_correccion": 0, "basura_eliminada": 0, "fusiones_llm": 0}
    try:
        if args.solo_fusion:
            pasada_fusion_paginada(grafo, motor, args, stats)
        else:
            pasada_correccion(grafo, motor, args, stats)
            pasada_fusion_paginada(grafo, motor, args, stats)
    finally:
        exportar(grafo, stats, motor)
        log(
            f"resumen: {inicial[0]} → {len(grafo.nodes)} nodos · {inicial[1]} → {len(grafo.edges)} relaciones · "
            f"{stats} · gasto ${motor.coste:.2f} ({motor.llamadas} llamadas)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
