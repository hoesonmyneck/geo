"""
Визуализация результатов геокодирования на карте Астаны.

Использует Leaflet + Leaflet.markercluster + Leaflet.draw:
  - Левый сайдбар: фильтры по уверенности, категориям, районам
  - Инструмент выделения области (прямоугольник / круг / полигон)
    с агрегированной статистикой по выделенной зоне
  - Клик по маркеру — подробный попап с демографией

Использование:
    python -m src.visualize --input output/astana_results.parquet
    python -m src.visualize --input output/astana_results.parquet --output reports/map.html
"""
from __future__ import annotations

import json
import webbrowser
from pathlib import Path

import click
import pandas as pd


# ---------------------------------------------------------------------------
# HTML-шаблон
# ---------------------------------------------------------------------------
_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Геокодирование — Астана</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:100%; height:100%; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; overflow:hidden; }

/* ── Экран авторизации ── */
#auth-overlay {
  position:fixed; inset:0; z-index:99999;
  background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);
  display:flex; align-items:center; justify-content:center;
}
#auth-box {
  background:#1e293b; border:1px solid #334155; border-radius:16px;
  padding:40px 48px; width:360px; box-shadow:0 25px 60px rgba(0,0,0,.5);
}
#auth-box h2 {
  color:#f1f5f9; font-size:22px; font-weight:700; margin-bottom:6px; text-align:center;
}
#auth-box p {
  color:#64748b; font-size:13px; text-align:center; margin-bottom:28px;
}
.auth-field { margin-bottom:16px; }
.auth-field label { display:block; color:#94a3b8; font-size:12px; font-weight:600;
  letter-spacing:.05em; text-transform:uppercase; margin-bottom:6px; }
.auth-field input {
  width:100%; padding:10px 14px; background:#0f172a; border:1px solid #334155;
  border-radius:8px; color:#f1f5f9; font-size:14px; outline:none;
  transition:border-color .2s;
}
.auth-field input:focus { border-color:#3b82f6; }
#auth-btn {
  width:100%; padding:12px; margin-top:8px;
  background:#3b82f6; color:#fff; font-size:15px; font-weight:600;
  border:none; border-radius:8px; cursor:pointer; transition:background .2s;
}
#auth-btn:hover { background:#2563eb; }
#auth-error {
  color:#f87171; font-size:13px; text-align:center; margin-top:12px;
  min-height:18px;
}

/* Левый сайдбар */
#sidebar {
  position:fixed; top:0; left:0; width:264px; height:100%;
  background:#fff; border-right:1px solid #e2e8f0;
  overflow-y:auto; z-index:1000;
  display:flex; flex-direction:column;
}
#sidebar-header {
  padding:13px 16px; background:#1e293b; color:#fff;
  font-size:14px; font-weight:600; letter-spacing:.2px; flex-shrink:0;
}

/* Карта */
#map { position:absolute; top:0; left:264px; right:0; bottom:0; }

