#!/usr/bin/env python3
"""Genera el sitio estático de perfiles de discurso en ``docs/`` (GitHub Pages).

Lee ``data/perfiles_discurso.json`` y produce ``docs/index.html``: una página
autónoma (CSS/JS inline, datos embebidos) con el análisis de a quiénes culpa
la presidenta, a quiénes trata como aliados o adversarios, y el perfil de
cada actor con sus citas enlazadas al momento exacto del video en YouTube.

Uso:
    python scripts/build_site.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PERFILES = Path("data/perfiles_discurso.json")
DOCS = Path("docs")

ETIQUETAS = {
    "aliado": "Aliado", "adversario": "Adversario", "culpado": "Culpado",
    "funcionario": "Funcionario", "neutral": "Neutral", "mixto": "Mixto",
}

TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hemeroteca Presidencial</title>
<meta name="description" content="Hemeroteca Presidencial: análisis del discurso público de la presidenta de Costa Rica — aliados, adversarios y culpas — con cada afirmación enlazada a su fuente original.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&display=swap">
<style>
  :root {
    --bg:#FAF9FB; --surface:#FFFFFF; --ink:#232030; --muted:#6B6577;
    --line:#E5E1EC; --accent:#6E3A82; --chip:#EFEBF4;
    --c-aliado:#15803d; --c-adversario:#b91c1c; --c-culpado:#92400e;
    --c-funcionario:#1d4ed8; --c-neutral:#57534e; --c-mixto:#7e22ce;
    --b-aliado:#dcfce7; --b-adversario:#fee2e2; --b-culpado:#fef3c7;
    --b-funcionario:#dbeafe; --b-neutral:#e7e5e4; --b-mixto:#f3e8ff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#16141C; --surface:#1E1B26; --ink:#E9E6F1; --muted:#9B94A8;
      --line:#322E3E; --accent:#C08BD6; --chip:#2A2635;
      --c-aliado:#4ade80; --c-adversario:#f87171; --c-culpado:#fbbf24;
      --c-funcionario:#93c5fd; --c-neutral:#a8a29e; --c-mixto:#d8b4fe;
      --b-aliado:#14532d40; --b-adversario:#7f1d1d40; --b-culpado:#78350f40;
      --b-funcionario:#1e3a8a40; --b-neutral:#44403c40; --b-mixto:#581c8740;
    }
  }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.6; }
  a { color:var(--accent); }
  .wrap { max-width:1080px; margin:0 auto; padding:0 1.2rem 4rem; }
  header { padding:3rem 0 1.5rem; }
  .eyebrow { font-family:Archivo,sans-serif; font-size:.72rem; font-weight:600; letter-spacing:.1em; text-transform:uppercase; color:var(--accent); margin-bottom:.7rem; }
  h1 { font-family:Archivo,sans-serif; font-size:clamp(1.9rem,5vw,2.8rem); line-height:1.1; letter-spacing:-.015em; margin-bottom:.8rem; }
  .lede { font-size:1.1rem; max-width:64ch; color:var(--ink); margin-bottom:1.2rem; }
  .aviso { background:var(--surface); border:1px solid var(--line); border-left:3px solid var(--accent); border-radius:8px; padding:.9rem 1.1rem; font-size:.88rem; color:var(--muted); max-width:74ch; }
  .aviso strong { color:var(--ink); }
  .stats { display:flex; flex-wrap:wrap; gap:.6rem; margin:1.6rem 0; }
  .stat { background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:.6rem 1rem; font-family:Archivo,sans-serif; }
  .stat b { font-size:1.3rem; display:block; }
  .stat span { font-size:.72rem; color:var(--muted); }
  .controls { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; margin-bottom:1.4rem; }
  .fchip { font-family:Archivo,sans-serif; font-size:.78rem; font-weight:600; border:1px solid var(--line); background:var(--surface); color:var(--muted); border-radius:999px; padding:.35rem .85rem; cursor:pointer; }
  .fchip.on { border-color:var(--accent); color:var(--accent); background:var(--chip); }
  #buscar { flex:1; min-width:180px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:8px; padding:.5rem .8rem; font-size:.9rem; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:.9rem; }
  .card { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:1rem 1.1rem; cursor:pointer; }
  .card:hover { border-color:var(--accent); }
  .card h3 { font-family:Archivo,sans-serif; font-size:1.05rem; margin-bottom:.15rem; }
  .cargo { font-size:.8rem; color:var(--muted); margin-bottom:.5rem; }
  .badge { display:inline-block; font-family:Archivo,sans-serif; font-size:.68rem; font-weight:700; letter-spacing:.05em; text-transform:uppercase; border-radius:999px; padding:.18rem .6rem; margin-bottom:.5rem; }
  .card p { font-size:.86rem; color:var(--muted); }
  dialog { border:1px solid var(--line); border-radius:12px; background:var(--surface); color:var(--ink); max-width:760px; width:calc(100vw - 2rem); max-height:85vh; padding:0; }
  dialog::backdrop { background:rgba(10,8,14,.55); }
  .dlg-inner { padding:1.5rem 1.6rem; overflow-y:auto; max-height:85vh; }
  .dlg-inner h2 { font-family:Archivo,sans-serif; margin-bottom:.2rem; }
  .cerrar { float:right; background:none; border:none; color:var(--muted); font-size:1.4rem; cursor:pointer; }
  .sec { margin-top:1.2rem; }
  .sec h4 { font-family:Archivo,sans-serif; font-size:.75rem; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); margin-bottom:.5rem; }
  .rasgos { display:flex; flex-wrap:wrap; gap:.4rem; }
  .rasgo { background:var(--chip); border-radius:999px; padding:.2rem .7rem; font-size:.78rem; }
  .item { border-left:2px solid var(--line); padding:.2rem 0 .2rem .9rem; margin-bottom:.9rem; }
  .item .t { font-weight:600; font-size:.92rem; }
  blockquote { font-size:.85rem; color:var(--muted); font-style:italic; margin:.3rem 0; }
  .fuente { font-size:.76rem; }
  footer { border-top:1px solid var(--line); margin-top:3rem; padding-top:1.2rem; font-size:.8rem; color:var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="eyebrow">Análisis de discurso · Costa Rica · mayo–agosto 2026</p>
    <h1>Hemeroteca Presidencial</h1>
    <p class="lede">¿A quiénes culpa, a quiénes trata como aliados y a quiénes como adversarios la presidenta Laura Fernández? Perfiles de los actores de su discurso público, construidos a partir de __VIDEOS__ videos (conferencias de prensa, cadenas nacionales y entrevistas) con cada afirmación enlazada al momento exacto de la fuente.</p>
    <div class="aviso"><strong>Nota metodológica.</strong> Esta página describe el <strong>trato que la presidenta da en su discurso público</strong> a personas e instituciones — no afirma hechos sobre ellas. Los perfiles se generan de forma automatizada (transcripción por voz → grafo de conocimiento → análisis con modelo de lenguaje) sobre declaraciones públicas, y pueden contener errores de transcripción o interpretación: cada cita enlaza a su video original con marca de tiempo para verificación.</div>
  </header>

  <div class="stats" id="stats"></div>
  <div class="controls" id="filtros"></div>
  <div class="grid" id="grid"></div>

  <dialog id="dlg"><div class="dlg-inner" id="dlg-inner"></div></dialog>

  <footer>
    Generado el __FECHA__ a partir del corpus público del canal de la Presidencia de la República de Costa Rica y entrevistas en medios ·
    Pipeline de código abierto: <a href="https://github.com/Lapeyus/presidencial">Lapeyus/presidencial</a> · Motor de perfiles: __MOTOR__
  </footer>
</div>
<script>
const DATA = __DATA__;
const ETIQ = {aliado:"Aliado", adversario:"Adversario", culpado:"Culpado", funcionario:"Funcionario", neutral:"Neutral", mixto:"Mixto"};
const ORDEN = ["culpado","adversario","mixto","aliado","funcionario","neutral"];
let filtro = "todos", q = "";

function esc(s){ return String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function badge(c){ return `<span class="badge" style="color:var(--c-${c});background:var(--b-${c})">${ETIQ[c]||c}</span>`; }

function stats(){
  const n = {}; DATA.perfiles.forEach(p=>n[p.clasificacion]=(n[p.clasificacion]||0)+1);
  const decl = DATA.perfiles.reduce((a,p)=>a+p.declaraciones.length+p.culpas.length,0);
  document.getElementById("stats").innerHTML =
    `<div class="stat"><b>${DATA.perfiles.length}</b><span>perfiles</span></div>` +
    ORDEN.filter(c=>n[c]).map(c=>`<div class="stat"><b style="color:var(--c-${c})">${n[c]}</b><span>${ETIQ[c].toLowerCase()}s</span></div>`).join("") +
    `<div class="stat"><b>${decl}</b><span>afirmaciones con fuente</span></div>`;
}
function filtros(){
  const el = document.getElementById("filtros");
  el.innerHTML = ["todos",...ORDEN].map(c=>`<button class="fchip${filtro===c?" on":""}" data-c="${c}">${c==="todos"?"Todos":ETIQ[c]}</button>`).join("") +
    `<input id="buscar" placeholder="Buscar persona…" value="${esc(q)}">`;
  el.querySelectorAll(".fchip").forEach(b=>b.onclick=()=>{filtro=b.dataset.c; render();});
  const inp = el.querySelector("#buscar");
  inp.oninput = ()=>{q=inp.value; grid(); };
}
function grid(){
  const norm = s=>String(s??"").toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g,"");
  const items = DATA.perfiles.filter(p =>
    (filtro==="todos"||p.clasificacion===filtro) &&
    (!q || norm(p.nombre+" "+p.cargo+" "+(p.alias||[]).join(" ")).includes(norm(q))));
  document.getElementById("grid").innerHTML = items.map((p,i)=>`
    <div class="card" data-n="${esc(p.nombre)}">
      ${badge(p.clasificacion)}
      <h3>${esc(p.nombre)}</h3>
      <div class="cargo">${esc(p.cargo)||"&nbsp;"}</div>
      <p>${esc(p.resumen.slice(0,180))}${p.resumen.length>180?"…":""}</p>
    </div>`).join("") || '<p style="color:var(--muted)">Sin resultados.</p>';
  document.querySelectorAll(".card").forEach(c=>c.onclick=()=>abrir(c.dataset.n));
}
function fuente(x){
  return x.url ? `<div class="fuente"><a href="${esc(x.url)}" target="_blank" rel="noopener">▶ ${esc(x.video.slice(0,70))} · ${esc(x.fecha)} · ${esc(x.timestamp)}</a></div>` : "";
}
function abrir(nombre){
  const p = DATA.perfiles.find(x=>x.nombre===nombre); if(!p) return;
  const sec = (titulo, arr, campo)=> arr.length ? `<div class="sec"><h4>${titulo}</h4>` +
    arr.map(x=>`<div class="item"><div class="t">${esc(x[campo])}</div>${x.cita?`<blockquote>«${esc(x.cita.slice(0,320))}»</blockquote>`:""}${fuente(x)}</div>`).join("") + `</div>` : "";
  document.getElementById("dlg-inner").innerHTML = `
    <button class="cerrar" onclick="document.getElementById('dlg').close()">✕</button>
    ${badge(p.clasificacion)}
    <h2>${esc(p.nombre)}</h2>
    <div class="cargo">${esc(p.cargo)}</div>
    <p>${esc(p.resumen)}</p>
    ${p.caracteristicas.length?`<div class="sec"><h4>Cómo la presidenta le caracteriza</h4><div class="rasgos">${p.caracteristicas.map(c=>`<span class="rasgo">${esc(c)}</span>`).join("")}</div></div>`:""}
    ${sec("De qué le culpa", p.culpas, "tema")}
    ${sec("Declaraciones clave", p.declaraciones, "sintesis")}
    <div class="sec" style="color:var(--muted);font-size:.78rem">Basado en ${p.menciones_analizadas} menciones del corpus.</div>`;
  document.getElementById("dlg").showModal();
}
document.getElementById("dlg").onclick = e=>{ if(e.target.id==="dlg") e.target.close(); };
stats(); filtros(); grid();
</script>
</body>
</html>
"""


def main() -> int:
    if not PERFILES.exists():
        raise SystemExit("No existe data/perfiles_discurso.json; corre primero scripts/discourse_profiles.py")
    data = json.loads(PERFILES.read_text(encoding="utf-8"))
    orden = {"culpado": 0, "adversario": 1, "mixto": 2, "aliado": 3, "funcionario": 4, "neutral": 5}
    data["perfiles"].sort(key=lambda p: (orden.get(p.get("clasificacion"), 9), -len(p.get("declaraciones", []))))
    videos = len({d.get("video") for p in data["perfiles"] for d in p.get("declaraciones", []) + p.get("culpas", []) if d.get("video")})

    html = (
        TEMPLATE
        .replace("__DATA__", json.dumps(data, ensure_ascii=False))
        .replace("__FECHA__", datetime.now().strftime("%Y-%m-%d"))
        .replace("__MOTOR__", str(data.get("motor") or ""))
        .replace("__VIDEOS__", str(max(videos, 1)))
    )
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"docs/index.html generado: {len(data['perfiles'])} perfiles, {len(html):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
