"""Página Streamlit de grafos de conocimiento (integración con textgraph).

Selecciona transcripciones del corpus, lanza ``scripts/knowledge_graph.py``
como proceso desacoplado (textgraph extrae entidades y relaciones con el
motor elegido — codex-cli por defecto) y muestra los grafos generados
incrustando el visor HTML autónomo de textgraph. Los archivos quedan en
``data/grafos/``.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

BUILD_LOG = Path("data/graph_build.log")
BUILD_PID = Path("data/.graph_build.pid")
GRAPHS_DIR = Path("data/grafos")
TRANSCRIPTS = Path("data/presidencia_transcripts.jsonl")

st.set_page_config(page_title="Grafos de Conocimiento", page_icon="🕸️", layout="wide")


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
    home_bin = str(Path.home() / ".local" / "bin")
    env["PATH"] = f"{venv_bin}:/opt/homebrew/bin:{home_bin}:" + env.get("PATH", "")
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


@st.cache_data(show_spinner=False)
def load_records(path: str, mtime: float) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                text = ((item.get("transcript") or {}).get("text") or "").strip()
                if text:
                    records.append(
                        {
                            "video_id": item.get("video_id", ""),
                            "title": item.get("title", ""),
                            "upload_date": item.get("upload_date", ""),
                            "channel": item.get("channel", ""),
                            "chars": len(text),
                        }
                    )
    records.sort(key=lambda r: (r["upload_date"], r["video_id"]), reverse=True)
    return records


def iso_date(value: str) -> str:
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else "¿fecha?"


def main() -> None:
    st.title("🕸️ Grafos de Conocimiento (textgraph)")

    if not TRANSCRIPTS.exists():
        st.error("No existe el JSONL de transcripciones. Ejecuta primero la cosecha.")
        st.stop()
    records = load_records(str(TRANSCRIPTS), TRANSCRIPTS.stat().st_mtime)
    building = is_running(BUILD_PID)

    with st.sidebar:
        st.header("Construir un grafo")
        options = {f"{iso_date(r['upload_date'])} | {r['title'][:55]}": r for r in records}
        selected_labels = st.multiselect("Videos a incluir", list(options.keys()))
        selection = [options[label] for label in selected_labels]
        total_chars = sum(r["chars"] for r in selection)

        motor = st.selectbox("Motor", ["codex-cli", "gemini-cli", "antigravity-cli", "ollama", "claude-cli", "api", "auto", "local"])
        modelo = st.text_input("Modelo", "", placeholder="(default del motor; codex: gpt-5.6-sol, gemini: gemini-3-pro-preview)")
        fragmento = st.number_input("Caracteres por fragmento", 1500, 20000, 6000, 500)
        paralelo = st.number_input("Fragmentos en paralelo", 1, 8, 2)
        exhaustividad = st.checkbox("Exhaustividad asistida", value=True)
        titulo = st.text_input("Título del grafo", "", placeholder="(derivado de la selección)")

        if selection:
            calls = max(1, math.ceil(total_chars / int(fragmento)))
            st.caption(f"{len(selection)} video(s) · {total_chars:,} caracteres · ~{calls} llamadas al modelo")
            if total_chars > 380_000:
                st.error("Supera el límite de ~400k caracteres de textgraph; reduce la selección.")

        launch = st.button(
            "🕸️ Construir grafo",
            type="primary",
            disabled=building or not selection or total_chars > 380_000,
        )
        if launch:
            script_args = ["scripts/knowledge_graph.py", "--videos", *[r["video_id"] for r in selection], "--motor", motor]
            if modelo.strip():
                script_args.extend(["--modelo", modelo.strip()])
            script_args.extend(["--fragmento", str(int(fragmento)), "--paralelo", str(int(paralelo))])
            if not exhaustividad:
                script_args.append("--sin-exhaustividad")
            if titulo.strip():
                script_args.extend(["--titulo", titulo.strip()])
            spawn_detached(script_args, BUILD_LOG, BUILD_PID)
            st.rerun()

        if st.button("🔄 Actualizar estado"):
            st.rerun()

    if building:
        st.info("⏳ Hay un grafo construyéndose. El avance aparece abajo; actualiza el estado para refrescar.")
        with st.expander("Log de construcción", expanded=True):
            st.code(log_tail(BUILD_LOG, 30), language=None)

    graphs = sorted(
        list(GRAPHS_DIR.glob("*.html")) + list(GRAPHS_DIR.glob("por_video/*/*.html")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not graphs:
        st.info("Aún no hay grafos en data/grafos/. Selecciona videos en la barra lateral y construye el primero.")
        return

    st.markdown(f"### Grafos generados ({len(graphs)})")
    chosen = st.selectbox(
        "Grafo",
        graphs,
        format_func=lambda p: f"{p.stem}  ·  {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}",
    )
    download_cols = st.columns(4)
    download_cols[0].download_button("⤓ HTML autónomo", chosen.read_bytes(), file_name=chosen.name)
    for column, suffix, label in ((download_cols[1], ".json", "⤓ JSON"), (download_cols[2], ".llm.md", "⤓ Paquete LLM")):
        sibling = chosen.with_name(chosen.stem + suffix)
        if sibling.exists():
            column.download_button(label, sibling.read_bytes(), file_name=sibling.name)
    components.html(chosen.read_text(encoding="utf-8"), height=820, scrolling=True)


main()
