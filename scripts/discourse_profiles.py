#!/usr/bin/env python3
"""Perfiles de discurso EXHAUSTIVOS: todo lo que la presidenta dice de cada actor y tema.

Para cada persona/colectivo (tipo PERSONA del grafo unificado, variantes
agrupadas) y cada tema (tipo CONCEPTO), reúne **todas** las menciones del
corpus (hasta ``--max-menciones``, deduplicadas por chunk) con su video,
fecha, timestamp y URL, y las procesa en **map-reduce**:

- *map*: lotes de ``--lote`` evidencias → el modelo extrae TODO lo dicho
  (declaraciones, culpas y rasgos en personas; declaraciones y enfoques en
  temas), cada elemento anclado a su evidencia numerada;
- *reduce*: con el total extraído, el modelo sintetiza cargo, clasificación
  del trato (aliado/adversario/culpado/funcionario/neutral/mixto) o postura
  del tema (positiva/negativa/mixta/neutral), resumen y características.

La salida (``data/perfiles_discurso.json``) es progresiva y reanudable; el
gasto se corta en ``--max-usd`` y al agotarse el motor (3 fallos seguidos)
se guarda lo hecho y se detiene: relanzar retoma.

Uso:
    python scripts/discourse_profiles.py --motor codex-cli --force
    python scripts/discourse_profiles.py --temas 16 --max-personas 40
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
POSTURAS = ("positiva", "negativa", "mixta", "neutral")
TEMAS_EXCLUIR = {
    "pueblo de costa rica", "poblacion costarricense", "presidente de la republica",
    "presidencia de la republica", "presidenta de la republica", "costa rica",
    "gobierno", "gobierno de la republica", "pais", "costarricenses",
}


class MotorAgotado(RuntimeError):
    pass


PROMPT_MAPA_PERSONA = """Analiza este LOTE de evidencias (citas de transcripciones de conferencias, cadenas y \
entrevistas de la presidenta de Costa Rica Laura Fernández, mayo-agosto 2026) y extrae de forma \
EXHAUSTIVA todo lo que la presidenta dice sobre «{nombre}», hace con esa persona o le atribuye.

Devuelve UNICAMENTE este JSON:
{{
  "declaraciones": [{{"sintesis": "qué dijo/hizo la presidenta respecto a la persona, en tercera persona", "evidencia": N}}],
  "culpas": [{{"tema": "de qué la culpa o responsabiliza", "evidencia": N}}],
  "rasgos": ["atributo o caracterización que la presidenta le adjudica"]
}}

Reglas: sé exhaustivo — captura TODA declaración, anuncio, elogio, reproche o interacción relevante \
del lote, pero cada elemento DEBE estar respaldado por el número de su evidencia y no repitas el \
mismo hecho dos veces. Ignora menciones triviales de paso. Listas vacías si no hay nada."""

PROMPT_REDUCE_PERSONA = """Con base en TODO lo extraído del discurso de la presidenta Laura Fernández sobre \
«{nombre}» (rasgos y muestra de declaraciones/culpas abajo), devuelve UNICAMENTE este JSON:
{{
  "cargo": "cargo o rol si se conoce, o \\"\\"",
  "clasificacion": "aliado | adversario | culpado | funcionario | neutral | mixto",
  "resumen": "3-4 frases sobre cómo la presidenta retrata a esta persona en su discurso",
  "caracteristicas": ["rasgos consolidados y deduplicados, máximo 12"]
}}
Clasifica según el TRATO de la presidenta: "aliado" si elogia/colabora; "adversario" si confronta; \
"culpado" si le atribuye culpas; "funcionario" si solo rol oficial sin carga; "mixto" si hay de \
ambos; "neutral" sin señal.

Rasgos extraídos: {rasgos}

Declaraciones ({total_decl} en total, muestra):
{muestra_decl}

Culpas ({total_culpas} en total):
{muestra_culpas}"""

PROMPT_MAPA_TEMA = """Analiza este LOTE de evidencias (citas de transcripciones de la presidenta de Costa Rica \
Laura Fernández, mayo-agosto 2026) y extrae de forma EXHAUSTIVA todo lo que la presidenta dice, \
anuncia o propone sobre el tema «{nombre}».

Devuelve UNICAMENTE este JSON:
{{
  "declaraciones": [{{"sintesis": "qué dijo/anunció/propuso, en tercera persona", "evidencia": N}}],
  "enfoques": ["ángulo concreto desde el que aborda el tema"]
}}

