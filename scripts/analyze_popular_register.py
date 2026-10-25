#!/usr/bin/env python3
"""Mide el registro popular del discurso y pone a prueba la hipótesis del 'bagaje forzado'.

Contrasta la densidad de habla popular (refranes, costarriqueñismos, diminutivos,
metáforas rurales y de esfuerzo) entre géneros discursivos con distinto grado de
guion: cadena nacional (guionada), conferencia (mixta), entrevista (espontánea) y
evento (semi-guionado). Si el registro popular fuera idiolecto nativo, debería
aparecer con densidad similar o mayor en el habla espontánea; si es un recurso
construido, debería concentrarse en los géneros guionados y en los pasajes de
confrontación.

Mide además cinco marcadores de "performance":

1. **Marcado metalingüístico** ("como dice el dicho", "como decimos los ticos"):
   citar el refrán en vez de hablarlo.
2. **Anclaje biográfico** (chanchos, Esparza, casa humilde): autenticación del
   origen popular.
3. **Concentración funcional**: proporción de usos en pasajes de confrontación
   frente a explicativos.
4. **Repertorio cerrado**: reincidencia de pocas unidades frente a cola larga.
5. **Salto de registro**: coocurrencia de tecnicismo institucional y unidad
   popular en la misma ventana de texto.

Salida: ``data/registro_popular.json`` con todas las métricas y ejemplos citables.

Uso:
    python scripts/analyze_popular_register.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

TRANSCRIPTS = Path("data/presidencia_transcripts.jsonl")
OUTPUT = Path("data/registro_popular.json")

# --- repertorio: las 23 del informe + candidatos adicionales a verificar ---
UNIDADES = {
    "pura vida": r"pura vida",
    "machetico / chapiar": r"machetic\w*|chapi\w+",
    "chiquillos": r"chiquill\w+",
    "tufito / olorcito": r"tufit\w+|olorcit\w+",
    "pellizquito": r"pellizquit\w+",
    "a todo chancho le llega su diciembre": r"chancho[^.]{0,40}diciembre|diciembre[^.]{0,40}chancho",
    "ni pica leña ni presta el hacha": r"pica leña|presta el hacha|presta la hacha",
    "una vez capan al perro": r"cap[ae]n al perro",
    "poner las barbas en remojo": r"barbas en remojo",
    "inventar el agua con dulce": r"agua (con|de) dulce",
    "asustar con la vaina vacía": r"vaina vac[ií]a",
    "buscarle la comba al palo": r"comba al palo",
    "partirse el lomo": r"part\w+ el lomo|lomo part\w+",
    "no tener cola que le majen": r"cola que (me|le|nos|te) maj\w+",
    "meter los dedos en la boca": r"dedos en la boca",
    "tirar la toalla": r"tir\w+ la toalla",
    "trabajar con las uñas": r"con las uñas",
    "recetar con cuchara grande": r"cuchara grande",
    "ponerse las botas / camiseta": r"pon\w+ las botas|la camiseta",
    "a toda máquina": r"a toda m[aá]quina",
    "no dar pelota": r"d[ai]\w* (tanta )?pelota",
    "bienes de difunto": r"bienes de difunto",
    "dedo en la llaga / pus": r"dedo en la llaga|sacando la pus|sacar la pus",
    # candidatos adicionales del habla popular tica
    "brete / bretear": r"\bbrete\b|breteand\w+|bretea\w+",
    "plata (por dinero)": r"\bla plata\b|\bplata del pueblo\b",
    "vara / carajillo": r"\buna vara\b|carajill\w+",
    "jalarse una torta": r"jal\w+ una torta",
    "meter el hombro": r"met\w+ el hombro",
    "dar la cara": r"d[aoy]\w* la cara|dando la cara",
    "agarrar el toro por los cuernos": r"toro por los cuernos",
    "poner el pecho": r"pon\w+ el pecho|ponen el pecho",
    "de a poquitos / poquito a poco": r"poquit\w+ a poquit\w+|de a poquit\w+",
    "alcahuetería": r"alcahueter\w+|alcahuet\w+",
    "pataletas y berrinches": r"pataleta\w*|berrinche\w*",
    "cuento chino / carreta": r"cuento chino|pura carreta|\bcarreta\b",
    "al chile / de verdad verdad": r"al chile|verdad verdad",
    "zafarse / zafar el cuerpo": r"zafar\w* el cuerpo|se zaf\w+",
    "echar el agua sucia": r"agua sucia",
    "hacer la tarea": r"hacer la tarea|hicimos la tarea|haciendo la tarea",
}

# marcado metalingüístico: la hablante señala que cita un dicho
METALINGUISTICO = r"(como (se )?dice(n)? (el|ese|un|la gente|mi|los ticos|aquí|allá|por ahí)|como dec[ií]\w+|el dicho|hay un dicho|un refr[aá]n|mi (abuela|abuelo|mam[aá]|pap[aá]|t[ií]a|abuelita) dec[ií]a|en mi casa (se )?dec[ií]a|como bien dice)"

# anclaje biográfico del origen popular/rural
BIOGRAFICO = r"(en mi casa|cuando yo era (niña|chiquilla|pequeña)|mi (abuela|abuelo|mam[aá]|pap[aá])|cri[aá]bamos|de donde yo vengo|yo vengo de|en Esparza|mi pueblo|humilde|mi familia era)"

# registro institucional/técnico (para medir el salto de registro)
TECNICO = r"(presupuesto|superávit|d[eé]ficit|regla fiscal|normativa|decreto|licitaci[oó]n|jurisdicci[oó]n|constitucional|expediente|proyecto de ley|art[ií]culo|reglamento|contrataci[oó]n|fideicomiso|amortizaci[oó]n|inversi[oó]n p[uú]blica)"

# marcadores de confrontación (para medir concentración funcional)
CONFRONTACION = r"(oposici[oó]n|diputad\w+|magistrad\w+|fiscal|Poder Judicial|corrupci[oó]n|corrupt\w+|mentira|miente|irrespet\w+|ataqu\w+|critic\w+|enemig\w+|se acostumbraron|no voy a permitir|les molesta)"


def texto_limpio(registro: dict) -> str:
    """Reconstruye el texto sin la duplicación de los subtítulos rodantes.

    Los subtítulos automáticos de YouTube repiten cada frase en líneas
    sucesivas (formato rodante); contar sobre ese texto infla las frecuencias.
    Se fusionan los segmentos colapsando el solape sufijo-prefijo a nivel de
    palabra, igual que en la reconstrucción de referencia del benchmark.
    """
    transcript = registro.get("transcript") or {}
    segmentos = transcript.get("segments") or []
    if not segmentos:
        return transcript.get("text") or ""
    palabras: list[str] = []
    for segmento in segmentos:
        nuevas = str(segmento.get("text") or "").split()
        if not nuevas:
            continue
        if not palabras:
            palabras.extend(nuevas)
            continue
        solape = 0
        for k in range(min(len(palabras), len(nuevas)), 0, -1):
            if palabras[-k:] == nuevas[:k]:
                solape = k
                break
        palabras.extend(nuevas[solape:])
    return " ".join(palabras)


def norm(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def genero(titulo: str, canal: str) -> str:
    t = norm(titulo)
    if "cadena nacional" in t:
        return "cadena nacional (guionada)"
    if "conferencia de prensa" in t and "presidencia" in norm(canal):
        return "conferencia de prensa (mixta)"
    if norm(canal) != "presidencia de la republica":
        return "entrevista en medios (espontánea)"
    if "en vivo" in t or "inaugura" in t or "firma" in t or "sesion" in t:
        return "evento oficial (semi-guionado)"
    return "otro"


DESDE = "20260508"  # traspaso de poderes; antes el hablante dominante es el gobierno anterior
EXCLUIR_TITULO = re.compile(r"GRACIAS POR TANTO", re.IGNORECASE)  # cadena de despedida del expresidente


def main() -> int:
    registros = [json.loads(l) for l in TRANSCRIPTS.open(encoding="utf-8") if l.strip()]
    antes = len(registros)
    registros = [
        r for r in registros
        if (r.get("upload_date") or "") >= DESDE and not EXCLUIR_TITULO.search(r.get("title") or "")
    ]
    print(f"filtro de periodo (>= {DESDE}): {antes} → {len(registros)} videos")
    patrones = {nombre: re.compile(px, re.IGNORECASE) for nombre, px in UNIDADES.items()}
    meta_px = re.compile(METALINGUISTICO, re.IGNORECASE)
    bio_px = re.compile(BIOGRAFICO, re.IGNORECASE)
    tec_px = re.compile(TECNICO, re.IGNORECASE)
    conf_px = re.compile(CONFRONTACION, re.IGNORECASE)

    por_genero: dict[str, dict] = defaultdict(lambda: {"palabras": 0, "videos": 0, "hits": 0, "unidades": Counter()})
    por_unidad: Counter = Counter()
    unidad_por_genero: dict[str, Counter] = defaultdict(Counter)
    por_mes: dict[str, dict] = defaultdict(lambda: {"palabras": 0, "hits": 0})
    ejemplos: dict[str, list] = defaultdict(list)
    meta_ejemplos: list[dict] = []
    bio_ejemplos: list[dict] = []
    salto_registro: list[dict] = []
    confrontacion_hits = 0
    explicativo_hits = 0

    for registro in registros:
        texto = texto_limpio(registro)
        if not texto.strip():
            continue
        titulo = registro.get("title") or ""
        canal = registro.get("channel") or ""
        fecha = registro.get("upload_date") or ""
        g = genero(titulo, canal)
        palabras = len(texto.split())
        por_genero[g]["palabras"] += palabras
        por_genero[g]["videos"] += 1
        mes = f"{fecha[0:4]}-{fecha[4:6]}" if len(fecha) == 8 else "?"
        por_mes[mes]["palabras"] += palabras

        vistos_video: set[tuple[str, str]] = set()
        for nombre, px in patrones.items():
            for m in px.finditer(texto):
                # los subtítulos rodantes repiten la misma frase varias veces:
                # se cuenta una sola vez por contexto normalizado y video
                ctx = re.sub(r"\W+", " ", norm(texto[max(0, m.start() - 60): m.end() + 60])).strip()
                if (nombre, ctx) in vistos_video:
                    continue
                vistos_video.add((nombre, ctx))
                por_unidad[nombre] += 1
                por_genero[g]["hits"] += 1
                por_genero[g]["unidades"][nombre] += 1
                unidad_por_genero[nombre][g] += 1
                por_mes[mes]["hits"] += 1
                ini, fin = max(0, m.start() - 320), min(len(texto), m.end() + 320)
                ventana = texto[ini:fin]
                if conf_px.search(ventana):
                    confrontacion_hits += 1
                else:
                    explicativo_hits += 1
                if tec_px.search(ventana):
                    if len(salto_registro) < 40:
                        salto_registro.append({"unidad": nombre, "video": titulo[:70], "fecha": fecha, "pasaje": ventana[:420]})
                if len(ejemplos[nombre]) < 4:
                    ejemplos[nombre].append({"video": titulo[:70], "fecha": fecha, "genero": g, "pasaje": ventana[:400]})

        for etiqueta, px, destino, margen in (
            ("meta", meta_px, meta_ejemplos, 320), ("bio", bio_px, bio_ejemplos, 340)
        ):
            for m in px.finditer(texto):
                ctx = re.sub(r"\W+", " ", norm(texto[max(0, m.start() - 60): m.end() + 60])).strip()
                if (etiqueta, ctx) in vistos_video:
                    continue
                vistos_video.add((etiqueta, ctx))
                ini, fin = max(0, m.start() - 200), min(len(texto), m.end() + margen)
                destino.append({"video": titulo[:70], "fecha": fecha, "genero": g, "pasaje": texto[ini:fin]})

    densidades = {
        g: {
            "videos": d["videos"],
            "palabras": d["palabras"],
            "hits": d["hits"],
            "densidad_por_10k": round(10000 * d["hits"] / d["palabras"], 2) if d["palabras"] else 0,
            "top": d["unidades"].most_common(8),
        }
        for g, d in sorted(por_genero.items(), key=lambda kv: -kv[1]["hits"])
    }
    meses = {
        m: {"palabras": d["palabras"], "hits": d["hits"],
            "densidad_por_10k": round(10000 * d["hits"] / d["palabras"], 2) if d["palabras"] else 0}
        for m, d in sorted(por_mes.items())
    }
    total_hits = sum(por_unidad.values())
    top10 = por_unidad.most_common(10)
    salida = {
        "corpus": {"videos": len(registros), "palabras": sum(d["palabras"] for d in por_genero.values())},
        "densidad_por_genero": densidades,
        "densidad_por_mes": meses,
        "unidades_frecuencia": por_unidad.most_common(),
        "unidad_por_genero": {k: dict(v) for k, v in unidad_por_genero.items()},
        "concentracion_repertorio": {
            "total_ocurrencias": total_hits,
            "top10_ocurrencias": sum(c for _, c in top10),
            "top10_porcentaje": round(100 * sum(c for _, c in top10) / total_hits, 1) if total_hits else 0,
            "unidades_distintas": len([u for u, c in por_unidad.items() if c]),
            "unidades_una_sola_vez": len([u for u, c in por_unidad.items() if c == 1]),
        },
        "funcion_pragmatica": {
            "en_pasaje_confrontacion": confrontacion_hits,
            "en_pasaje_explicativo": explicativo_hits,
            "porcentaje_confrontacion": round(100 * confrontacion_hits / (confrontacion_hits + explicativo_hits), 1)
            if (confrontacion_hits + explicativo_hits) else 0,
        },
        "marcado_metalinguistico": {"total": len(meta_ejemplos), "ejemplos": meta_ejemplos[:25]},
        "anclaje_biografico": {"total": len(bio_ejemplos), "ejemplos": bio_ejemplos[:25]},
        "salto_de_registro": {"total": len(salto_registro), "ejemplos": salto_registro[:20]},
        "ejemplos_por_unidad": {k: v for k, v in ejemplos.items()},
    }
    OUTPUT.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"corpus: {salida['corpus']['videos']} videos · {salida['corpus']['palabras']:,} palabras")
    print("\nDENSIDAD POR GÉNERO (ocurrencias por 10.000 palabras)")
    for g, d in densidades.items():
        print(f"  {g:38} {d['densidad_por_10k']:6.2f}  ({d['hits']:4} hits / {d['palabras']:7,} pal · {d['videos']} videos)")
    print("\nDENSIDAD POR MES")
    for m, d in meses.items():
        print(f"  {m}  {d['densidad_por_10k']:6.2f}  ({d['hits']} hits)")
    print(f"\nfunción: {salida['funcion_pragmatica']['porcentaje_confrontacion']}% en pasajes de confrontación")
    print(f"repertorio: top-10 concentra {salida['concentracion_repertorio']['top10_porcentaje']}% de las ocurrencias")
    print(f"metalingüístico: {len(meta_ejemplos)} · biográfico: {len(bio_ejemplos)} · salto de registro: {len(salto_registro)}")
    print("\nTOP UNIDADES")
    for u, c in por_unidad.most_common(15):
        print(f"  {c:4}  {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
