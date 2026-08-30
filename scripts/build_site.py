#!/usr/bin/env python3
"""Genera la Hemeroteca Presidencial multipágina en ``docs/`` (GitHub Pages) — v4.

Páginas con navegación por pestañas:

- ``index.html`` — Resumen: cifras del corpus y adelantos de cada sección.
- ``temas.html`` — Atención por tema y Posiciones en tensión.
- ``personas.html`` — **directorio exhaustivo**: todas las personas que
  aparecen al menos una vez en el discurso (``data/personas_index.json``),
  con buscador, orden y ficha por persona; las fichas se sirven como JSON
  estático por persona (``docs/data/personas/<slug>.json``) y se cargan al
  abrir. Los 40 perfiles completos (``perfiles_discurso.json``) se marcan
  como destacados y conservan balance de tono, comparativa y culpas.

Uso:
    python scripts/build_site.py
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

PERFILES = Path("data/perfiles_discurso.json")
PERSONAS = Path("data/personas_index.json")
DOCS = Path("docs")
FOTOS = DOCS / "fotos"
DATA_DIR = DOCS / "data" / "personas"


def slug(value: str, limit: int = 60) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")[:limit] or "x"


CSS = """
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
  header { padding:2.2rem 0 0; }
  .eyebrow { font-family:Archivo,sans-serif; font-size:.68rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--accent); margin-bottom:.5rem; }
  h1 { font-family:Archivo,sans-serif; font-weight:800; font-size:clamp(1.6rem,4vw,2.3rem); line-height:1.08; letter-spacing:-.02em; margin-bottom:.5rem; }
  h1 a { color:inherit; text-decoration:none; }
  h2 { font-family:Archivo,sans-serif; font-weight:700; font-size:1.22rem; margin:2.2rem 0 .3rem; }
  h2 a.mas { font-size:.78rem; font-weight:600; margin-left:.6rem; }
  .sub { color:var(--muted); font-size:.88rem; margin-bottom:.9rem; max-width:78ch; }
  .lede { font-size:1rem; max-width:74ch; margin-bottom:.9rem; color:var(--muted); }
  .lede strong { color:var(--ink); }
  .aviso { border:1px solid var(--line); border-left:3px solid var(--accent); border-radius:6px; padding:.65rem 1rem; font-size:.78rem; color:var(--muted); max-width:82ch; background:var(--surface); }
  nav.tabs { display:flex; gap:.2rem; border-bottom:1px solid var(--line); margin:1.3rem 0 0; }
  nav.tabs a { font-family:Archivo,sans-serif; font-size:.86rem; font-weight:600; padding:.5rem .95rem; color:var(--muted); text-decoration:none; border-bottom:2px solid transparent; }
  nav.tabs a.on { color:var(--accent); border-color:var(--accent); }
  nav.tabs a:hover { color:var(--ink); }
  .cifras { display:grid; grid-template-columns:repeat(auto-fit,minmax(118px,1fr)); gap:1px; background:var(--line); border:1px solid var(--line); border-radius:8px; overflow:hidden; margin:1.3rem 0 0; }
  .cifra { background:var(--surface); padding:.7rem .85rem; }
  .cifra b { font-family:Archivo,sans-serif; font-size:1.4rem; font-weight:800; display:block; letter-spacing:-.02em; }
  .cifra span { font-size:.68rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .temas { border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  .tema { display:grid; grid-template-columns:minmax(120px,190px) 1fr 76px 62px 92px; gap:.8rem; align-items:center; padding:.5rem .9rem; background:var(--surface); border-bottom:1px solid var(--line); cursor:pointer; font-size:.85rem; }
  .tema:last-child { border-bottom:none; }
  .tema:hover { background:var(--chip); }
  .tema .n { font-family:Archivo,sans-serif; font-weight:600; }
  .thead { font-family:Archivo,sans-serif; font-size:.66rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); background:var(--bg) !important; cursor:default !important; }
  .barra { height:8px; border-radius:99px; background:var(--chip); overflow:hidden; }
  .barra i { display:block; height:100%; }
  .num { text-align:right; color:var(--muted); font-size:.8rem; }
  .pill { display:inline-block; font-family:Archivo,sans-serif; font-size:.62rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; border-radius:4px; padding:.13rem .42rem; }
  .leyenda { display:flex; flex-wrap:wrap; gap:1rem; margin:.55rem 0 0; font-size:.72rem; color:var(--muted); }
  .leyenda i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:.3rem; }
  .controls { display:flex; flex-wrap:wrap; gap:.45rem; align-items:center; margin:.8rem 0 .8rem; }
  .fchip { font-family:Archivo,sans-serif; font-size:.72rem; font-weight:600; border:1px solid var(--line); background:var(--surface); color:var(--muted); border-radius:6px; padding:.28rem .65rem; cursor:pointer; }
  .fchip.on { border-color:var(--accent); color:var(--accent); background:var(--chip); }
  #buscar { flex:1; min-width:170px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:6px; padding:.4rem .7rem; font-size:.85rem; }
  .tscroll { overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:var(--surface); }
  table.master { border-collapse:collapse; width:100%; min-width:680px; font-size:.84rem; }
  table.master th { font-family:Archivo,sans-serif; font-size:.66rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); text-align:left; padding:.5rem .8rem; border-bottom:1px solid var(--ink); cursor:pointer; user-select:none; white-space:nowrap; background:var(--bg); position:sticky; top:0; }
  table.master th.num, table.master td.num { text-align:right; }
  table.master td { padding:.42rem .8rem; border-bottom:1px solid var(--line); vertical-align:middle; }
  table.master tr:hover td { background:var(--chip); cursor:pointer; }
  .actor { display:flex; align-items:center; gap:.55rem; min-width:190px; }
  .mini-av { width:28px; height:28px; border-radius:50%; object-fit:cover; display:flex; align-items:center; justify-content:center; font-family:Archivo,sans-serif; font-weight:700; font-size:.64rem; color:#fff; flex:none; }
  .actor b { font-family:Archivo,sans-serif; font-weight:600; display:block; line-height:1.15; }
  .actor small { color:var(--muted); font-size:.7rem; display:block; max-width:420px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .balance { display:flex; height:8px; border-radius:99px; overflow:hidden; background:var(--chip); min-width:100px; }
  .balance i { display:block; height:100%; }
  dialog { border:1px solid var(--line); border-radius:10px; background:var(--surface); color:var(--ink); max-width:900px; width:calc(100vw - 2rem); max-height:90vh; padding:0; }
  dialog::backdrop { background:rgba(4,10,16,.6); }
  .dlg-inner { padding:1.5rem 1.6rem; overflow-y:auto; max-height:90vh; }
  .cerrar { float:right; background:none; border:none; color:var(--muted); font-size:1.35rem; cursor:pointer; }
  .phead { display:flex; gap:1rem; align-items:center; margin-bottom:.7rem; }
  .phead .mini-av { width:68px; height:68px; font-size:1.3rem; }
  .phead h2 { margin:0 0 .1rem; font-size:1.25rem; }
  .phead .cargo { color:var(--muted); font-size:.8rem; }
  .kpis { display:flex; flex-wrap:wrap; gap:1px; background:var(--line); border:1px solid var(--line); border-radius:6px; overflow:hidden; margin:.8rem 0; }
  .kpi { background:var(--surface); padding:.42rem .8rem; flex:1; min-width:92px; }
  .kpi b { font-family:Archivo,sans-serif; font-size:1rem; display:block; }
  .kpi span { font-size:.64rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .sec { margin-top:1.05rem; }
  .sec h4 { font-family:Archivo,sans-serif; font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); margin-bottom:.4rem; }
  .rasgos { display:flex; flex-wrap:wrap; gap:.35rem; }
  .rasgo { background:var(--chip); border-radius:4px; padding:.16rem .55rem; font-size:.75rem; }
  .compara { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:var(--line); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  @media (max-width:700px){ .compara { grid-template-columns:1fr; } }
  .lado { background:var(--surface); padding:.65rem .9rem; }
  .lado h5 { font-family:Archivo,sans-serif; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; margin-bottom:.45rem; }
  .fila { border-left:2px solid var(--line); padding:.12rem 0 .12rem .7rem; margin-bottom:.55rem; font-size:.8rem; }
  .fila a { font-size:.7rem; white-space:nowrap; }
  details.refs { margin-top:1rem; border:1px solid var(--line); border-radius:8px; background:var(--surface); }
  details.refs summary { font-family:Archivo,sans-serif; font-weight:600; font-size:.83rem; padding:.55rem .9rem; cursor:pointer; }
  .refscroll { overflow-x:auto; max-height:420px; overflow-y:auto; }
  table.refs { border-collapse:collapse; width:100%; font-size:.74rem; min-width:620px; }
  table.refs th { position:sticky; top:0; background:var(--bg); font-family:Archivo,sans-serif; font-size:.62rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); text-align:left; padding:.38rem .7rem; border-bottom:1px solid var(--line); }
  table.refs td { padding:.32rem .7rem; border-bottom:1px solid var(--line); vertical-align:top; }
  table.refs td.cita { color:var(--muted); font-style:italic; min-width:280px; }
  blockquote { font-size:.76rem; color:var(--muted); font-style:italic; margin:.18rem 0; }
  footer { border-top:1px solid var(--line); margin-top:3rem; padding-top:1rem; font-size:.76rem; color:var(--muted); }
"""

JS_COMUN = """
function esc(s){ return String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function pill(c, txt){ return `<span class="pill" style="color:var(--c-${c});background:var(--b-${c})">${esc(txt||c)}</span>`; }
function iniciales(n){ const p = n.split(/\\s+/); return ((p[0]||n)[0]+((p[1]||"")[0]||"")).toUpperCase(); }
function avatar(p, cls="mini-av"){
  if (p.foto) return `<img class="${cls}" src="${esc(p.foto)}" alt="">`;
  const c = p.clasificacion || "neutral";
  return `<div class="${cls}" style="background:var(--c-${c})">${esc(iniciales(p.nombre))}</div>`;
}
function fuenteLink(x){ return x.url ? ` <a href="${esc(x.url)}" target="_blank" rel="noopener" title="${esc(x.video||"")} · ${esc(x.timestamp||"")}">▶ ${esc(x.fecha||"fuente")}</a>` : ""; }
function refsTable(refs){
  if (!refs || !refs.length) return "";
  return `<details class="refs"><summary>Referencias completas (${refs.length.toLocaleString("es")})</summary>
    <div class="refscroll"><table class="refs"><thead><tr><th>#</th><th>Fecha</th><th>Video</th><th>Momento</th><th>Cita</th></tr></thead><tbody>` +
    refs.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.fecha)}</td><td>${esc((r.video||"").slice(0,48))}</td>
      <td>${r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.timestamp)} ▶</a>`:esc(r.timestamp)}</td>
      <td class="cita">«${esc(r.cita)}»</td></tr>`).join("") +
    `</tbody></table></div></details>`;
}
const ETIQ = {aliado:"Aliado", adversario:"Adversario", culpado:"Culpado", funcionario:"Funcionario", neutral:"Neutral", mixto:"Mixto"};
const PCOLOR = {positiva:"aliado", negativa:"adversario", mixta:"mixto", neutral:"neutral"};
"""


def pagina(titulo_pestana: str, activa: str, contenido: str, script: str) -> str:
    tabs = "".join(
        f'<a href="{href}" class="{"on" if activa == href else ""}">{label}</a>'
        for href, label in (("index.html", "Resumen"), ("temas.html", "Temas"), ("personas.html", "Personas"))
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo_pestana}</title>
<meta name="description" content="Hemeroteca Presidencial: análisis de datos del discurso público de la presidenta de Costa Rica, con toda afirmación verificable en su fuente.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&display=swap">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="eyebrow">Análisis de discurso · Costa Rica · mayo–agosto 2026</p>
    <h1><a href="index.html">Hemeroteca Presidencial</a></h1>
    <p class="lede">Los datos del discurso público de la presidenta <strong>Laura Fernández</strong> — cada afirmación verificable en su fuente con marca de tiempo.</p>
    <div class="aviso"><strong>Nota metodológica.</strong> Esta página describe el trato que la presidenta da <em>en su discurso público</em> a temas, personas e instituciones — no afirma hechos sobre ellas. Extracción automatizada; puede contener errores: toda cita enlaza a su video original.</div>
    <nav class="tabs">{tabs}</nav>
  </header>
  {contenido}
  <dialog id="dlg"><div class="dlg-inner" id="dlg-inner"></div></dialog>
  <footer>
    Generado el __FECHA__ · Corpus: canal público de la Presidencia de la República de Costa Rica y entrevistas en medios ·
    Pipeline de código abierto: <a href="https://github.com/Lapeyus/presidencial">Lapeyus/presidencial</a>
    <details><summary>Créditos de fotografías (Wikipedia / Wikimedia Commons)</summary><div id="creditos"></div></details>
  </footer>
</div>
<script>
{JS_COMUN}
const CREDITOS = __CREDITOS__;
document.getElementById("creditos").innerHTML = Object.values(CREDITOS||{{}}).map(c=>`<div>${{esc(c.persona)}}: <a href="${{esc(c.wikipedia)}}" target="_blank" rel="noopener">Wikipedia</a> · ${{esc(c.licencia||"")}}</div>`).join("") || "<div>Sin fotos externas.</div>";
const dlg = document.getElementById("dlg");
dlg.onclick = e=>{{ if(e.target===dlg) dlg.close(); }};
{script}
</script>
</body>
</html>
"""


