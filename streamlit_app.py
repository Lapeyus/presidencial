"""App Streamlit de preguntas y respuestas (RAG) sobre las transcripciones.

Interfaz de chat sobre el indice vectorial ChromaDB construido a partir de
``data/presidencia_transcripts.jsonl`` (transcripciones del canal de YouTube
de la Presidencia de Costa Rica). Toda la logica de indexacion, recuperacion
y consulta al LLM vive en ``scripts/rag_transcripts.py``; este modulo solo
arma la interfaz y delega en ese modulo.

Uso:
    streamlit run streamlit_app.py

Funcionalidad:
    - Barra lateral para configurar rutas (JSONL de transcripciones,
      directorio ChromaDB, nombre de coleccion), host de Ollama, modelo de
      embeddings, y para construir/reconstruir el indice con parametros de
      chunking (tamano, overlap, batch).
    - Dos modos de consulta: "RAG" (recupera contextos del indice y pregunta
      a un LLM: ollama, openai o gemini) y "Menciones exactas" (busqueda
      literal del termino en todos los chunks, sin LLM).
    - Historial de conversacion en ``st.session_state.history``, con los
      contextos recuperados o las menciones encontradas expandibles bajo
      cada respuesta.

Requiere un servidor Ollama accesible (por defecto en
``http://localhost:11434``) para generar embeddings, incluso cuando el LLM
de respuesta es openai o gemini. Define ``TOKENIZERS_PARALLELISM=false`` en
el entorno si no estaba definida.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import chromadb
import streamlit as st

from scripts import rag_transcripts as rag


st.set_page_config(
    page_title="RAG Transcripciones Presidencia CR",
    page_icon="",
    layout="wide",
)


def app_args(**overrides: object) -> argparse.Namespace:
    """Construye un Namespace equivalente al CLI de rag_transcripts con valores de la app.

    Emula los argumentos que ``scripts/rag_transcripts.py`` recibiria por
    linea de comandos, para poder reutilizar sus funciones desde Streamlit.

    Args:
        **overrides: Pares clave/valor que reemplazan los valores por
            defecto (por ejemplo ``provider``, ``top_k``, ``chroma_dir``).

    Returns:
        argparse.Namespace con los defaults del modulo RAG mas los
        overrides aplicados.
    """
    defaults = {
        "chroma_dir": Path("data/chroma"),
        "collection": rag.DEFAULT_COLLECTION,
        "transcripts": Path("data/presidencia_transcripts.jsonl"),
        "chunk_chars": rag.DEFAULT_CHUNK_CHARS,
        "overlap_chars": rag.DEFAULT_OVERLAP_CHARS,
        "batch_size": 128,
        "provider": "ollama",
        "model": None,
        "embedding_model": rag.DEFAULT_EMBEDDING_MODEL,
        "top_k": rag.DEFAULT_TOP_K,
        "temperature": 0.2,
        "ollama_host": "http://localhost:11434",
        "show_context": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def collection_count(chroma_dir: Path, collection_name: str) -> int | None:
    """Cuenta los chunks indexados en la coleccion ChromaDB indicada.

    Abre un cliente persistente sobre ``chroma_dir`` y consulta la
    coleccion. Cualquier excepcion (directorio inexistente, coleccion no
    creada, etc.) se silencia y se devuelve ``None``, que la interfaz
    interpreta como "indice no encontrado".

    Returns:
        Numero de chunks en la coleccion, o None si no se pudo abrir.
    """
    try:
        client = chromadb.PersistentClient(path=str(chroma_dir))
        collection = client.get_collection(collection_name)
        return collection.count()
    except Exception:
        return None


def provider_default_model(provider: str) -> str:
    """Devuelve el modelo por defecto del proveedor LLM (delegando en rag.default_model)."""
    return rag.default_model(provider)


def render_contexts(contexts: list[dict[str, object]]) -> None:
    for index, context in enumerate(contexts, start=1):
        metadata = context.get("metadata", {}) or {}
        title = metadata.get("title", "Sin titulo")
        video_id = metadata.get("video_id", "")
        url = metadata.get("timestamp_url") or metadata.get("url", "")
        start = rag.format_timestamp(metadata.get("start"))
        end = rag.format_timestamp(metadata.get("end"))
        label = f"[S{index}] {title}"
        if start:
            label += f" @ {start}"
        with st.expander(label):
            cols = st.columns([1, 1, 1, 2])
            cols[0].caption(f"Video: {video_id}")
            cols[1].caption(f"Fecha: {rag.format_upload_date(str(metadata.get('upload_date', '')))}")
            time_range = f"{start} - {end}" if start and end else "sin timestamp"
            cols[2].caption(f"Tiempo: {time_range}")
            if url:
                cols[3].link_button("Abrir referencia", str(url))
            st.caption(f"Transcript: {metadata.get('transcript_source', '')} | Chunk: {metadata.get('chunk_index', '')}")
            st.write(context.get("text", ""))


def render_mentions(matches: list[dict[str, object]]) -> None:
    st.subheader(f"Menciones encontradas: {len(matches)}")
    for index, match in enumerate(matches, start=1):
        metadata = match.get("metadata", {}) or {}
        title = metadata.get("title", "Sin titulo")
        start = rag.format_timestamp(metadata.get("start"))
        end = rag.format_timestamp(metadata.get("end"))
        url = metadata.get("timestamp_url") or metadata.get("url", "")
        label = f"[M{index}] {title}"
        if start:
            label += f" @ {start}"
        with st.expander(label):
            cols = st.columns([1, 1, 1, 2])
            cols[0].caption(f"Video: {metadata.get('video_id', '')}")
            cols[1].caption(f"Fecha: {rag.format_upload_date(str(metadata.get('upload_date', '')))}")
            time_range = f"{start} - {end}" if start and end else "sin timestamp"
            cols[2].caption(f"Tiempo: {time_range}")
            if url:
                cols[3].link_button("Abrir referencia", str(url))
            st.caption(f"Transcript: {metadata.get('transcript_source', '')} | Chunk: {metadata.get('chunk_index', '')}")
            st.markdown(str(match.get("context", "")))


def main() -> None:
    st.title("Preguntas a Transcripciones")

    with st.sidebar:
        st.header("Indice")
        transcripts = Path(st.text_input("JSONL de transcripciones", "data/presidencia_transcripts.jsonl"))
        chroma_dir = Path(st.text_input("Directorio ChromaDB", "data/chroma"))
        collection = st.text_input("Coleccion", rag.DEFAULT_COLLECTION)
        ollama_host = st.text_input("Ollama host", "http://localhost:11434")
        embedding_model = st.text_input("Modelo de embeddings", rag.DEFAULT_EMBEDDING_MODEL)
        st.caption("ChromaDB usa Ollama para embeddings. El default es embeddinggemma:latest.")

        count = collection_count(chroma_dir, collection)
        if count is None:
            st.warning("Indice no encontrado")
        else:
            st.success(f"Indice listo: {count} chunks")

        with st.expander("Opciones de indexacion"):
            chunk_chars = st.number_input("Tamano de chunk", min_value=500, max_value=12000, value=rag.DEFAULT_CHUNK_CHARS, step=100)
            overlap_chars = st.number_input("Overlap", min_value=0, max_value=3000, value=rag.DEFAULT_OVERLAP_CHARS, step=50)
            batch_size = st.number_input("Batch size", min_value=16, max_value=1000, value=128, step=16)

        if st.button("Construir / reconstruir indice", type="primary"):
            args = app_args(
                transcripts=transcripts,
                chroma_dir=chroma_dir,
                collection=collection,
                chunk_chars=int(chunk_chars),
                overlap_chars=int(overlap_chars),
                batch_size=int(batch_size),
                ollama_host=ollama_host,
                embedding_model=embedding_model,
            )
            with st.spinner("Indexando transcripciones en ChromaDB..."):
                try:
                    total = rag.rebuild_index(args)
                    st.success(f"Indice reconstruido: {total} chunks")
                except Exception as exc:
                    st.error(str(exc))

        st.header("LLM")
        provider = st.selectbox("Proveedor", ["ollama", "openai", "gemini"])
        default_model = provider_default_model(provider)
        model = st.text_input("Modelo", default_model)
        temperature = st.slider("Temperatura", min_value=0.0, max_value=1.5, value=0.2, step=0.1)
        top_k = st.slider("Contextos", min_value=1, max_value=20, value=rag.DEFAULT_TOP_K)
        show_context = st.toggle("Mostrar contexto recuperado", value=True)
        use_graph = st.toggle("Enriquecer con grafo de conocimiento", value=True)

        st.header("Consulta")
        query_mode = st.radio("Tipo", ["RAG", "Menciones exactas"], horizontal=False)
        with st.expander("Opciones de menciones"):
            mention_case_sensitive = st.checkbox("Distinguir mayusculas", value=False)
            mention_whole_word = st.checkbox("Palabra completa", value=False)
            mention_dedupe = st.checkbox("Deduplicar overlap de chunks", value=True)
            mention_context_chars = st.number_input("Caracteres de contexto", min_value=40, max_value=1000, value=220, step=20)
            mention_limit = st.number_input("Limite de menciones", min_value=10, max_value=5000, value=500, step=50)

    if "history" not in st.session_state:
        st.session_state.history = []

    question = st.chat_input("Pregunta algo sobre las transcripciones")
    if question:
        args = app_args(
            chroma_dir=chroma_dir,
            collection=collection,
            provider=provider,
            model=model,
            embedding_model=embedding_model,
            top_k=top_k,
            temperature=temperature,
            ollama_host=ollama_host,
            show_context=show_context,
        )
        if query_mode == "Menciones exactas":
            with st.spinner("Buscando menciones exactas en todos los chunks..."):
                matches = rag.search_mentions(
                    args,
                    question,
                    case_sensitive=mention_case_sensitive,
                    whole_word=mention_whole_word,
                    context_chars=int(mention_context_chars),
                    limit=int(mention_limit),
                    dedupe=mention_dedupe,
                )
            st.session_state.history.append(
                {
                    "mode": "mentions",
                    "question": question,
                    "answer": f"Menciones encontradas: {len(matches)}",
                    "contexts": [],
                    "matches": matches,
                }
            )
        else:
            with st.spinner("Recuperando contexto..."):
                contexts = rag.retrieve(args, question)

            if not contexts:
                st.session_state.history.append(
                    {
                        "mode": "rag",
                        "question": question,
                        "answer": "No encontre contexto. Construye el indice primero o revisa que existan transcripciones.",
                        "contexts": [],
                        "matches": [],
                    }
                )
            else:
                conocimiento = rag.graph_context(question) if use_graph else ""
                with st.spinner(f"Consultando {provider} / {model}..."):
                    try:
                        answer = rag.answer_with_llm(
                            args, rag.build_prompt(question, contexts, conocimiento=conocimiento)
                        ).strip()
                    except Exception as exc:
                        answer = f"Error consultando el LLM: {exc}"
                st.session_state.history.append(
                    {
                        "mode": "rag",
                        "question": question,
                        "answer": answer,
                        "contexts": contexts,
                        "matches": [],
                        "graph_block": conocimiento,
                    }
                )

    for item in st.session_state.history:
        with st.chat_message("user"):
            st.write(item["question"])
        with st.chat_message("assistant"):
            st.write(item["answer"])
            if item.get("graph_block"):
                with st.expander("🕸️ Conocimiento del grafo usado"):
                    st.markdown(item["graph_block"])
            if item.get("mode") == "mentions":
                render_mentions(item.get("matches", []))
            elif show_context and item["contexts"]:
                render_contexts(item["contexts"])


if __name__ == "__main__":
    main()
