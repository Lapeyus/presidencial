#!/usr/bin/env python3
"""Genera un grafo por video y un grafo unificado, rotando motores por fases.

Orquesta el ciclo de motores acordado sobre textgraph, avanzando de fase
cuando se agota el presupuesto, vence su plazo, el motor no está disponible
o encadena 3 fallos (cuota agotada):

    1. gemini-cli        hasta gastar ~``--gemini-usd`` (coste equivalente
                         reportado por textgraph en la metadata del grafo)
    2. antigravity-cli   mientras la CLI ``agy`` responda
    3. ollama            local y gratis, hasta que llegue la ventana de codex
    4. codex-cli         a partir de ``--codex-en-horas`` desde el arranque
    5. ollama (remate)   fase final: nunca se rinde, con pausas de 30 min

Cada video fallido se re-encola para intentarse en fases posteriores (máximo
3 intentos). Un grafo con 0 nodos cuenta como fallo y se borra para que otra
fase lo regenere. Es **reanudable**: los videos con grafo en
``data/grafos/por_video/<video_id>/`` se saltan. Al final (siempre) fusiona
todo en ``data/grafos/grafo_unificado.*`` con el propio modelo de textgraph.

Estado en ``data/grafos/batch_estado.json``; progreso en el log del proceso.

Uso:
    python scripts/batch_graphs.py                    # ciclo completo
    python scripts/batch_graphs.py --limite 2         # prueba corta
    python scripts/batch_graphs.py --solo-unificar    # solo re-fusionar
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
TRANSCRIPTS = Path("data/presidencia_transcripts.jsonl")
PER_VIDEO_DIR = Path("data/grafos/por_video")
UNIFIED_DIR = Path("data/grafos")
STATE_FILE = Path("data/grafos/batch_estado.json")
MAX_CHARS = 398_000
PAUSA_FASE_FINAL = 1800
MAX_INTENTOS_POR_VIDEO = 3
BIN_POR_MOTOR = {"claude-cli": "claude", "codex-cli": "codex", "gemini-cli": "gemini", "antigravity-cli": "agy"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grafo por video + unificado, rotando motores por fases.")
    parser.add_argument("--textgraph-dir", type=Path, default=DEFAULT_TEXTGRAPH_DIR)
    parser.add_argument("--gemini-usd", type=float, default=5.0, help="Presupuesto (coste equivalente) de la fase gemini. Default: 5.")
    parser.add_argument("--modelo-gemini", default="gemini-3-pro-preview")
    parser.add_argument("--modelo-antigravity", default=None, help="Default: el de textgraph (gemini-3.7-flash-high).")
    parser.add_argument("--modelo-ollama", default="gemma4:12b-mlx")
    parser.add_argument("--modelo-codex", default="gpt-5.6-terra")
    parser.add_argument("--codex-en-horas", type=float, default=5.0, help="Horas desde el arranque para intentar la fase codex. Default: 5.")
    parser.add_argument("--fragmento", type=int, default=8000)
    parser.add_argument("--paralelo", type=int, default=2)
    parser.add_argument("--limite", type=int, help="Procesar solo N videos pendientes (pruebas).")
    parser.add_argument("--solo-unificar", action="store_true")
    parser.add_argument("--rebuild-unificado", action="store_true",
                        help="Reconstruir el unificado desde cero aunque el existente tenga limpieza aplicada (la descarta).")
    parser.add_argument("--timeout-video", type=int, default=7200)
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def iso_date(value: str | None) -> str:
    value = value or ""
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else "¿fecha?"


def load_records() -> list[dict]:
    records = []
    with TRANSCRIPTS.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            text = ((record.get("transcript") or {}).get("text") or "").strip()
            if text and record.get("video_id"):
                records.append(record)
    records.sort(key=lambda r: (r.get("upload_date") or "", r.get("video_id")))
    return records


def video_text(record: dict) -> str:
    header = (
        f"=== {record.get('title') or record.get('video_id')} "
        f"({iso_date(record.get('upload_date'))}) — {record.get('url', '')} ==="
    )
    return f"{header}\n{(record['transcript']['text']).strip()}"


def graph_json_path(video_id: str) -> Path | None:
    outdir = PER_VIDEO_DIR / video_id
    if not outdir.is_dir():
        return None
    candidates = sorted(outdir.glob("*.json"))
    return candidates[0] if candidates else None


def motor_disponible(motor: str) -> bool:
    if motor == "ollama":
        host = (os.environ.get("OLLAMA_HOST") or "http://localhost:11434").strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        host = host.replace("://0.0.0.0", "://localhost")
        try:
            with urllib.request.urlopen(host + "/api/version", timeout=3):
                return True
        except Exception:
            return False
    binario = BIN_POR_MOTOR.get(motor)
    return shutil.which(binario) is not None if binario else True


def fases_del_ciclo(args: argparse.Namespace, epoch_codex: float) -> list[dict]:
    return [
        {"nombre": "gemini", "motor": "gemini-cli", "modelo": args.modelo_gemini, "presupuesto_usd": args.gemini_usd},
        {"nombre": "antigravity", "motor": "antigravity-cli", "modelo": args.modelo_antigravity},
        {"nombre": "ollama-espera", "motor": "ollama", "modelo": args.modelo_ollama, "hasta_epoch": epoch_codex},
        {"nombre": "codex-ventana", "motor": "codex-cli", "modelo": args.modelo_codex},
        {"nombre": "ollama-remate", "motor": "ollama", "modelo": args.modelo_ollama, "final": True},
    ]


def fase_agotada(fase: dict, gasto: float) -> str | None:
    """Devuelve la razón por la que la fase no puede seguir, o None."""
    if fase.get("final"):
        return None
    if not motor_disponible(fase["motor"]):
        return "motor no disponible"
    presupuesto = fase.get("presupuesto_usd")
    if presupuesto is not None and gasto >= presupuesto:
        return f"presupuesto agotado (${gasto:.2f} de ${presupuesto:.2f})"
    hasta = fase.get("hasta_epoch")
    if hasta is not None and time.time() >= hasta:
        return "venció su plazo"
    return None


def coste_del_grafo(json_path: Path) -> float:
    try:
        meta = json.loads(json_path.read_text(encoding="utf-8")).get("meta") or {}
        uso = meta.get("uso") or {}
        if isinstance(uso.get("coste_usd"), (int, float)):
            return float(uso["coste_usd"])
        if isinstance(meta.get("coste_usd"), (int, float)):
            return float(meta["coste_usd"])
    except Exception:
        pass
    return 0.0


def save_state(state: dict) -> None:
    state["actualizado"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_one(record: dict, fase: dict, args: argparse.Namespace) -> tuple[bool, float]:
    """Genera el grafo de un video con el motor de la fase. Devuelve (ok, coste)."""
    video_id = record["video_id"]
    outdir = PER_VIDEO_DIR / video_id
    outdir.mkdir(parents=True, exist_ok=True)
    text = video_text(record)
    if len(text) > MAX_CHARS:
        log(f"{video_id}: {len(text):,} caracteres supera el límite de textgraph; omitido")
        return False, 0.0
    titulo = f"{(record.get('title') or video_id)[:80]} ({iso_date(record.get('upload_date'))})"
    command = [
        sys.executable, str(args.textgraph_dir / "cli.py"), "-",
        "-o", str(outdir), "-t", titulo,
        "-m", fase["motor"],
        "--fragmento", str(args.fragmento), "--paralelo", str(args.paralelo),
        "--no-guardar",
    ]
    if fase.get("modelo") and fase["motor"] != "local":
        command.extend(["--modelo", fase["modelo"]])
    result = subprocess.run(command, input=text, text=True, capture_output=True, timeout=args.timeout_video)
    tail = "\n".join((result.stderr or "").splitlines()[-3:])
    json_path = graph_json_path(video_id)
    if result.returncode != 0 or json_path is None:
        log(f"{video_id}: FALLO (rc={result.returncode})\n{tail}")
        shutil.rmtree(outdir, ignore_errors=True)
        return False, 0.0
    nodos = len(json.loads(json_path.read_text(encoding="utf-8")).get("nodes", []))
    if nodos == 0:
        log(f"{video_id}: grafo VACÍO (0 nodos, motor sin cuota?); descartado")
        shutil.rmtree(outdir, ignore_errors=True)
        return False, 0.0
    coste = coste_del_grafo(json_path)
    log(f"{video_id}: listo · {nodos} nodos · ${coste:.3f} · {tail.splitlines()[-1] if tail else ''}")
    return True, coste


def unify(args: argparse.Namespace) -> dict | None:
    sys.path.insert(0, str(args.textgraph_dir))
    from textgraph import analysis, exporters, normalize, store  # noqa: E402
    from textgraph.model import Edge, Graph, Node  # noqa: E402

    # El unificado se reconstruye desde cero: eso DESCARTA correcciones de
    # etiquetas, poda de basura y fusiones ya aplicadas por
    # refine_unified_graph.py. Para sumar videos nuevos conservando la
    # limpieza, usar scripts/merge_into_unified.py.
    destino_json = UNIFIED_DIR / "grafo_unificado.json"
    if destino_json.exists() and not args.rebuild_unificado:
        try:
            previo = json.loads(destino_json.read_text(encoding="utf-8"))
            if (previo.get("meta") or {}).get("limpieza"):
                log("unificado: el existente tiene limpieza aplicada; NO se reconstruye "
                    "(usa scripts/merge_into_unified.py --videos ... o --rebuild-unificado para forzar)")
                return None
        except Exception:
            pass

    paths = sorted(PER_VIDEO_DIR.glob("*/*.json"))
    if not paths:
        log("unificado: no hay grafos por video que fusionar")
        return None

    fuentes = []
    unified = Graph(title="Grafo unificado del corpus Presidencia CR")
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        fuentes.append({"video_id": path.parent.name, "titulo": data.get("title", "")})
        for node_dict in data.get("nodes", []):
            node = Node.from_dict(node_dict)
            node.mentions = []
            node.metrics = {}
            unified.add_node(node)
        for edge_dict in data.get("edges", []):
            unified.add_edge(Edge.from_dict(edge_dict))

    unified.text = "\n".join(f"- {f['titulo']}" for f in fuentes)
    unified.meta = {
        "tipo": "unificado",
        "videos": len(fuentes),
        "generado": datetime.now(timezone.utc).isoformat(),
    }
    antes = (len(unified.nodes), len(unified.edges))
    # Fusión CONSERVADORA: solo nodos con la misma clave normalizada (tildes,
    # artículos, plurales). El resolver heurístico de textgraph encadena
    # fusiones transitivas a esta escala (10k nodos) y produce meganodos.
    por_clave: dict[str, str] = {}
    for node in list(unified.nodes.values()):
        k = normalize.clave(node.label)
        previo = por_clave.get(k)
        if previo is None or previo not in unified.nodes:
            por_clave[k] = node.id
            continue
        canon, absorbido = normalize.canonico(unified.nodes[previo], node)
        normalize.fusionar(unified, canon.id, absorbido.id)
        por_clave[k] = canon.id
    analysis.calcular(unified)
    log(f"unificado: {antes[0]} nodos brutos → {len(unified.nodes)} tras fusión · {len(unified.edges)} relaciones · {len(fuentes)} videos")

    UNIFIED_DIR.mkdir(parents=True, exist_ok=True)
    web_dir = str(args.textgraph_dir / "web")
    for formato in ("json", "html", "llm"):
        datos, _mime, ext = exporters.exportar(unified, formato, web_dir)
        destino = UNIFIED_DIR / f"grafo_unificado{ext}"
        destino.write_bytes(datos if isinstance(datos, bytes) else datos.encode("utf-8"))
        log(f"unificado: escrito {destino}")
    try:
        gid = store.guardar(unified)
        log(f"unificado: registrado en el store de textgraph como {gid}")
    except Exception as exc:
        log(f"unificado: no se pudo registrar en el store ({exc})")
    return {"nodos": len(unified.nodes), "relaciones": len(unified.edges), "videos": len(fuentes)}


def main() -> int:
    args = parse_args()
    if not (args.textgraph_dir / "cli.py").exists():
        raise SystemExit(f"No se encontró textgraph en {args.textgraph_dir}")

    records = load_records()
    epoch_codex = time.time() + args.codex_en_horas * 3600
    fases = fases_del_ciclo(args, epoch_codex)
    state = {
        "iniciado": datetime.now(timezone.utc).isoformat(),
        "codex_a_partir_de": datetime.fromtimestamp(epoch_codex).isoformat(),
        "total_videos": len(records),
        "fase_actual": None,
        "gasto_por_fase": {},
        "hechos": [],
        "fallidos": {},
        "unificado": None,
    }

    if not args.solo_unificar:
        pendientes = [r for r in records if graph_json_path(r["video_id"]) is None]
        if args.limite:
            pendientes = pendientes[: args.limite]
        log(
            f"batch: {len(records)} videos en corpus · {len(records) - len(pendientes)} ya con grafo · "
            f"{len(pendientes)} por generar · codex a partir de {state['codex_a_partir_de'][11:16]}"
        )
        fase_idx = 0
        gasto = 0.0
        racha = 0
        intentos: dict[str, int] = {}
        cola = list(pendientes)
        while cola:
            while fase_idx < len(fases) - 1:
                razon = fase_agotada(fases[fase_idx], gasto)
                if razon is None:
                    break
                log(f"fase {fases[fase_idx]['nombre']}: {razon} → paso a {fases[fase_idx + 1]['nombre']}")
                state["gasto_por_fase"][fases[fase_idx]["nombre"]] = round(gasto, 3)
                fase_idx += 1
                gasto = 0.0
                racha = 0
            fase = fases[fase_idx]
            state["fase_actual"] = fase["nombre"]

            record = cola.pop(0)
            video_id = record["video_id"]
            intentos[video_id] = intentos.get(video_id, 0) + 1
            log(
                f"[quedan {len(cola) + 1}] {video_id} · intento {intentos[video_id]} · fase {fase['nombre']} "
                f"({fase['motor']}/{fase.get('modelo') or 'default'}) · {(record.get('title') or '')[:55]}"
            )
            ok = False
            try:
                ok, coste = build_one(record, fase, args)
                gasto += coste
            except subprocess.TimeoutExpired:
                log(f"{video_id}: timeout de {args.timeout_video}s")
            except Exception as exc:
                log(f"{video_id}: error inesperado: {exc}")

            if ok:
                state["hechos"].append(video_id)
                racha = 0
            else:
                racha += 1
                if intentos[video_id] < MAX_INTENTOS_POR_VIDEO:
                    cola.append(record)
                else:
                    state["fallidos"][video_id] = f"{MAX_INTENTOS_POR_VIDEO} intentos agotados"
                if racha >= 3:
                    if fase.get("final"):
                        log(f"fase final con 3 fallos seguidos; pausa de {PAUSA_FASE_FINAL // 60} min")
                        time.sleep(PAUSA_FASE_FINAL)
                    else:
                        log(f"fase {fase['nombre']}: 3 fallos seguidos → paso a {fases[fase_idx + 1]['nombre']}")
                        state["gasto_por_fase"][fase["nombre"]] = round(gasto, 3)
                        fase_idx += 1
                        gasto = 0.0
                    racha = 0
            save_state(state)
        state["gasto_por_fase"][fases[fase_idx]["nombre"]] = round(gasto, 3)

    state["unificado"] = unify(args)
    save_state(state)
    hechos_total = sum(1 for r in records if graph_json_path(r["video_id"]) is not None)
    log(f"batch terminado: {hechos_total}/{len(records)} videos con grafo · gasto por fase: {state['gasto_por_fase']}")
    log("Relanzar este mismo script retoma los pendientes (es reanudable).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
