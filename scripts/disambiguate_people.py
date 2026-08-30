#!/usr/bin/env python3
"""Desambigua el directorio de personas: personas vs cargos vs colectivos.

El grafo mezcla nombres propios («Rodrigo Chaves Robles») con designaciones
de cargo («expresidente de la República», «ministro de la Presidencia») y
colectivos («diputados»). Este pase clasifica cada entrada de
``data/personas_index.json`` con un modelo y:

- si una entrada tipo **cargo** designa claramente a una persona del
  catálogo (p. ej. «expresidente» → Rodrigo Chaves), **la fusiona**: sus
  referencias se suman a la persona (deduplicadas), la etiqueta queda como
  alias, y la entrada desaparece del directorio;
- las demás entradas quedan tipificadas (``tipo``: persona / cargo /
  colectivo / otro) para poder filtrarlas en la página.

Uso:
    python scripts/disambiguate_people.py                 # gemini-cli
    python scripts/disambiguate_people.py --motor codex-cli --modelo gpt-5.6-terra
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

INDICE = Path("data/personas_index.json")
TIPOS = ("persona", "cargo", "colectivo", "otro")

PROMPT = """Estás depurando el directorio de actores mencionados en el discurso de la presidenta de \
Costa Rica Laura Fernández (2026). Clasifica cada entrada numerada:

- "persona": nombre propio de un individuo (aunque incluya el cargo entre paréntesis);
- "cargo": designación de un puesto SIN nombre propio («ministro de Seguridad», «expresidente de la República», «fiscal general»);
- "colectivo": grupo de gente («diputados», «familias costarricenses», «oficiales de policía»);
- "otro": no es un actor real (fragmento sin sentido, objeto, error de transcripción).

Además, si una entrada tipo "cargo" (o una variante de nombre) designa INEQUÍVOCAMENTE a una de las \
personas del catálogo de abajo en el contexto de Costa Rica 2026, indica su nombre EXACTO del \
catálogo en "refiere_a"; si hay cualquier duda, null. Contexto útil: la presidenta es Laura \
Fernández; el expresidente inmediato es Rodrigo Chaves Robles, hoy ministro de la Presidencia y de \
Hacienda; el fiscal general es Carlos Díaz.

Devuelve UNICAMENTE: {{"clasificaciones": [{{"i": 1, "tipo": "…", "refiere_a": null}}, …]}} — una por entrada.

Catálogo de personas conocidas:
{catalogo}

Entradas:
{entradas}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Desambiguación persona/cargo/colectivo del directorio.")
    parser.add_argument("--motor", default="gemini-cli", choices=["gemini-cli", "codex-cli", "antigravity-cli", "claude-cli", "ollama", "api"])
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--lote", type=int, default=60)
    parser.add_argument("--max-usd", type=float, default=6.0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def log(mensaje: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}", flush=True)


def main() -> int:
    args = parse_args()
    data = json.loads(INDICE.read_text(encoding="utf-8"))
    personas = data["personas"]
    pendientes = [p for p in personas if args.force or "tipo" not in p]
    catalogo_lista = [p["nombre"] for p in sorted(personas, key=lambda x: -x["menciones"]) if p.get("destacado")][:60]
    catalogo = "\n".join(f"- {n}" for n in catalogo_lista)
    por_nombre = {p["nombre"]: p for p in personas}
    motor = crear_motor(args.motor, args.modelo)
    log(f"entradas por clasificar: {len(pendientes)} · motor {motor.nombre}/{motor.modelo}")

    fusiones = 0
    absorber: dict[str, str] = {}
    for inicio in range(0, len(pendientes), args.lote):
        if motor.coste >= args.max_usd:
            log("tope de gasto; el resto queda sin clasificar (relanzar retoma)")
            break
        lote = pendientes[inicio:inicio + args.lote]
        entradas = "\n".join(
            f"{i + 1}. «{p['nombre']}» — {p.get('descripcion', '')[:110]} ({p['menciones']} menciones)"
            for i, p in enumerate(lote)
        )
        try:
            r = motor.json(PROMPT.format(catalogo=catalogo, entradas=entradas), max_tokens=6000)
        except ErrorMotor as exc:
            log(f"  lote {inicio // args.lote + 1}: fallo del motor ({str(exc)[:100]})")
            continue
        for item in r.get("clasificaciones") or []:
            if not isinstance(item, dict):
                continue
            try:
                persona = lote[int(item.get("i")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            tipo = str(item.get("tipo") or "persona").lower().strip()
            persona["tipo"] = tipo if tipo in TIPOS else "persona"
            refiere = item.get("refiere_a")
            if refiere and refiere in por_nombre and refiere != persona["nombre"] and not persona.get("destacado"):
                absorber[persona["nombre"]] = refiere
        log(f"  lote {inicio // args.lote + 1}/{(len(pendientes) - 1) // args.lote + 1} · ${motor.coste:.2f}")

    for nombre, destino_nombre in absorber.items():
        origen = por_nombre.get(nombre)
        destino = por_nombre.get(destino_nombre)
        if not origen or not destino or origen is destino:
            continue
        vistos = {(r.get("url"), r.get("timestamp")) for r in destino.get("referencias", [])}
        for ref in origen.get("referencias", []):
            if (ref.get("url"), ref.get("timestamp")) not in vistos:
                destino.setdefault("referencias", []).append(ref)
                vistos.add((ref.get("url"), ref.get("timestamp")))
        alias = set(destino.get("alias", [])) | {origen["nombre"]} | set(origen.get("alias", []))
        destino["alias"] = sorted(alias, key=len)[:14]
        destino["menciones"] = len(destino.get("referencias", []))
        personas.remove(origen)
        fusiones += 1
        log(f"  fusionado: «{nombre}» → {destino_nombre}")

    data["generado"] = datetime.now(timezone.utc).isoformat()
    INDICE.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    from collections import Counter
    conteo = Counter(p.get("tipo", "persona") for p in personas)
    log(f"listo: {len(personas)} entradas · {dict(conteo)} · {fusiones} fusionadas · ${motor.coste:.2f} ({motor.llamadas} llamadas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
