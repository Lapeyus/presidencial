"""Dashboard Streamlit de análisis lingüístico de discursos de la Presidencia de Costa Rica.

Carga las transcripciones del canal de YouTube de la Presidencia desde un archivo
JSONL (por defecto ``data/presidencia_transcripts.jsonl``, ruta editable desde la
barra lateral) y ofrece un panel interactivo con cinco pestañas:

* **Red Semántica y Comunidades**: grafo de co-ocurrencia de términos con detección
  de comunidades de Louvain y matriz de adyacencia expandible.
* **Estrategia de Discurso**: métricas de polarización in-group/out-group, marcadores
  populistas y extracción de entidades (spaCy si está instalado; heurística de
  mayúsculas en caso contrario).
* **Histórico Temporal**: evolución de las puntuaciones VAD (valencia, activación,
  dominancia) por ventanas de segmentos dentro de cada video.
* **Distribución de Frecuencias**: términos más frecuentes sin stopwords.
* **Lenguaje de confrontación**: frases agresivas reproducibles, clasificadas y
  enlazadas al momento exacto de la fuente.

Los análisis usan léxicos en español definidos en este módulo (VAD_LEXICON,
IN_GROUP_TERMS, OUT_GROUP_TERMS, POPULIST_TERMS) y normalizan el texto quitando
tildes antes de contar. Todo el procesamiento es en memoria: el módulo no escribe
archivos, solo lee el JSONL de entrada.

Uso:
    streamlit run streamlit_analysis_app.py
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from scripts import aggressive_language as aggressive

# --- CONFIGURACIÓN DE PARÁMETROS Y LÉXICOS ---
DEFAULT_TRANSCRIPTS: Final[Path] = Path("data/presidencia_transcripts.jsonl")

SPANISH_STOPWORDS: Final[set[str]] = {
    "a", "al", "algo", "ante", "asi", "cada", "como", "con", "contra", "cual", "cuando",
    "de", "del", "desde", "donde", "dos", "el", "ella", "ellos", "en", "entre", "era",
    "es", "esa", "ese", "eso", "esta", "este", "esto", "estos", "fue", "ha", "hay", "la",
    "las", "le", "les", "lo", "los", "mas", "me", "mi", "mis", "muy", "no", "nos", "o",
    "para", "pero", "por", "porque", "que", "se", "si", "sin", "son", "su", "sus", "tambien",
    "tan", "te", "tiene", "todo", "todos", "un", "una", "uno", "y", "ya",
}

# Diccionario mapeado para optimizar la categorización VAD en complejidad O(1)
VAD_LEXICON: Final[dict[str, str]] = {}
for w in ["alegria", "bueno", "buenas", "calidad", "desarrollo", "digno", "eficiencia", "esperanza", "exito", "feliz", "gracias", "logro", "mejor", "orgullo", "positivo", "progreso", "trabajo"]:
    VAD_LEXICON[w] = "v_pos"
for w in ["amenaza", "corrupcion", "criminal", "delito", "dolor", "enemigo", "fracaso", "grave", "hambre", "indignacion", "injusticia", "ira", "miedo", "pobre", "problema", "vagabundo", "violencia"]:
    VAD_LEXICON[w] = "v_neg"
for w in ["ataque", "basta", "crisis", "delincuente", "emergencia", "furia", "grave", "hierve", "indignante", "lucha", "matar", "urgente", "vagabundo", "violencia"]:
    VAD_LEXICON[w] = "arousal"
for w in ["autoridad", "control", "decidir", "ejecutar", "firme", "gobierno", "liderar", "mandato", "orden", "poder", "presidencia", "resolver", "vamos"]:
    VAD_LEXICON[w] = "dominance"

IN_GROUP_TERMS: Final[set[str]] = {"nosotros", "nuestro", "nuestra", "pueblo", "costa rica", "costarricenses", "patria"}
OUT_GROUP_TERMS: Final[set[str]] = {"ellos", "los mismos", "corruptos", "criminales", "delincuentes", "comunismo", "venezuela", "cuba"}
POPULIST_TERMS: Final[set[str]] = {"pueblo", "elite", "corrupcion", "corruptos", "los mismos de siempre", "casta", "oligarquia"}

@dataclass(frozen=True)
class TranscriptRecord:
    """Representa una transcripción de video ya limpia, con sus metadatos y segmentos temporales."""

    video_id: str
    title: str
    url: str
    upload_date: str
    transcript_source: str
    text: str
    segments: list[dict[str, Any]]

@st.cache_data(show_spinner=False)
def load_records(path: str) -> list[TranscriptRecord]:
    """Carga las transcripciones desde un archivo JSONL y las convierte en registros tipados.

    Lee el archivo línea por línea; cada línea debe ser un objeto JSON con los
    campos ``video_id``, ``title``, ``url``, ``upload_date`` y ``transcript``
    (con ``text``, ``source`` y ``segments``). Descarta líneas vacías y
    registros cuya transcripción quede sin texto tras la limpieza de espacios.
    El resultado se memoiza con ``st.cache_data``, por lo que cambios en el
    archivo no se reflejan hasta invalidar la caché de Streamlit.

    Args:
        path: Ruta al archivo JSONL de transcripciones.

    Returns:
        Lista de TranscriptRecord; lista vacía si el archivo no existe.
    """
    jsonl = Path(path)
    if not jsonl.exists():
        return []
    records: list[TranscriptRecord] = []
    with jsonl.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            transcript = item.get("transcript") or {}
            text = clean_text(transcript.get("text") or "")
            if not text:
                continue
            records.append(
                TranscriptRecord(
                    video_id=str(item.get("video_id") or ""),
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    upload_date=str(item.get("upload_date") or ""),
                    transcript_source=str(transcript.get("source") or ""),
                    text=text,
                    segments=transcript.get("segments") or [],
                )
            )
    return records

@st.cache_resource(show_spinner=False)
def load_spacy_model() -> Any | None:
    """Carga un modelo spaCy de español si hay alguno instalado, o devuelve None.

    Prueba en orden ``es_core_news_lg``, ``es_core_news_md`` y ``es_core_news_sm``
    (con los componentes ``parser`` y ``ner`` desactivados) y devuelve el primero
    disponible. Si spaCy no está instalado o ningún modelo se puede cargar,
    devuelve None y la app cae al modo heurístico. El modelo se memoiza con
    ``st.cache_resource`` para no recargarlo en cada rerun.
    """
    try:
        import spacy
        for model in ("es_core_news_lg", "es_core_news_md", "es_core_news_sm"):
            try:
                return spacy.load(model, disable=["parser", "ner"])
            except OSError:
                continue
    except ImportError:
        return None
    return None

def clean_text(value: str) -> str:
    """Colapsa cualquier secuencia de espacios en blanco a un espacio simple y recorta extremos."""
    return re.sub(r"\s+", " ", str(value)).strip()

def normalize(value: str) -> str:
    """Pasa el texto a minúsculas y reemplaza vocales acentuadas, diéresis y eñes por sus equivalentes ASCII."""
    trans = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return value.lower().translate(trans)

def tokenize(text: str) -> list[str]:
    """Extrae los tokens alfabéticos de 3 o más letras del texto normalizado (sin tildes, en minúsculas)."""
    return re.findall(r"\b[a-z]{3,}\b", normalize(text))

def sentences(text: str) -> list[str]:
    """Divide el texto en oraciones cortando tras los signos ``.``, ``!`` o ``?`` seguidos de espacio."""
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]

def segment_windows(record: TranscriptRecord, window_segments: int = 12) -> list[dict[str, Any]]:
    """Agrupa los segmentos de la transcripción en ventanas consecutivas para el análisis temporal.

    Une los segmentos en bloques de ``window_segments`` (12 por defecto) y devuelve
    por cada bloque un dict con ``start``/``end`` (segundos), el texto concatenado
    y limpio, los metadatos del video y una ``url`` de YouTube con marca de tiempo
    apuntando al inicio de la ventana. Si el registro no tiene segmentos, devuelve
    una única ventana con todo el texto y tiempos en 0.
    """
    if not record.segments:
        return [{"start": 0, "end": 0, "text": record.text, "video_id": record.video_id, "title": record.title, "url": record.url}]
    windows = []
    for i in range(0, len(record.segments), window_segments):
        chunk = record.segments[i : i + window_segments]
        text = clean_text(" ".join(str(seg.get("text") or "") for seg in chunk))
        if text:
            windows.append({
                "start": chunk[0].get("start") or 0,
                "end": chunk[-1].get("end") or 0,
                "text": text,
                "video_id": record.video_id,
                "title": record.title,
                "url": timestamp_url(record.url, chunk[0].get("start")),
            })
    return windows

def lexical_metrics(text: str) -> dict[str, float]:
    """Calcula métricas léxicas básicas del texto: volumen, riqueza y estructura oracional.

    Returns:
        Dict con ``tokens``, ``unique_words``, ``type_token_ratio``,
        ``lexical_density`` (proporción de tokens que no son stopwords),
        ``sentence_count`` y ``avg_sentence_words``. Si el texto no produce
        tokens, todas las métricas valen 0.
    """
    toks = tokenize(text)
    sents = sentences(text)
    len_toks = len(toks)
    if len_toks == 0:
        return {"tokens": 0, "unique_words": 0, "type_token_ratio": 0, "lexical_density": 0, "sentence_count": 0, "avg_sentence_words": 0}
    
    unique_words = len(set(toks))
    content_words = sum(1 for token in toks if token not in SPANISH_STOPWORDS)
    sentence_count = len(sents)
    
    return {
        "tokens": len_toks,
        "unique_words": unique_words,
        "type_token_ratio": unique_words / len_toks,
        "lexical_density": content_words / len_toks,
        "sentence_count": sentence_count,
        "avg_sentence_words": len_toks / sentence_count if sentence_count else 0,
    }

def vad_scores(text: str) -> dict[str, float]:
    """Calcula las puntuaciones VAD (valencia, activación, dominancia) del texto con el léxico interno.

    Cuenta las apariciones de los términos de ``VAD_LEXICON`` en el texto
    tokenizado y normaliza cada conteo dividiéndolo por la raíz cuadrada del
    total de tokens. La valencia es la diferencia entre términos positivos y
    negativos; activación y dominancia son conteos simples normalizados.
    Devuelve 0.0 en las tres dimensiones si no hay tokens.
    """
    toks = tokenize(text)
    if not toks:
        return {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
    
    counts = Counter(toks)
    v_pos = sum(counts[w] for w in counts if VAD_LEXICON.get(w) == "v_pos")
    v_neg = sum(counts[w] for w in counts if VAD_LEXICON.get(w) == "v_neg")
    arousal = sum(counts[w] for w in counts if VAD_LEXICON.get(w) == "arousal")
    dominance = sum(counts[w] for w in counts if VAD_LEXICON.get(w) == "dominance")
    
    norm = math.sqrt(len(toks))
    return {
        "valence": (v_pos - v_neg) / norm,
        "arousal": arousal / norm,
        "dominance": dominance / norm,
    }

def discourse_scores(text: str) -> dict[str, int | float]:
    lower = normalize(text)
    in_group = sum(lower.count(term) for term in IN_GROUP_TERMS)
    out_group = sum(lower.count(term) for term in OUT_GROUP_TERMS)
    populist = sum(lower.count(term) for term in POPULIST_TERMS)
    
    total = max(1, len(tokenize(text)))
    return {
        "in_group_terms": in_group,
        "out_group_terms": out_group,
        "polarization_balance": in_group - out_group,
        "populist_markers": populist,
        "populist_rate_per_1k": 1000 * populist / total,
    }

def top_terms(text: str, limit: int = 30) -> pd.DataFrame:
    toks = [token for token in tokenize(text) if token not in SPANISH_STOPWORDS]
    return pd.DataFrame(Counter(toks).most_common(limit), columns=["term", "count"])

def extract_entities(text: str, nlp: Any | None, limit: int = 60) -> pd.DataFrame:
    if nlp is not None:
        doc = nlp(text[:100000])
        rows = Counter((ent.text.strip().title(), ent.label_) for ent in doc.ents if len(ent.text.strip()) > 2)
        return pd.DataFrame(
            [{"entity": ent, "label": label, "count": count} for (ent, label), count in rows.most_common(limit)]
        )
    candidates = re.findall(r"\b(?:[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+|$)){1,4}", text)
    normalized = [clean_text(c).title() for c in candidates if len(clean_text(c)) > 3]
    return pd.DataFrame(
        [{"entity": entity, "label": "HEURISTIC", "count": count} for entity, count in Counter(normalized).most_common(limit)]
    )

def semantic_network(text: str, top_n: int = 45, window: int = 7) -> nx.Graph:
    terms = [token for token in tokenize(text) if token not in SPANISH_STOPWORDS]
    top_counts = Counter(terms).most_common(top_n)
    vocabulary = {term for term, _ in top_counts}
    
    graph = nx.Graph()
    for term, count in top_counts:
        graph.add_node(term, count=count)
        
    for i, term in enumerate(terms):
        if term not in vocabulary:
            continue
        end_window = min(i + 1 + window, len(terms))
        for j in range(i + 1, end_window):
            other = terms[j]
            if other == term or other not in vocabulary:
                continue
            if graph.has_edge(term, other):
                graph[term][other]["weight"] += 1
            else:
                graph.add_edge(term, other, weight=1)
    return graph

def network_figure(graph: nx.Graph) -> go.Figure:
    if graph.number_of_nodes() == 0:
        return go.Figure()
    
    # Segmentación por Comunidades de Louvain para clasificar clústeres ideológicos
    try:
        communities = nx.community.louvain_communities(graph, seed=7)
        palette = ["#0284c7", "#b45309", "#dc2626", "#15803d", "#6d28d9", "#be185d"]
        color_map = {}
        for c_idx, community in enumerate(communities):
            color = palette[c_idx % len(palette)]
            for node in community:
                color_map[node] = color
    except Exception:
        color_map = {node: "#0284c7" for node in graph.nodes()}

    pos = nx.spring_layout(graph, seed=7, weight="weight", k=0.75)
    
    edge_x, edge_y = [], []
    for source, target in graph.edges():
        x0, y0 = pos[source]
        x1, y1 = pos[target]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])
        
    edge_trace = go.Scatter(x=edge_x, y=edge_y, mode="lines", line=dict(width=1.0, color="#cbd5e1"), hoverinfo="none")
    
    node_x, node_y, labels, sizes, node_colors = [], [], [], [], []
    for node, data in graph.nodes(data=True):
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)
        labels.append(f"<b>{node}</b><br>Frecuencia: {data.get('count', 0)}")
        sizes.append(14 + min(30, data.get("count", 1) * 1.5))
        node_colors.append(color_map.get(node, "#0284c7"))
        
    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        text=[label.split("<b>")[1].split("</b>")[0] for label in labels],
        textposition="top center",
        hovertext=labels,
        hoverinfo="text",
        marker=dict(size=sizes, color=node_colors, opacity=1.0, line=dict(width=1.5, color="#ffffff")),
        textfont=dict(size=11, color="#0f172a", family="Arial, sans-serif")
    )
    
    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(
        showlegend=False, 
        margin=dict(l=15, r=15, t=15, b=15), 
        height=650,
        plot_bgcolor="#f8fafc",  # Fondo gris-azul ultra claro (contraste óptimo)
        paper_bgcolor="#f8fafc"
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig

def timestamp_url(url: str, start: Any) -> str:
    if not isinstance(start, (int, float)) or start < 0:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}t={int(start)}"

def timestamp(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    seconds = int(value)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def main() -> None:
    st.set_page_config(page_title="Dashboard Lingüístico Presidencia CR", layout="wide")
    st.title("🇨🇷 Análisis Avanzado de Discurso y Redes Semánticas")
    st.markdown("---")

    with st.sidebar:
        st.header("🎛️ Panel de Configuración")
        path = st.text_input("Repositorio de datos (JSONL)", str(DEFAULT_TRANSCRIPTS))
        records = load_records(path)
        
        if not records:
            st.error("Error: Archivo de datos no encontrado o vacío.")
            st.stop()
            
        options = {f"{r.upload_date} | {r.title[:60]}...": r.video_id for r in records}
        selected_labels = st.multiselect("Selección de Transcripciones", list(options.keys()), default=list(options.keys())[:1])
        selected_ids = [options[label] for label in selected_labels]
        
        if not selected_ids:
            st.warning("Seleccione al menos un video de la lista.")
            st.stop()
            
        nlp = load_spacy_model()
        st.info("🧬 Lemmatizer: Activo" if nlp is not None else "⚠️ Modo Heurístico Activo.")
        
        st.markdown("---")
        st.subheader("Parámetros de Red")
        top_n = st.slider("Densidad de Nodos (Top términos)", 15, 100, 40, 5)
        co_window = st.slider("Ventana de Contexto (Tokens)", 3, 15, 6)

    chosen = [r for r in records if r.video_id in selected_ids]
    combined_text = "\n".join(r.text for r in chosen)
    
    metrics = lexical_metrics(combined_text)
    vad = vad_scores(combined_text)
    discourse = discourse_scores(combined_text)
    aggressive_events = aggressive.scan_records(chosen)

    st.markdown("### 📊 Métricas Cuantitativas de Control")
    kpis = st.columns(6)
    kpis[0].metric("Volumen (Tokens)", f"{metrics['tokens']:,}")
    kpis[1].metric("Riqueza Vocabulario", f"{metrics['unique_words']:,}")
    kpis[2].metric("Type-Token Ratio (TTR)", f"{metrics['type_token_ratio']:.3f}")
    kpis[3].metric("Densidad Léxica", f"{metrics['lexical_density']:.3f}")
    kpis[4].metric("Arousal Emocional", f"{vad['arousal']:.2f}")
    kpis[5].metric("Índice de Dominancia", f"{vad['dominance']:.2f}")

    tab_network, tab_discourse, tab_aggressive, tab_timeline, tab_lexicon = st.tabs(
        [
            "🕸️ Red Semántica y Comunidades",
            "📢 Estrategia de Discurso",
            "⚠️ Lenguaje de confrontación",
            "📈 Histórico Temporal",
            "🔤 Distribución de Frecuencias",
        ]
    )

    with tab_network:
        st.markdown("#### Topología de Co-ocurrencias y Clústeres Ideológicos")
        graph = semantic_network(combined_text, top_n=top_n, window=co_window)
        st.plotly_chart(network_figure(graph), use_container_width=True)
        
        with st.expander("Ver Matriz de Adyacencia y Peso de Enlaces"):
            edge_rows = [
                {"Nodo Origen": a, "Nodo Destino": b, "Peso Frecuencia": d.get("weight", 1)}
                for a, b, d in sorted(graph.edges(data=True), key=lambda x: x[2].get("weight", 1), reverse=True)
            ]
            st.dataframe(pd.DataFrame(edge_rows), use_container_width=True)

    with tab_discourse:
        st.markdown("#### Análisis de Polarización de Actores e Identidad")
        dcols = st.columns(4)
        dcols[0].metric("Términos In-Group (Nosotros)", discourse["in_group_terms"])
        dcols[1].metric("Términos Out-Group (Ellos)", discourse["out_group_terms"])
        dcols[2].metric("Balance Neto de Polarización", discourse["polarization_balance"], delta=int(discourse["polarization_balance"]))
        dcols[3].metric("Densidad de Marcadores Populistas / 1k", f"{discourse['populist_rate_per_1k']:.2f}")
        
        st.markdown("---")
        st.markdown("#### Extracción de Entidades y Actores Clave")
        entities_df = extract_entities(combined_text, nlp)
        if entities_df.empty:
            st.info("No se encontraron entidades registradas.")
        else:
            st.plotly_chart(px.bar(entities_df.head(25), x="count", y="entity", color="label", orientation="h", title="Top 25 Entidades Más Citadas"), use_container_width=True)

    with tab_aggressive:
        st.markdown("#### Frases agresivas y construcción del adversario")
        with st.expander("Definición operacional y límites", expanded=False):
            st.write(aggressive.DEFINITION)
            st.caption(
                f"Método {aggressive.METHOD_VERSION}. La unidad es la cláusula. "
                "El detector identifica lenguaje, no prueba la veracidad de las acusaciones. "
                "Hablante y blanco son inferencias cuando la transcripción no tiene diarización."
            )

        aggressive_summary = aggressive.summarize(chosen, aggressive_events)
        acols = st.columns(5)
        acols[0].metric("Frases detectadas", aggressive_summary["events"])
        acols[1].metric("Por conferencia", f"{aggressive_summary['events_per_record']:.2f}")
        acols[2].metric("Por 1.000 palabras", f"{aggressive_summary['events_per_1000_words']:.2f}")
        acols[3].metric("Marco nosotros/ellos", aggressive_summary["us_them_events"])
        acols[4].metric(
            "Alta confianza",
            sum(event.confidence >= 0.90 for event in aggressive_events),
        )

        if not aggressive_events:
            st.info("No se detectaron frases con la definición y el umbral actuales.")
        else:
            category_rows = [
                {"Categoría": category.replace("_", " ").title(), "Eventos": count}
                for category, count in aggressive_summary["by_category"].items()
            ]
            st.plotly_chart(
                px.bar(
                    pd.DataFrame(category_rows),
                    x="Eventos",
                    y="Categoría",
                    orientation="h",
                    title="Recursos de confrontación detectados",
                ),
                use_container_width=True,
            )

            event_rows = [
                {
                    "Fecha": event.date,
                    "Momento": event.timestamp,
                    "Hablante (estimado)": event.speaker,
                    "Blanco (sugerido)": event.target,
                    "Categoría": ", ".join(event.labels),
                    "Frase": event.phrase,
                    "Confianza": event.confidence,
                    "Nosotros/ellos": "sí" if event.us_them_frame else "no",
                    "Fuente": event.url,
                }
                for event in aggressive_events
            ]
            st.dataframe(
                pd.DataFrame(event_rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Fuente": st.column_config.LinkColumn("Fuente", display_text="Abrir video ↗"),
                    "Confianza": st.column_config.ProgressColumn(
                        "Confianza", min_value=0.0, max_value=1.0, format="%.2f"
                    ),
                },
            )
            st.caption(
                "Antes de publicar una atribución, abra el video: la confianza mide la regla lingüística, "
                "no la certeza del hablante ni del blanco."
            )

        with st.expander("Auditoría semántica: buscar formulaciones nuevas", expanded=False):
            st.write(
                "La base vectorial recupera pasajes parecidos a las ocho categorías y el grafo aporta "
                "contexto sobre los actores mencionados. Son candidatos para revisión: no alteran las "
                "métricas hasta convertirse en una regla versionada y una prueba de regresión."
            )
            semantic_top_k = st.slider(
                "Pasajes por categoría",
                min_value=1,
                max_value=10,
                value=4,
                key="aggressive_semantic_top_k",
            )
            selection_key = tuple(sorted(selected_ids))
            if st.button("Buscar en BD vectorial + grafo", key="aggressive_semantic_search"):
                try:
                    with st.spinner("Consultando las ocho categorías semánticas…"):
                        rows = aggressive.semantic_candidates(
                            selected_ids,
                            top_k_per_category=semantic_top_k,
                        )
                    st.session_state["aggressive_semantic_audit"] = {
                        "selection": selection_key,
                        "rows": rows,
                    }
                except Exception as exc:
                    st.error(f"No fue posible ejecutar la auditoría semántica: {exc}")

            audit = st.session_state.get("aggressive_semantic_audit") or {}
            if audit.get("selection") == selection_key:
                candidate_rows = []
                for candidate in audit.get("rows", []):
                    metadata = candidate.get("metadata") or {}
                    candidate_rows.append(
                        {
                            "Categorías sugeridas": ", ".join(
                                category.replace("_", " ") for category in candidate.get("categories", [])
                            ),
                            "Pasaje candidato": candidate.get("text", ""),
                            "Distancia vectorial": candidate.get("distance"),
                            "Contexto del grafo": candidate.get("graph_context", ""),
                            "Fuente": metadata.get("timestamp_url") or metadata.get("url", ""),
                        }
                    )
                if candidate_rows:
                    st.dataframe(
                        pd.DataFrame(candidate_rows),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Fuente": st.column_config.LinkColumn("Fuente", display_text="Abrir video ↗"),
                            "Distancia vectorial": st.column_config.NumberColumn(format="%.3f"),
                        },
                    )
                    st.caption("Menor distancia significa mayor cercanía semántica; no equivale a probabilidad.")
                else:
                    st.info("La consulta no devolvió candidatos para la selección actual.")

    with tab_timeline:
        st.markdown("#### Fluctuación de Carga Emocional en el Tiempo de la Conferencia")
        rows = []
        for record in chosen:
            for window in segment_windows(record):
                scores = vad_scores(window["text"])
                rows.append({
                    "Video": record.title[:40],
                    "Segundo Inclinación": window["start"],
                    "Timestamp": timestamp(window["start"]),
                    "Valence": scores["valence"],
                    "Arousal": scores["arousal"],
                    "Dominance": scores["dominance"]
                })
        
        timeline_df = pd.DataFrame(rows)
        if timeline_df.empty:
            st.info("No hay suficientes marcas de tiempo en el payload de entrada.")
        else:
            st.plotly_chart(
                px.line(timeline_df, x="Segundo Inclinación", y=["Valence", "Arousal", "Dominance"], color_discrete_sequence=["#10b981", "#ef4444", "#3b82f6"]),
                use_container_width=True
            )
            st.dataframe(timeline_df, use_container_width=True)

    with tab_lexicon:
        st.markdown("#### Distribución de Frecuencia de Palabras Clave Limpias")
        terms_df = top_terms(combined_text)
        st.plotly_chart(px.bar(terms_df.head(20), x="count", y="term", orientation="h", color="count", color_continuous_scale="Viridis"), use_container_width=True)

if __name__ == "__main__":
    main()
