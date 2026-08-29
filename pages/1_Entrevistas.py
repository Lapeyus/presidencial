"""Página Streamlit de validación de entrevistas en medios.

Interfaz sobre la cola de candidatos de ``scripts/search_interviews.py``
(``data/interview_candidates.json``): muestra cada candidato con su thumbnail
de YouTube, enlace para verlo, y un checkbox para decidir si se procesa. El
botón "Procesar seleccionados" marca los elegidos como ``approved`` y lanza
``search_interviews.py process`` como proceso desacoplado (sobrevive al
servidor Streamlit), cuyo avance se sigue desde esta misma página. La
búsqueda de candidatos nuevos también se puede lanzar desde la barra lateral.

Se abre junto al chat RAG: ``streamlit run streamlit_app.py`` y elegir
"Entrevistas" en la navegación lateral.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

from scripts import search_interviews as si

PROCESS_LOG = Path("data/interview_process.log")
PROCESS_PID = Path("data/.interview_process.pid")
SEARCH_LOG = Path("data/interview_search.log")
SEARCH_PID = Path("data/.interview_search.pid")

st.set_page_config(page_title="Validación de Entrevistas", page_icon="📺", layout="wide")


def is_running(pid_path: Path) -> bool:
    """Devuelve True si el proceso cuyo PID guarda ``pid_path`` sigue vivo."""
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def spawn_detached(subcommand: list[str], log_path: Path, pid_path: Path) -> None:
    """Lanza un subcomando de search_interviews.py desacoplado del servidor.

    El proceso hereda el venv actual, con ``.venv/bin`` y Homebrew en el PATH
    para que encuentre yt-dlp, ffmpeg y mlx_whisper. stdout/stderr van a
    ``log_path`` y el PID queda en ``pid_path`` para monitoreo.
    """
    env = os.environ.copy()
    venv_bin = str(Path(sys.executable).parent)
    env["PATH"] = f"{venv_bin}:/opt/homebrew/bin:" + env.get("PATH", "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n===== lanzado {datetime.now().isoformat()} =====\n")
        log.flush()
        process = subprocess.Popen(
            [sys.executable, "scripts/search_interviews.py", *subcommand, "--no-browser-cookies"],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    pid_path.write_text(str(process.pid))


def format_date(value: str | None) -> str:
    parsed = si.collector.parse_upload_date(value)
    return parsed.isoformat() if parsed else "¿fecha?"


def log_tail(path: Path, lines: int = 25) -> str:
    if not path.exists():
        return "(sin log todavía)"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def candidate_row(cand: dict, selectable: bool) -> None:
    video_id = cand["video_id"]
    cols = st.columns([2, 5, 2], vertical_alignment="center")
    cols[0].image(f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", use_container_width=True)
    with cols[1]:
        st.markdown(f"**{cand.get('title') or '(sin título)'}**")
        views = cand.get("view_count")
        views_text = f" · {views:,} vistas" if isinstance(views, int) else ""
        st.caption(
            f"{cand.get('channel') or '¿canal?'} · {format_date(cand.get('upload_date'))} · "
            f"{si.format_duration(cand.get('duration'))}{views_text} · encontrado por: {cand.get('found_by_query', '')}"
        )
        st.link_button("▶ Ver / oír en YouTube", cand.get("url", ""))
    with cols[2]:
        if selectable:
            st.checkbox("Procesar", key=f"sel_{video_id}")
        else:
            outcome = cand.get("last_outcome")
            st.caption(f"Estado: {cand.get('status')}" + (f" ({outcome})" if outcome else ""))
    st.divider()


def main() -> None:
    st.title("📺 Validación de Entrevistas en Medios")

    data = si.load_candidates(si.DEFAULT_CANDIDATES)
    by_status: dict[str, list[dict]] = {"pending": [], "approved": [], "processed": [], "rejected": []}
    for cand in data["candidates"]:
        by_status.setdefault(cand.get("status", "pending"), []).append(cand)

    processing = is_running(PROCESS_PID)
    searching = is_running(SEARCH_PID)

    with st.sidebar:
        st.header("Cola de candidatos")
        st.caption(
            f"Pendientes: **{len(by_status['pending'])}** · En cola: **{len(by_status['approved'])}** · "
            f"Procesados: **{len(by_status['processed'])}** · Rechazados: **{len(by_status['rejected'])}**"
        )
        if st.button("🔄 Actualizar estado"):
            st.rerun()
        st.markdown("---")
        st.subheader("Buscar candidatos nuevos")
        if searching:
            st.info("Búsqueda en curso…")
            with st.expander("Log de búsqueda"):
                st.code(log_tail(SEARCH_LOG), language=None)
        elif st.button("🔍 Ejecutar búsqueda en YouTube"):
            spawn_detached(["search"], SEARCH_LOG, SEARCH_PID)
            st.rerun()

    if processing:
        st.info("⏳ Hay un procesamiento en curso. Actualiza el estado para ver el avance.")
        with st.expander("Log de procesamiento", expanded=True):
            st.code(log_tail(PROCESS_LOG, 30), language=None)

    pending = by_status["pending"]
    if not pending:
        st.success("No hay candidatos pendientes de validar.")
        st.caption("Ejecuta una búsqueda desde la barra lateral para traer candidatos nuevos.")
    else:
        st.markdown(f"### Pendientes de validar: {len(pending)}")
        st.caption("Marca ✅ los que quieras transcribir e incorporar al corpus; al final presiona *Procesar seleccionados*.")
        for cand in pending:
            candidate_row(cand, selectable=True)

        selected_ids = [c["video_id"] for c in pending if st.session_state.get(f"sel_{c['video_id']}")]
        action_cols = st.columns([3, 3, 6])
        reject_rest = action_cols[1].toggle("Rechazar los no seleccionados", value=False)
        clicked = action_cols[0].button(
            f"🚀 Procesar seleccionados ({len(selected_ids)})",
            type="primary",
            disabled=not selected_ids or processing,
        )
        if clicked:
            now = datetime.now().astimezone().isoformat()
            for cand in pending:
                if cand["video_id"] in selected_ids:
                    cand["status"] = "approved"
                    cand["decided_at"] = now
                elif reject_rest:
                    cand["status"] = "rejected"
                    cand["decided_at"] = now
            si.save_candidates(si.DEFAULT_CANDIDATES, data)
            spawn_detached(["process"], PROCESS_LOG, PROCESS_PID)
            st.success(f"Procesamiento lanzado para {len(selected_ids)} video(s).")
            st.rerun()

    for status_key, label in (("approved", "En cola de procesamiento"), ("processed", "Procesados"), ("rejected", "Rechazados")):
        items = by_status[status_key]
        if items:
            with st.expander(f"{label} ({len(items)})"):
                for cand in items:
                    candidate_row(cand, selectable=False)


main()
