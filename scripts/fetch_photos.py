#!/usr/bin/env python3
"""Busca retratos con licencia libre para los perfiles (Wikipedia/Wikimedia).

Para cada persona de ``data/perfiles_discurso.json`` consulta la Wikipedia en
español: si existe un artículo cuyo título coincide con el nombre (se exige
compartir el apellido y al menos dos tokens significativos, para no traer a un
homónimo), descarga la imagen principal del artículo a
``docs/fotos/<slug>.jpg`` y registra la fuente y licencia en
``docs/fotos/creditos.json``. Los colectivos y quienes no tengan artículo
quedan sin foto (el sitio usa un avatar de iniciales).

Las fotos que consigas manualmente van en la misma carpeta con el mismo
esquema de nombre (``<slug>.jpg``) y tienen prioridad: este script no
sobrescribe archivos existentes.

Uso:
    python scripts/fetch_photos.py
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

PERFILES = Path("data/perfiles_discurso.json")
FOTOS = Path("docs/fotos")
CREDITOS = FOTOS / "creditos.json"
API = "https://es.wikipedia.org/w/api.php"
UA = {"User-Agent": "HemerotecaPresidencial/1.0 (https://github.com/Lapeyus/presidencial)"}
STOP = {"de", "del", "la", "el", "los", "las", "don", "dona", "doña", "jesus", "maria"}


def slug(value: str, limit: int = 60) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")[:limit]


def tokens(value: str) -> set[str]:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return {t for t in re.findall(r"[a-z]+", value) if t not in STOP and len(t) > 2}


def api_get(params: dict) -> dict:
    params = {"format": "json", "formatversion": "2", **params}
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def buscar_pagina(nombre: str) -> dict | None:
    data = api_get({"action": "query", "titles": nombre, "prop": "pageimages|info", "inprop": "url",
                    "pithumbsize": "480", "redirects": "1"})
    paginas = (data.get("query") or {}).get("pages") or []
    for pagina in paginas:
        if pagina.get("missing"):
            continue
        toks_nombre, toks_titulo = tokens(nombre), tokens(pagina.get("title", ""))
        if len(toks_nombre & toks_titulo) < 2:
            continue
        if not (toks_titulo <= toks_nombre or toks_nombre <= toks_titulo):
            continue
        if pagina.get("thumbnail", {}).get("source"):
            return pagina
    return None


def licencia_de(archivo: str) -> dict:
    try:
        data = api_get({"action": "query", "titles": f"File:{archivo}", "prop": "imageinfo",
                        "iiprop": "extmetadata|url"})
        info = ((data.get("query") or {}).get("pages") or [{}])[0].get("imageinfo") or [{}]
        meta = info[0].get("extmetadata") or {}

        def campo(clave):
            return re.sub(r"<[^>]+>", "", str((meta.get(clave) or {}).get("value") or "")).strip()

        return {"licencia": campo("LicenseShortName"), "autor": campo("Artist")[:120]}
    except Exception:
        return {}


def main() -> int:
    perfiles = json.loads(PERFILES.read_text(encoding="utf-8"))["perfiles"]
    FOTOS.mkdir(parents=True, exist_ok=True)
    creditos = json.loads(CREDITOS.read_text(encoding="utf-8")) if CREDITOS.exists() else {}

    conseguidas = 0
    for perfil in perfiles:
        nombre = perfil["nombre"]
        destino = FOTOS / f"{slug(nombre)}.jpg"
        if destino.exists():
            continue
        try:
            pagina = buscar_pagina(nombre)
        except Exception as exc:
            print(f"  ! {nombre}: {exc}")
            continue
        if pagina is None:
            print(f"  - {nombre}: sin artículo/foto verificable")
            continue
        thumb = pagina["thumbnail"]["source"]
        try:
            with urllib.request.urlopen(urllib.request.Request(thumb, headers=UA), timeout=30) as r:
                destino.write_bytes(r.read())
        except Exception as exc:
            print(f"  ! {nombre}: fallo descargando ({exc})")
            continue
        archivo = pagina.get("pageimage", "")
        creditos[slug(nombre)] = {
            "persona": nombre,
            "wikipedia": pagina.get("fullurl", ""),
            "archivo": archivo,
            **licencia_de(archivo),
        }
        conseguidas += 1
        print(f"  ✓ {nombre} ← {pagina.get('title')} ({creditos[slug(nombre)].get('licencia', '?')})")

    CREDITOS.write_text(json.dumps(creditos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"fotos nuevas: {conseguidas} · total en carpeta: {len(list(FOTOS.glob('*.jpg')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
