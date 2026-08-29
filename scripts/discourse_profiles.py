#!/usr/bin/env python3
"""Perfiles de discurso: cómo trata la presidente a cada actor público.

Para cada persona o colectivo relevante del grafo unificado (tipo PERSONA,
priorizado por PageRank, con variantes del mismo nombre agrupadas), reúne:

- las **menciones textuales** del corpus (vía el índice ChromaDB), cada una
  con su video, fecha, timestamp y URL — la evidencia citable; y
- las **relaciones del grafo** que tocan a la persona, como contexto.

Con eso, un modelo (por defecto Ollama local) clasifica el TRATO que la
presidente le da en su discurso — aliado, adversario, culpado, funcionario,
neutral o mixto — y sintetiza cargo, características, culpas atribuidas y
declaraciones clave, cada una anclada a una evidencia numerada cuyo enlace
con timestamp se conserva en la salida.

Salida: ``data/perfiles_discurso.json`` (progresiva y reanudable: las
personas ya perfiladas se saltan; ``--force`` las rehace).

Uso:
    python scripts/discourse_profiles.py                       # ollama local
    python scripts/discourse_profiles.py --motor antigravity-cli
    python scripts/discourse_profiles.py --max-personas 20
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))

from scripts import rag_transcripts as rag  # noqa: E402
from textgraph.llm import ErrorMotor, crear_motor  # noqa: E402

GRAPH_JSON = Path("data/grafos/grafo_unificado.json")
OUTPUT = Path("data/perfiles_discurso.json")
STOP_TOKENS = {"de", "del", "la", "el", "los", "las", "don", "dona", "doña", "jesus", "maria", "señor", "senor", "señora", "senora"}
CLASIFICACIONES = ("aliado", "adversario", "culpado", "funcionario", "neutral", "mixto")

PROMPT = """Eres un analista de discurso político. Analiza QUÉ DICE la presidenta de Costa Rica \
Laura Fernández sobre «{nombre}», usando SOLO las evidencias numeradas (citas de transcripciones \
de sus conferencias de prensa, cadenas nacionales y entrevistas, mayo-agosto 2026).

Devuelve UNICAMENTE un objeto JSON con esta forma:
{{
  "cargo": "cargo o rol de la persona si se menciona, o \\"\\"",
  "clasificacion": "aliado | adversario | culpado | funcionario | neutral | mixto",
  "resumen": "2-3 frases sobre cómo la presidenta retrata a esta persona en su discurso",
  "caracteristicas": ["rasgos o atributos que la presidenta le adjudica", "..."],
  "culpas": [{{"tema": "de qué la culpa o responsabiliza", "evidencia": 1}}],
  "declaraciones_clave": [{{"sintesis": "qué dijo la presidenta, en tercera persona", "evidencia": 2}}]
}}

Reglas:
- Clasifica según el TRATO de la presidenta en el discurso, no según tu opinión: "aliado" si lo \
elogia o presenta como colaborador; "adversario" si lo confronta; "culpado" si le atribuye culpas \
o responsabilidades por problemas; "funcionario" si solo lo menciona en su rol oficial sin carga; \
"mixto" si hay de ambos; "neutral" si no hay señal.
- Cada culpa y declaración DEBE citar el número de una evidencia que la respalde.
- No inventes nada fuera de las evidencias. Si no hay base suficiente, deja listas vacías.

Contexto del grafo de conocimiento (relaciones extraídas del corpus, solo como contexto):
{contexto_grafo}

