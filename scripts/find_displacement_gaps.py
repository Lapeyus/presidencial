#!/usr/bin/env python3
"""Busca casos de desplazamiento de responsabilidad NO documentados en el informe.

Barre el corpus con los marcadores léxicos de las siete formas semánticas del
informe (agencialidad asimétrica, eslabón roto, triangulación, teatralización
del desaire, imposibilidad estructural, deslegitimación y prolepsis), descarta
los pasajes que ya están documentados —por video y proximidad temporal a los
casos con enlace del informe— y somete el resto a un modelo que confirma si
hay desplazamiento real, lo clasifica por forma y valora su fuerza retórica.

Salida: ``data/desplazamiento_nuevos.json`` con los casos nuevos, su cita, su
enlace con marca de tiempo y la clasificación.

Uso:
    python scripts/find_displacement_gaps.py --motor gemini-cli
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))

from scripts import rag_transcripts as rag  # noqa: E402
from textgraph.llm import ErrorMotor, crear_motor  # noqa: E402

TRANSCRIPTS = Path("data/presidencia_transcripts.jsonl")
DOCUMENTADOS = Path("/tmp/casos_documentados.json")
OUTPUT = Path("data/desplazamiento_nuevos.json")
DESDE = "20260508"
VENTANA_DUPLICADO = 420  # segundos alrededor de un caso ya documentado

MARCADORES = {
    "V-imposibilidad": r"(manos (nos quedan |quedan )?atadas|no (nos |me )?(lo )?permite la ley|la ley no (nos|me) (deja|permite)|no est[aá] en (mis|nuestras) manos|no depende de (m[ií]|nosotros|este gobierno)|no tengo la (potestad|competencia|facultad)|no me corresponde|amarrad[oa]s de manos|camisa de fuerza|regla fiscal (no|nos)|trabas)",
    "II-eslabon": r"(nosotros (hacemos|hicimos|ya hicimos) (lo )?nuestr[oa]|hasta ah[ií] llega (nuestra|mi)|nuestra parte (ya )?(est[aá]|la) (hecha|hicimos|cumplida)|los capturamos y|nosotros los (capturamos|detenemos)[^.]{0,80}(sueltan|libera|deja)|el (último )?eslab[oó]n|se cae en (los tribunales|el poder judicial)|de nada sirve que)",
    "III-triangulacion": r"(a ellos (es a quienes|hay que|tienen que)|p[ií]danle[ns] cuentas|preg[uú]ntenle[ns] a (ellos|los diputados|los magistrados)|exijan(le)?[ns]? (a ellos|cuentas)|reclamen(le)?[ns]? a|que le[s]? respondan a ustedes|el pueblo (les|le) tiene que (exigir|pedir))",
    "IV-desaire": r"(no (llegaron|vinieron|asistieron|estuvieron)|sillas vac[ií]as|no se present(ó|aron)|brill(ó|aron) por su ausencia|los invit[eé] (personalmente|a todos)|falta (absoluta )?de voluntad pol[ií]tica|le he tendido la mano|abr[ií] las puertas)",
    "VI-deslegitimacion": r"(garantis(mo|ta)s?|progresistas que|jueces que (sueltan|liberan)|puerta giratoria|c[oó]mplices|alcahueter[ií]a|se hacen (los |la )?(de la vista gorda|ciegos)|est[aá]n del lado de los (delincuentes|criminales)|defienden a los (delincuentes|criminales))",
    "VII-prolepsis": r"(que (después )?no (vengan|venga) a decir|si esto no (sale|se aprueba|camina)|va a ser (culpa|responsabilidad) de|que quede claro qui[eé]n|van a tener que (explicar|responder)|si no (lo )?aprueban|el que no (vote|firme))",
    "I-agencialidad": r"(de sol a sol|alma, coraz[oó]n y vida|toda mi fuerza|me part[oi] el lomo|amarr(arme|arse) las enaguas|comerme la bronca|yo (he |ya )?(puesto|dado) (todo|la cara)|trabajando sin descanso|no vamos a aflojar)",
}

PROMPT = """Eres analista de discurso político. Cada fragmento numerado proviene de transcripciones de la \
presidenta de Costa Rica Laura Fernández (mayo–agosto 2026). Determina en cuáles hay **desplazamiento de \
responsabilidad**: la hablante atribuye el fracaso, la demora o el límite de una política pública a otro \
actor (Asamblea Legislativa, Poder Judicial, jueces, fiscalía, marco legal, gobierno anterior) o a una \
restricción estructural, preservando o realzando la responsabilidad cumplida del Ejecutivo.

Formas semánticas:
I  = agencialidad asimétrica (hiperagencia propia frente a pasividad ajena)
II = eslabón roto (el Ejecutivo cumple su fase; la cadena se rompe en la fase del otro)
III= triangulación (instruye al público a pedir cuentas a un tercero)
IV = teatralización del desaire (exhibe la ausencia o el desdén del otro)
V  = imposibilidad estructural, legal o presupuestaria
VI = deslegitimación ideológica o moral del contrapeso
VII= prolepsis (asigna por anticipado la culpa de un fracaso futuro)

Devuelve UNICAMENTE:
{{"casos": [{{"i": N, "hay_desplazamiento": true, "formas": ["II","III"], "actor_senalado": "...", \
"ambito": "tema en 3-6 palabras", "fuerza": "alta|media-alta|media", "cita": "la oración literal más \
representativa copiada del fragmento", "lectura": "1-2 frases de análisis"}}]}}

