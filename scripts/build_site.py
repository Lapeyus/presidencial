#!/usr/bin/env python3
"""Genera la Hemeroteca Presidencial en ``docs/`` (GitHub Pages).

Lee ``data/perfiles_discurso.json`` (perfiles de personas y temas) y produce
``docs/index.html``: página autónoma con

- la sección **¿A qué presta más atención?** — los temas del discurso como
  barras de atención (menciones en el corpus) coloreadas por postura
  (positiva/negativa/mixta/neutral), con detalle expandible; y
- los **perfiles de personas estilo red social** — avatar (foto de
  ``docs/fotos/<slug>.jpg`` si existe, con créditos de Wikipedia/Wikimedia,
  o iniciales), clasificación, bio; al abrir la foto aparecen los rasgos
  que la presidenta le adjudica, sus culpas y declaraciones con fuente.

Uso:
    python scripts/build_site.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

PERFILES = Path("data/perfiles_discurso.json")
DOCS = Path("docs")
FOTOS = DOCS / "fotos"


def slug(value: str, limit: int = 60) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")[:limit]


TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hemeroteca Presidencial</title>
<meta name="description" content="Hemeroteca Presidencial: a qué le presta atención la presidenta de Costa Rica y cómo trata a los actores de su discurso — con cada afirmación enlazada a su fuente original.">
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
  h2 { font-family:Archivo,sans-serif; font-size:1.4rem; margin:2.4rem 0 .4rem; }
  .sub { color:var(--muted); font-size:.92rem; margin-bottom:1rem; max-width:70ch; }
  .lede { font-size:1.08rem; max-width:64ch; margin-bottom:1.2rem; }
  .aviso { background:var(--surface); border:1px solid var(--line); border-left:3px solid var(--accent); border-radius:8px; padding:.9rem 1.1rem; font-size:.86rem; color:var(--muted); max-width:74ch; }
  .aviso strong { color:var(--ink); }
  .stats { display:flex; flex-wrap:wrap; gap:.6rem; margin:1.6rem 0 0; }
  .stat { background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:.6rem 1rem; font-family:Archivo,sans-serif; }
  .stat b { font-size:1.3rem; display:block; }
  .stat span { font-size:.72rem; color:var(--muted); }

  .temas { display:flex; flex-direction:column; gap:.45rem; }
  .tema { display:grid; grid-template-columns:minmax(140px,220px) 1fr auto; gap:.8rem; align-items:center; background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:.55rem .9rem; cursor:pointer; }
  .tema:hover { border-color:var(--accent); }
  .tema .n { font-family:Archivo,sans-serif; font-weight:600; font-size:.9rem; }
  .barra { height:10px; border-radius:999px; background:var(--chip); overflow:hidden; }
  .barra i { display:block; height:100%; border-radius:999px; }
  .tema .m { font-size:.75rem; color:var(--muted); white-space:nowrap; font-variant-numeric:tabular-nums; }
  .leyenda { display:flex; flex-wrap:wrap; gap:1rem; margin:.7rem 0 0; font-size:.78rem; color:var(--muted); }
  .leyenda i { display:inline-block; width:10px; height:10px; border-radius:99px; margin-right:.35rem; }

  .controls { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; margin:1rem 0 1.2rem; }
  .fchip { font-family:Archivo,sans-serif; font-size:.78rem; font-weight:600; border:1px solid var(--line); background:var(--surface); color:var(--muted); border-radius:999px; padding:.35rem .85rem; cursor:pointer; }
  .fchip.on { border-color:var(--accent); color:var(--accent); background:var(--chip); }
  #buscar { flex:1; min-width:180px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:8px; padding:.5rem .8rem; font-size:.9rem; }

  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:.9rem; }
  .card { background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:1.4rem 1.1rem 1.1rem; text-align:center; cursor:pointer; }
  .card:hover { border-color:var(--accent); transform:translateY(-2px); transition:.15s; }
  .avatar { width:84px; height:84px; border-radius:50%; margin:0 auto .7rem; display:flex; align-items:center; justify-content:center; font-family:Archivo,sans-serif; font-weight:700; font-size:1.6rem; color:#fff; object-fit:cover; border:3px solid var(--line); }
  img.avatar { display:block; }
  .card h3 { font-family:Archivo,sans-serif; font-size:1.02rem; line-height:1.25; }
  .cargo { font-size:.78rem; color:var(--muted); margin:.15rem 0 .5rem; min-height:1.2em; }
  .badge { display:inline-block; font-family:Archivo,sans-serif; font-size:.66rem; font-weight:700; letter-spacing:.05em; text-transform:uppercase; border-radius:999px; padding:.18rem .6rem; }
  .mini { display:flex; justify-content:center; gap:1rem; margin-top:.6rem; font-size:.72rem; color:var(--muted); font-variant-numeric:tabular-nums; }
  .mini b { color:var(--ink); }
  .card p { font-size:.82rem; color:var(--muted); margin-top:.55rem; text-align:left; }

  dialog { border:1px solid var(--line); border-radius:14px; background:var(--surface); color:var(--ink); max-width:780px; width:calc(100vw - 2rem); max-height:88vh; padding:0; }
  dialog::backdrop { background:rgba(10,8,14,.55); }
  .dlg-inner { padding:1.6rem 1.7rem; overflow-y:auto; max-height:88vh; }
  .cerrar { float:right; background:none; border:none; color:var(--muted); font-size:1.4rem; cursor:pointer; }
  .perfil-head { display:flex; gap:1.1rem; align-items:center; margin-bottom:.9rem; }
  .perfil-head .avatar { margin:0; width:96px; height:96px; }
  .perfil-head h2 { margin:0 0 .15rem; font-size:1.35rem; }
  .sec { margin-top:1.15rem; }
  .sec h4 { font-family:Archivo,sans-serif; font-size:.74rem; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); margin-bottom:.5rem; }
  .rasgos { display:flex; flex-wrap:wrap; gap:.4rem; }
  .rasgo { background:var(--chip); border-radius:999px; padding:.22rem .75rem; font-size:.8rem; }
  .item { border-left:2px solid var(--line); padding:.2rem 0 .2rem .9rem; margin-bottom:.9rem; }
  .item .t { font-weight:600; font-size:.92rem; }
  blockquote { font-size:.84rem; color:var(--muted); font-style:italic; margin:.3rem 0; }
  .fuente { font-size:.76rem; }
  footer { border-top:1px solid var(--line); margin-top:3rem; padding-top:1.2rem; font-size:.8rem; color:var(--muted); }
  footer details { margin-top:.5rem; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="eyebrow">Análisis de discurso · Costa Rica · mayo–agosto 2026</p>
    <h1>Hemeroteca Presidencial</h1>
    <p class="lede">¿A qué le presta más atención la presidenta Laura Fernández, y cómo trata a las personas de su discurso — a quiénes culpa, quiénes son sus aliados y sus adversarios? Análisis de __VIDEOS__ videos públicos (conferencias, cadenas nacionales y entrevistas), con cada afirmación enlazada al momento exacto de la fuente.</p>
    <div class="aviso"><strong>Nota metodológica.</strong> Esta página describe el <strong>trato que la presidenta da en su discurso público</strong> a temas, personas e instituciones — no afirma hechos sobre ellas. Los perfiles se generan de forma automatizada (transcripción por voz → grafo de conocimiento → análisis con modelo de lenguaje) y pueden contener errores de transcripción o interpretación: cada cita enlaza a su video original con marca de tiempo para verificación.</div>
    <div class="stats" id="stats"></div>
  </header>

  <h2>¿A qué le presta más atención?</h2>
  <p class="sub">Temas del discurso ordenados por menciones en el corpus; el color indica la postura con que la presidenta los aborda. Toca un tema para ver cómo lo enmarca.</p>
  <div class="temas" id="temas"></div>
  <div class="leyenda">
    <span><i style="background:var(--c-aliado)"></i>positiva (logro/promesa)</span>
    <span><i style="background:var(--c-adversario)"></i>negativa (problema/amenaza)</span>
    <span><i style="background:var(--c-mixto)"></i>mixta</span>
    <span><i style="background:var(--c-neutral)"></i>neutral</span>
  </div>

  <h2>Los actores de su discurso</h2>
  <p class="sub">Perfiles por persona o colectivo. Toca la foto para ver los rasgos que la presidenta le adjudica, de qué le culpa y sus declaraciones con fuente.</p>
  <div class="controls" id="filtros"></div>
  <div class="grid" id="grid"></div>

  <dialog id="dlg"><div class="dlg-inner" id="dlg-inner"></div></dialog>

  <footer>
    Generado el __FECHA__ a partir del corpus público del canal de la Presidencia de la República de Costa Rica y entrevistas en medios ·
    Pipeline de código abierto: <a href="https://github.com/Lapeyus/presidencial">Lapeyus/presidencial</a> · Motor de perfiles: __MOTOR__
    <details><summary>Créditos de fotografías (Wikipedia / Wikimedia Commons)</summary><div id="creditos"></div></details>
  </footer>
</div>
<script>
const DATA = __DATA__;
const CREDITOS = __CREDITOS__;
const ETIQ = {aliado:"Aliado", adversario:"Adversario", culpado:"Culpado", funcionario:"Funcionario", neutral:"Neutral", mixto:"Mixto"};
const ORDEN = ["culpado","adversario","mixto","aliado","funcionario","neutral"];
const POSTURA_COLOR = {positiva:"aliado", negativa:"adversario", mixta:"mixto", neutral:"neutral"};
let filtro = "todos", q = "";

function esc(s){ return String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function badge(c){ return `<span class="badge" style="color:var(--c-${c});background:var(--b-${c})">${ETIQ[c]||c}</span>`; }
function iniciales(nombre){
  const p = nombre.split(/\\s+/).filter(w=>w[0]===w[0].toUpperCase()||w.length>3);
  return ((p[0]||nombre)[0]+((p[1]||"")[0]||"")).toUpperCase();
}
function avatar(p, extra=""){
  if (p.foto) return `<img class="avatar" src="${esc(p.foto)}" alt="${esc(p.nombre)}" ${extra}>`;
  return `<div class="avatar" style="background:var(--c-${p.clasificacion})" ${extra}>${esc(iniciales(p.nombre))}</div>`;
}
function fuente(x){
  return x.url ? `<div class="fuente"><a href="${esc(x.url)}" target="_blank" rel="noopener">▶ ${esc((x.video||"").slice(0,70))} · ${esc(x.fecha)} · ${esc(x.timestamp)}</a></div>` : "";
}
function sec(titulo, arr, campo){
  return arr && arr.length ? `<div class="sec"><h4>${titulo}</h4>` +
    arr.map(x=>`<div class="item"><div class="t">${esc(x[campo])}</div>${x.cita?`<blockquote>«${esc(x.cita.slice(0,320))}»</blockquote>`:""}${fuente(x)}</div>`).join("") + `</div>` : "";
}

function stats(){
  const n = {}; DATA.perfiles.forEach(p=>n[p.clasificacion]=(n[p.clasificacion]||0)+1);
  const afirm = DATA.perfiles.reduce((a,p)=>a+p.declaraciones.length+p.culpas.length,0) +
                (DATA.temas||[]).reduce((a,t)=>a+t.declaraciones.length,0);
  document.getElementById("stats").innerHTML =
    `<div class="stat"><b>${DATA.perfiles.length}</b><span>perfiles</span></div>` +
    `<div class="stat"><b>${(DATA.temas||[]).length}</b><span>temas</span></div>` +
    ORDEN.filter(c=>n[c]).map(c=>`<div class="stat"><b style="color:var(--c-${c})">${n[c]}</b><span>${ETIQ[c].toLowerCase()}s</span></div>`).join("") +
    `<div class="stat"><b>${afirm}</b><span>afirmaciones con fuente</span></div>`;
}

function temas(){
  const lista = (DATA.temas||[]).slice().sort((a,b)=>b.menciones-a.menciones);
  if(!lista.length){ document.getElementById("temas").innerHTML = '<p class="sub">(aún sin temas analizados)</p>'; return; }
  const max = lista[0].menciones || 1;
  document.getElementById("temas").innerHTML = lista.map((t,i)=>`
    <div class="tema" data-t="${esc(t.nombre)}">
      <span class="n">${esc(t.nombre)}</span>
      <span class="barra"><i style="width:${Math.max(6, Math.round(100*t.menciones/max))}%;background:var(--c-${POSTURA_COLOR[t.postura]||"neutral"})"></i></span>
      <span class="m">${t.menciones} menciones</span>
    </div>`).join("");
  document.querySelectorAll(".tema").forEach(el=>el.onclick=()=>abrirTema(el.dataset.t));
}
function abrirTema(nombre){
  const t = (DATA.temas||[]).find(x=>x.nombre===nombre); if(!t) return;
  document.getElementById("dlg-inner").innerHTML = `
    <button class="cerrar" onclick="document.getElementById('dlg').close()">✕</button>
    ${badge(POSTURA_COLOR[t.postura]||"neutral")}
    <h2 style="font-family:Archivo">${esc(t.nombre)}</h2>
    <div class="cargo">${t.menciones} menciones en el corpus · postura ${esc(t.postura)}</div>
    <p>${esc(t.resumen)}</p>
    ${t.enfoques && t.enfoques.length?`<div class="sec"><h4>Enfoques</h4><div class="rasgos">${t.enfoques.map(c=>`<span class="rasgo">${esc(c)}</span>`).join("")}</div></div>`:""}
    ${sec("Declaraciones clave", t.declaraciones, "sintesis")}`;
  document.getElementById("dlg").showModal();
}

function filtros(){
  const el = document.getElementById("filtros");
  el.innerHTML = ["todos",...ORDEN].map(c=>`<button class="fchip${filtro===c?" on":""}" data-c="${c}">${c==="todos"?"Todos":ETIQ[c]}</button>`).join("") +
    `<input id="buscar" placeholder="Buscar persona…" value="${esc(q)}">`;
  el.querySelectorAll(".fchip").forEach(b=>b.onclick=()=>{filtro=b.dataset.c; filtros(); grid();});
  const inp = el.querySelector("#buscar");
  inp.oninput = ()=>{q=inp.value; grid(); };
}
function grid(){
  const norm = s=>String(s??"").toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g,"");
  const items = DATA.perfiles.filter(p =>
    (filtro==="todos"||p.clasificacion===filtro) &&
    (!q || norm(p.nombre+" "+p.cargo+" "+(p.alias||[]).join(" ")).includes(norm(q))));
  document.getElementById("grid").innerHTML = items.map(p=>`
    <div class="card" data-n="${esc(p.nombre)}">
      ${avatar(p)}
      <h3>${esc(p.nombre)}</h3>
      <div class="cargo">${esc(p.cargo)||"&nbsp;"}</div>
      ${badge(p.clasificacion)}
      <div class="mini"><span><b>${p.declaraciones.length}</b> declaraciones</span><span><b>${p.culpas.length}</b> culpas</span></div>
      <p>${esc(p.resumen.slice(0,150))}${p.resumen.length>150?"…":""}</p>
    </div>`).join("") || '<p style="color:var(--muted)">Sin resultados.</p>';
  document.querySelectorAll(".card").forEach(c=>c.onclick=()=>abrir(c.dataset.n));
}
function abrir(nombre){
  const p = DATA.perfiles.find(x=>x.nombre===nombre); if(!p) return;
  document.getElementById("dlg-inner").innerHTML = `
    <button class="cerrar" onclick="document.getElementById('dlg').close()">✕</button>
    <div class="perfil-head">
      ${avatar(p)}
      <div>
        <h2>${esc(p.nombre)}</h2>
        <div class="cargo">${esc(p.cargo)}</div>
        ${badge(p.clasificacion)}
      </div>
    </div>
    ${p.caracteristicas.length?`<div class="sec"><h4>Rasgos que la presidenta le adjudica</h4><div class="rasgos">${p.caracteristicas.map(c=>`<span class="rasgo">${esc(c)}</span>`).join("")}</div></div>`:""}
    <p style="margin-top:1rem">${esc(p.resumen)}</p>
    ${sec("De qué le culpa", p.culpas, "tema")}
    ${sec("Declaraciones clave", p.declaraciones, "sintesis")}
    <div class="sec" style="color:var(--muted);font-size:.78rem">Basado en ${p.menciones_analizadas} menciones del corpus.</div>`;
  document.getElementById("dlg").showModal();
}

function creditos(){
  const entradas = Object.values(CREDITOS||{});
  document.getElementById("creditos").innerHTML = entradas.length
    ? entradas.map(c=>`<div>${esc(c.persona)}: <a href="${esc(c.wikipedia)}" target="_blank" rel="noopener">Wikipedia</a> · ${esc(c.licencia||"")} ${c.autor?("· "+esc(c.autor)):""}</div>`).join("")
    : "<div>Sin fotos externas todavía.</div>";
}
document.getElementById("dlg").onclick = e=>{ if(e.target.id==="dlg") e.target.close(); };
stats(); temas(); filtros(); grid(); creditos();
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

    disponibles = {p.stem for p in FOTOS.glob("*.jpg")} if FOTOS.exists() else set()
    for perfil in data["perfiles"]:
        s = slug(perfil["nombre"])
        perfil["foto"] = f"fotos/{s}.jpg" if s in disponibles else ""

    creditos_path = FOTOS / "creditos.json"
    creditos = json.loads(creditos_path.read_text(encoding="utf-8")) if creditos_path.exists() else {}

    videos = len({d.get("video") for p in data["perfiles"] for d in p.get("declaraciones", []) + p.get("culpas", []) if d.get("video")})
    html = (
        TEMPLATE
        .replace("__DATA__", json.dumps(data, ensure_ascii=False))
        .replace("__CREDITOS__", json.dumps(creditos, ensure_ascii=False))
        .replace("__FECHA__", datetime.now().strftime("%Y-%m-%d"))
        .replace("__MOTOR__", str(data.get("motor") or ""))
        .replace("__VIDEOS__", str(max(videos, 1)))
    )
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"docs/index.html: {len(data['perfiles'])} perfiles · {len(data.get('temas', []))} temas · {len(disponibles)} fotos · {len(html):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
