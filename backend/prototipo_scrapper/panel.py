#!/usr/bin/env python3
"""
Panel local para revisar los CSV generados por scrapper.py.

Uso:
  python panel.py

Luego abre:
  http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


OUTPUT_DIR = Path("output")
DEFAULT_PORT = 8765


def infer_delimiter(header: str) -> str:
    return ";" if header.count(";") >= header.count(",") else ","


def infer_operation(row: dict[str, str]) -> str:
    operation = (row.get("operacion") or "").strip()
    if operation:
        return operation

    url = (row.get("url") or "").lower()
    if "/alquiler/" in url:
        return "alquiler"
    if "/venta/" in url:
        return "venta"
    return ""


def normalize_row(row: dict[str, str], source_file: Path) -> dict[str, str]:
    normalized = {key: (value or "").strip() for key, value in row.items() if key}
    normalized["operacion"] = infer_operation(normalized)
    normalized["snapshot_archivo"] = source_file.name
    normalized["snapshot_modificado"] = datetime.fromtimestamp(
        source_file.stat().st_mtime, timezone.utc
    ).isoformat()
    return normalized


def row_sort_key(row: dict[str, str]) -> str:
    return row.get("fecha_extraccion") or row.get("snapshot_modificado") or ""


def load_properties() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    OUTPUT_DIR.mkdir(exist_ok=True)
    latest_by_key: dict[str, dict[str, str]] = {}
    files: list[dict[str, str]] = []

    for path in sorted(OUTPUT_DIR.glob("*.csv")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        if not lines:
            continue

        delimiter = infer_delimiter(lines[0])
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        row_count = 0

        for raw_row in reader:
            row_count += 1
            row = normalize_row(raw_row, path)
            key = f"{row.get('operacion')}:{row.get('id_propiedad') or row.get('url')}"
            previous = latest_by_key.get(key)
            if previous is None or row_sort_key(row) >= row_sort_key(previous):
                latest_by_key[key] = row

        files.append(
            {
                "name": path.name,
                "rows": str(row_count),
                "modified": datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )

    rows = sorted(
        latest_by_key.values(),
        key=lambda row: row_sort_key(row),
        reverse=True,
    )
    return rows, files


DASHBOARD_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Panel Propi</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --surface: #ffffff;
      --line: #d9dee7;
      --line-strong: #b9c0cc;
      --text: #17202c;
      --muted: #667085;
      --accent: #0f766e;
      --accent-soft: #dff4f1;
      --danger: #a34124;
      --warning: #8a6d1d;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, "Segoe UI", Arial, sans-serif;
      font-size: 14px;
    }

    header {
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      padding: 14px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      position: sticky;
      top: 0;
      z-index: 20;
    }

    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }

    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    button,
    .link-button {
      border: 1px solid var(--line-strong);
      background: var(--surface);
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
      font: inherit;
      text-decoration: none;
      line-height: 1;
      white-space: nowrap;
    }

    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }

    button:hover,
    .link-button:hover {
      border-color: var(--accent);
    }

    main {
      padding: 16px 18px 24px;
      display: grid;
      gap: 14px;
    }

    .filters {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 12px;
      display: grid;
      grid-template-columns: minmax(220px, 1.6fr) repeat(4, minmax(130px, 1fr));
      gap: 10px;
    }

    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }

    input,
    select {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      background: #ffffff;
      color: var(--text);
      padding: 7px 9px;
      font: inherit;
    }

    .range {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      gap: 10px;
    }

    .metric {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 10px 12px;
      min-height: 64px;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }

    .metric strong {
      display: block;
      margin-top: 6px;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }

    .table-shell {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .table-status {
      min-height: 40px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 8px 12px;
      color: var(--muted);
      font-size: 13px;
    }

    .table-wrap {
      overflow: auto;
      max-height: calc(100vh - 300px);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 1180px;
    }

    th,
    td {
      border-bottom: 1px solid var(--line);
      padding: 9px 10px;
      text-align: left;
      vertical-align: top;
    }

    th {
      position: sticky;
      top: 0;
      z-index: 5;
      background: #eef2f6;
      color: #344054;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
      cursor: pointer;
    }

    tr:hover td {
      background: #f8fafc;
    }

    td.num {
      text-align: right;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }

    td.title {
      min-width: 260px;
      max-width: 360px;
      font-weight: 600;
    }

    td.address {
      min-width: 190px;
      max-width: 280px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 8px;
      background: var(--accent-soft);
      color: #115e59;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }

    .pill.rent {
      background: #fff1d6;
      color: var(--warning);
    }

    .pill.inactive {
      background: #fce7df;
      color: var(--danger);
    }

    .empty {
      padding: 30px;
      text-align: center;
      color: var(--muted);
    }

    @media (max-width: 980px) {
      header {
        align-items: flex-start;
        flex-direction: column;
      }

      .toolbar {
        justify-content: flex-start;
      }

      .filters {
        grid-template-columns: 1fr 1fr;
      }

      .metrics {
        grid-template-columns: 1fr 1fr;
      }
    }

    @media (max-width: 620px) {
      main {
        padding: 12px;
      }

      .filters,
      .metrics {
        grid-template-columns: 1fr;
      }

      .table-wrap {
        max-height: none;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>Panel Propi</h1>
    <div class="toolbar">
      <button id="refreshBtn" class="primary">Actualizar</button>
      <button id="clearBtn">Limpiar</button>
      <button id="downloadBtn">Descargar CSV</button>
    </div>
  </header>

  <main>
    <section class="filters" aria-label="Filtros">
      <label>
        Buscar
        <input id="searchInput" type="search" placeholder="Título, dirección o URL">
      </label>
      <label>
        Operación
        <select id="operationFilter"></select>
      </label>
      <label>
        Tipo
        <select id="typeFilter"></select>
      </label>
      <label>
        Estado
        <select id="statusFilter"></select>
      </label>
      <label>
        Estado interno
        <select id="internalStatusFilter"></select>
      </label>
      <label>
        Precio
        <span class="range">
          <input id="priceMin" type="number" min="0" placeholder="Mín">
          <input id="priceMax" type="number" min="0" placeholder="Máx">
        </span>
      </label>
      <label>
        Habitaciones mín.
        <input id="bedroomsMin" type="number" min="0" step="1">
      </label>
      <label>
        Baños mín.
        <input id="bathroomsMin" type="number" min="0" step="0.5">
      </label>
      <label>
        Parqueos mín.
        <input id="parkingsMin" type="number" min="0" step="1">
      </label>
      <label>
        Área mín. m²
        <input id="areaMin" type="number" min="0" step="1">
      </label>
    </section>

    <section class="metrics" aria-label="Resumen">
      <div class="metric"><span>Propiedades</span><strong id="metricCount">0</strong></div>
      <div class="metric"><span>Venta</span><strong id="metricSale">0</strong></div>
      <div class="metric"><span>Alquiler</span><strong id="metricRent">0</strong></div>
      <div class="metric"><span>Precio promedio</span><strong id="metricAvg">$0</strong></div>
      <div class="metric"><span>Precio mediano</span><strong id="metricMedian">$0</strong></div>
    </section>

    <section class="table-shell">
      <div class="table-status">
        <span id="statusText">Cargando...</span>
        <span id="filesText"></span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th data-sort="operacion">Operación</th>
              <th data-sort="tipo">Tipo</th>
              <th data-sort="titulo">Título</th>
              <th data-sort="precio">Precio</th>
              <th data-sort="estado">Estado</th>
              <th data-sort="estado_interno">Estado interno</th>
              <th data-sort="direccion">Dirección</th>
              <th data-sort="habitaciones">Hab.</th>
              <th data-sort="banos">Baños</th>
              <th data-sort="parqueos">Parq.</th>
              <th data-sort="area_m2">Área</th>
              <th data-sort="fecha_extraccion">Extracción</th>
              <th>URL</th>
            </tr>
          </thead>
          <tbody id="rowsBody"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    const state = {
      rows: [],
      files: [],
      filtered: [],
      sortKey: "fecha_extraccion",
      sortDir: "desc"
    };

    const controls = {
      search: document.getElementById("searchInput"),
      operation: document.getElementById("operationFilter"),
      type: document.getElementById("typeFilter"),
      status: document.getElementById("statusFilter"),
      internalStatus: document.getElementById("internalStatusFilter"),
      priceMin: document.getElementById("priceMin"),
      priceMax: document.getElementById("priceMax"),
      bedroomsMin: document.getElementById("bedroomsMin"),
      bathroomsMin: document.getElementById("bathroomsMin"),
      parkingsMin: document.getElementById("parkingsMin"),
      areaMin: document.getElementById("areaMin")
    };

    const money = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0
    });

    function toNumber(value) {
      if (value === null || value === undefined || value === "") return null;
      const parsed = Number(String(value).replace(/,/g, ""));
      return Number.isFinite(parsed) ? parsed : null;
    }

    function formatMoney(value) {
      const number = toNumber(value);
      return number === null ? "" : money.format(number);
    }

    function formatDate(value) {
      if (!value) return "";
      const number = Number(value);
      if (Number.isFinite(number) && String(value).length >= 12) {
        return new Date(number).toLocaleDateString("es-SV");
      }
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString("es-SV");
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function uniqueValues(key) {
      return [...new Set(state.rows.map(row => row[key]).filter(Boolean))]
        .sort((a, b) => String(a).localeCompare(String(b), "es"));
    }

    function fillSelect(select, values, label) {
      const current = select.value;
      select.innerHTML = `<option value="">${label}</option>` +
        values.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
      select.value = values.includes(current) ? current : "";
    }

    function hydrateFilters() {
      fillSelect(controls.operation, uniqueValues("operacion"), "Todas");
      fillSelect(controls.type, uniqueValues("tipo"), "Todos");
      fillSelect(controls.status, uniqueValues("estado"), "Todos");
      fillSelect(controls.internalStatus, uniqueValues("estado_interno"), "Todos");
    }

    function passesNumericMin(row, key, control) {
      const min = toNumber(control.value);
      if (min === null) return true;
      const value = toNumber(row[key]);
      return value !== null && value >= min;
    }

    function applyFilters() {
      const query = controls.search.value.trim().toLowerCase();
      const operation = controls.operation.value;
      const type = controls.type.value;
      const status = controls.status.value;
      const internalStatus = controls.internalStatus.value;
      const priceMin = toNumber(controls.priceMin.value);
      const priceMax = toNumber(controls.priceMax.value);

      state.filtered = state.rows.filter(row => {
        const haystack = [row.titulo, row.direccion, row.url, row.estado, row.estado_interno]
          .join(" ")
          .toLowerCase();
        const price = toNumber(row.precio);

        if (query && !haystack.includes(query)) return false;
        if (operation && row.operacion !== operation) return false;
        if (type && row.tipo !== type) return false;
        if (status && row.estado !== status) return false;
        if (internalStatus && row.estado_interno !== internalStatus) return false;
        if (priceMin !== null && (price === null || price < priceMin)) return false;
        if (priceMax !== null && (price === null || price > priceMax)) return false;
        if (!passesNumericMin(row, "habitaciones", controls.bedroomsMin)) return false;
        if (!passesNumericMin(row, "banos", controls.bathroomsMin)) return false;
        if (!passesNumericMin(row, "parqueos", controls.parkingsMin)) return false;
        if (!passesNumericMin(row, "area_m2", controls.areaMin)) return false;
        return true;
      });

      sortRows();
      render();
    }

    function sortRows() {
      const key = state.sortKey;
      const direction = state.sortDir === "asc" ? 1 : -1;
      state.filtered.sort((a, b) => {
        const av = toNumber(a[key]);
        const bv = toNumber(b[key]);
        if (av !== null || bv !== null) {
          return ((av ?? -Infinity) - (bv ?? -Infinity)) * direction;
        }
        return String(a[key] ?? "").localeCompare(String(b[key] ?? ""), "es") * direction;
      });
    }

    function updateMetrics() {
      const prices = state.filtered.map(row => toNumber(row.precio)).filter(value => value !== null);
      const sum = prices.reduce((total, value) => total + value, 0);
      const sorted = [...prices].sort((a, b) => a - b);
      const median = sorted.length
        ? sorted[Math.floor((sorted.length - 1) / 2)]
        : 0;

      document.getElementById("metricCount").textContent = state.filtered.length.toLocaleString("es-SV");
      document.getElementById("metricSale").textContent =
        state.filtered.filter(row => row.operacion === "venta").length.toLocaleString("es-SV");
      document.getElementById("metricRent").textContent =
        state.filtered.filter(row => row.operacion === "alquiler").length.toLocaleString("es-SV");
      document.getElementById("metricAvg").textContent =
        prices.length ? money.format(sum / prices.length) : "$0";
      document.getElementById("metricMedian").textContent =
        prices.length ? money.format(median) : "$0";
    }

    function renderRows() {
      const tbody = document.getElementById("rowsBody");
      if (!state.filtered.length) {
        tbody.innerHTML = `<tr><td class="empty" colspan="13">No hay datos para mostrar</td></tr>`;
        return;
      }

      tbody.innerHTML = state.filtered.map(row => {
        const operationClass = row.operacion === "alquiler" ? "rent" : "";
        const inactiveClass = /rented|taken|disabled|inactive|inactivo/i.test(row.estado || "")
          ? "inactive"
          : "";
        const statusClass = inactiveClass ? " inactive" : "";
        const url = escapeHtml(row.url || "");

        return `<tr>
          <td><span class="pill ${operationClass}">${escapeHtml(row.operacion)}</span></td>
          <td>${escapeHtml(row.tipo)}</td>
          <td class="title">${escapeHtml(row.titulo)}</td>
          <td class="num">${formatMoney(row.precio)}</td>
          <td><span class="pill${statusClass}">${escapeHtml(row.estado)}</span></td>
          <td>${escapeHtml(row.estado_interno)}</td>
          <td class="address">${escapeHtml(row.direccion)}</td>
          <td class="num">${escapeHtml(row.habitaciones)}</td>
          <td class="num">${escapeHtml(row.banos)}</td>
          <td class="num">${escapeHtml(row.parqueos)}</td>
          <td class="num">${escapeHtml(row.area_m2)}</td>
          <td>${escapeHtml(formatDate(row.fecha_extraccion))}</td>
          <td>${url ? `<a class="link-button" href="${url}" target="_blank" rel="noreferrer">Abrir</a>` : ""}</td>
        </tr>`;
      }).join("");
    }

    function render() {
      updateMetrics();
      renderRows();
      document.getElementById("statusText").textContent =
        `${state.filtered.length.toLocaleString("es-SV")} de ${state.rows.length.toLocaleString("es-SV")} propiedades`;
      document.getElementById("filesText").textContent =
        `${state.files.length.toLocaleString("es-SV")} archivo(s) CSV`;
    }

    function clearFilters() {
      Object.values(controls).forEach(control => {
        control.value = "";
      });
      applyFilters();
    }

    function downloadFilteredCsv() {
      const columns = [
        "id_propiedad", "operacion", "tipo", "titulo", "precio", "estado", "estado_interno",
        "direccion", "habitaciones", "banos", "parqueos", "area_m2",
        "url", "publicado_en", "fecha_extraccion", "snapshot_archivo"
      ];
      const csv = [
        columns.join(";"),
        ...state.filtered.map(row => columns
          .map(column => `"${String(row[column] ?? "").replaceAll('"', '""')}"`)
          .join(";"))
      ].join("\n");

      const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "propi_filtrado.csv";
      link.click();
      URL.revokeObjectURL(url);
    }

    async function loadData() {
      document.getElementById("statusText").textContent = "Cargando...";
      const response = await fetch("/api/properties");
      const payload = await response.json();
      state.rows = payload.rows || [];
      state.files = payload.files || [];
      hydrateFilters();
      applyFilters();
    }

    Object.values(controls).forEach(control => {
      control.addEventListener("input", applyFilters);
      control.addEventListener("change", applyFilters);
    });

    document.querySelectorAll("th[data-sort]").forEach(th => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
          state.sortKey = key;
          state.sortDir = "asc";
        }
        applyFilters();
      });
    });

    document.getElementById("refreshBtn").addEventListener("click", loadData);
    document.getElementById("clearBtn").addEventListener("click", clearFilters);
    document.getElementById("downloadBtn").addEventListener("click", downloadFilteredCsv);

    loadData().catch(error => {
      document.getElementById("statusText").textContent = `Error: ${error.message}`;
    });
  </script>
</body>
</html>
"""


class PanelHandler(BaseHTTPRequestHandler):
    def send_bytes(self, body: bytes, status: int = 200, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path in {"/", "/index.html"}:
            self.send_bytes(DASHBOARD_HTML.encode("utf-8"), content_type="text/html; charset=utf-8")
            return

        if path == "/api/properties":
            rows, files = load_properties()
            body = json.dumps(
                {"rows": rows, "files": files},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_bytes(body, content_type="application/json; charset=utf-8")
            return

        content_type = mimetypes.guess_type(path)[0] or "text/plain"
        self.send_bytes(b"Not found", status=404, content_type=content_type)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Panel local para CSV de Propi.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), PanelHandler)
    print(f"[+] Panel disponible en http://{args.host}:{args.port}")
    print("[+] Presiona Ctrl+C para detenerlo.")
    server.serve_forever()


if __name__ == "__main__":
    main()
