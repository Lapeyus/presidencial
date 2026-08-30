#!/usr/bin/env python3
"""Estudios ampliables sobre el discurso: v1 — posiciones contradictorias por tema.

Marco pensado para ir agregando lentes de análisis sobre el mismo dataset
(``data/perfiles_discurso.json``, que ya cruza el corpus, la base vectorial y
el grafo). Cada estudio escribe su resultado bajo ``estudios.<nombre>`` y el
sitio lo renderiza como sección propia.

Estudio ``contradicciones``: por cada tema perfilado, entrega al modelo todas
sus declaraciones fechadas y le exige detectar **tensiones reales** —
cambios de postura en el tiempo, promesas vs acciones, cifras inconsistentes
o tensión retórica — cada una anclada a las dos declaraciones enfrentadas
(con fecha, cita y enlace a la fuente). El prompt pide ser exigente: mejor
lista vacía que contradicción inventada.

Uso:
    python scripts/discourse_studies.py                     # gemini-cli
    python scripts/discourse_studies.py --tema seguridad
    python scripts/discourse_studies.py --motor codex-cli --modelo gpt-5.6-terra
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))

from textgraph.llm import ErrorMotor, crear_motor  # noqa: E402

OUTPUT = Path("data/perfiles_discurso.json")
TIPOS = ("cambio_de_postura", "promesa_vs_accion", "cifras_inconsistentes", "tension_retorica")
SEVERIDADES = ("alta", "media", "baja")

PROMPT_CONTRADICCIONES = """Eres un analista de discurso exigente. Abajo están, numeradas y con fecha, las \
declaraciones de la presidenta de Costa Rica Laura Fernández sobre el tema «{tema}» (mayo-agosto \
2026). Identifica ÚNICAMENTE posiciones en tensión REAL entre pares de declaraciones:

- "cambio_de_postura": dijo una cosa y después la contraria;
- "promesa_vs_accion": promete algo y luego relata acciones que lo contradicen;
- "cifras_inconsistentes": números incompatibles sobre lo mismo;
- "tension_retorica": marcos incompatibles (p. ej. celebrar y culpar por lo mismo).

Devuelve UNICAMENTE este JSON:
{{"hallazgos": [{{"tipo": "…", "severidad": "alta|media|baja", "descripcion": "1-2 frases neutras \
explicando la tensión", "a": N, "b": M}}]}}

Reglas: sé MUY exigente — una evolución razonable o dos matices compatibles NO son contradicción; \
mejor lista vacía que un hallazgo forzado. "a" y "b" son números de declaraciones que se tensionan \
entre sí. Máximo 5 hallazgos, los más sólidos.

Declaraciones:
{declaraciones}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estudios de discurso: contradicciones por tema.")
    parser.add_argument("--estudio", default="contradicciones", choices=["contradicciones"])
    parser.add_argument("--motor", default="gemini-cli", choices=["gemini-cli", "codex-cli", "antigravity-cli", "claude-cli", "ollama", "api"])
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--tema", help="Limitar a un tema (substring, insensible a mayúsculas).")
    parser.add_argument("--min-declaraciones", type=int, default=6)
    parser.add_argument("--max-usd", type=float, default=6.0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def estudiar_contradicciones(salida: dict, motor, args: argparse.Namespace) -> None:
    estudios = salida.setdefault("estudios", {})
    resultados = estudios.setdefault("contradicciones", [])
    hechos = {r["tema"] for r in resultados}

    temas = salida.get("temas", [])
    if args.tema:
        temas = [t for t in temas if args.tema.lower() in t["nombre"].lower()]
    for tema in temas:
        if tema["nombre"] in hechos and not args.force:
            continue
        if motor.coste >= args.max_usd:
            log("tope de gasto alcanzado; se detiene")
            break
        declaraciones = [d for d in tema.get("declaraciones", []) if d.get("sintesis")]
        if len(declaraciones) < args.min_declaraciones:
            continue
        listado = "\n".join(f"{i + 1}. ({d.get('fecha', '¿?')}) {d['sintesis'][:260]}" for i, d in enumerate(declaraciones))
        log(f"{tema['nombre']}: {len(declaraciones)} declaraciones · ${motor.coste:.2f}")
        try:
            r = motor.json(PROMPT_CONTRADICCIONES.format(tema=tema["nombre"], declaraciones=listado), max_tokens=4000)
        except ErrorMotor as exc:
            log(f"  fallo del motor: {str(exc)[:120]}")
            continue

        hallazgos = []
        for h in (r.get("hallazgos") or [])[:5]:
            if not isinstance(h, dict):
                continue
            tipo = str(h.get("tipo") or "").strip()
            severidad = str(h.get("severidad") or "media").strip()
            try:
                a = declaraciones[int(h.get("a")) - 1]
                b = declaraciones[int(h.get("b")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            if tipo not in TIPOS or a is b:
                continue
            recorte = lambda d: {k: d.get(k, "") for k in ("sintesis", "cita", "fecha", "video", "timestamp", "url")}
            hallazgos.append(
                {
                    "tipo": tipo,
                    "severidad": severidad if severidad in SEVERIDADES else "media",
                    "descripcion": str(h.get("descripcion") or "").strip(),
                    "a": recorte(a),
                    "b": recorte(b),
                }
            )
        estudios["contradicciones"] = [r for r in estudios["contradicciones"] if r["tema"] != tema["nombre"]]
        estudios["contradicciones"].append(
            {"tema": tema["nombre"], "hallazgos": hallazgos, "generado": datetime.now(timezone.utc).isoformat()}
        )
        OUTPUT.write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"  → {len(hallazgos)} tensiones")


def main() -> int:
    args = parse_args()
    salida = json.loads(OUTPUT.read_text(encoding="utf-8"))
    motor = crear_motor(args.motor, args.modelo)
    log(f"estudio {args.estudio} con {motor.nombre}/{motor.modelo}")
    estudiar_contradicciones(salida, motor, args)
    total = sum(len(r["hallazgos"]) for r in salida.get("estudios", {}).get("contradicciones", []))
    log(f"listo: {total} tensiones en {len(salida.get('estudios', {}).get('contradicciones', []))} temas · ${motor.coste:.2f} ({motor.llamadas} llamadas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