/* Секции сайдбара */
.s-head {
  padding:8px 14px; font-size:11px; font-weight:700;
  text-transform:uppercase; letter-spacing:.6px;
  color:#64748b; background:#f8fafc;
  border-top:1px solid #e2e8f0; border-bottom:1px solid #e2e8f0;
  display:flex; justify-content:space-between; align-items:center;
  cursor:pointer; user-select:none; flex-shrink:0;
}
.s-head:hover { background:#f1f5f9; }
.s-body { padding:8px 14px 10px; flex-shrink:0; }
.s-body.hidden { display:none; }

/* Пункты уверенности */
.conf-item {
  display:flex; align-items:center; padding:5px 6px 5px 0;
  font-size:13px; color:#374151; cursor:pointer; border-radius:4px;
  transition:opacity .15s;
}
.conf-item:hover { background:#f8fafc; }
.conf-item.off { opacity:.28; }
.dot {
  display:inline-block; width:10px; height:10px;
  border-radius:50%; margin-right:8px; flex-shrink:0;
}

/* Категории */
.cat-item {
  display:flex; align-items:center; padding:4px 0;
  font-size:13px; color:#374151; cursor:pointer; line-height:1.3;
}
.cat-item input[type=checkbox] {
  margin-right:8px; cursor:pointer; width:14px; height:14px;
  accent-color:#6366f1; flex-shrink:0;
}
.cat-hint { font-size:11px; color:#94a3b8; margin-bottom:6px; }

/* Районы */
.dist-item {
  display:flex; align-items:center; padding:5px 0;
  font-size:13px; color:#374151; cursor:pointer;
}
.dist-item input[type=checkbox] {
  margin-right:8px; cursor:pointer; width:14px; height:14px;
  accent-color:#0891b2; flex-shrink:0;
}

/* Статистика района */
#dist-stats {
  margin-top:6px; padding:8px 10px; background:#f0f9ff;
  border-radius:6px; font-size:12px; line-height:1.7; display:none;
}
#dist-stats table { width:100%; border-collapse:collapse; }
#dist-stats td:first-child { color:#64748b; padding-right:8px; }
#dist-stats td:last-child { font-weight:600; color:#0f172a; }

/* Ссылка-сброс */
.clear-link {
  font-size:11px; color:#94a3b8; cursor:pointer; text-decoration:underline;
  text-decoration-style:dotted;
}
.clear-link:hover { color:#475569; }

/* Маркеры */
.bld {
  border-radius:50%; display:flex; align-items:center; justify-content:center;
  color:#fff; font-weight:700;
  border:2px solid rgba(255,255,255,.85);
  box-shadow:0 1px 5px rgba(0,0,0,.35);
}
.cls {
  border-radius:50%; display:flex; align-items:center; justify-content:center;
  color:#fff; font-weight:700;
  border:3px solid rgba(255,255,255,.9);
  box-shadow:0 2px 8px rgba(0,0,0,.4);
}

/* Панель итогов (правый верх) */
#stats-panel {
  position:fixed; top:12px; right:12px; z-index:999;
  background:#fff; border-radius:8px;
  box-shadow:0 2px 10px rgba(0,0,0,.16);
  padding:10px 14px; font-size:13px; line-height:1.75;
}

/* Панель статистики выделенной области */
#sel-panel {
  position:fixed; bottom:28px; left:50%; transform:translateX(-50%);
  z-index:1001; background:#1e293b; color:#e2e8f0;
  border-radius:10px; box-shadow:0 6px 20px rgba(0,0,0,.38);
  padding:14px 20px 14px 16px; font-size:13px; line-height:1.7;
  min-width:290px; max-width:460px; display:none;
}
#sel-panel .sel-title { font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.5px; color:#94a3b8; margin-bottom:8px; }
#sel-panel .sel-close { position:absolute; top:10px; right:12px; cursor:pointer; color:#64748b; font-size:18px; line-height:1; }
#sel-panel .sel-close:hover { color:#e2e8f0; }
#sel-panel table { width:100%; border-collapse:collapse; }
#sel-panel td:first-child { color:#94a3b8; padding-right:12px; white-space:nowrap; }
#sel-panel td:last-child { font-weight:700; color:#f1f5f9; }
#sel-panel hr { border:none; border-top:1px solid #334155; margin:5px 0; }

/* Легенда кластеров */
#clust-body { font-size:12px; color:#64748b; line-height:2; }

/* Кнопка инструментов рисования — сдвиг вправо (в карту) */
.leaflet-draw-toolbar { margin-top:80px; }
/* Переключатель слоёв — опускаем ниже панели статистики */
.leaflet-top.leaflet-right .leaflet-control-layers { margin-top: 160px; }
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css"/>
</head>
<body>

<!-- Экран авторизации -->
<div id="auth-overlay">
  <div id="auth-box">
    <h2>Карта Астаны</h2>
    <p>Введите данные для входа</p>
    <div class="auth-field">
      <label>Логин</label>
      <input type="text" id="auth-login" placeholder="Логин" autocomplete="username"/>
    </div>
    <div class="auth-field">
      <label>Пароль</label>
      <input type="password" id="auth-pass" placeholder="Пароль" autocomplete="current-password"/>
    </div>
    <button id="auth-btn" onclick="doLogin()">Войти</button>
    <div id="auth-error"></div>
  </div>
</div>
<script>
(function(){
  var k = "geo_auth_ok";
  if(sessionStorage.getItem(k)==="1"){
    document.getElementById("auth-overlay").style.display="none";
  }
})();
function doLogin(){
  var u=document.getElementById("auth-login").value.trim();
  var p=document.getElementById("auth-pass").value;
  if(u==="admin"&&p==="admin"){
    sessionStorage.setItem("geo_auth_ok","1");
    document.getElementById("auth-overlay").style.display="none";
  } else {
    document.getElementById("auth-error").textContent="Неверный логин или пароль";
    document.getElementById("auth-pass").value="";
    document.getElementById("auth-pass").focus();
  }
}
document.addEventListener("keydown",function(e){
  if(e.key==="Enter"&&document.getElementById("auth-overlay").style.display!=="none") doLogin();
});
</script>

<!-- Сайдбар -->
<div id="sidebar">
  <div id="sidebar-header">Карта</div>

  <!-- Уверенность -->
  <div class="s-head" onclick="toggleSection('conf')">
    Уверенность (дом) <span id="conf-arr">&#9660;</span>
  </div>
  <div id="conf-body" class="s-body">
    <div class="conf-item" data-conf="high"   onclick="toggleConf('high')">
      <span class="dot" style="background:#22c55e"></span>Точный адрес (high)
    </div>
    <div class="conf-item" data-conf="medium" onclick="toggleConf('medium')">
      <span class="dot" style="background:#f59e0b"></span>Найдена улица (medium)
    </div>
    <div class="conf-item" data-conf="low"    onclick="toggleConf('low')">
      <span class="dot" style="background:#f97316"></span>Нечеткое (low)
    </div>
    <div class="conf-item" data-conf="miss"   onclick="toggleConf('miss')">
      <span class="dot" style="background:#ef4444"></span>Не найдено
    </div>
    <div style="margin-top:6px">
      <span class="clear-link" onclick="resetConf()">Показать все</span>
    </div>
  </div>

  <!-- Категории -->
  <div class="s-head" onclick="toggleSection('cats')">
    Категории жителей <span id="cats-arr">&#9660;</span>
  </div>
  <div id="cats-body" class="s-body">
    <div class="cat-hint">Показывать дома где есть хотя бы один житель выбранной категории (можно несколько)</div>
    <label class="cat-item"><input type="checkbox" value="lsi">        ЛСИ (лица с инвалидностью)</label>
    <label class="cat-item"><input type="checkbox" value="asp">        АСП</label>
    <label class="cat-item"><input type="checkbox" value="deti">       Дети до 18</label>
    <label class="cat-item"><input type="checkbox" value="working">    Работающие</label>
    <label class="cat-item"><input type="checkbox" value="student">    Студенты</label>
    <label class="cat-item"><input type="checkbox" value="pensioners"> Пенсионеры</label>
    <label class="cat-item"><input type="checkbox" value="ip">         ИП</label>
    <label class="cat-item"><input type="checkbox" value="kandas">     КАНДАС</label>
    <div style="margin-top:6px">
      <span class="clear-link" onclick="clearCats()">Сбросить</span>
    </div>
  </div>

  <!-- Районы -->
  <div class="s-head" onclick="toggleSection('dists')">
    Районы <span id="dists-arr">&#9660;</span>
  </div>
  <div id="dists-body" class="s-body">
    <div id="dist-list"></div>
    <div id="dist-stats">
      <b id="dst-name" style="display:block;margin-bottom:4px;color:#0f172a"></b>
      <table id="dst-table"></table>
    </div>
    <div style="margin-top:6px">
      <span class="clear-link" onclick="clearDists()">Показать все районы</span>
    </div>
  </div>

  <!-- Легенда кластеров — прижата к низу -->
  <div style="flex:1"></div>
  <div class="s-head" onclick="toggleSection('clust')" style="border-top:1px solid #e2e8f0">
    Цвет кластера <span id="clust-arr">&#9660;</span>
  </div>
  <div id="clust-body" class="s-body">
    <span class="dot" style="background:#22c55e"></span>&lt; 100 чел.<br>
    <span class="dot" style="background:#059669"></span>100 — 999<br>
    <span class="dot" style="background:#0891b2"></span>1 000 — 9 999<br>
    <span class="dot" style="background:#2563eb"></span>10 000 — 99 999<br>
    <span class="dot" style="background:#7c3aed"></span>500 000+<br>
    <span style="font-size:11px">Цифра = количество человек</span>
  </div>
</div>

<!-- Карта -->
<div id="map"></div>

<!-- Панель итогов -->
<div id="stats-panel">
  <b>Итого записей:</b> <span id="st-total"></span><br>
  <b>Найдено:</b> <span id="st-found"></span><br>
  <b>Уникальных домов:</b> <span id="st-houses"></span><br>
  <span id="st-note" style="font-size:11px;color:#6b7280;display:none">
    Фильтр: <b id="st-vis"></b> домов
  </span>
</div>

<!-- Панель выделенной области -->
<div id="sel-panel">
  <span class="sel-close" onclick="clearDraw()">&#x2715;</span>
  <div class="sel-title">Выбранная область</div>
  <div id="sel-content"></div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
<script>
// ── Данные ───────────────────────────────────────────────────────
var POINTS = __POINTS__;
var STATS  = __STATS__;

// ── Начальная статистика ─────────────────────────────────────────
document.getElementById("st-total").textContent  = STATS.total.toLocaleString("ru");
document.getElementById("st-found").textContent  = STATS.found.toLocaleString("ru") + " (" + STATS.pct + "%)";
document.getElementById("st-houses").textContent = STATS.houses.toLocaleString("ru");

// ── Список районов ───────────────────────────────────────────────
var allDistricts = (function() {
  var seen = {}, arr = [];
  POINTS.forEach(function(p) {
    var d = p.district || "";
    if (d && !seen[d]) { seen[d] = true; arr.push(d); }
  });
  return arr.sort();
})();

var distList = document.getElementById("dist-list");
if (allDistricts.length === 0) {
  distList.innerHTML = '<span style="font-size:12px;color:#94a3b8">Данные о районах не найдены</span>';
} else {
  allDistricts.forEach(function(d) {
    var lbl = document.createElement("label");
    lbl.className = "dist-item";
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = d;
    cb.addEventListener("change", onDistChange);
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(d));
    distList.appendChild(lbl);
  });
}