JS_TEMAS = """
const TEMAS = __TEMAS__;
const ESTUDIOS = __ESTUDIOS__;
const TIPO_TXT = {cambio_de_postura:"Cambio de postura", promesa_vs_accion:"Promesa vs acción", cifras_inconsistentes:"Cifras inconsistentes", tension_retorica:"Tensión retórica"};
const SEV_COLOR = {alta:"adversario", media:"culpado", baja:"neutral"};
function temas(){
  const lista = TEMAS.slice().sort((a,b)=>b.menciones-a.menciones);
  const max = (lista[0]||{}).menciones || 1;
  document.getElementById("temas").innerHTML =
    `<div class="tema thead"><span>Tema</span><span>Atención</span><span class="num">Menciones</span><span class="num">Decl.</span><span>Postura</span></div>` +
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
function abrirTema(nombre){
  const t = TEMAS.find(x=>x.nombre===nombre); if(!t) return;
  document.getElementById("dlg-inner").innerHTML = `
    <button class="cerrar" onclick="dlg.close()">✕</button>
    <h2 style="font-family:Archivo">${esc(t.nombre)} ${pill(PCOLOR[t.postura]||"neutral", t.postura)}</h2>
    <div class="kpis">
      <div class="kpi"><b>${t.menciones.toLocaleString("es")}</b><span>menciones</span></div>
      <div class="kpi"><b>${t.declaraciones.length}</b><span>declaraciones</span></div>
      <div class="kpi"><b>${t.enfoques.length}</b><span>enfoques</span></div>
    </div>
    <p style="font-size:.88rem">${esc(t.resumen)}</p>
    ${t.enfoques.length?`<div class="sec"><h4>Enfoques</h4><div class="rasgos">${t.enfoques.map(c=>`<span class="rasgo">${esc(c)}</span>`).join("")}</div></div>`:""}
    <div class="sec"><h4>Declaraciones (${t.declaraciones.length})</h4>${t.declaraciones.map(d=>`<div class="fila">${esc(d.sintesis)}${fuenteLink(d)}</div>`).join("")}</div>
    ${refsTable(t.referencias)}
    <p style="color:var(--muted);font-size:.72rem;margin-top:.7rem">Basado en ${(t.referencias||[]).length.toLocaleString("es")} menciones — todas listadas arriba.</p>`;
  dlg.showModal();
}
function lado(x, letra){
  return `<div class="lado"><h5>${letra} · ${esc(x.fecha)}</h5>
    <div class="fila">${esc(x.sintesis)}${x.cita?`<blockquote>«${esc(x.cita.slice(0,260))}»</blockquote>`:""}${fuenteLink(x)}</div></div>`;
}
function estudios(){
  const contra = (ESTUDIOS.contradicciones||[]).filter(r=>r.hallazgos.length);
  document.getElementById("estudios").innerHTML = contra.length ? contra.map(r=>
    r.hallazgos.map(h=>`
    <details class="refs" style="margin-bottom:.55rem">
      <summary>${pill("mixto", r.tema)} ${pill(SEV_COLOR[h.severidad]||"neutral", h.severidad)} <b style="font-family:Archivo">${TIPO_TXT[h.tipo]||h.tipo}</b> — <span style="font-weight:400">${esc(h.descripcion)}</span></summary>
      <div class="compara" style="border:none;border-top:1px solid var(--line);border-radius:0">${lado(h.a,"A")}${lado(h.b,"B")}</div>
    </details>`).join("")
  ).join("") : '<p class="sub">Sin tensiones detectadas todavía.</p>';
}
temas(); estudios();
"""