Evidencias:
{evidencias}"""


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text)).strip()


def tokens_de(label: str) -> frozenset[str]:
    return frozenset(t for t in norm(label).split() if t not in STOP_TOKENS and len(t) > 2)


def es_la_presidenta(label: str) -> bool:
    n = norm(label)
    if "laura" in n and "fernandez" in n:
        return True
    return n in {
        "presidenta", "presidente", "la presidenta", "la presidente",
        "presidenta de costa rica", "presidente de costa rica",
        "presidenta de la republica", "presidente de la republica",
        "señor presidente de la republica", "senor presidente de la republica",
        "presidenta electa", "mandataria",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Perfiles del discurso presidencial por persona.")
    parser.add_argument("--motor", default="ollama", choices=["ollama", "codex-cli", "gemini-cli", "antigravity-cli", "claude-cli", "api"])
    parser.add_argument("--modelo", default=None, help="Default: el modelo propio del motor (ollama: gemma4:12b-mlx).")
    parser.add_argument("--max-personas", type=int, default=40)
    parser.add_argument("--menciones", type=int, default=10, help="Evidencias textuales máximas por persona.")
    parser.add_argument("--force", action="store_true", help="Rehacer perfiles ya existentes.")
    parser.add_argument("--chroma-dir", type=Path, default=rag.DEFAULT_CHROMA_DIR)
    parser.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    parser.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL))
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def seleccionar_personas(max_personas: int) -> list[dict]:
    data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in data["nodes"]}

    def rank(n: dict) -> float:
        try:
            return float((n.get("metrics") or {}).get("pagerank") or 0.0)
        except Exception:
            return 0.0

    personas = [n for n in data["nodes"] if n.get("type") == "PERSONA" and not es_la_presidenta(n.get("label", ""))]
    personas.sort(key=rank, reverse=True)

    grupos: list[dict] = []
    for node in personas:
        toks = tokens_de(node["label"])
        if not toks:
            continue
        destino = None
        for grupo in grupos:
            if toks <= grupo["tokens"] or grupo["tokens"] <= toks:
                destino = grupo
                break
        if destino is None:
            grupos.append({"tokens": toks, "nodos": [node], "rank": rank(node)})
            if len(grupos) >= max_personas * 2:
                break
        else:
            destino["nodos"].append(node)
            destino["tokens"] = destino["tokens"] | toks
            destino["rank"] += rank(node)

    grupos.sort(key=lambda g: g["rank"], reverse=True)
    seleccion = []
    for grupo in grupos[:max_personas]:
        nombre = max((n["label"] for n in grupo["nodos"]), key=len)
        alias: set[str] = set()
        ids: set[str] = set()
        for n in grupo["nodos"]:
            ids.add(n["id"])
            alias.add(n["label"])
            alias.update(a for a in (n.get("aliases") or []) if len(a) < 50)
        seleccion.append({"nombre": nombre, "ids": ids, "alias": sorted(alias, key=len)})

    edges_por_nodo: dict[str, list[dict]] = {}
    for e in data.get("edges", []):
        if e.get("type") == "estructural" or not e.get("evidence"):
            continue
        for lado in (e.get("source"), e.get("target")):
            edges_por_nodo.setdefault(lado, []).append(e)
    for persona in seleccion:
        relaciones = []
        for nid in persona["ids"]:
            for e in edges_por_nodo.get(nid, []):
                src = nodes.get(e["source"], {}).get("label", "?")
                dst = nodes.get(e["target"], {}).get("label", "?")
                relaciones.append((float(e.get("confidence") or 0), f"- {src} → {e.get('label')} → {dst} (\"{str(e['evidence'])[:140]}\")"))
        relaciones.sort(reverse=True)
        persona["contexto_grafo"] = "\n".join(linea for _, linea in relaciones[:10]) or "(sin relaciones registradas)"
    return seleccion


def evidencias_de(persona: dict, rag_args: argparse.Namespace, limite: int) -> list[dict]:
    terminos = [persona["nombre"]]
    for a in persona["alias"]:
        if norm(a) != norm(persona["nombre"]) and len(a) >= 5:
            terminos.append(a)
        if len(terminos) >= 3:
            break
    evidencias: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    for termino in terminos:
        try:
            matches = rag.search_mentions(rag_args, termino, whole_word=False, context_chars=380, limit=40)
        except Exception as exc:
            log(f"  aviso: fallo la búsqueda de «{termino}»: {exc}")
            continue
        for match in matches:
            metadata = match.get("metadata") or {}
            clave = (str(metadata.get("video_id")), str(metadata.get("start")))
            if clave in vistos:
                continue
            vistos.add(clave)
            evidencias.append(
                {
                    "n": len(evidencias) + 1,
                    "cita": re.sub(r"\*\*", "", str(match.get("context") or "")).strip(),
                    "video_id": metadata.get("video_id", ""),
                    "video": metadata.get("title", ""),
                    "fecha": rag.format_upload_date(str(metadata.get("upload_date", ""))),
                    "timestamp": rag.format_timestamp(metadata.get("start")),
                    "url": metadata.get("timestamp_url") or metadata.get("url", ""),
                }
            )
            if len(evidencias) >= limite:
                return evidencias
    return evidencias


def perfilar(persona: dict, evidencias: list[dict], motor) -> dict:
    listado = "\n".join(f"[{e['n']}] ({e['video'][:60]}, {e['fecha']}) \"{e['cita'][:420]}\"" for e in evidencias)
    prompt = PROMPT.format(nombre=persona["nombre"], contexto_grafo=persona["contexto_grafo"], evidencias=listado)
    respuesta = motor.json(prompt, max_tokens=4000)
    clasificacion = str(respuesta.get("clasificacion") or "neutral").lower().strip()
    if clasificacion not in CLASIFICACIONES:
        clasificacion = "neutral"

    def con_fuente(items: list, campo: str) -> list[dict]:
        salida = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            n = item.get("evidencia")
            fuente = next((e for e in evidencias if e["n"] == n), None)
            salida.append(
                {
                    campo: str(item.get(campo) or "").strip(),
                    "cita": (fuente or {}).get("cita", ""),
                    "video": (fuente or {}).get("video", ""),
                    "fecha": (fuente or {}).get("fecha", ""),
                    "timestamp": (fuente or {}).get("timestamp", ""),
                    "url": (fuente or {}).get("url", ""),
                }
            )
        return [s for s in salida if s[campo]]

    return {
        "nombre": persona["nombre"],
        "alias": persona["alias"][:8],
        "cargo": str(respuesta.get("cargo") or "").strip(),
        "clasificacion": clasificacion,
        "resumen": str(respuesta.get("resumen") or "").strip(),
        "caracteristicas": [str(c).strip() for c in (respuesta.get("caracteristicas") or []) if str(c).strip()][:8],
        "culpas": con_fuente(respuesta.get("culpas") or [], "tema"),
        "declaraciones": con_fuente(respuesta.get("declaraciones_clave") or [], "sintesis"),
        "menciones_analizadas": len(evidencias),
        "generado": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    args = parse_args()
    if not GRAPH_JSON.exists():
        raise SystemExit("No existe el grafo unificado; genera primero data/grafos/grafo_unificado.json")

    salida = {"generado": None, "motor": None, "perfiles": []}
    if OUTPUT.exists():
        salida = json.loads(OUTPUT.read_text(encoding="utf-8"))
    hechos = {p["nombre"] for p in salida.get("perfiles", [])}

    rag_args = argparse.Namespace(
        chroma_dir=args.chroma_dir, collection=args.collection,
        ollama_host=args.ollama_host, embedding_model=args.embedding_model,
    )
    motor = crear_motor(args.motor, args.modelo)
    personas = seleccionar_personas(args.max_personas)
    log(f"personas seleccionadas: {len(personas)} · motor {motor.nombre}/{motor.modelo} · ya perfiladas: {len(hechos)}")

    fallos_seguidos = 0
    for index, persona in enumerate(personas, start=1):
        if persona["nombre"] in hechos and not args.force:
            continue
        evidencias = evidencias_de(persona, rag_args, args.menciones)
        log(f"[{index}/{len(personas)}] {persona['nombre']} · {len(evidencias)} evidencias")
        if not evidencias:
            continue
        try:
            perfil = perfilar(persona, evidencias, motor)
            fallos_seguidos = 0
        except ErrorMotor as exc:
            fallos_seguidos += 1
            log(f"  fallo del motor: {str(exc)[:120]}")
            if fallos_seguidos >= 3:
                log("3 fallos seguidos; se detiene (relanzar retoma donde quedó)")
                break
            continue
        salida["perfiles"] = [p for p in salida["perfiles"] if p["nombre"] != persona["nombre"]]
        salida["perfiles"].append(perfil)
        salida["generado"] = datetime.now(timezone.utc).isoformat()
        salida["motor"] = f"{motor.nombre}/{motor.modelo}"
        OUTPUT.write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"  → {perfil['clasificacion']} · {len(perfil['culpas'])} culpas · {len(perfil['declaraciones'])} declaraciones")

    log(f"listo: {len(salida['perfiles'])} perfiles en {OUTPUT} · coste ${motor.coste:.2f} ({motor.llamadas} llamadas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
