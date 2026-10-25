"""Sala de edición: de una pregunta a un montaje ajustado a mano.

Flujo: (1) buscar segmentos candidatos en el corpus — por pregunta al índice
RAG, por menciones exactas, o por episodio descrito en lenguaje natural
(confirmado con un modelo); (2) armar el timeline: cada clip se descarga una
sola vez con colchón de ±20 s y luego los **sliders de entrada/salida**
recortan localmente sin re-descargar; (3) orden de secuencia numérico,
**súper** (rótulo de dos líneas quemado como PNG + overlay de ffmpeg, sin
depender de drawtext) y separadores a negro; (4) render del montaje final,
con vista previa y descarga. Los proyectos quedan en ``data/editor/``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEXTGRAPH_DIR = Path(os.environ.get("TEXTGRAPH_DIR", "/Users/joseph/Documents/textgraph"))
sys.path.insert(0, str(TEXTGRAPH_DIR))

from scripts import make_clips as mc  # noqa: E402
from scripts import rag_transcripts as rag  # noqa: E402

EDITOR_DIR = Path("data/editor")
FFMPEG = "/opt/homebrew/bin/ffmpeg"
# yt-dlp vive en .venv/bin y ffmpeg en homebrew; el server puede no traerlos en el PATH
os.environ["PATH"] = f"{Path(sys.executable).parent}:/opt/homebrew/bin:" + os.environ.get("PATH", "")
FONT = "/System/Library/Fonts/Helvetica.ttc"
PAD = 45.0  # colchón de descarga a cada lado, para poder ajustar con sliders

st.set_page_config(page_title="Sala de Edición", page_icon="🎬", layout="wide")


def slug(value: str, limit: int = 40) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")[:limit] or "proyecto"


def rag_args() -> "object":
    import argparse

    return argparse.Namespace(
        chroma_dir=rag.DEFAULT_CHROMA_DIR, collection=rag.DEFAULT_COLLECTION,
        ollama_host=rag.normalize_ollama_host(os.getenv("OLLAMA_HOST", "http://localhost:11434")),
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", rag.DEFAULT_EMBEDDING_MODEL),
        top_k=10, temperature=0.2, provider="ollama", model=None, show_context=False,
    )


PROMPT_CITAS = """Pregunta del editor de video: «{pregunta}»

{conocimiento}Abajo hay fragmentos numerados de transcripciones de la presidenta de Costa Rica (con video y
fecha). Elige los momentos que MEJOR responden a la pregunta y devuelve la cita textual exacta de
cada uno (copiada del fragmento, la oración u oraciones precisas que se deben ver en video).

Devuelve UNICAMENTE: {{"citas": [{{"fuente": N, "cita": "texto literal del fragmento"}}]}} — entre 2 y 10 citas,
de fuentes distintas cuando sea posible, sin parafrasear.

