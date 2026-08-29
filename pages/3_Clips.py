"""Página Streamlit de cortes de video a partir de preguntas al corpus.

Lanza ``scripts/make_clips.py`` como proceso desacoplado: la pregunta pasa
por el RAG (o por menciones exactas), el LLM responde citando fuentes
``[S#]``, y los segmentos citados se cortan de YouTube con yt-dlp + ffmpeg
usando los timestamps de las transcripciones. Cada corrida queda en
``data/clips/<slug>_<fecha>/`` con sus MP4, manifest y respuesta; esta
página lista las corridas y reproduce los clips.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import rag_transcripts as rag  # noqa: E402

CLIPS_ROOT = Path("data/clips")
BUILD_LOG = Path("data/clips_build.log")
BUILD_PID = Path("data/.clips_build.pid")

st.set_page_config(page_title="Cortes de Video", page_icon="🎬", layout="wide")


def is_running(pid_path: Path) -> bool:
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def spawn_detached(script_args: list[str], log_path: Path, pid_path: Path) -> None:
    env = os.environ.copy()
    venv_bin = str(Path(sys.executable).parent)
    env["PATH"] = f"{venv_bin}:/opt/homebrew/bin:" + env.get("PATH", "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n===== lanzado {datetime.now().isoformat()} =====\n")
        log.flush()
        process = subprocess.Popen(
            [sys.executable, "-u", *script_args],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    pid_path.write_text(str(process.pid))


def log_tail(path: Path, lines: int = 25) -> str:
    if not path.exists():
        return "(sin log todavía)"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def main() -> None:
    st.title("🎬 Cortes de Video desde Preguntas")
    building = is_running(BUILD_PID)

    with st.sidebar:
        st.header("Nueva corrida")
        mode = st.radio("Modo", ["Pregunta (RAG)", "Menciones exactas"])
        query = st.text_area(
            "Pregunta o término",
            placeholder="¿Qué ha dicho la presidente sobre el fiscal general?",
            height=90,
        )
        clips = st.slider("Máximo de clips", 1, 12, 4)
        margin = st.number_input("Margen (segundos antes/después)", 0.0, 30.0, 3.0, 1.0)
        max_seconds = st.number_input("Duración máxima por clip (s)", 20.0, 600.0, 120.0, 10.0)
        compilacion = st.toggle("Montaje (unir clips en un MP4)", value=True)
        orden = st.selectbox("Orden del montaje", ["fecha", "relevancia"])
        rotulos = st.toggle("Rótulos (fecha · título sobre el video)", value=True)
        if mode == "Pregunta (RAG)":
            provider = st.selectbox("Proveedor LLM", ["ollama", "openai", "gemini"])
            model = st.text_input("Modelo", rag.default_model(provider))
            top_k = st.slider("Contextos recuperados", 1, 20, rag.DEFAULT_TOP_K)
            no_llm = st.checkbox("Sin LLM (cortar los top contextos)", value=False)

        launch = st.button("🎬 Responder y cortar", type="primary", disabled=building or not query.strip())
        if launch:
            script_args = ["scripts/make_clips.py"]
            if mode == "Menciones exactas":
                script_args.extend(["--mentions", query.strip()])
            else:
                script_args.append(query.strip())
                script_args.extend(["--provider", provider, "--model", model, "--top-k", str(top_k)])
                if no_llm:
                    script_args.append("--no-llm")
            script_args.extend(["--clips", str(clips), "--margin", str(margin), "--max-seconds", str(max_seconds)])
            if compilacion:
                script_args.extend(["--compilacion", "--orden", orden])
                if not rotulos:
                    script_args.append("--no-rotulos")
            spawn_detached(script_args, BUILD_LOG, BUILD_PID)
            st.rerun()

        if st.button("🔄 Actualizar estado"):
            st.rerun()

    if building:
        st.info("⏳ Hay una corrida en curso (respuesta + descarga y corte de video). Actualiza el estado para ver el avance.")
        with st.expander("Log", expanded=True):
            st.code(log_tail(BUILD_LOG, 30), language=None)

    runs = sorted(CLIPS_ROOT.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        st.info("Aún no hay corridas. Escribe una pregunta en la barra lateral y presiona *Responder y cortar*.")
        return

    st.markdown(f"### Corridas ({len(runs)})")
    chosen = st.selectbox(
        "Corrida",
        runs,
        format_func=lambda p: f"{json.loads(p.read_text(encoding='utf-8'))['question'][:80]}  ·  {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}",
    )
    manifest = json.loads(chosen.read_text(encoding="utf-8"))
    rundir = chosen.parent

    st.markdown(f"#### {manifest['question']}")
    if manifest.get("model"):
        st.caption(f"Modo: {manifest.get('mode')} · {manifest.get('provider')} / {manifest.get('model')} · {manifest.get('created_at', '')[:16]}")
    st.markdown(manifest.get("answer", ""))

    if manifest.get("compilation"):
        compilation_path = rundir / manifest["compilation"]
        if compilation_path.exists():
            st.markdown("##### 🎞️ Compilación")
            st.video(str(compilation_path))

    st.markdown("##### Clips")
    for clip in manifest.get("clips", []):
        clip_path = rundir / clip["file"]
        cols = st.columns([3, 2], vertical_alignment="top")
        with cols[0]:
            if clip_path.exists():
                st.video(str(clip_path))
            else:
                st.warning(f"Archivo no encontrado: {clip['file']}")
        with cols[1]:
            st.markdown(f"**{clip.get('title') or clip['video_id']}**")
            precision = f" · alineación {clip['align_score']:.2f}" if clip.get("align_score") else " · sin alineación (chunk)"
            st.caption(f"{clip.get('timestamp', '')} · clip {clip.get('clip_start')}s → {clip.get('clip_end')}s{precision}")
            if clip.get("timestamp_url"):
                st.link_button("▶ Ver en YouTube", clip["timestamp_url"])
            if clip.get("quote"):
                st.markdown(f"> {clip['quote'][:300]}…")
            if clip_path.exists():
                st.download_button("⤓ Descargar MP4", clip_path.read_bytes(), file_name=clip["file"], key=f"dl_{clip['file']}")
        st.divider()


main()
