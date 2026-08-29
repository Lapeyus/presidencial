#!/usr/bin/env python3
"""Collect YouTube transcripts for Presidencia de la Republica CR."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from tqdm import tqdm


DEFAULT_CHANNEL_URL = "https://www.youtube.com/@PresidenciadelaRepublicaCR"
DEFAULT_DATA_DIR = Path("data")
VIDEO_URL = "https://www.youtube.com/watch?v={video_id}"
LANGUAGE_PRIORITY = (
    "es",
    "es-419",
    "es-US",
    "es-ES",
    "a.es",
    "a.es-419",
    "a.es-US",
    "a.es-ES",
)


@dataclass(frozen=True)
class Transcript:
    source: str
    language: str | None
    text: str
    segments: list[dict[str, Any]]


def run_json(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def run_visible(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True)


def mlx_whisper_command() -> str:
    candidate = Path(sys.executable).with_name("mlx_whisper")
    return str(candidate) if candidate.exists() else "mlx_whisper"


def command_error_detail(exc: subprocess.CalledProcessError) -> str:
    output = "\n".join(part for part in (exc.stderr, exc.stdout) if part).strip()
    if output:
        output = "\n".join(output.splitlines()[-20:])
    return f"{exc.cmd} returned exit status {exc.returncode}" + (f"\n{output}" if output else "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recolecta metadata y transcripciones del canal de Presidencia CR."
    )
    parser.add_argument("--channel-url", default=DEFAULT_CHANNEL_URL)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--date-after",
        default="2026-05-01",
        help="Fecha minima de publicacion, YYYY-MM-DD. Default: 2026-05-01.",
    )
    parser.add_argument("--date-before", help="Fecha maxima de publicacion, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, help="Maximo de videos candidatos por tab.")
    parser.add_argument(
        "--whisper",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Usar Whisper si no hay subtitulos. Default: activado.",
    )
    parser.add_argument(
        "--whisper-model",
        default="mlx-community/whisper-large-v3-turbo",
        help="Modelo Whisper para mlx-whisper: repo de Hugging Face o directorio local.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default="chrome",
        help="Browser/perfil para cookies de yt-dlp. Default: chrome. Ej: chrome:Profile 1",
    )
    parser.add_argument(
        "--no-browser-cookies",
        action="store_true",
        help="No pasar cookies del navegador a yt-dlp.",
    )
    parser.add_argument(
        "--js-runtimes",
        default="",
        help="Runtimes JS para yt-dlp. Default: desactivado (no todas las versiones de yt-dlp soportan la opcion).",
    )
    parser.add_argument(
        "--extractor-args",
        default="youtube:player_client=default,tv_simply",
        help="Extractor args para yt-dlp; el default evita el bloqueo SABR de YouTube al descargar audio. Usa '' para no pasar la opcion.",
    )
    parser.add_argument(
        "--no-check-certificates",
        action="store_true",
        help="Pasar --no-check-certificates a yt-dlp si el entorno local tiene problemas SSL.",
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--force",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reprocesar videos aunque ya tengan estado previo. Default: activado.",
    )
    parser.add_argument(
        "--retry-no-transcript-after-hours",
        type=float,
        default=24,
        help="Horas antes de volver a revisar videos sin subtitulos. Default: 24.",
    )
    parser.add_argument(
        "--keep-audio",
        action="store_true",
        help="Conservar audio descargado para auditoria o reintentos.",
    )
    return parser.parse_args()


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_upload_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y%m%d").date()


def ytdlp_date(value: str | None) -> str | None:
    parsed = parse_iso_date(value)
    return parsed.strftime("%Y%m%d") if parsed else None


def date_in_range(uploaded: date | None, start: date | None, end: date | None) -> bool:
    if uploaded is None:
        return True
    if start and uploaded < start:
        return False
    if end and uploaded > end:
        return False
    return True


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            source TEXT,
            upload_date TEXT,
            title TEXT,
            updated_at TEXT NOT NULL,
            error TEXT
        )
        """
    )
    db.commit()


def ytdlp_args(args: argparse.Namespace, *extra: str) -> list[str]:
    command = ["yt-dlp"]
    if args.no_check_certificates:
        command.append("--no-check-certificates")
    if args.cookies_from_browser and not args.no_browser_cookies:
        command.extend(["--cookies-from-browser", args.cookies_from_browser])
    if args.js_runtimes:
        command.extend(["--js-runtimes", args.js_runtimes])
    if args.extractor_args:
        command.extend(["--extractor-args", args.extractor_args])
    command.extend(extra)
    return command