JS_PERSONAS = """
const DIR = __DIRECTORIO__;
let q = "", filtro = "todas", sortCol = "menciones", sortDir = -1;
function filtros(){
  const el = document.getElementById("filtros");
  el.innerHTML = [["todas","Todas"],["destacadas","Con perfil completo"],["frecuentes","10+ menciones"]].map(([c,l])=>
    `<button class="fchip${filtro===c?" on":""}" data-c="${c}">${l}</button>`).join("") +
    `<input id="buscar" placeholder="Buscar entre ${DIR.length.toLocaleString("es")} personas…" value="${esc(q)}">`;
  el.querySelectorAll(".fchip").forEach(b=>b.onclick=()=>{filtro=b.dataset.c; filtros(); tabla();});
  el.querySelector("#buscar").oninput = e=>{q=e.target.value; tabla();};
}
function valorDe(p, col){
  if (col==="nombre") return p.nombre.toLowerCase();
  if (col==="menciones") return p.menciones;
  if (col==="perfil") return p.destacado?1:0;
  return 0;
}
function tabla(){
  const cols = [["nombre","Persona"],["perfil","Perfil"],["menciones","Menciones"]];
  document.getElementById("thead").innerHTML = "<tr>" + cols.map(([c,l])=>
    `<th class="${c==="menciones"?"num":""}" data-c="${c}">${l}${sortCol===c?(sortDir<0?" ↓":" ↑"):""}</th>`).join("") + "</tr>";
  document.querySelectorAll("#thead th").forEach(th=>th.onclick=()=>{
    const c = th.dataset.c;
    if (sortCol===c) sortDir=-sortDir; else { sortCol=c; sortDir=(c==="nombre")?1:-1; }
    tabla();
  });
  const norm = s=>String(s??"").toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g,"");
  const items = DIR
    .filter(p=>(filtro==="todas" || (filtro==="destacadas"&&p.destacado) || (filtro==="frecuentes"&&p.menciones>=10)) &&
      (!q || norm(p.nombre+" "+(p.snippet||"")).includes(norm(q))))
    .sort((a,b)=>{ const va=valorDe(a,sortCol), vb=valorDe(b,sortCol); return (va<vb?-1:va>vb?1:0)*sortDir; });
  document.getElementById("total").textContent = items.length.toLocaleString("es");
  document.getElementById("tbody").innerHTML = items.map(p=>`
    <tr data-s="${esc(p.slug)}">
      <td><div class="actor">${avatar(p)}<div><b>${esc(p.nombre)}</b><small>${esc(p.snippet||"")}</small></div></div></td>
      <td>${p.destacado?pill(p.clasificacion||"neutral", "★ "+(ETIQ[p.clasificacion]||"completo")):'<span style="color:var(--muted);font-size:.72rem">básico</span>'}</td>
      <td class="num">${p.menciones.toLocaleString("es")}</td>
    </tr>`).join("") || `<tr><td colspan="3" style="color:var(--muted)">Sin resultados.</td></tr>`;
  document.querySelectorAll("#tbody tr[data-s]").forEach(tr=>tr.onclick=()=>abrir(tr.dataset.s));
}
function tonos(p){
  const t = {positivo:0, neutro:0, negativo:(p.culpas||[]).length};
  (p.declaraciones||[]).forEach(d=>{ t[d.tono==="positivo"?"positivo":d.tono==="negativo"?"negativo":"neutro"]++; });
  return t;
}
function balanceBar(t){
  const total = t.positivo+t.neutro+t.negativo || 1;
  return `<div class="balance"><i style="width:${100*t.positivo/total}%;background:var(--c-aliado)"></i><i style="width:${100*t.neutro/total}%;background:var(--c-neutral)"></i><i style="width:${100*t.negativo/total}%;background:var(--c-adversario)"></i></div>`;
}
async function abrir(s){
  let p;
  try { p = await (await fetch(`data/personas/${s}.json`)).json(); }
  catch(e){ return; }
  const fila = d=>`<div class="fila">${esc(d.sintesis||d.tema)}${d.cita?`<blockquote>«${esc(d.cita.slice(0,260))}»</blockquote>`:""}${fuenteLink(d)}</div>`;
  let cuerpo = "";
  if (p.destacado){
    const t = tonos(p);
    const pos = (p.declaraciones||[]).filter(d=>d.tono==="positivo");
    const neg = (p.declaraciones||[]).filter(d=>d.tono==="negativo");
    const neu = (p.declaraciones||[]).filter(d=>!d.tono||d.tono==="neutro");
    cuerpo = `
    <div class="kpis">
      <div class="kpi"><b>${p.menciones.toLocaleString("es")}</b><span>menciones</span></div>
      <div class="kpi"><b>${(p.declaraciones||[]).length}</b><span>declaraciones</span></div>
      <div class="kpi"><b style="color:var(--c-aliado)">${t.positivo}</b><span>positivas</span></div>
      <div class="kpi"><b style="color:var(--c-adversario)">${t.negativo}</b><span>negativas</span></div>
      <div class="kpi">${balanceBar(t)}<span>balance</span></div>
    </div>
    <p style="font-size:.88rem">${esc(p.resumen||"")}</p>
    ${(p.caracteristicas||[]).length?`<div class="sec"><h4>Rasgos que la presidenta le adjudica</h4><div class="rasgos">${p.caracteristicas.map(c=>`<span class="rasgo">${esc(c)}</span>`).join("")}</div></div>`:""}
    ${(pos.length||neg.length||(p.culpas||[]).length)?`<div class="sec"><h4>Balance del trato</h4><div class="compara">
      <div class="lado"><h5 style="color:var(--c-aliado)">Trato positivo (${pos.length})</h5>${pos.map(fila).join("")||"—"}</div>
      <div class="lado"><h5 style="color:var(--c-adversario)">Trato negativo (${neg.length+(p.culpas||[]).length})</h5>
        ${(p.culpas||[]).map(c=>`<div class="fila"><b>Culpa:</b> ${esc(c.tema)}${c.cita?`<blockquote>«${esc(c.cita.slice(0,260))}»</blockquote>`:""}${fuenteLink(c)}</div>`).join("")}${neg.map(fila).join("")||""}</div>
    </div></div>`:""}
    ${neu.length?`<div class="sec"><details><summary style="font-family:Archivo;font-weight:600;font-size:.83rem;cursor:pointer">Declaraciones neutras (${neu.length})</summary>${neu.map(fila).join("")}</details></div>`:""}`;
  } else {
    cuerpo = `
    <div class="kpis">
      <div class="kpi"><b>${p.menciones.toLocaleString("es")}</b><span>menciones</span></div>
      <div class="kpi"><b>${(p.relaciones||[]).length}</b><span>relaciones</span></div>
    </div>
    ${p.descripcion?`<p style="font-size:.88rem">${esc(p.descripcion)}</p>`:""}
    ${(p.relaciones||[]).length?`<div class="sec"><h4>Lo que registra el grafo</h4>${p.relaciones.map(r=>`<div class="fila">${esc(r.texto)}${r.evidencia?`<blockquote>«${esc(r.evidencia)}»</blockquote>`:""}</div>`).join("")}</div>`:""}`;
  }
  document.getElementById("dlg-inner").innerHTML = `
    <button class="cerrar" onclick="dlg.close()">✕</button>
    <div class="phead">${avatar(p)}<div><h2>${esc(p.nombre)}</h2><div class="cargo">${esc(p.cargo||p.snippet||"")}</div>${p.destacado?pill(p.clasificacion||"neutral"):""}</div></div>
    ${cuerpo}
    ${refsTable(p.referencias)}
    <p style="color:var(--muted);font-size:.72rem;margin-top:.7rem">Basado en ${p.menciones.toLocaleString("es")} menciones del corpus — todas listadas arriba.</p>`;
  dlg.showModal();
}
filtros(); tabla();
"""