Reglas: sé exhaustivo pero fiel — cada declaración DEBE citar el número de su evidencia; sin \
repeticiones; ignora menciones de paso sin contenido. Listas vacías si no hay nada."""

PROMPT_REDUCE_TEMA = """Con base en TODO lo extraído del discurso de la presidenta sobre el tema «{nombre}» \
(abajo), devuelve UNICAMENTE este JSON:
{{
  "postura": "positiva | negativa | mixta | neutral",
  "resumen": "3-4 frases: cómo enmarca el tema y qué busca comunicar",
  "enfoques": ["enfoques consolidados y deduplicados, máximo 10"]
}}
"positiva" = logro/promesa/causa propia; "negativa" = problema/amenaza/culpa de otros; "mixta" = ambas.

Enfoques extraídos: {rasgos}

Declaraciones ({total_decl} en total, muestra):
{muestra_decl}"""


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
        "presidenta electa", "mandataria", "doña laura",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Perfiles exhaustivos del discurso presidencial (personas y temas).")
    parser.add_argument("--motor", default="codex-cli", choices=["codex-cli", "gemini-cli", "antigravity-cli", "claude-cli", "ollama", "api"])
    parser.add_argument("--modelo", default=None, help="Default: el modelo propio del motor.")
    parser.add_argument("--max-personas", type=int, default=40)
    parser.add_argument("--temas", type=int, default=16, help="Temas con más atención a perfilar (0 = ninguno).")
    parser.add_argument("--max-menciones", type=int, default=250, help="Tope de evidencias por entidad. Default: 250.")
    parser.add_argument("--lote", type=int, default=16, help="Evidencias por llamada de extracción. Default: 16.")
    parser.add_argument("--max-usd", type=float, default=25.0, help="Tope de gasto equivalente. Default: 25.")
    parser.add_argument("--force", action="store_true", help="Rehacer perfiles ya existentes.")
    parser.add_argument("--chroma-dir", type=Path, default=rag.DEFAULT_CHROMA_DIR)
    parser.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    parser.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--embedding-model", default=os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL))
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def _rank(n: dict) -> float:
    try:
        return float((n.get("metrics") or {}).get("pagerank") or 0.0)
    except Exception:
        return 0.0


def _agrupar(candidatos: list[dict], maximo: int) -> list[dict]:
    grupos: list[dict] = []
    for node in candidatos:
        toks = tokens_de(node["label"])
        if not toks:
            continue
        destino = next((g for g in grupos if toks <= g["tokens"] or g["tokens"] <= toks), None)
        if destino is None:
            grupos.append({"tokens": toks, "nodos": [node], "rank": _rank(node)})
            if len(grupos) >= maximo * 2:
                break
        else:
            destino["nodos"].append(node)
            destino["tokens"] = destino["tokens"] | toks
            destino["rank"] += _rank(node)
    grupos.sort(key=lambda g: g["rank"], reverse=True)
    return grupos[:maximo]


def seleccionar_personas(max_personas: int) -> list[dict]:
    data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    personas = [n for n in data["nodes"] if n.get("type") == "PERSONA" and not es_la_presidenta(n.get("label", ""))]
    personas.sort(key=_rank, reverse=True)
    seleccion = []
    for grupo in _agrupar(personas, max_personas):
        nombre = max((n["label"] for n in grupo["nodos"]), key=len)
        alias: set[str] = set()
        for n in grupo["nodos"]:
            alias.add(n["label"])
            alias.update(a for a in (n.get("aliases") or []) if len(a) < 50)
        seleccion.append({"nombre": nombre, "alias": sorted(alias, key=len)})
    return seleccion


def seleccionar_temas(max_temas: int) -> list[dict]:
    data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    candidatos = [
        n for n in data["nodes"]
        if n.get("type") == "CONCEPTO" and norm(n.get("label", "")) not in TEMAS_EXCLUIR
        and not es_la_presidenta(n.get("label", "")) and len(n.get("label", "")) <= 45
    ]
    candidatos.sort(key=_rank, reverse=True)
    seleccion = []
    for grupo in _agrupar(candidatos, max_temas):
        nombre = min((n["label"] for n in grupo["nodos"]), key=len)
        seleccion.append({"nombre": nombre, "alias": sorted({n["label"] for n in grupo["nodos"]}, key=len)})
    return seleccion


def evidencias_de(entidad: dict, rag_args: argparse.Namespace, limite: int) -> list[dict]:
    terminos = [entidad["nombre"]]
    for a in entidad["alias"]:
        if norm(a) != norm(entidad["nombre"]) and len(a) >= 5:
            terminos.append(a)
        if len(terminos) >= 4:
            break
    evidencias: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    for termino in terminos:
        try:
            matches = rag.search_mentions(rag_args, termino, whole_word=False, context_chars=380, limit=600)
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


def _con_fuente(item: dict, campo: str, evidencias: list[dict]) -> dict | None:
    valor = str(item.get(campo) or "").strip()
    if not valor:
        return None
    fuente = next((e for e in evidencias if e["n"] == item.get("evidencia")), None) or {}
    return {
        campo: valor,
        "cita": fuente.get("cita", ""),
        "video": fuente.get("video", ""),
        "fecha": fuente.get("fecha", ""),
        "timestamp": fuente.get("timestamp", ""),
        "url": fuente.get("url", ""),
    }


def extraer_map_reduce(
    entidad: dict,
    evidencias: list[dict],
    motor,
    args: argparse.Namespace,
    prompt_mapa: str,
    prompt_reduce: str,
    con_culpas: bool,
) -> dict:
    declaraciones: list[dict] = []
    culpas: list[dict] = []
    rasgos: list[str] = []
    vistas: set[str] = set()
    fallos = 0
    lotes = [evidencias[i:i + args.lote] for i in range(0, len(evidencias), args.lote)]
    for numero, lote in enumerate(lotes, start=1):
        if motor.coste >= args.max_usd:
            log(f"  tope de ${args.max_usd:.2f} alcanzado en el lote {numero}/{len(lotes)}")
            break
        listado = "\n".join(f"[{e['n']}] ({e['video'][:55]}, {e['fecha']}) \"{e['cita'][:400]}\"" for e in lote)
        try:
            r = motor.json(prompt_mapa.format(nombre=entidad["nombre"], evidencias="") + "\n\nEvidencias:\n" + listado, max_tokens=6000)
            fallos = 0
        except ErrorMotor as exc:
            fallos += 1
            log(f"  lote {numero}/{len(lotes)}: fallo del motor ({str(exc)[:100]})")
            if fallos >= 3:
                raise MotorAgotado(str(exc))
            continue
        for item in r.get("declaraciones") or []:
            if isinstance(item, dict):
                anexo = _con_fuente(item, "sintesis", evidencias)
                if anexo and norm(anexo["sintesis"]) not in vistas:
                    vistas.add(norm(anexo["sintesis"]))
                    declaraciones.append(anexo)
        if con_culpas:
            for item in r.get("culpas") or []:
                if isinstance(item, dict):
                    anexo = _con_fuente(item, "tema", evidencias)
                    if anexo:
                        culpas.append(anexo)
            rasgos.extend(str(x).strip() for x in (r.get("rasgos") or []) if str(x).strip())
        else:
            rasgos.extend(str(x).strip() for x in (r.get("enfoques") or []) if str(x).strip())

    muestra_decl = "\n".join(f"- {d['sintesis']}" for d in declaraciones[:60])
    muestra_culpas = "\n".join(f"- {c['tema']}" for c in culpas[:30]) or "(ninguna)"
    rasgos_txt = "; ".join(dict.fromkeys(rasgos))[:1800] or "(ninguno)"
    try:
        sintesis = motor.json(
            prompt_reduce.format(
                nombre=entidad["nombre"], rasgos=rasgos_txt,
                total_decl=len(declaraciones), muestra_decl=muestra_decl or "(ninguna)",
                total_culpas=len(culpas), muestra_culpas=muestra_culpas,
            ),
            max_tokens=3000,
        )
    except ErrorMotor as exc:
        raise MotorAgotado(str(exc))
    return {"declaraciones": declaraciones, "culpas": culpas, "rasgos": rasgos, "sintesis": sintesis}


def perfilar_persona(persona: dict, evidencias: list[dict], motor, args: argparse.Namespace) -> dict:
    r = extraer_map_reduce(persona, evidencias, motor, args, PROMPT_MAPA_PERSONA, PROMPT_REDUCE_PERSONA, con_culpas=True)
    s = r["sintesis"]
    clasificacion = str(s.get("clasificacion") or "neutral").lower().strip()
    if clasificacion not in CLASIFICACIONES:
        clasificacion = "neutral"
    return {
        "nombre": persona["nombre"],
        "alias": persona["alias"][:8],
        "cargo": str(s.get("cargo") or "").strip(),
        "clasificacion": clasificacion,
        "resumen": str(s.get("resumen") or "").strip(),
        "caracteristicas": [str(c).strip() for c in (s.get("caracteristicas") or []) if str(c).strip()][:12],
        "culpas": r["culpas"],
        "declaraciones": r["declaraciones"],
        "menciones_analizadas": len(evidencias),
        "generado": datetime.now(timezone.utc).isoformat(),
    }


def perfilar_tema(tema: dict, evidencias: list[dict], atencion: int, motor, args: argparse.Namespace) -> dict:
    r = extraer_map_reduce(tema, evidencias, motor, args, PROMPT_MAPA_TEMA, PROMPT_REDUCE_TEMA, con_culpas=False)
    s = r["sintesis"]
    postura = str(s.get("postura") or "neutral").lower().strip()
    if postura not in POSTURAS:
        postura = "neutral"
    return {
        "nombre": tema["nombre"],
        "postura": postura,
        "menciones": atencion,
        "resumen": str(s.get("resumen") or "").strip(),
        "enfoques": [str(e).strip() for e in (s.get("enfoques") or []) if str(e).strip()][:10],
        "declaraciones": r["declaraciones"],
        "generado": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    args = parse_args()
    if not GRAPH_JSON.exists():
        raise SystemExit("No existe el grafo unificado.")
    salida = {"generado": None, "motor": None, "perfiles": [], "temas": []}
    if OUTPUT.exists():
        previa = json.loads(OUTPUT.read_text(encoding="utf-8"))
        salida.update({k: previa.get(k, salida[k]) for k in salida})
    hechos = {p["nombre"] for p in salida["perfiles"]}
    hechos_temas = {t["nombre"] for t in salida["temas"]}

    rag_args = argparse.Namespace(
        chroma_dir=args.chroma_dir, collection=args.collection,
        ollama_host=args.ollama_host, embedding_model=args.embedding_model,
    )
    motor = crear_motor(args.motor, args.modelo)

    def guardar():
        salida["generado"] = datetime.now(timezone.utc).isoformat()
        salida["motor"] = f"{motor.nombre}/{motor.modelo} (exhaustivo)"
        OUTPUT.write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    personas = seleccionar_personas(args.max_personas)
    temas = seleccionar_temas(args.temas) if args.temas else []
    log(f"exhaustivo con {motor.nombre}/{motor.modelo}: {len(personas)} personas · {len(temas)} temas · tope ${args.max_usd}")

    try:
        for index, persona in enumerate(personas, start=1):
            if persona["nombre"] in hechos and not args.force:
                continue
            if motor.coste >= args.max_usd:
                log("tope de gasto alcanzado; se detiene")
                break
            evidencias = evidencias_de(persona, rag_args, args.max_menciones)
            if not evidencias:
                continue
            log(f"[persona {index}/{len(personas)}] {persona['nombre']} · {len(evidencias)} evidencias · ${motor.coste:.2f}")
            perfil = perfilar_persona(persona, evidencias, motor, args)
            salida["perfiles"] = [p for p in salida["perfiles"] if p["nombre"] != persona["nombre"]]
            salida["perfiles"].append(perfil)
            guardar()
            log(f"  → {perfil['clasificacion']} · {len(perfil['declaraciones'])} declaraciones · {len(perfil['culpas'])} culpas")

        for index, tema in enumerate(temas, start=1):
            if tema["nombre"] in hechos_temas and not args.force:
                continue
            if motor.coste >= args.max_usd:
                log("tope de gasto alcanzado; se detiene")
                break
            evidencias = evidencias_de(tema, rag_args, args.max_menciones)
            if not evidencias:
                continue
            try:
                atencion = len(rag.search_mentions(rag_args, tema["nombre"], whole_word=False, context_chars=60, limit=900))
            except Exception:
                atencion = len(evidencias)
            log(f"[tema {index}/{len(temas)}] {tema['nombre']} · {len(evidencias)} evidencias · {atencion} menciones · ${motor.coste:.2f}")
            perfil_tema = perfilar_tema(tema, evidencias, atencion, motor, args)
            salida["temas"] = [t for t in salida["temas"] if t["nombre"] != tema["nombre"]]
            salida["temas"].append(perfil_tema)
            guardar()
            log(f"  → {perfil_tema['postura']} · {len(perfil_tema['declaraciones'])} declaraciones")
    except MotorAgotado as exc:
        log(f"motor agotado ({str(exc)[:120]}); lo hecho quedó guardado — relanzar retoma")

    guardar()
    log(f"listo: {len(salida['perfiles'])} perfiles y {len(salida['temas'])} temas · coste ${motor.coste:.2f} ({motor.llamadas} llamadas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
