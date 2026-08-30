#!/usr/bin/env python3
"""Fusión manual de entradas del directorio de personas (curaduría).

Une una o más entradas de ``data/personas_index.json`` en una entrada
destino: las referencias se suman (deduplicadas por URL+timestamp), las
etiquetas y alias de los orígenes quedan como alias del destino, y los
orígenes desaparecen del directorio. Después hay que regenerar el sitio
(``build_site.py``).

Uso:
    python scripts/merge_people.py --destino "Orlando Aguirre Gómez" \\
        "don Catalino Orlando Aguirre" "Presidente de la Corte del Poder Judicial"
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

INDICE = Path("data/personas_index.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fusiona entradas del directorio en una persona destino.")
    parser.add_argument("--destino", required=True, help="Nombre exacto de la entrada que sobrevive.")
    parser.add_argument("origenes", nargs="+", help="Nombres exactos de las entradas a absorber.")
    args = parser.parse_args()

    data = json.loads(INDICE.read_text(encoding="utf-8"))
    por_nombre = {p["nombre"]: p for p in data["personas"]}
    destino = por_nombre.get(args.destino)
    if destino is None:
        raise SystemExit(f"No existe la entrada destino: {args.destino!r}")

    vistos = {(r.get("url"), r.get("timestamp")) for r in destino.get("referencias", [])}
    for nombre in args.origenes:
        origen = por_nombre.get(nombre)
        if origen is None:
            print(f"  ! no existe: {nombre!r} (omitido)")
            continue
        if origen is destino:
            continue
        nuevos = 0
        for ref in origen.get("referencias", []):
            clave = (ref.get("url"), ref.get("timestamp"))
            if clave not in vistos:
                destino.setdefault("referencias", []).append(ref)
                vistos.add(clave)
                nuevos += 1
        alias = set(destino.get("alias", [])) | {origen["nombre"]} | set(origen.get("alias", []))
        destino["alias"] = sorted(alias, key=len)[:16]
        if len(origen.get("descripcion", "")) > len(destino.get("descripcion", "")):
            destino["descripcion"] = origen["descripcion"]
        for rel in origen.get("relaciones", []):
            if rel not in destino.setdefault("relaciones", []):
                destino["relaciones"].append(rel)
        data["personas"].remove(origen)
        print(f"  fusionado: {nombre!r} → {args.destino!r} (+{nuevos} referencias)")

    destino["menciones"] = len(destino.get("referencias", []))
    destino["relaciones"] = destino.get("relaciones", [])[:10]
    destino["tipo"] = "persona"
    data["generado"] = datetime.now(timezone.utc).isoformat()
    INDICE.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{args.destino!r}: {destino['menciones']} menciones · {len(destino['alias'])} alias · directorio: {len(data['personas'])} entradas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
