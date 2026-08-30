#!/usr/bin/env python3
"""Genera la Hemeroteca Presidencial en ``docs/`` (GitHub Pages) — diseño v3, orientado a datos.

Lee ``data/perfiles_discurso.json`` (perfiles y temas exhaustivos, con
``referencias`` completas y ``tono`` por declaración de
``scripts/augment_references.py``) y produce ``docs/index.html``:

- cifras del corpus y sección de temas con barras de atención;
- **tabla maestra ordenable** de actores (trato, declaraciones, culpas,
  menciones y barra de balance positivo/neutro/negativo);
- ficha por actor con **tabla comparativa** del trato (columna positiva vs
  negativa — clave en los perfiles mixtos), declaraciones neutras, y la
  **tabla de referencias completas** cuyo conteo coincide exactamente con el
  pie «basado en N menciones»;
- avatares (foto de ``docs/fotos/<slug>.jpg`` con créditos, o iniciales).

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
<meta name="description" content="Hemeroteca Presidencial: análisis de datos del discurso público de la presidenta de Costa Rica — atención por temas, trato por actor y todas las referencias verificables.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&display=swap">
<style>
  :root {
    --bg:#F6F8FA; --surface:#FFFFFF; --ink:#0F172A; --muted:#5B6472;
    --line:#DEE3EA; --accent:#0F766E; --chip:#EEF2F5;
    --c-aliado:#047857; --c-adversario:#BE123C; --c-culpado:#B45309;
    --c-funcionario:#1D4ED8; --c-neutral:#64748B; --c-mixto:#7C3AED;
    --b-aliado:#04785714; --b-adversario:#BE123C14; --b-culpado:#B4530914;
    --b-funcionario:#1D4ED814; --b-neutral:#64748B14; --b-mixto:#7C3AED14;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0D1117; --surface:#161B22; --ink:#E6EDF3; --muted:#8B949E;
      --line:#30363D; --accent:#2DD4BF; --chip:#21262D;
      --c-aliado:#34D399; --c-adversario:#FB7185; --c-culpado:#FBBF24;
      --c-funcionario:#60A5FA; --c-neutral:#94A3B8; --c-mixto:#C4B5FD;
      --b-aliado:#34D39922; --b-adversario:#FB718522; --b-culpado:#FBBF2422;
      --b-funcionario:#60A5FA22; --b-neutral:#94A3B822; --b-mixto:#C4B5FD22;
    }
  }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.55; font-variant-numeric:tabular-nums; }
  a { color:var(--accent); }
  .wrap { max-width:1140px; margin:0 auto; padding:0 1.2rem 4rem; }
  header { padding:2.6rem 0 1.2rem; }
  .eyebrow { font-family:Archivo,sans-serif; font-size:.7rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--accent); margin-bottom:.6rem; }
  h1 { font-family:Archivo,sans-serif; font-weight:800; font-size:clamp(1.8rem,4.5vw,2.6rem); line-height:1.08; letter-spacing:-.02em; margin-bottom:.6rem; }
  h2 { font-family:Archivo,sans-serif; font-weight:700; font-size:1.25rem; margin:2.6rem 0 .3rem; }
  .sub { color:var(--muted); font-size:.9rem; margin-bottom:1rem; max-width:78ch; }
  .lede { font-size:1.02rem; max-width:72ch; margin-bottom:1rem; color:var(--muted); }
  .lede strong { color:var(--ink); }
  .aviso { border:1px solid var(--line); border-left:3px solid var(--accent); border-radius:6px; padding:.7rem 1rem; font-size:.8rem; color:var(--muted); max-width:80ch; background:var(--surface); }

  .cifras { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:1px; background:var(--line); border:1px solid var(--line); border-radius:8px; overflow:hidden; margin:1.4rem 0 0; }
  .cifra { background:var(--surface); padding:.75rem .9rem; }
  .cifra b { font-family:Archivo,sans-serif; font-size:1.45rem; font-weight:800; display:block; letter-spacing:-.02em; }
  .cifra span { font-size:.7rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }

  .temas { border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  .tema { display:grid; grid-template-columns:minmax(120px,190px) 1fr 76px 62px 92px; gap:.8rem; align-items:center; padding:.5rem .9rem; background:var(--surface); border-bottom:1px solid var(--line); cursor:pointer; font-size:.85rem; }
  .tema:last-child { border-bottom:none; }
  .tema:hover { background:var(--chip); }
  .tema .n { font-family:Archivo,sans-serif; font-weight:600; }
  .thead { font-family:Archivo,sans-serif; font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); background:var(--bg) !important; cursor:default !important; }
  .barra { height:8px; border-radius:99px; background:var(--chip); overflow:hidden; }
  .barra i { display:block; height:100%; }
  .num { text-align:right; color:var(--muted); font-size:.8rem; }
  .pill { display:inline-block; font-family:Archivo,sans-serif; font-size:.64rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; border-radius:4px; padding:.14rem .45rem; }
  .leyenda { display:flex; flex-wrap:wrap; gap:1rem; margin:.6rem 0 0; font-size:.74rem; color:var(--muted); }
  .leyenda i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:.3rem; }

  .controls { display:flex; flex-wrap:wrap; gap:.45rem; align-items:center; margin:.8rem 0 .9rem; }
  .fchip { font-family:Archivo,sans-serif; font-size:.74rem; font-weight:600; border:1px solid var(--line); background:var(--surface); color:var(--muted); border-radius:6px; padding:.3rem .7rem; cursor:pointer; }
  .fchip.on { border-color:var(--accent); color:var(--accent); background:var(--chip); }
  #buscar { flex:1; min-width:170px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:6px; padding:.42rem .7rem; font-size:.85rem; }

  .tscroll { overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:var(--surface); }
  table.master { border-collapse:collapse; width:100%; min-width:760px; font-size:.85rem; }
  table.master th { font-family:Archivo,sans-serif; font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); text-align:left; padding:.55rem .8rem; border-bottom:1px solid var(--ink); cursor:pointer; user-select:none; white-space:nowrap; background:var(--bg); }
  table.master th.num, table.master td.num { text-align:right; }
  table.master td { padding:.5rem .8rem; border-bottom:1px solid var(--line); vertical-align:middle; }
  table.master tr:hover td { background:var(--chip); cursor:pointer; }
  .actor { display:flex; align-items:center; gap:.6rem; min-width:200px; }
  .mini-av { width:30px; height:30px; border-radius:50%; object-fit:cover; display:flex; align-items:center; justify-content:center; font-family:Archivo,sans-serif; font-weight:700; font-size:.7rem; color:#fff; flex:none; }
  .actor b { font-family:Archivo,sans-serif; font-weight:600; display:block; line-height:1.15; }
  .actor small { color:var(--muted); font-size:.72rem; }
  .balance { display:flex; height:8px; border-radius:99px; overflow:hidden; background:var(--chip); min-width:110px; }
  .balance i { display:block; height:100%; }

  dialog { border:1px solid var(--line); border-radius:10px; background:var(--surface); color:var(--ink); max-width:900px; width:calc(100vw - 2rem); max-height:90vh; padding:0; }
  dialog::backdrop { background:rgba(4,10,16,.6); }
  .dlg-inner { padding:1.5rem 1.6rem; overflow-y:auto; max-height:90vh; }
  .cerrar { float:right; background:none; border:none; color:var(--muted); font-size:1.35rem; cursor:pointer; }
  .phead { display:flex; gap:1rem; align-items:center; margin-bottom:.8rem; }
  .phead .mini-av { width:72px; height:72px; font-size:1.35rem; }
  .phead h2 { margin:0 0 .1rem; font-size:1.3rem; }
  .phead .cargo { color:var(--muted); font-size:.82rem; }
  .kpis { display:flex; flex-wrap:wrap; gap:1px; background:var(--line); border:1px solid var(--line); border-radius:6px; overflow:hidden; margin:.8rem 0; }
  .kpi { background:var(--surface); padding:.45rem .8rem; flex:1; min-width:96px; }
  .kpi b { font-family:Archivo,sans-serif; font-size:1.05rem; display:block; }
  .kpi span { font-size:.66rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .sec { margin-top:1.1rem; }
  .sec h4 { font-family:Archivo,sans-serif; font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); margin-bottom:.45rem; }
  .rasgos { display:flex; flex-wrap:wrap; gap:.35rem; }
  .rasgo { background:var(--chip); border-radius:4px; padding:.18rem .6rem; font-size:.76rem; }
  .compara { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:var(--line); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  @media (max-width:700px){ .compara { grid-template-columns:1fr; } }
  .lado { background:var(--surface); padding:.7rem .9rem; }
  .lado h5 { font-family:Archivo,sans-serif; font-size:.74rem; text-transform:uppercase; letter-spacing:.05em; margin-bottom:.5rem; }
  .fila { border-left:2px solid var(--line); padding:.15rem 0 .15rem .7rem; margin-bottom:.6rem; font-size:.82rem; }
  .fila a { font-size:.72rem; white-space:nowrap; }
  details.refs { margin-top:1.1rem; border:1px solid var(--line); border-radius:8px; background:var(--surface); }
  details.refs summary { font-family:Archivo,sans-serif; font-weight:600; font-size:.85rem; padding:.6rem .9rem; cursor:pointer; }
  .refscroll { overflow-x:auto; max-height:420px; overflow-y:auto; }
  table.refs { border-collapse:collapse; width:100%; font-size:.76rem; min-width:640px; }
  table.refs th { position:sticky; top:0; background:var(--bg); font-family:Archivo,sans-serif; font-size:.64rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); text-align:left; padding:.4rem .7rem; border-bottom:1px solid var(--line); }
  table.refs td { padding:.35rem .7rem; border-bottom:1px solid var(--line); vertical-align:top; }
  table.refs td.cita { color:var(--muted); font-style:italic; min-width:280px; }
  blockquote { font-size:.78rem; color:var(--muted); font-style:italic; margin:.2rem 0; }
  footer { border-top:1px solid var(--line); margin-top:3rem; padding-top:1.1rem; font-size:.78rem; color:var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="eyebrow">Análisis de discurso · Costa Rica · mayo–agosto 2026</p>
    <h1>Hemeroteca Presidencial</h1>
    <p class="lede">Los datos del discurso público de la presidenta <strong>Laura Fernández</strong>: a qué le presta atención, a quiénes culpa y cómo trata a cada actor — <strong>__AFIRM__ afirmaciones extraídas</strong> de __VIDEOS__ videos, cada una verificable en su fuente con marca de tiempo.</p>
    <div class="aviso"><strong>Nota metodológica.</strong> Esta página describe el trato que la presidenta da <em>en su discurso público</em> a temas, personas e instituciones — no afirma hechos sobre ellas. Extracción automatizada (voz → texto → grafo → modelo de lenguaje); puede contener errores: toda cita enlaza a su video original para verificación.</div>
    <div class="cifras" id="cifras"></div>
  </header>

  <h2>Atención por tema</h2>
  <p class="sub">Menciones de cada tema en el corpus; el color de la barra es la postura dominante. Clic en la fila para el detalle con todas sus referencias.</p>
  <div class="temas" id="temas"></div>
  <div class="leyenda">
    <span><i style="background:var(--c-aliado)"></i>positiva</span>
    <span><i style="background:var(--c-adversario)"></i>negativa</span>
    <span><i style="background:var(--c-mixto)"></i>mixta</span>
    <span><i style="background:var(--c-neutral)"></i>neutral</span>
  </div>

  <div id="estudios-sec" style="display:none">
    <h2>Posiciones en tensión</h2>
    <p class="sub">Contradicciones y tensiones detectadas entre declaraciones de la presidenta sobre un mismo tema — cambios de postura, promesas vs acciones, cifras inconsistentes. Cada hallazgo confronta las dos declaraciones con fecha y fuente. Detección automatizada con criterio exigente; verifica siempre en las fuentes.</p>
    <div id="estudios"></div>
  </div>

  <h2>Trato por actor</h2>
  <p class="sub">Ordena por cualquier columna. El balance muestra la proporción de declaraciones positivas / neutras / negativas hacia cada actor (las culpas cuentan como negativas). Clic en la fila para la ficha completa — en los perfiles mixtos, con tabla comparativa del trato.</p>
  <div class="controls" id="filtros"></div>
  <div class="tscroll"><table class="master"><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>

  <dialog id="dlg"><div class="dlg-inner" id="dlg-inner"></div></dialog>

  <footer>
    Generado el __FECHA__ · Corpus: canal público de la Presidencia de la República de Costa Rica y entrevistas en medios · Motores: __MOTOR__ ·
    Pipeline de código abierto: <a href="https://github.com/Lapeyus/presidencial">Lapeyus/presidencial</a>
    <details><summary>Créditos de fotografías (Wikipedia / Wikimedia Commons)</summary><div id="creditos"></div></details>
  </footer>
</div>
<script>
const DATA = __DATA__;
const CREDITOS = __CREDITOS__;
const ETIQ = {aliado:"Aliado", adversario:"Adversario", culpado:"Culpado", funcionario:"Funcionario", neutral:"Neutral", mixto:"Mixto"};
const ORDEN = ["culpado","adversario","mixto","aliado","funcionario","neutral"];
const PCOLOR = {positiva:"aliado", negativa:"adversario", mixta:"mixto", neutral:"neutral"};
let filtro = "todos", q = "", sortCol = "declaraciones", sortDir = -1;

function esc(s){ return String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function pill(c, txt){ return `<span class="pill" style="color:var(--c-${c});background:var(--b-${c})">${esc(txt||ETIQ[c]||c)}</span>`; }
function iniciales(n){ const p = n.split(/\\s+/); return ((p[0]||n)[0]+((p[1]||"")[0]||"")).toUpperCase(); }
function avatar(p, cls="mini-av"){
  if (p.foto) return `<img class="${cls}" src="${esc(p.foto)}" alt="">`;
  return `<div class="${cls}" style="background:var(--c-${p.clasificacion})">${esc(iniciales(p.nombre))}</div>`;
}
function tonos(p){
  const t = {positivo:0, neutro:0, negativo:p.culpas.length};
  p.declaraciones.forEach(d=>{ t[d.tono==="positivo"?"positivo":d.tono==="negativo"?"negativo":"neutro"]++; });
  return t;
}
function balanceBar(t){
  const total = t.positivo+t.neutro+t.negativo || 1;
  return `<div class="balance" title="${t.positivo} positivas · ${t.neutro} neutras · ${t.negativo} negativas">
    <i style="width:${100*t.positivo/total}%;background:var(--c-aliado)"></i>
    <i style="width:${100*t.neutro/total}%;background:var(--c-neutral)"></i>
    <i style="width:${100*t.negativo/total}%;background:var(--c-adversario)"></i></div>`;
}
function fuenteLink(x){ return x.url ? ` <a href="${esc(x.url)}" target="_blank" rel="noopener" title="${esc(x.video)} · ${esc(x.timestamp)}">▶ ${esc(x.fecha)}</a>` : ""; }

function cifras(){
  const totalRefs = DATA.perfiles.reduce((a,p)=>a+(p.referencias||[]).length,0) + (DATA.temas||[]).reduce((a,t)=>a+(t.referencias||[]).length,0);
  const decl = DATA.perfiles.reduce((a,p)=>a+p.declaraciones.length,0);
  const culpas = DATA.perfiles.reduce((a,p)=>a+p.culpas.length,0);
  const declT = (DATA.temas||[]).reduce((a,t)=>a+t.declaraciones.length,0);
  const tensiones = ((DATA.estudios||{}).contradicciones||[]).reduce((a,r)=>a+r.hallazgos.length,0);
  const filas = [
    [DATA.perfiles.length, "actores"], [(DATA.temas||[]).length, "temas"],
    [decl+declT, "declaraciones"], [culpas, "culpas"], [totalRefs, "referencias"],
  ];
  if (tensiones) filas.push([tensiones, "tensiones"]);
  document.getElementById("cifras").innerHTML =
    filas.map(([v,k])=>`<div class="cifra"><b>${v.toLocaleString("es")}</b><span>${k}</span></div>`).join("");
}

function temas(){
  const lista = (DATA.temas||[]).slice().sort((a,b)=>b.menciones-a.menciones);
  const max = (lista[0]||{}).menciones || 1;
  document.getElementById("temas").innerHTML =
    `<div class="tema thead"><span>Tema</span><span>Atención (menciones)</span><span class="num">Menciones</span><span class="num">Decl.</span><span>Postura</span></div>` +
    lista.map(t=>`
    <div class="tema" data-t="${esc(t.nombre)}">
      <span class="n">${esc(t.nombre)}</span>
      <span class="barra"><i style="width:${Math.max(4, Math.round(100*t.menciones/max))}%;background:var(--c-${PCOLOR[t.postura]||"neutral"})"></i></span>
      <span class="num">${t.menciones.toLocaleString("es")}</span>
      <span class="num">${t.declaraciones.length}</span>
      <span>${pill(PCOLOR[t.postura]||"neutral", t.postura)}</span>
    </div>`).join("");
  document.querySelectorAll(".tema[data-t]").forEach(el=>el.onclick=()=>abrirTema(el.dataset.t));
}

function refsTable(refs){
  if (!refs || !refs.length) return "";
  return `<details class="refs"><summary>Referencias completas (${refs.length.toLocaleString("es")})</summary>
    <div class="refscroll"><table class="refs"><thead><tr><th>#</th><th>Fecha</th><th>Video</th><th>Momento</th><th>Cita</th></tr></thead><tbody>` +
    refs.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.fecha)}</td><td>${esc((r.video||"").slice(0,48))}</td>
      <td>${r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.timestamp)} ▶</a>`:esc(r.timestamp)}</td>
      <td class="cita">«${esc(r.cita)}»</td></tr>`).join("") +
    `</tbody></table></div></details>`;
}

function abrirTema(nombre){
  const t = (DATA.temas||[]).find(x=>x.nombre===nombre); if(!t) return;
  document.getElementById("dlg-inner").innerHTML = `
    <button class="cerrar" onclick="dlg.close()">✕</button>
    <h2 style="font-family:Archivo">${esc(t.nombre)} ${pill(PCOLOR[t.postura]||"neutral", t.postura)}</h2>
    <div class="kpis">
      <div class="kpi"><b>${t.menciones.toLocaleString("es")}</b><span>menciones</span></div>
      <div class="kpi"><b>${t.declaraciones.length}</b><span>declaraciones</span></div>
      <div class="kpi"><b>${t.enfoques.length}</b><span>enfoques</span></div>
    </div>
    <p style="font-size:.9rem">${esc(t.resumen)}</p>
    ${t.enfoques.length?`<div class="sec"><h4>Enfoques</h4><div class="rasgos">${t.enfoques.map(c=>`<span class="rasgo">${esc(c)}</span>`).join("")}</div></div>`:""}
    <div class="sec"><h4>Declaraciones (${t.declaraciones.length})</h4>${t.declaraciones.map(d=>`<div class="fila">${esc(d.sintesis)}${fuenteLink(d)}</div>`).join("")}</div>
    ${refsTable(t.referencias)}
    <p style="color:var(--muted);font-size:.74rem;margin-top:.8rem">Basado en ${(t.referencias||[]).length.toLocaleString("es")} menciones del corpus — todas listadas arriba.</p>`;
  dlg.showModal();
}

const COLS = [
  ["actor","Actor"], ["clasificacion","Trato"], ["declaraciones","Decl."],
  ["culpas","Culpas"], ["menciones","Menciones"], ["balance","Balance del trato"],
];
function valorDe(p, col){
  if (col==="actor") return p.nombre.toLowerCase();
  if (col==="clasificacion") return ORDEN.indexOf(p.clasificacion);
  if (col==="declaraciones") return p.declaraciones.length;
  if (col==="culpas") return p.culpas.length;
  if (col==="menciones") return (p.referencias||[]).length || p.menciones_analizadas;
  if (col==="balance"){ const t=tonos(p); return (t.positivo-t.negativo)/(t.positivo+t.neutro+t.negativo||1); }
  return 0;
}
function filtros(){
  const el = document.getElementById("filtros");
  el.innerHTML = ["todos",...ORDEN].map(c=>`<button class="fchip${filtro===c?" on":""}" data-c="${c}">${c==="todos"?"Todos":ETIQ[c]}</button>`).join("") +
    `<input id="buscar" placeholder="Buscar actor…" value="${esc(q)}">`;
  el.querySelectorAll(".fchip").forEach(b=>b.onclick=()=>{filtro=b.dataset.c; filtros(); tabla();});
  el.querySelector("#buscar").oninput = e=>{q=e.target.value; tabla();};
}
function tabla(){
  document.getElementById("thead").innerHTML = "<tr>" + COLS.map(([c,l])=>
    `<th class="${["declaraciones","culpas","menciones"].includes(c)?"num":""}" data-c="${c}">${l}${sortCol===c?(sortDir<0?" ↓":" ↑"):""}</th>`).join("") + "</tr>";
  document.querySelectorAll("#thead th").forEach(th=>th.onclick=()=>{
    const c = th.dataset.c;
    if (sortCol===c) sortDir=-sortDir; else { sortCol=c; sortDir=(c==="actor")?1:-1; }
    tabla();
  });
  const norm = s=>String(s??"").toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g,"");
  const items = DATA.perfiles
    .filter(p=>(filtro==="todos"||p.clasificacion===filtro) &&
      (!q || norm(p.nombre+" "+p.cargo+" "+(p.alias||[]).join(" ")).includes(norm(q))))
    .sort((a,b)=>{ const va=valorDe(a,sortCol), vb=valorDe(b,sortCol); return (va<vb?-1:va>vb?1:0)*sortDir; });
  document.getElementById("tbody").innerHTML = items.map(p=>{
    const t = tonos(p);
    return `<tr data-n="${esc(p.nombre)}">
      <td><div class="actor">${avatar(p)}<div><b>${esc(p.nombre)}</b><small>${esc(p.cargo)}</small></div></div></td>
      <td>${pill(p.clasificacion)}</td>
      <td class="num">${p.declaraciones.length}</td>
      <td class="num">${p.culpas.length}</td>
      <td class="num">${((p.referencias||[]).length || p.menciones_analizadas).toLocaleString("es")}</td>
      <td>${balanceBar(t)}</td></tr>`;
  }).join("") || `<tr><td colspan="6" style="color:var(--muted)">Sin resultados.</td></tr>`;
  document.querySelectorAll("#tbody tr[data-n]").forEach(tr=>tr.onclick=()=>abrir(tr.dataset.n));
}

function abrir(nombre){
  const p = DATA.perfiles.find(x=>x.nombre===nombre); if(!p) return;
  const t = tonos(p);
  const pos = p.declaraciones.filter(d=>d.tono==="positivo");
  const neg = p.declaraciones.filter(d=>d.tono==="negativo");
  const neu = p.declaraciones.filter(d=>!d.tono || d.tono==="neutro");
  const fila = d=>`<div class="fila">${esc(d.sintesis||d.tema)}${d.cita?`<blockquote>«${esc(d.cita.slice(0,260))}»</blockquote>`:""}${fuenteLink(d)}</div>`;
  const compara = (pos.length||neg.length||p.culpas.length) ? `
    <div class="sec"><h4>Balance del trato${p.clasificacion==="mixto"?" (perfil mixto: comparación)":""}</h4>
    <div class="compara">
      <div class="lado"><h5 style="color:var(--c-aliado)">Trato positivo (${pos.length})</h5>${pos.map(fila).join("")||'<p style="color:var(--muted);font-size:.8rem">—</p>'}</div>
      <div class="lado"><h5 style="color:var(--c-adversario)">Trato negativo (${neg.length+p.culpas.length})</h5>
        ${p.culpas.map(c=>`<div class="fila"><b>Culpa:</b> ${esc(c.tema)}${c.cita?`<blockquote>«${esc(c.cita.slice(0,260))}»</blockquote>`:""}${fuenteLink(c)}</div>`).join("")}
        ${neg.map(fila).join("")}
        ${(neg.length+p.culpas.length)?"":'<p style="color:var(--muted);font-size:.8rem">—</p>'}</div>
    </div></div>` : "";
  document.getElementById("dlg-inner").innerHTML = `
    <button class="cerrar" onclick="dlg.close()">✕</button>
    <div class="phead">${avatar(p)}<div><h2>${esc(p.nombre)}</h2><div class="cargo">${esc(p.cargo)}</div>${pill(p.clasificacion)}</div></div>
    <div class="kpis">
      <div class="kpi"><b>${((p.referencias||[]).length||p.menciones_analizadas).toLocaleString("es")}</b><span>menciones</span></div>
      <div class="kpi"><b>${p.declaraciones.length}</b><span>declaraciones</span></div>
      <div class="kpi"><b style="color:var(--c-aliado)">${t.positivo}</b><span>positivas</span></div>
      <div class="kpi"><b style="color:var(--c-adversario)">${t.negativo}</b><span>negativas</span></div>
      <div class="kpi">${balanceBar(t)}<span>balance</span></div>
    </div>
    <p style="font-size:.9rem">${esc(p.resumen)}</p>
    ${p.caracteristicas.length?`<div class="sec"><h4>Rasgos que la presidenta le adjudica</h4><div class="rasgos">${p.caracteristicas.map(c=>`<span class="rasgo">${esc(c)}</span>`).join("")}</div></div>`:""}
    ${compara}
    ${neu.length?`<div class="sec"><details><summary style="font-family:Archivo;font-weight:600;font-size:.85rem;cursor:pointer">Declaraciones neutras (${neu.length})</summary>${neu.map(fila).join("")}</details></div>`:""}
    ${refsTable(p.referencias)}
    <p style="color:var(--muted);font-size:.74rem;margin-top:.8rem">Basado en ${((p.referencias||[]).length||p.menciones_analizadas).toLocaleString("es")} menciones del corpus — todas listadas arriba.</p>`;
  dlg.showModal();
}

function creditos(){
  const entradas = Object.values(CREDITOS||{});
  document.getElementById("creditos").innerHTML = entradas.length
    ? entradas.map(c=>`<div>${esc(c.persona)}: <a href="${esc(c.wikipedia)}" target="_blank" rel="noopener">Wikipedia</a> · ${esc(c.licencia||"")} ${c.autor?("· "+esc(c.autor)):""}</div>`).join("")
    : "<div>Sin fotos externas todavía.</div>";
}
const TIPO_TXT = {cambio_de_postura:"Cambio de postura", promesa_vs_accion:"Promesa vs acción",
  cifras_inconsistentes:"Cifras inconsistentes", tension_retorica:"Tensión retórica"};
const SEV_COLOR = {alta:"adversario", media:"culpado", baja:"neutral"};
function lado(x, letra){
  return `<div class="lado"><h5>${letra} · ${esc(x.fecha)}</h5>
    <div class="fila">${esc(x.sintesis)}${x.cita?`<blockquote>«${esc(x.cita.slice(0,260))}»</blockquote>`:""}${fuenteLink(x)}</div></div>`;
}
function estudios(){
  const contra = ((DATA.estudios||{}).contradicciones||[]).filter(r=>r.hallazgos.length);
  if(!contra.length) return;
  document.getElementById("estudios-sec").style.display = "";
  document.getElementById("estudios").innerHTML = contra.map(r=>
    r.hallazgos.map(h=>`
    <details class="refs" style="margin-bottom:.6rem">
      <summary>${pill("mixto", r.tema)} ${pill(SEV_COLOR[h.severidad]||"neutral", h.severidad)} <b style="font-family:Archivo">${TIPO_TXT[h.tipo]||h.tipo}</b> — <span style="font-weight:400">${esc(h.descripcion)}</span></summary>
      <div class="compara" style="border:none;border-top:1px solid var(--line);border-radius:0">${lado(h.a,"A")}${lado(h.b,"B")}</div>
    </details>`).join("")
  ).join("");
}
const dlg = document.getElementById("dlg");
dlg.onclick = e=>{ if(e.target===dlg) dlg.close(); };
cifras(); temas(); estudios(); filtros(); tabla(); creditos();
</script>
</body>
</html>
"""