JS_RESUMEN = """
const R = __RESUMEN__;
document.getElementById("cifras").innerHTML = R.cifras.map(([v,k])=>`<div class="cifra"><b>${v.toLocaleString("es")}</b><span>${k}</span></div>`).join("");
const maxT = (R.temas[0]||{}).menciones||1;
document.getElementById("mini-temas").innerHTML = R.temas.map(t=>`
  <div class="tema" onclick="location.href='temas.html'">
    <span class="n">${esc(t.nombre)}</span>
    <span class="barra"><i style="width:${Math.max(4, Math.round(100*t.menciones/maxT))}%;background:var(--c-${PCOLOR[t.postura]||"neutral"})"></i></span>
    <span class="num">${t.menciones.toLocaleString("es")}</span>
    <span class="num">${t.decl}</span>
    <span>${pill(PCOLOR[t.postura]||"neutral", t.postura)}</span>
  </div>`).join("");
document.getElementById("mini-actores").innerHTML = R.actores.map(p=>`
  <tr onclick="location.href='personas.html'">
    <td><div class="actor">${avatar(p)}<div><b>${esc(p.nombre)}</b><small>${esc(p.cargo||"")}</small></div></div></td>
    <td>${pill(p.clasificacion)}</td>
    <td class="num">${p.decl}</td>
    <td class="num">${p.menciones.toLocaleString("es")}</td>
  </tr>`).join("");
"""