// ── Состояние фильтров ───────────────────────────────────────────
var confActive = { high: true, medium: true, low: true, miss: true };
var activeCats  = new Set();
var activeDists = new Set();

// ── Цвета ────────────────────────────────────────────────────────
var CONF_COLOR = { high:"#22c55e", medium:"#f59e0b", low:"#f97316", miss:"#ef4444" };

function clusterColor(n) {
  if (n >= 500000) return "#7c3aed";
  if (n >= 100000) return "#2563eb";
  if (n >=  10000) return "#0891b2";
  if (n >=   1000) return "#059669";
  if (n >=    100) return "#16a34a";
  return "#22c55e";
}
function fmt(n) {
  if (n >= 1000000) return (n/1e6).toFixed(1).replace(".0","") + "\u00a0млн";
  if (n >=    1000) return (n/1e3).toFixed(0) + "\u00a0тыс";
  return String(n);
}

// ── Карта ────────────────────────────────────────────────────────
var map = L.map("map", { zoomControl: true }).setView([51.1801, 71.4460], 12);

var baseLayers = {
  "Светлая": L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    { attribution: "© OpenStreetMap, © CartoDB", subdomains: "abcd", maxZoom: 19 }
  ),
  "Стандартная": L.tileLayer(
    "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap contributors", maxZoom: 19 }
  ),
  "Спутник": L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "© Esri, © USGS, © NASA", maxZoom: 19 }
  ),
  "Тёмная": L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { attribution: "© OpenStreetMap, © CartoDB", subdomains: "abcd", maxZoom: 19 }
  ),
  "Рельеф": L.tileLayer(
    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap, © OpenTopoMap", maxZoom: 17 }
  )
};