def is_ssl_certificate_error(exc: subprocess.CalledProcessError) -> bool:
    output = f"{exc.stdout or ''}\n{exc.stderr or ''}"
    return "CERTIFICATE_VERIFY_FAILED" in output or "certificate verify failed" in output


def run_ytdlp(
    args: argparse.Namespace,
    *extra: str,
    visible: bool = False,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[None]:
    command = ytdlp_args(args, *extra)
    runner = run_visible if visible else run
    try:
        return runner(command)
    except subprocess.CalledProcessError as exc:
        if args.no_check_certificates or not is_ssl_certificate_error(exc):
            raise
        print(
            "warning: yt-dlp fallo por certificados SSL; reintentando con --no-check-certificates "
            "para el resto de esta corrida.",
            file=sys.stderr,
        )
        args.no_check_certificates = True
        return runner(ytdlp_args(args, *extra))


def run_ytdlp_json(args: argparse.Namespace, *extra: str) -> dict[str, Any]:
    result = run_ytdlp(args, *extra)
    if result.stdout is None:
        raise RuntimeError("yt-dlp no devolvio salida JSON")
    return json.loads(result.stdout)


def get_status(db: sqlite3.Connection, video_id: str) -> str | None:
    row = db.execute("SELECT status FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return row[0] if row else None


def get_state(db: sqlite3.Connection, video_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT status, updated_at FROM videos WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if not row:
        return None
    return {"status": row[0], "updated_at": row[1]}


def recently_checked(updated_at: str | None, retry_after_hours: float) -> bool:
    if not updated_at:
        return False
    try:
        checked_at = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked_at < timedelta(hours=retry_after_hours)


def set_status(
    db: sqlite3.Connection,
    video_id: str,
    status: str,
    *,
    source: str | None = None,
    upload_date: str | None = None,
    title: str | None = None,
    error: str | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO videos(video_id, status, source, upload_date, title, updated_at, error)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            status = excluded.status,
            source = COALESCE(excluded.source, videos.source),
            upload_date = COALESCE(excluded.upload_date, videos.upload_date),
            title = COALESCE(excluded.title, videos.title),
            updated_at = excluded.updated_at,
            error = excluded.error
        """,
        (
            video_id,
            status,
            source,
            upload_date,
            title,
            datetime.now(timezone.utc).isoformat(),
            error,
        ),
    )
    db.commit()


def discover_video_ids(db: sqlite3.Connection, args: argparse.Namespace) -> list[str]:
    channel_url = args.channel_url
    urls = [f"{channel_url.rstrip('/')}/videos", f"{channel_url.rstrip('/')}/streams"]
    seen: set[str] = set()
    video_ids: list[str] = []
    date_after = ytdlp_date(args.date_after)
    date_before = ytdlp_date(args.date_before)
    start_date = parse_iso_date(args.date_after)
    end_date = parse_iso_date(args.date_before)

    for url in urls:
        cmd = ["--flat-playlist", "--dump-single-json", "--ignore-errors"]
        if date_after:
            cmd.extend(["--dateafter", date_after])
        if date_before:
            cmd.extend(["--datebefore", date_before])
        if args.limit:
            cmd.extend(["--playlist-end", str(args.limit)])
        cmd.append(url)

        try:
            playlist = run_ytdlp_json(args, *cmd)
        except subprocess.CalledProcessError as exc:
            print(f"warning: no se pudo leer {url}: {exc.stderr.strip()}", file=sys.stderr)
            continue

        tab_ids: list[str] = []
        for entry in playlist.get("entries") or []:
            video_id = extract_video_id(entry)
            if video_id and video_id not in seen:
                seen.add(video_id)
                tab_ids.append(video_id)
        video_ids.extend(filter_ids_by_date(db, args, tab_ids, start_date, end_date))

    return video_ids


def extract_video_id(entry: dict[str, Any]) -> str | None:
    candidate = entry.get("id")
    if candidate and re.fullmatch(r"[\w-]{11}", candidate):
        return candidate
    url = entry.get("url") or entry.get("webpage_url")
    if not url:
        return None
    if re.fullmatch(r"[\w-]{11}", url):
        return url
    parsed = urlparse(url)
    if parsed.hostname == "youtu.be":
        return parsed.path.strip("/") or None
    return parse_qs(parsed.query).get("v", [None])[0]


def fetch_metadata(args: argparse.Namespace, video_id: str) -> dict[str, Any]:
    cache = getattr(args, "metadata_cache", None)
    if cache is not None and video_id in cache:
        return cache[video_id]
    metadata = run_ytdlp_json(
        args,
        "--skip-download",
        "--dump-single-json",
        "--ignore-no-formats-error",
        VIDEO_URL.format(video_id=video_id),
    )
    if cache is not None:
        cache[video_id] = metadata
    return metadata


def fetch_metadata_for_filter(args: argparse.Namespace, video_id: str) -> dict[str, Any] | None:
    try:
        return fetch_metadata(args, video_id)
    except subprocess.CalledProcessError as exc:
        print(f"warning: no se pudo leer metadata de {video_id}: {command_error_detail(exc)}", file=sys.stderr)
        return None


def filter_ids_by_date(
    db: sqlite3.Connection,
    args: argparse.Namespace,
    video_ids: Iterable[str],
    start_date: date | None,
    end_date: date | None,
) -> list[str]:
    if not start_date and not end_date:
        return list(video_ids)

    filtered: list[str] = []
    consecutive_older = 0
    STOP_THRESHOLD = 15

    for video_id in tqdm(list(video_ids), desc="filtrando fechas", unit="video", leave=False):
        if start_date and consecutive_older >= STOP_THRESHOLD:
            break

        uploaded = None
        row = db.execute("SELECT upload_date FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if row and row[0]:
            uploaded = parse_upload_date(row[0])

        if uploaded is None:
            metadata = fetch_metadata_for_filter(args, video_id)
            if metadata is not None:
                uploaded = parse_upload_date(metadata.get("upload_date"))
                title = metadata.get("title")
                status = get_status(db, video_id) or "discovered"
                set_status(db, video_id, status, upload_date=metadata.get("upload_date"), title=title)

        if uploaded is None:
            filtered.append(video_id)
            consecutive_older = 0
            continue

        if start_date and uploaded < start_date:
            consecutive_older += 1
            continue
        else:
            consecutive_older = 0

        if end_date and uploaded > end_date:
            continue

        filtered.append(video_id)

    return filtered


def should_skip_live(metadata: dict[str, Any]) -> bool:
    return metadata.get("live_status") in {"is_live", "is_upcoming"}





def choose_subtitle(metadata: dict[str, Any]) -> tuple[str, str, bool] | None:
    subtitles = filter_caption_tracks(metadata.get("subtitles") or {})
    automatic = filter_caption_tracks(metadata.get("automatic_captions") or {})

    # 1. Buscar subtítulos manuales en español según prioridad
    for lang in LANGUAGE_PRIORITY:
        if lang in subtitles:
            return lang, "manual", False
            
    # 2. Buscar cualquier subtítulo manual que comience con 'es' (ej: es-CR)
    for lang in sorted(subtitles):
        if lang.startswith("es"):
            return lang, "manual", False

    # 3. Buscar subtítulos automáticos en español según prioridad
    for lang in LANGUAGE_PRIORITY:
        if lang in automatic:
            return lang, "automatic", True
            
    # 4. Buscar cualquier subtítulo automático que comience con 'es' o 'a.es'
    for lang in sorted(automatic):
        if lang.startswith("es") or lang.startswith("a.es"):
            return lang, "automatic", True

    return None


def filter_caption_tracks(tracks: dict[str, Any]) -> dict[str, Any]:
    return {
        lang: formats
        for lang, formats in tracks.items()
        if lang != "live_chat" and not lang.endswith(".live_chat")
    }


def transcript_from_youtube(
    args: argparse.Namespace,
    metadata: dict[str, Any],
    tmpdir: Path,
) -> Transcript | None:
    choice = choose_subtitle(metadata)
    if not choice:
        return None

    video_id = metadata["id"]
    lang, kind, is_auto = choice
    cmd = [
        "--skip-download",
        "--sub-format",
        "vtt",
        "--sub-langs",
        lang,
        "-o",
        str(tmpdir / "%(id)s.%(ext)s"),
    ]
    cmd.append("--write-auto-subs" if is_auto else "--write-subs")
    cmd.append(VIDEO_URL.format(video_id=video_id))
    run_ytdlp(args, *cmd)

    files = sorted(tmpdir.glob(f"{video_id}*.vtt"))
    if not files:
        return None

    segments = parse_vtt(files[0].read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(segment["text"] for segment in segments if segment["text"]).strip()
    if not text:
        return None
    return Transcript(source=f"youtube_{kind}", language=lang, text=text, segments=segments)


def parse_vtt(content: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_times: tuple[float | None, float | None] | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_times, current_lines
        if current_times is None:
            current_lines = []
            return
        text = clean_caption_text(" ".join(current_lines))
        if text:
            if not segments or segments[-1]["text"] != text:
                segments.append({"start": current_times[0], "end": current_times[1], "text": text})
        current_times = None
        current_lines = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            flush()
            continue
        if "-->" in line:
            flush()
            left, right = line.split("-->", 1)
            current_times = (parse_vtt_time(left.strip()), parse_vtt_time(right.strip().split()[0]))
            continue
        if current_times is not None and not line.isdigit():
            current_lines.append(line)

    flush()
    return segments


def parse_vtt_time(value: str) -> float | None:
    match = re.match(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int(match.group(4))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def clean_caption_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    value = re.sub(r"\s+", " ", value)
    # Eliminar repeticiones consecutivas de la misma palabra (3 o más veces) causadas por alucinaciones de Whisper
    value = re.sub(r'\b(\w+)(?:\s+\1){2,}\b', r'\1', value, flags=re.IGNORECASE)
    # E.g. "y, y, y" -> "y"
    value = re.sub(r'\b(\w+)(?:,\s+\1){2,}\b', r'\1', value, flags=re.IGNORECASE)
    return value.strip()


def transcript_from_whisper(
    args: argparse.Namespace,
    metadata: dict[str, Any],
    audio_dir: Path,
    model: str,
    keep_audio: bool,
) -> Transcript:
    video_id = metadata["id"]
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(audio_dir / f"{video_id}.%(ext)s")

    tqdm.write(f"{video_id}: descargando audio para Whisper")
    run_ytdlp(
        args,
            "-f",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "-o",
            output_template,
            VIDEO_URL.format(video_id=video_id),
        visible=True,
    )

    audio_files = sorted(audio_dir.glob(f"{video_id}.*"))
    audio_file = next((path for path in audio_files if path.suffix.lower() in {".mp3", ".m4a", ".webm"}), None)
    if audio_file is None:
        raise RuntimeError("No se encontro audio descargado para Whisper.")

    with tempfile.TemporaryDirectory() as whisper_tmp:
        whisper_dir = Path(whisper_tmp)
        tqdm.write(f"{video_id}: transcribiendo con mlx-whisper ({model})")
        # condition-on-previous-text False evita que Whisper se salte habla de
        # bajo volumen tras silencios largos (intros de transmisiones en vivo).
        run_visible(
            [
                mlx_whisper_command(),
                str(audio_file),
                "--model",
                model,
                "--language",
                "es",
                "--condition-on-previous-text",
                "False",
                "--output-format",
                "json",
                "--output-dir",
                str(whisper_dir),
            ]
        )
        whisper_json = whisper_dir / f"{audio_file.stem}.json"
        payload = json.loads(whisper_json.read_text(encoding="utf-8"))

    if not keep_audio:
        audio_file.unlink(missing_ok=True)

    segments = [
        {"start": item.get("start"), "end": item.get("end"), "text": clean_caption_text(item.get("text", ""))}
        for item in payload.get("segments", [])
    ]
    text = clean_caption_text(payload.get("text", ""))
    if not text:
        text = "\n".join(segment["text"] for segment in segments if segment["text"]).strip()
    model_name = model.rsplit("/", 1)[-1]
    return Transcript(source=f"mlx_whisper_{model_name}", language="es", text=text, segments=segments)


def build_record(metadata: dict[str, Any], transcript: Transcript) -> dict[str, Any]:
    return {
        "video_id": metadata.get("id"),
        "url": metadata.get("webpage_url") or VIDEO_URL.format(video_id=metadata["id"]),
        "title": metadata.get("title"),
        "description": metadata.get("description"),
        "channel": metadata.get("channel"),
        "channel_id": metadata.get("channel_id"),
        "upload_date": metadata.get("upload_date"),
        "timestamp": metadata.get("timestamp"),
        "duration": metadata.get("duration"),
        "view_count": metadata.get("view_count"),
        "live_status": metadata.get("live_status"),
        "was_live": metadata.get("was_live"),
        "transcript": {
            "source": transcript.source,
            "language": transcript.language,
            "text": transcript.text,
            "segments": transcript.segments,
        },
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def write_record(record: dict[str, Any], records_dir: Path) -> None:
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / f"{record['video_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rebuild_jsonl(records_dir: Path, output_path: Path) -> int:
    records = []
    for path in sorted(records_dir.glob("*.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    records.sort(key=lambda item: (item.get("upload_date") or "", item.get("video_id") or ""), reverse=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(records)


def process_video(
    db: sqlite3.Connection,
    video_id: str,
    args: argparse.Namespace,
    start_date: date | None,
    end_date: date | None,
) -> str:
    state = get_state(db, video_id)
    status = state["status"] if state else None
    state_updated_at = state["updated_at"] if state else None
    if status == "done" and not args.force:
        return "skipped_done"
    if status == "failed" and not (args.retry_failed or args.force):
        return "skipped_failed"
    if (
        status == "no_transcript"
        and not (args.whisper or args.force)
        and recently_checked(state_updated_at, args.retry_no_transcript_after_hours)
    ):
        return "skipped_no_transcript"

    try:
        tqdm.write(f"{video_id}: leyendo metadata")
        metadata = fetch_metadata(args, video_id)
        uploaded = parse_upload_date(metadata.get("upload_date"))
        if not date_in_range(uploaded, start_date, end_date):
            return "skipped_date"
        if should_skip_live(metadata):
            set_status(
                db,
                video_id,
                "deferred_live",
                upload_date=metadata.get("upload_date"),
                title=metadata.get("title"),
            )
            return "deferred_live"

        tqdm.write(f"{video_id}: buscando subtitulos/CC de YouTube")
        with tempfile.TemporaryDirectory() as tmp:
            transcript = transcript_from_youtube(args, metadata, Path(tmp))
        if transcript is None and args.whisper:
            tqdm.write(f"{video_id}: sin subtitulos utilizables; usando Whisper")
            transcript = transcript_from_whisper(
                args,
                metadata,
                args.data_dir / "audio",
                args.whisper_model,
                args.keep_audio,
            )
        if transcript is None:
            set_status(
                db,
                video_id,
                "no_transcript",
                upload_date=metadata.get("upload_date"),
                title=metadata.get("title"),
                error="Sin subtitulos de YouTube y Whisper no activado.",
            )
            return "no_transcript"

        record = build_record(metadata, transcript)
        write_record(record, args.data_dir / "records")
        set_status(
            db,
            video_id,
            "done",
            source=transcript.source,
            upload_date=metadata.get("upload_date"),
            title=metadata.get("title"),
        )
        return "done"
    except Exception as exc:
        error = command_error_detail(exc) if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        tqdm.write(f"{video_id}: failed\n{error}")
        set_status(db, video_id, "failed", error=error)
        return "failed"


def main() -> int:
    args = parse_args()
    args.metadata_cache = {}
    args.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.data_dir / "state.sqlite")
    ensure_schema(db)

    start_date = parse_iso_date(args.date_after)
    end_date = parse_iso_date(args.date_before)
    video_ids = discover_video_ids(db, args)
    print(f"videos candidatos: {len(video_ids)}")

    counts: dict[str, int] = {}
    for index, video_id in enumerate(tqdm(video_ids, desc="procesando videos", unit="video"), start=1):
        outcome = process_video(db, video_id, args, start_date, end_date)
        counts[outcome] = counts.get(outcome, 0) + 1
        tqdm.write(f"[{index}/{len(video_ids)}] {video_id}: {outcome}")

    total_records = rebuild_jsonl(args.data_dir / "records", args.data_dir / "presidencia_transcripts.jsonl")
    print(f"resumen: {json.dumps(counts, ensure_ascii=False, sort_keys=True)}")
    print(f"archivo final: {args.data_dir / 'presidencia_transcripts.jsonl'} ({total_records} registros)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