def main() -> int:
    perfiles_data = json.loads(PERFILES.read_text(encoding="utf-8"))
    personas_data = json.loads(PERSONAS.read_text(encoding="utf-8")) if PERSONAS.exists() else {"personas": []}
    creditos_path = FOTOS / "creditos.json"
    creditos = json.loads(creditos_path.read_text(encoding="utf-8")) if creditos_path.exists() else {}
    fotos = {p.stem for p in FOTOS.glob("*.jpg")} if FOTOS.exists() else set()

    completos = {p["nombre"]: p for p in perfiles_data["perfiles"]}

    def _tokens(texto: str) -> frozenset[str]:
        t = unicodedata.normalize("NFD", texto.lower())
        t = "".join(c for c in t if unicodedata.category(c) != "Mn")
        stop = {"de", "del", "la", "el", "los", "las", "don", "dona", "jesus", "maria"}
        return frozenset(w for w in re.findall(r"[a-z]+", t) if w not in stop and len(w) > 2)

    completos_tokens = [( _tokens(n), n) for n in completos]

    def completo_de(nombre: str, alias: list[str]) -> dict | None:
        if nombre in completos:
            return completos[nombre]
        toks = _tokens(nombre) | frozenset().union(*(_tokens(a) for a in alias[:6])) if alias else _tokens(nombre)
        for ctoks, cnombre in completos_tokens:
            if ctoks and (ctoks <= toks or toks <= ctoks):
                return completos[cnombre]
        return None
    temas = perfiles_data.get("temas", [])
    estudios = perfiles_data.get("estudios", {})

    # --- fichas JSON por persona -------------------------------------------
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True)
    directorio = []
    usados: set[str] = set()
    for persona in personas_data["personas"]:
        nombre = persona["nombre"]
        s = slug(nombre)
        completo = completo_de(nombre, persona.get("alias") or [])
        if completo:
            usados.add(completo["nombre"])
        ficha = dict(persona)
        ficha["slug"] = s
        ficha["foto"] = f"fotos/{s}.jpg" if s in fotos else ""
        if completo:
            ficha.update(
                {
                    "destacado": True,
                    "cargo": completo.get("cargo", ""),
                    "clasificacion": completo.get("clasificacion", "neutral"),
                    "resumen": completo.get("resumen", ""),
                    "caracteristicas": completo.get("caracteristicas", []),
                    "culpas": completo.get("culpas", []),
                    "declaraciones": completo.get("declaraciones", []),
                    "referencias": completo.get("referencias") or persona.get("referencias", []),
                    "menciones": len(completo.get("referencias") or persona.get("referencias", [])),
                }
            )
        (DATA_DIR / f"{s}.json").write_text(json.dumps(ficha, ensure_ascii=False), encoding="utf-8")
        directorio.append(
            {
                "nombre": nombre,
                "slug": s,
                "menciones": ficha["menciones"],
                "destacado": bool(completo),
                "clasificacion": (completo or {}).get("clasificacion", ""),
                "snippet": ((completo or {}).get("cargo") or persona.get("descripcion", ""))[:90],
                "foto": ficha["foto"],
            }
        )

    # perfiles completos que aún no estén en el índice exhaustivo
    for nombre, completo in completos.items():
        if nombre in usados:
            continue
        s = slug(nombre)
        ficha = {**completo, "slug": s, "destacado": True, "foto": f"fotos/{s}.jpg" if s in fotos else "",
                 "menciones": len(completo.get("referencias", []))}
        (DATA_DIR / f"{s}.json").write_text(json.dumps(ficha, ensure_ascii=False), encoding="utf-8")
        directorio.append({"nombre": nombre, "slug": s, "menciones": ficha["menciones"], "destacado": True,
                           "clasificacion": completo.get("clasificacion", ""), "snippet": completo.get("cargo", "")[:90],
                           "foto": ficha["foto"]})

    # --- datos de resumen ---------------------------------------------------
    decl_total = sum(len(p.get("declaraciones", [])) for p in perfiles_data["perfiles"]) + sum(len(t.get("declaraciones", [])) for t in temas)
    culpas_total = sum(len(p.get("culpas", [])) for p in perfiles_data["perfiles"])
    refs_total = sum(d["menciones"] for d in directorio) + sum(len(t.get("referencias", [])) for t in temas)
    tensiones = sum(len(r["hallazgos"]) for r in estudios.get("contradicciones", []))
    orden = {"culpado": 0, "adversario": 1, "mixto": 2, "aliado": 3, "funcionario": 4, "neutral": 5}
    top_actores = sorted(perfiles_data["perfiles"], key=lambda p: -len(p.get("declaraciones", [])))[:10]
    resumen = {
        "cifras": [
            [len(directorio), "personas"], [len(completos), "perfiles completos"], [len(temas), "temas"],
            [decl_total, "declaraciones"], [culpas_total, "culpas"], [refs_total, "referencias"], [tensiones, "tensiones"],
        ],
        "temas": [{"nombre": t["nombre"], "menciones": t["menciones"], "postura": t["postura"], "decl": len(t["declaraciones"])}
                  for t in sorted(temas, key=lambda t: -t["menciones"])[:8]],
        "actores": [{"nombre": p["nombre"], "cargo": p.get("cargo", ""), "clasificacion": p["clasificacion"],
                     "decl": len(p["declaraciones"]), "menciones": len(p.get("referencias", [])),
                     "foto": f"fotos/{slug(p['nombre'])}.jpg" if slug(p["nombre"]) in fotos else ""}
                    for p in sorted(top_actores, key=lambda p: (orden.get(p["clasificacion"], 9), -len(p["declaraciones"])))],
    }

    # --- páginas ------------------------------------------------------------
    cont_index = """
  <div class="cifras" id="cifras"></div>
  <h2>Atención por tema <a class="mas" href="temas.html">ver todos →</a></h2>
  <div class="temas" id="mini-temas"></div>
  <h2>Actores del discurso <a class="mas" href="personas.html">directorio completo →</a></h2>
  <div class="tscroll"><table class="master"><thead><tr><th>Actor</th><th>Trato</th><th class="num">Decl.</th><th class="num">Menciones</th></tr></thead><tbody id="mini-actores"></tbody></table></div>
"""
    cont_temas = """
  <h2>Atención por tema</h2>
  <p class="sub">Menciones de cada tema en el corpus; el color de la barra es la postura dominante. Clic para el detalle con todas sus referencias.</p>
  <div class="temas" id="temas"></div>
  <div class="leyenda">
    <span><i style="background:var(--c-aliado)"></i>positiva</span>
    <span><i style="background:var(--c-adversario)"></i>negativa</span>
    <span><i style="background:var(--c-mixto)"></i>mixta</span>
    <span><i style="background:var(--c-neutral)"></i>neutral</span>
  </div>
  <h2>Posiciones en tensión</h2>
  <p class="sub">Contradicciones detectadas entre declaraciones sobre un mismo tema — cada hallazgo confronta las dos declaraciones con fecha y fuente. Detección automatizada exigente; verifica en las fuentes.</p>
  <div id="estudios"></div>
"""
    cont_personas = """
  <h2>Directorio de personas <span style="font-family:Archivo;font-size:.9rem;color:var(--muted)">(<span id="total"></span> mostradas)</span></h2>
  <p class="sub">Todas las personas y colectivos que aparecen al menos una vez en el discurso de la presidenta. Los ★ tienen perfil completo (clasificación del trato, balance y culpas); el resto, perfil básico con su descripción, relaciones del grafo y todas sus referencias. Clic en cualquier fila.</p>
  <div class="controls" id="filtros"></div>
  <div class="tscroll" style="max-height:72vh;overflow-y:auto"><table class="master"><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
"""

    paginas = {
        "index.html": ("Hemeroteca Presidencial", cont_index, JS_RESUMEN.replace("__RESUMEN__", json.dumps(resumen, ensure_ascii=False))),
        "temas.html": ("Temas · Hemeroteca Presidencial", cont_temas,
                       JS_TEMAS.replace("__TEMAS__", json.dumps(temas, ensure_ascii=False)).replace("__ESTUDIOS__", json.dumps(estudios, ensure_ascii=False))),
        "personas.html": ("Personas · Hemeroteca Presidencial", cont_personas,
                          JS_PERSONAS.replace("__DIRECTORIO__", json.dumps(directorio, ensure_ascii=False))),
    }
    DOCS.mkdir(exist_ok=True)
    for nombre_archivo, (titulo, contenido, js) in paginas.items():
        html = (
            pagina(titulo, nombre_archivo, contenido, js)
            .replace("__CREDITOS__", json.dumps(creditos, ensure_ascii=False))
            .replace("__FECHA__", datetime.now().strftime("%Y-%m-%d"))
        )
        (DOCS / nombre_archivo).write_text(html, encoding="utf-8")
        print(f"{nombre_archivo}: {len(html):,} bytes")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"fichas: {len(directorio):,} en docs/data/personas/ · fotos: {len(fotos)} · temas: {len(temas)} · tensiones: {tensiones}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