baseLayers["Светлая"].addTo(map);
L.control.layers(baseLayers, null, { position: "topright", collapsed: false }).addTo(map);

// ── Построение попапа ────────────────────────────────────────────
function buildPopup(p) {
  var pop = "<b>" + p.street + (p.house ? ", " + p.house : "") + "</b><br>"
    + "<span style='color:#555'>Всего жителей: <b>" + p.n + "</b></span>";

  if (p.male !== undefined) {
    pop += "<hr style='margin:6px 0;border-color:#eee'>"
      + "<table style='font-size:12px;border-collapse:collapse;width:100%'>";

    function row(label, val, color) {
      if (!val) return "";
      return "<tr><td style='padding:2px 6px 2px 0;color:#666'>" + label + "</td>"
           + "<td style='padding:2px 0;font-weight:600;color:" + (color||"#222") + "'>" + val + "</td></tr>";
    }
    pop += row("Мужчин",         p.male,        "#2563eb");
    pop += row("Женщин",         p.female,      "#db2777");
    if (p.age_avg) pop += row("Средний возраст", p.age_avg.toFixed(1) + " лет");
    pop += "<tr><td colspan='2'><hr style='margin:4px 0;border-color:#eee'></td></tr>";
    pop += row("Трудоспособных", p.trud);
    pop += row("Детей до 18",    p.deti,        "#16a34a");
    pop += row("Работающих",     p.working);
    pop += row("Студентов",      p.student);
    pop += row("Пенсионеров",    p.pensioners);
    pop += row("ЛСИ",            p.lsi,         "#9333ea");
    pop += row("АСП",            p.asp,         "#dc2626");
    pop += row("ИП",             p.ip);
    pop += row("КАНДАС",         p.kandas);
    pop += "</table>";
  }
  if (p.district) {
    pop += "<div style='font-size:11px;color:#6b7280;margin-top:4px'>Район: " + p.district + "</div>";
  }
  pop += "<div style='font-size:10px;color:#999;margin-top:4px'>"
       + p.lat.toFixed(6) + ", " + p.lon.toFixed(6) + " · " + p.conf + "</div>";
  return pop;
}