Sé exigente: una crítica genérica o una mención de otro poder sin transferencia de responsabilidad NO es \
desplazamiento. Omite del arreglo los fragmentos que no califiquen.

Fragmentos:
{fragmentos}"""


def norm(t: str) -> str:
    t = unicodedata.normalize("NFD", str(t).lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Casos de desplazamiento no documentados.")
    p.add_argument("--motor", default="gemini-cli", choices=["gemini-cli", "codex-cli", "antigravity-cli", "claude-cli", "ollama"])
    p.add_argument("--modelo", default=None)
    p.add_argument("--lote", type=int, default=12)
    p.add_argument("--max-usd", type=float, default=8.0)
    p.add_argument("--max-candidatos", type=int, default=180)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    documentados = json.loads(DOCUMENTADOS.read_text())
    doc_por_video: dict[str, list[int]] = {}
    for d in documentados:
        doc_por_video.setdefault(d["video_id"], []).append(d["t"])

    registros = [json.loads(l) for l in TRANSCRIPTS.open(encoding="utf-8") if l.strip()]
    registros = [r for r in registros if (r.get("upload_date") or "") >= DESDE]
    patrones = {k: re.compile(v, re.IGNORECASE) for k, v in MARCADORES.items()}

    candidatos: list[dict] = []
    vistos: set[tuple[str, int]] = set()
    for registro in registros:
        video_id = registro.get("video_id") or ""
        segmentos = (registro.get("transcript") or {}).get("segments") or []
        if not segmentos:
            continue
        # texto por ventanas de segmentos, conservando el tiempo de inicio
        for i in range(0, len(segmentos), 6):
            bloque = segmentos[i:i + 12]
            texto = " ".join(str(s.get("text") or "") for s in bloque)
            if len(texto) < 120:
                continue
            inicio = bloque[0].get("start")
            if not isinstance(inicio, (int, float)):
                continue
            formas = [k for k, px in patrones.items() if px.search(texto)]
            if not formas:
                continue
            # descartar lo ya documentado
            if any(abs(inicio - t) <= VENTANA_DUPLICADO for t in doc_por_video.get(video_id, [])):
                continue
            clave = (video_id, int(inicio // 90))
            if clave in vistos:
                continue
            vistos.add(clave)
            candidatos.append({
                "video_id": video_id,
                "titulo": registro.get("title", "")[:70],
                "fecha": rag.format_upload_date(str(registro.get("upload_date", ""))),
                "inicio": float(inicio),
                "timestamp": rag.format_timestamp(inicio),
                "url": f"https://youtu.be/{video_id}?t={int(inicio)}s",
                "marcadores": formas,
                "texto": re.sub(r"\s+", " ", texto)[:900],
            })

    candidatos.sort(key=lambda c: (-len(c["marcadores"]), c["fecha"]))
    candidatos = candidatos[: args.max_candidatos]
    print(f"candidatos tras filtrar duplicados: {len(candidatos)}")

    motor = crear_motor(args.motor, args.modelo)
    nuevos: list[dict] = []
    for inicio in range(0, len(candidatos), args.lote):
        if motor.coste >= args.max_usd:
            print("tope de gasto alcanzado")
            break
        lote = candidatos[inicio:inicio + args.lote]
        fragmentos = "\n\n".join(
            f"[{i + 1}] ({c['fecha']} · {c['titulo']} · {c['timestamp']}) «{c['texto']}»"
            for i, c in enumerate(lote)
        )
        try:
            r = motor.json(PROMPT.format(fragmentos=fragmentos), max_tokens=6000)
        except ErrorMotor as exc:
            print(f"  lote {inicio // args.lote + 1}: fallo ({str(exc)[:90]})")
            continue
        confirmados = 0
        for item in r.get("casos") or []:
            if not isinstance(item, dict) or not item.get("hay_desplazamiento"):
                continue
            try:
                base = lote[int(item.get("i")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            nuevos.append({
                **{k: base[k] for k in ("video_id", "titulo", "fecha", "timestamp", "url")},
                "formas": item.get("formas") or [],
                "actor_senalado": str(item.get("actor_senalado") or "").strip(),
                "ambito": str(item.get("ambito") or "").strip(),
                "fuerza": str(item.get("fuerza") or "media").strip(),
                "cita": str(item.get("cita") or "").strip(),
                "lectura": str(item.get("lectura") or "").strip(),
                "marcadores": base["marcadores"],
            })
            confirmados += 1
        print(f"  lote {inicio // args.lote + 1}/{(len(candidatos) - 1) // args.lote + 1}: {confirmados} confirmados · ${motor.coste:.2f}")

    nuevos.sort(key=lambda c: (c["fecha"], c["timestamp"]))
    OUTPUT.write_text(json.dumps({"generado_con": f"{motor.nombre}/{motor.modelo}", "casos": nuevos}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\ncasos NUEVOS confirmados: {len(nuevos)} · coste ${motor.coste:.2f}")
    for c in nuevos:
        print(f"  {c['fecha']} {c['timestamp']} · {'/'.join(c['formas']):12} · {c['ambito'][:38]:38} · {c['actor_senalado'][:30]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