Fragmentos:
{fragmentos}"""


def buscar(modo: str, consulta: str, descripcion: str, motor_nombre: str) -> list[dict]:
    """Devuelve candidatos [{video_id, title, fecha, quote, start, end}]."""
    args = rag_args()
    candidatos: list[dict] = []
    vistos: set[tuple[str, int]] = set()

    def agregar(metadata: dict, texto: str, hint_bounds=None) -> None:
        video_id = str(metadata.get("video_id") or "")
        aligned = mc.align_quote(mc.load_segments(video_id), texto, hint_bounds)
        if aligned is not None:
            start, end = aligned[0], aligned[1]
        elif hint_bounds:
            start, end = hint_bounds
        else:
            return
        clave = (video_id, int(start // 15))
        if clave in vistos:
            return
        vistos.add(clave)
        candidatos.append(
            {
                "video_id": video_id,
                "title": metadata.get("title", ""),
                "fecha": rag.format_upload_date(str(metadata.get("upload_date", ""))),
                "quote": texto[:260],
                "start": max(0.0, float(start)),
                "end": float(end if end > start else start + 25),
            }
        )

    if modo == "Menciones exactas":
        for match in rag.search_mentions(args, consulta, context_chars=300, limit=200):
            metadata = match.get("metadata") or {}
            hint = None
            if isinstance(metadata.get("start"), (int, float)) and metadata["start"] >= 0:
                hint = (float(metadata["start"]), float(metadata.get("end") or metadata["start"]))
            agregar(metadata, re.sub(r"\*\*", "", str(match.get("context") or "")), hint)
    elif modo.startswith("Pregunta"):
        from textgraph.llm import crear_motor

        args.top_k = 14
        contextos = rag.retrieve(args, consulta)
        conocimiento = rag.graph_context(consulta)
        bloque_grafo = f"Conocimiento del grafo del corpus (contexto):\n{conocimiento}\n\n" if conocimiento else ""
        fragmentos = "\n".join(
            f"[{i + 1}] ({(c.get('metadata') or {}).get('title', '')[:50]}, "
            f"{rag.format_upload_date(str((c.get('metadata') or {}).get('upload_date', '')))}) "
            f"«{(c.get('text') or '')[:600]}»"
            for i, c in enumerate(contextos)
        )
        try:
            motor = crear_motor(motor_nombre)
            r = motor.json(
                PROMPT_CITAS.format(pregunta=consulta, conocimiento=bloque_grafo, fragmentos=fragmentos),
                max_tokens=4000,
            )
            citas = r.get("citas") or []
        except Exception as exc:
            st.warning(f"El motor {motor_nombre} falló ({str(exc)[:120]}); se usan los chunks crudos.")
            citas = []
        if citas:
            for item in citas:
                if not isinstance(item, dict):
                    continue
                try:
                    contexto = contextos[int(item.get("fuente")) - 1]
                except (TypeError, ValueError, IndexError):
                    continue
                metadata = contexto.get("metadata") or {}
                hint = None
                if isinstance(metadata.get("start"), (int, float)) and metadata["start"] >= 0:
                    hint = (float(metadata["start"]), float(metadata.get("end") or metadata["start"]))
                agregar(metadata, str(item.get("cita") or "")[:300], hint)
        else:
            for contexto in contextos[:10]:
                metadata = contexto.get("metadata") or {}
                start = metadata.get("start")
                if not isinstance(start, (int, float)) or start < 0:
                    continue
                end = float(metadata.get("end") or start + 60)
                centro = (float(start) + end) / 2
                agregar(metadata, (contexto.get("text") or "")[:260], (max(0.0, centro - 30), centro + 30))
    else:  # Episodio (confirmado con modelo)
        from textgraph.llm import crear_motor

        terminos = [t.strip() for t in consulta.split(",") if t.strip()]
        crudos: list[dict] = []
        for termino in terminos:
            for match in rag.search_mentions(args, termino, context_chars=320, limit=300):
                metadata = match.get("metadata") or {}
                clave = (str(metadata.get("video_id")), str(metadata.get("start")))
                if clave in {(c["_k1"], c["_k2"]) for c in crudos}:
                    continue
                crudos.append(
                    {"_k1": str(metadata.get("video_id")), "_k2": str(metadata.get("start")),
                     "metadata": metadata, "contexto": re.sub(r"\*\*", "", str(match.get("context") or ""))}
                )
        motor = crear_motor(motor_nombre)
        for inicio in range(0, len(crudos), 25):
            lote = crudos[inicio:inicio + 25]
            fragmentos = "\n".join(f"{i + 1}. «{c['contexto'][:280]}»" for i, c in enumerate(lote))
            try:
                r = motor.json(
                    "Episodio: «" + descripcion + "»\n\n¿Cuáles fragmentos numerados se refieren a ese episodio? "
                    'Devuelve UNICAMENTE {"episodio": [números]}.\n\nFragmentos:\n' + fragmentos,
                    max_tokens=1500,
                )
            except Exception:
                continue
            numeros = {int(x) for x in (r.get("episodio") or []) if isinstance(x, int) or str(x).isdigit()}
            for i, c in enumerate(lote):
                if i + 1 in numeros:
                    metadata = c["metadata"]
                    hint = None
                    if isinstance(metadata.get("start"), (int, float)) and metadata["start"] >= 0:
                        hint = (float(metadata["start"]), float(metadata.get("end") or metadata["start"]))
                    agregar(metadata, c["contexto"], hint)
    return candidatos


def guardar_proyecto() -> None:
    """Persiste el timeline para que una recarga del navegador no lo pierda."""
    nombre = st.session_state.get("proyecto")
    if not nombre:
        return
    carpeta = EDITOR_DIR / nombre
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "timeline.json").write_text(
        json.dumps(
            {"guardado": datetime.now().astimezone().isoformat(),
             "timeline": st.session_state.timeline,
             "candidatos": st.session_state.candidatos},
            ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def proyectos_guardados() -> list[str]:
    if not EDITOR_DIR.exists():
        return []
    return sorted(p.name for p in EDITOR_DIR.iterdir() if (p / "timeline.json").exists())


def cargar_proyecto(nombre: str) -> None:
    datos = json.loads((EDITOR_DIR / nombre / "timeline.json").read_text(encoding="utf-8"))
    st.session_state.timeline = datos.get("timeline", [])
    st.session_state.candidatos = datos.get("candidatos", [])
    st.session_state.proyecto = nombre


def preview_path(proyecto: Path, seg: dict) -> Path:
    return proyecto / f"prev_{seg['video_id']}_{int(seg['pad_start'])}.mp4"


def descargar_preview(proyecto: Path, seg: dict) -> bool:
    destino = preview_path(proyecto, seg)
    if destino.exists():
        return True
    return mc.cut_clip(seg["video_id"], (seg["pad_start"], seg["pad_end"]), destino, 720)


def png_super(proyecto: Path, indice: int, linea1: str, linea2: str) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    ancho, alto = 1280, 720
    imagen = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    dibujo = ImageDraw.Draw(imagen)
    banda_alto = 110 if linea2 else 78
    y0 = alto - banda_alto - 42
    dibujo.rectangle([0, y0, ancho, y0 + banda_alto], fill=(8, 12, 18, 200))
    dibujo.rectangle([0, y0, 7, y0 + banda_alto], fill=(45, 212, 191, 255))
    f1 = ImageFont.truetype(FONT, 40)
    f2 = ImageFont.truetype(FONT, 26)
    dibujo.text((26, y0 + 12), linea1[:70], font=f1, fill=(255, 255, 255, 255))
    if linea2:
        dibujo.text((26, y0 + 64), linea2[:90], font=f2, fill=(200, 210, 220, 255))
    destino = proyecto / f"super_{indice}.png"
    imagen.save(destino)
    return destino


def render_montaje(proyecto: Path, timeline: list[dict], separadores: bool) -> Path | None:
    trabajo = proyecto / ".render"
    trabajo.mkdir(exist_ok=True)
    piezas: list[Path] = []
    for orden, seg in enumerate(timeline):
        origen = preview_path(proyecto, seg)
        if not origen.exists():
            st.error(f"Falta el preview del clip {orden + 1}; descárgalo primero.")
            return None
        salida = trabajo / f"{orden:02d}.mp4"
        entrada_rel = seg["in"] - seg["pad_start"]
        duracion = max(1.0, seg["out"] - seg["in"])
        comando = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                   "-ss", f"{entrada_rel:.2f}", "-t", f"{duracion:.2f}", "-i", str(origen)]
        if seg.get("super1"):
            overlay = png_super(proyecto, orden, seg["super1"], seg.get("super2", ""))
            comando += ["-i", str(overlay), "-filter_complex",
                        "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30[v];[v][1:v]overlay=0:0[out]",
                        "-map", "[out]", "-map", "0:a?"]
        else:
            comando += ["-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30"]
        comando += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", str(salida)]
        r = subprocess.run(comando, capture_output=True, text=True)
        if r.returncode != 0:
            st.error(f"ffmpeg falló en el clip {orden + 1}: {r.stderr[-300:]}")
            return None
        piezas.append(salida)

    if separadores and len(piezas) > 1:
        sep = trabajo / "sep.mp4"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=black:s=1280x720:r=30:d=0.7",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.7",
                        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", str(sep)],
                       capture_output=True)
        entrelazadas: list[Path] = []
        for posicion, pieza in enumerate(piezas):
            if posicion:
                entrelazadas.append(sep)
            entrelazadas.append(pieza)
        piezas = entrelazadas

    lista = trabajo / "lista.txt"
    lista.write_text("".join(f"file '{p.resolve()}'\n" for p in piezas), encoding="utf-8")
    final = proyecto / "montaje.mp4"
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lista), "-c", "copy", str(final)], capture_output=True, text=True)
    return final if r.returncode == 0 else None


def main() -> None:
    st.title("🎬 Sala de Edición")
    if "timeline" not in st.session_state:
        st.session_state.timeline = []
        st.session_state.candidatos = []
        st.session_state.proyecto = ""

    with st.sidebar:
        guardados = proyectos_guardados()
        if guardados:
            with st.expander("📂 Proyectos guardados", expanded=not st.session_state.timeline):
                elegido = st.selectbox("Abrir proyecto", guardados, label_visibility="collapsed")
                if st.button("Abrir", use_container_width=True):
                    cargar_proyecto(elegido)
                    st.rerun()
        st.header("1 · Buscar segmentos")
        modo = st.radio("Modo", ["Menciones exactas", "Pregunta (BD vectorial + grafo + modelo)", "Episodio (con modelo)"])
        motor_nombre = st.selectbox(
            "Motor (CLI)",
            ["gemini-cli", "codex-cli", "claude-cli", "antigravity-cli", "ollama"],
            help="CLI autenticada que elige las citas usando la BD vectorial y el grafo de conocimiento.",
        )
        if modo == "Episodio (con modelo)":
            descripcion = st.text_area("Describe el episodio", height=80,
                                       placeholder="la magistrada quitó un micrófono en una conferencia…")
            consulta = st.text_input("Términos de arranque (separados por coma)", placeholder="micrófono, arrebat, Solano")
        else:
            descripcion = ""
            consulta = st.text_input("Pregunta o término", placeholder="¿Qué dijo sobre…? / término exacto")
        if st.button("🔍 Buscar", type="primary", disabled=not consulta.strip()):
            with st.spinner("Buscando y alineando segmentos…"):
                st.session_state.candidatos = buscar(modo, consulta.strip(), descripcion.strip(), motor_nombre)
                st.session_state.proyecto = slug(descripcion or consulta)
            st.rerun()
        st.caption(f"Candidatos: {len(st.session_state.candidatos)}")
        st.session_state.pad_actual = st.number_input(
            "Colchón por lado (s)", 10.0, 180.0, float(st.session_state.get("pad_actual", PAD)), 5.0,
            help="Margen que se descarga alrededor del segmento; define hasta dónde llegan los sliders de recorte.",
        )
        st.markdown("---")
        st.header("3 · Render")
        separadores = st.toggle("Separadores a negro entre clips", value=True)
        if st.button("🎞️ Renderizar montaje", type="primary", disabled=len(st.session_state.timeline) < 1):
            proyecto = EDITOR_DIR / st.session_state.proyecto
            proyecto.mkdir(parents=True, exist_ok=True)
            orden = sorted(st.session_state.timeline, key=lambda s: s["orden"])
            with st.spinner("Renderizando…"):
                final = render_montaje(proyecto, orden, separadores)
            if final:
                st.session_state.render_final = str(final)
                manifest = {"pregunta": st.session_state.proyecto, "clips": orden,
                            "generado": datetime.now().astimezone().isoformat()}
                (proyecto / "proyecto.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                st.rerun()

    if st.session_state.get("render_final") and Path(st.session_state.render_final).exists():
        st.subheader("Montaje final")
        st.video(st.session_state.render_final)
        st.download_button("⤓ Descargar montaje", Path(st.session_state.render_final).read_bytes(),
                           file_name=f"{st.session_state.proyecto}.mp4")
        st.divider()

    if st.session_state.candidatos:
        st.subheader(f"2a · Candidatos ({len(st.session_state.candidatos)}) — agrega al timeline")
        for i, c in enumerate(st.session_state.candidatos):
            cols = st.columns([6, 2, 1], vertical_alignment="center")
            cols[0].markdown(f"**{c['fecha']}** · {c['title'][:55]} · `{rag.format_timestamp(c['start'])}`  \n"
                             f"<span style='color:gray;font-size:.85em'>«{c['quote'][:150]}…»</span>", unsafe_allow_html=True)
            cols[1].caption(f"{round(c['end'] - c['start'])} s aprox.")
            if cols[2].button("➕", key=f"add_{i}"):
                pad = float(st.session_state.get("pad_actual", PAD))
                seg = dict(c)
                seg.update({"pad_start": max(0.0, c["start"] - pad), "pad_end": c["end"] + pad,
                            "in": c["start"], "out": c["end"], "super1": "", "super2": "",
                            "orden": len(st.session_state.timeline) + 1})
                st.session_state.timeline.append(seg)
                st.rerun()
        st.divider()

    if st.session_state.timeline:
        st.subheader(f"2b · Timeline ({len(st.session_state.timeline)} clips)")
        proyecto = EDITOR_DIR / (st.session_state.proyecto or "proyecto")
        proyecto.mkdir(parents=True, exist_ok=True)
        acciones = st.columns([2, 2, 6])
        if acciones[0].button("🕐 Ordenar cronológicamente"):
            for posicion, seg in enumerate(
                sorted(st.session_state.timeline, key=lambda s: (s.get("fecha") or "", s["in"])), start=1
            ):
                seg["orden"] = posicion
            st.rerun()
        if acciones[1].button("🔁 Invertir orden"):
            total = len(st.session_state.timeline)
            for seg in st.session_state.timeline:
                seg["orden"] = total + 1 - seg["orden"]
            st.rerun()

        faltan = [s for s in st.session_state.timeline if not preview_path(proyecto, s).exists()]
        if faltan and st.button(f"⬇️ Descargar previews pendientes ({len(faltan)})", type="primary"):
            barra = st.progress(0.0)
            for n, seg in enumerate(faltan, start=1):
                descargar_preview(proyecto, seg)
                barra.progress(n / len(faltan))
            st.rerun()

        for i, seg in enumerate(sorted(st.session_state.timeline, key=lambda s: s["orden"])):
            with st.container(border=True):
                arriba = st.columns([1, 5, 2, 1], vertical_alignment="center")
                seg["orden"] = arriba[0].number_input("Orden", 1, 99, seg["orden"], key=f"ord_{i}")
                arriba[1].markdown(f"**{seg['fecha']}** · {seg['title'][:60]}")
                arriba[2].caption(f"{rag.format_timestamp(seg['in'])} → {rag.format_timestamp(seg['out'])} · {round(seg['out'] - seg['in'])} s")
                with arriba[3]:
                    if st.button("🗑", key=f"del_{i}", help="Quitar del timeline"):
                        st.session_state.timeline.remove(seg)
                        st.rerun()
                    if st.button("⏪⏩", key=f"pad_{i}", help="Ampliar el colchón 60 s por lado (re-descarga el preview)"):
                        preview_path(proyecto, seg).unlink(missing_ok=True)
                        seg["pad_start"] = max(0.0, seg["pad_start"] - 60.0)
                        seg["pad_end"] = seg["pad_end"] + 60.0
                        st.rerun()
                rango = st.slider(
                    "Recorte (segundos del video original)",
                    min_value=float(seg["pad_start"]), max_value=float(seg["pad_end"]),
                    value=(float(seg["in"]), float(seg["out"])), step=0.5, key=f"rng_{i}",
                )
                seg["in"], seg["out"] = rango
                abajo = st.columns([3, 3, 3])
                seg["super1"] = abajo[0].text_input("Súper — línea 1", seg["super1"], key=f"s1_{i}",
                                                   placeholder="LAURA FERNÁNDEZ · PRESIDENTA")
                seg["super2"] = abajo[1].text_input("Súper — línea 2", seg["super2"], key=f"s2_{i}",
                                                   placeholder="Conferencia de prensa · 26 ago 2026")
                with abajo[2]:
                    pv = preview_path(proyecto, seg)
                    if pv.exists():
                        st.video(str(pv), start_time=int(max(0, seg["in"] - seg["pad_start"])))
                    else:
                        st.caption("(preview sin descargar)")
    else:
        st.info("Busca segmentos en la barra lateral y agrégalos al timeline con ➕.")

    guardar_proyecto()


main()