// ── Проверка фильтра ─────────────────────────────────────────────
function filterPoint(p) {
  if (!confActive[p.conf]) return false;
  if (activeCats.size > 0) {
    var ok = false;
    activeCats.forEach(function(cat) { if ((p[cat] || 0) > 0) ok = true; });
    if (!ok) return false;
  }
  if (activeDists.size > 0) {
    if (!activeDists.has(p.district || "")) return false;
  }
  return true;
}

// ── Пересборка кластера ──────────────────────────────────────────
var cluster = null;

function rebuild() {
  if (cluster) map.removeLayer(cluster);
  cluster = L.markerClusterGroup({
    maxClusterRadius: 80,
    disableClusteringAtZoom: 17,
    iconCreateFunction: function(cl) {
      var ch = cl.getAllChildMarkers(), total = 0;
      for (var i = 0; i < ch.length; i++) total += (ch[i].options.people || 0);
      var sz = Math.min(78, Math.max(38, Math.round(28 + Math.log10(total + 1) * 16)));
      var fs = Math.max(11, Math.round(sz / 3.8));
      return L.divIcon({
        html: '<div class="cls" style="width:' + sz + 'px;height:' + sz + 'px;background:'
            + clusterColor(total) + ';font-size:' + fs + 'px">' + fmt(total) + '</div>',
        className: "", iconSize: [sz, sz], iconAnchor: [sz/2, sz/2]
      });
    }
  });

  var visible = 0;
  POINTS.forEach(function(p) {
    if (!filterPoint(p)) return;
    visible++;
    var color = CONF_COLOR[p.conf] || "#94a3b8";
    var r = Math.min(26, Math.max(13, Math.round(11 + Math.log1p(p.n) * 3.2)));
    var fs = Math.max(9, r - 2);
    var icon = L.divIcon({
      html: '<div class="bld" style="width:' + (r*2) + 'px;height:' + (r*2) + 'px;background:'
          + color + ';font-size:' + fs + 'px">' + p.n + '</div>',
      className: "", iconSize: [r*2, r*2], iconAnchor: [r, r]
    });
    var m = L.marker([p.lat, p.lon], { icon: icon, people: p.n });
    m.bindPopup(buildPopup(p), { maxWidth: 300 });
    cluster.addLayer(m);
  });
  map.addLayer(cluster);

  var note = document.getElementById("st-note");
  if (visible < STATS.houses) {
    document.getElementById("st-vis").textContent = visible.toLocaleString("ru");
    note.style.display = "";
  } else {
    note.style.display = "none";
  }
}

rebuild();

// ── Фильтр: Уверенность ──────────────────────────────────────────
function toggleConf(c) {
  confActive[c] = !confActive[c];
  document.querySelector('[data-conf="' + c + '"]').classList.toggle("off", !confActive[c]);
  rebuild();
}
function resetConf() {
  ["high","medium","low","miss"].forEach(function(c) {
    confActive[c] = true;
    document.querySelector('[data-conf="' + c + '"]').classList.remove("off");
  });
  rebuild();
}