def main() -> int:
    if not PERFILES.exists():
        raise SystemExit("No existe data/perfiles_discurso.json")
    data = json.loads(PERFILES.read_text(encoding="utf-8"))
    orden = {"culpado": 0, "adversario": 1, "mixto": 2, "aliado": 3, "funcionario": 4, "neutral": 5}
    data["perfiles"].sort(key=lambda p: (orden.get(p.get("clasificacion"), 9), -len(p.get("declaraciones", []))))

    disponibles = {p.stem for p in FOTOS.glob("*.jpg")} if FOTOS.exists() else set()
    for perfil in data["perfiles"]:
        s = slug(perfil["nombre"])
        perfil["foto"] = f"fotos/{s}.jpg" if s in disponibles else ""

    creditos_path = FOTOS / "creditos.json"
    creditos = json.loads(creditos_path.read_text(encoding="utf-8")) if creditos_path.exists() else {}

    afirm = sum(len(p.get("declaraciones", [])) + len(p.get("culpas", [])) for p in data["perfiles"])
    afirm += sum(len(t.get("declaraciones", [])) for t in data.get("temas", []))
    videos = len({d.get("video") for p in data["perfiles"] for d in p.get("declaraciones", []) + p.get("culpas", []) if d.get("video")})

    html = (
        TEMPLATE
        .replace("__DATA__", json.dumps(data, ensure_ascii=False))
        .replace("__CREDITOS__", json.dumps(creditos, ensure_ascii=False))
        .replace("__FECHA__", datetime.now().strftime("%Y-%m-%d"))
        .replace("__MOTOR__", str(data.get("motor") or ""))
        .replace("__AFIRM__", f"{afirm:,}".replace(",", " "))
        .replace("__VIDEOS__", str(max(videos, 1)))
    )
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    refs = sum(len(p.get("referencias", [])) for p in data["perfiles"]) + sum(len(t.get("referencias", [])) for t in data.get("temas", []))
    print(f"docs/index.html: {len(data['perfiles'])} perfiles · {len(data.get('temas', []))} temas · {refs:,} referencias · {len(disponibles)} fotos · {len(html):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