// ── Фильтр: Категории ────────────────────────────────────────────
document.querySelectorAll('#cats-body input[type=checkbox]').forEach(function(cb) {
  cb.addEventListener('change', function() {
    if (this.checked) activeCats.add(this.value);
    else activeCats.delete(this.value);
    rebuild();
  });
});
function clearCats() {
  activeCats.clear();
  document.querySelectorAll('#cats-body input[type=checkbox]').forEach(function(cb) { cb.checked = false; });
  rebuild();
}

// ── Фильтр: Районы ──────────────────────────────────────────────
function onDistChange() {
  activeDists.clear();
  document.querySelectorAll('#dist-list input:checked').forEach(function(cb) {
    activeDists.add(cb.value);
  });
  rebuild();
  updateDistStats();
}
function clearDists() {
  activeDists.clear();
  document.querySelectorAll('#dist-list input').forEach(function(cb) { cb.checked = false; });
  document.getElementById("dist-stats").style.display = "none";
  rebuild();
}

// Агрегат статистики по выбранным районам
function updateDistStats() {
  var panel = document.getElementById("dist-stats");
  if (activeDists.size === 0) { panel.style.display = "none"; return; }

  var totals = { n:0, male:0, female:0, trud:0, deti:0, working:0, lsi:0, asp:0, student:0, pensioners:0, ip:0, kandas:0, ageSum:0, ageN:0 };
  var hasAttrs = POINTS.length > 0 && POINTS[0].male !== undefined;

  POINTS.forEach(function(p) {
    if (!activeDists.has(p.district || "")) return;
    totals.n += p.n;
    if (hasAttrs) {
      totals.male       += p.male || 0;
      totals.female     += p.female || 0;
      totals.trud       += p.trud || 0;
      totals.deti       += p.deti || 0;
      totals.working    += p.working || 0;
      totals.lsi        += p.lsi || 0;
      totals.asp        += p.asp || 0;
      totals.student    += p.student || 0;
      totals.pensioners += p.pensioners || 0;
      totals.ip         += p.ip || 0;
      totals.kandas     += p.kandas || 0;
      if (p.age_avg && p.n) { totals.ageSum += p.age_avg * p.n; totals.ageN += p.n; }
    }
  });

  var name = Array.from(activeDists).join(", ");
  document.getElementById("dst-name").textContent = name;

  function drow(label, val) {
    if (!val) return "";
    return "<tr><td>" + label + "</td><td>" + val.toLocaleString("ru") + "</td></tr>";
  }
  var html = drow("Жителей", totals.n);
  if (hasAttrs) {
    html += drow("Мужчин", totals.male) + drow("Женщин", totals.female);
    if (totals.ageN) {
      html += "<tr><td>Ср. возраст</td><td>" + (totals.ageSum/totals.ageN).toFixed(1) + " лет</td></tr>";
    }
    html += drow("Трудоспособных", totals.trud) + drow("Детей до 18", totals.deti)
         + drow("Работающих", totals.working) + drow("Студентов", totals.student)
         + drow("Пенсионеров", totals.pensioners) + drow("ЛСИ", totals.lsi)
         + drow("АСП", totals.asp) + drow("ИП", totals.ip) + drow("КАНДАС", totals.kandas);
  }
  document.getElementById("dst-table").innerHTML = html;
  panel.style.display = "block";
}

// ── Сворачивание секций ──────────────────────────────────────────
function toggleSection(id) {
  var body  = document.getElementById(id + "-body");
  var arrow = document.getElementById(id + "-arr");
  if (!body) return;
  var hidden = body.classList.toggle("hidden");
  if (arrow) arrow.innerHTML = hidden ? "&#9658;" : "&#9660;";
}

// ── Leaflet.draw: выделение области ─────────────────────────────
var drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

var drawControl = new L.Control.Draw({
  position: "topleft",
  draw: {
    polygon:      { allowIntersection: false },
    rectangle:    {},
    circle:       {},
    polyline:     false,
    marker:       false,
    circlemarker: false
  },
  edit: { featureGroup: drawnItems, edit: false, remove: false }
});
map.addControl(drawControl);

var currentShape = null;

map.on(L.Draw.Event.CREATED, function(e) {
  if (currentShape) drawnItems.removeLayer(currentShape);
  currentShape = e.layer;
  drawnItems.addLayer(currentShape);
  showAreaStats(e.layer, e.layerType);
});

function clearDraw() {
  if (currentShape) { drawnItems.removeLayer(currentShape); currentShape = null; }
  document.getElementById("sel-panel").style.display = "none";
}

// Точка в полигоне (ray casting)
function pointInPolygon(lat, lon, latlngs) {
  var x = lon, y = lat, inside = false;
  var ring = latlngs[0] || latlngs;
  for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    var xi = ring[i].lng, yi = ring[i].lat;
    var xj = ring[j].lng, yj = ring[j].lat;
    if (((yi > y) !== (yj > y)) && (x < (xj-xi)*(y-yi)/(yj-yi)+xi)) inside = !inside;
  }
  return inside;
}

function inShape(lat, lon, layer, type) {
  if (type === "circle") {
    return layer.getLatLng().distanceTo(L.latLng(lat, lon)) <= layer.getRadius();
  } else if (type === "rectangle") {
    return layer.getBounds().contains([lat, lon]);
  } else {
    return pointInPolygon(lat, lon, layer.getLatLngs());
  }
}

function showAreaStats(layer, type) {
  var t = { n:0, male:0, female:0, trud:0, deti:0, working:0, lsi:0, asp:0, student:0, pensioners:0, ip:0, kandas:0, ageSum:0, ageN:0 };
  var hasAttrs = POINTS.length > 0 && POINTS[0].male !== undefined;
  var houses = 0;

  POINTS.forEach(function(p) {
    if (!filterPoint(p)) return;
    if (!inShape(p.lat, p.lon, layer, type)) return;
    houses++;
    t.n += p.n;
    if (hasAttrs) {
      t.male       += p.male || 0;
      t.female     += p.female || 0;
      t.trud       += p.trud || 0;
      t.deti       += p.deti || 0;
      t.working    += p.working || 0;
      t.lsi        += p.lsi || 0;
      t.asp        += p.asp || 0;
      t.student    += p.student || 0;
      t.pensioners += p.pensioners || 0;
      t.ip         += p.ip || 0;
      t.kandas     += p.kandas || 0;
      if (p.age_avg && p.n) { t.ageSum += p.age_avg * p.n; t.ageN += p.n; }
    }
  });

  var panel = document.getElementById("sel-panel");
  var cont  = document.getElementById("sel-content");

  if (t.n === 0) {
    cont.innerHTML = "<span style='color:#64748b'>В выбранной области данных нет</span>";
    panel.style.display = "block";
    return;
  }

  function sr(label, val, color) {
    if (!val) return "";
    return "<tr><td>" + label + "</td><td style='color:" + (color||"#f1f5f9") + "'>"
         + val.toLocaleString("ru") + "</td></tr>";
  }

  var html = "<table>"
    + sr("Домов в области", houses, "#f1f5f9")
    + sr("Всего жителей",   t.n,    "#fff");
  if (hasAttrs) {
    html += sr("Мужчин",  t.male,  "#93c5fd")
          + sr("Женщин",  t.female,"#f9a8d4");
    if (t.ageN) html += "<tr><td>Средний возраст</td><td>" + (t.ageSum/t.ageN).toFixed(1) + " лет</td></tr>";
    html += "<tr><td colspan='2'><hr></td></tr>";
    html += sr("Трудоспособных", t.trud)
          + sr("Детей до 18",    t.deti,        "#86efac")
          + sr("Работающих",     t.working)
          + sr("Студентов",      t.student)
          + sr("Пенсионеров",    t.pensioners)
          + sr("ЛСИ",            t.lsi,         "#c4b5fd")
          + sr("АСП",            t.asp,         "#fca5a5")
          + sr("ИП",             t.ip)
          + sr("КАНДАС",         t.kandas);
  }
  html += "</table>";
  cont.innerHTML = html;
  panel.style.display = "block";
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Построение данных
# ---------------------------------------------------------------------------

_ATTR_COLS = [
    "gender_id", "vozrast", "trud_vozrast", "deti_do18",
    "working", "lsi", "asp", "student", "pensioners", "ip", "kandas",
    "rainame",
]


def _clean_district(raw: str) -> str:
    """Нормализуем название района: убираем ' ауданы', ' района' и т.п."""
    s = str(raw or "").strip()
    for suffix in (" ауданы", " района", " district", " р-н", " р."):
        if s.lower().endswith(suffix.lower()):
            s = s[: -len(suffix)].strip()
    return s


def _build_points(df: pd.DataFrame) -> list[dict]:
    found = df.dropna(subset=["lat", "lon"]).copy()
    has_attrs = all(c in found.columns for c in _ATTR_COLS)

    agg_dict: dict = {
        "n":          ("sicid",       "count"),
        "confidence": ("confidence",  lambda x: x.value_counts().idxmax()),
        "street":     ("street_used", "first"),
        "house":      ("house_used",  "first"),
    }

    if has_attrs:
        agg_dict.update({
            "male":       ("gender_id",    lambda x: (x == 1).sum()),
            "female":     ("gender_id",    lambda x: (x == 2).sum()),
            "age_avg":    ("vozrast",      lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0),
            "trud":       ("trud_vozrast", "sum"),
            "deti":       ("deti_do18",    "sum"),
            "working":    ("working",      "sum"),
            "lsi":        ("lsi",          "sum"),
            "asp":        ("asp",          "sum"),
            "student":    ("student",      "sum"),
            "pensioners": ("pensioners",   "sum"),
            "ip":         ("ip",           "sum"),
            "kandas":     ("kandas",       "sum"),
            "district":   ("rainame",      lambda x: _clean_district(x.mode()[0]) if len(x) else ""),
        })

    grp = (
        found
        .groupby(["lat", "lon"])
        .agg(**agg_dict)
        .reset_index()
    )

    points = []
    for r in grp.itertuples():
        p: dict = {
            "lat":    round(float(r.lat), 7),
            "lon":    round(float(r.lon), 7),
            "n":      int(r.n),
            "conf":   r.confidence,
            "street": str(r.street or ""),
            "house":  str(r.house or ""),
        }
        if has_attrs:
            p.update({
                "male":       int(r.male),
                "female":     int(r.female),
                "age_avg":    float(r.age_avg) if r.age_avg else 0,
                "trud":       int(r.trud),
                "deti":       int(r.deti),
                "working":    int(r.working),
                "lsi":        int(r.lsi),
                "asp":        int(r.asp),
                "student":    int(r.student),
                "pensioners": int(r.pensioners),
                "ip":         int(r.ip),
                "kandas":     int(r.kandas),
                "district":   str(r.district) if r.district else "",
            })
        points.append(p)
    return points


def build_html(df: pd.DataFrame) -> str:
    points = _build_points(df)
    found  = int(df["lat"].notna().sum())
    total  = len(df)
    stats  = {
        "total":  total,
        "found":  found,
        "pct":    round(found / total * 100, 1) if total else 0,
        "houses": len(points),
    }

    html = _TEMPLATE
    html = html.replace("__POINTS__", json.dumps(points, ensure_ascii=False))
    html = html.replace("__STATS__",  json.dumps(stats,  ensure_ascii=False))
    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--input",  "input_path",  required=True,
              type=click.Path(exists=True), help="Parquet-файл с результатами")
@click.option("--output", "output_path", default=None,
              type=click.Path(), help="Куда сохранить HTML")
@click.option("--open/--no-open", "open_browser", default=True,
              help="Открыть карту в браузере")
def main(input_path: str, output_path: str | None, open_browser: bool) -> None:
    """Генерирует интерактивную карту из результатов геокодирования."""
    src = Path(input_path)
    if output_path is None:
        reports_dir = src.parent.parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        output_path = str(reports_dir / f"{src.stem}_map.html")

    click.echo(f"Читаем {src} ...")
    df = pd.read_parquet(src)
    click.echo(f"  Записей: {len(df):,}")

    click.echo("Строим карту ...")
    html = build_html(df)
    Path(output_path).write_text(html, encoding="utf-8")

    click.echo(f"Карта сохранена: {output_path}")
    if open_browser:
        webbrowser.open(Path(output_path).resolve().as_uri())


if __name__ == "__main__":
    main()
