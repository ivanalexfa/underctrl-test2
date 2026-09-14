#!/usr/bin/env python3
"""
Prototipo de extractor para Propilatam (propilatam.com).

Estrategia: Propi usa Next.js App Router. El HTML de cualquier página de
resultados incluye, embebido en tags <script>self.__next_f.push([1,"..."])
</script>, el mismo payload de datos (RSC flight) que normalmente se pediría
vía fetch con ?_rsc=. Esto significa que NO hace falta Playwright ni
simular búsquedas por API: basta un requests.get() a la URL de resultados
y parsear ese payload embebido para sacar el array "initialProperties",
que trae precio, estado, habitaciones, baños, parqueos, área y fecha de
publicación por cada propiedad, con un UUID estable como identificador.

LIMITACIONES CONOCIDAS (verificar antes de usar en producción):
  1. Solo se confirmó el slug de URL para VENTA ("comprar"). Para RENTA
     (el alcance real del proyecto) hay que abrir una búsqueda de alquiler
     en el navegador y copiar el slug real de la URL (probablemente algo
     como ".../alquilar/propiedades/<zona>" o similar) y ajustar
     MARKETPLACE_PATH_SEGMENT más abajo.
  2. No se ha confirmado paginación: esta carga inicial trajo ~90
     resultados (incluye recomendaciones de zonas cercanas cuando la
     búsqueda exacta tiene pocos resultados). Si Propi pagina resultados
     via scroll infinito, revisar el Network tab buscando llamadas
     adicionales (?page=2, ?offset=..., etc.) y extenderlo aquí.
  3. No probado en vivo contra propilatam.com (el entorno de desarrollo
     no tiene acceso a ese dominio) — ejecútalo tú y reporta cualquier
     error de parseo; el sitio puede cambiar su estructura sin aviso.
  4. Respeta el robots.txt del sitio (Allow: / en la raíz al momento de
     escribir esto) y añade pausas entre requests.
"""

from __future__ import annotations

import csv
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-SV,es;q=0.9,en;q=0.8",
}

BASE_URL = "https://www.propilatam.com"
COUNTRY = "sv"

# TODO: confirmar el segmento real para alquiler (ver limitación #1 arriba)
MARKETPLACE_PATH_SEGMENT = {
    "venta": "comprar",
    "alquiler": "alquilar",  # <- placeholder, verificar en el navegador
}

DETAIL_PATH_SEGMENT = {
    "venta": "venta",
    "alquiler": "alquiler",
}

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

REQUEST_DELAY_RANGE = (2.0, 4.0)  # segundos, pausa entre requests

CSV_FIELDNAMES = [
    "id_propiedad",
    "operacion",
    "tipo",
    "titulo",
    "precio",
    "estado",
    "estado_interno",
    "direccion",
    "habitaciones",
    "banos",
    "parqueos",
    "area_m2",
    "url",
    "publicado_en",
    "fecha_extraccion",
    "fuente_url",
]


def build_search_url(zona_slug: str, marketplace: str = "venta") -> str:
    """
    Construye la URL de búsqueda por zona.
    Ejemplo: build_search_url("colonia-escalon-norte", marketplace="venta")
    """
    segment = MARKETPLACE_PATH_SEGMENT[marketplace]
    return f"{BASE_URL}/{COUNTRY}/bienes-raices/{segment}/propiedades/{zona_slug}"


def build_marketplace_url(marketplace: str = "venta") -> str:
    """Construye la URL principal del marketplace para venta o alquiler."""
    segment = MARKETPLACE_PATH_SEGMENT[marketplace]
    return f"{BASE_URL}/{COUNTRY}/bienes-raices/{segment}"


def build_detail_url(item: dict, marketplace: str = "venta", is_project: bool = False) -> str:
    """Construye la URL canónica de detalle usando el slug y código del payload."""
    detail = str(item.get("url_detail") or "").strip("/")
    if not detail:
        return ""

    if is_project:
        code = item.get("project_code") or item.get("code")
        path = f"{detail}/{code}" if code else detail
        return f"{BASE_URL}/{COUNTRY}/venta/{path}"

    code = item.get("code")
    if marketplace == "venta":
        parts = detail.split("/")
        if len(parts) >= 2 and parts[1] != "propiedad":
            detail = "/".join([parts[0], "propiedad", *parts[1:]])

    detail_segment = DETAIL_PATH_SEGMENT[marketplace]
    path = f"{detail}/{code}" if code else detail
    return f"{BASE_URL}/{COUNTRY}/{detail_segment}/{path}"


def get_public_status(item: dict, is_project: bool = False) -> str | None:
    """
    Devuelve el estado que refleja la disponibilidad pública.

    En Propilatam, item.status puede ser un estado interno del flujo operativo
    (por ejemplo "taken") aunque la publicación siga disponible para oferta.
    Para propiedades, sale_status es el mejor indicador comercial cuando existe.
    """
    if is_project:
        return item.get("sale_status") or item.get("project_status") or item.get("work_progress")
    return item.get("sale_status") or item.get("status")


# ---------------------------------------------------------------------------
# Extracción del payload RSC embebido en el HTML
# ---------------------------------------------------------------------------

NEXT_F_PATTERN = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)
INITIAL_PROPERTIES_PATTERN = re.compile(
    r'"initialProperties":(\[.*?\])\s*\}\s*\]', re.DOTALL
)


def fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def extract_rsc_text(html: str) -> str:
    """Concatena y desescapa todos los chunks self.__next_f.push del HTML."""
    chunks = NEXT_F_PATTERN.findall(html)
    if not chunks:
        raise ValueError(
            "No se encontraron chunks self.__next_f.push en el HTML. "
            "El sitio pudo haber cambiado de estructura; revisa manualmente "
            "con 'Ver código fuente' en el navegador."
        )
    decoded = []
    for chunk in chunks:
        try:
            decoded.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError as exc:
            raise ValueError(f"No se pudo decodificar un chunk RSC: {exc}") from exc
    return "".join(decoded)


def extract_initial_properties(rsc_text: str) -> list[dict]:
    marker = '"initialProperties":'
    marker_pos = rsc_text.find(marker)
    if marker_pos == -1:
        raise ValueError(
            "No se encontró 'initialProperties' en el payload. Puede que "
            "la búsqueda no tenga resultados, o que el formato cambió."
        )

    array_start = rsc_text.find("[", marker_pos + len(marker))
    if array_start == -1:
        raise ValueError(
            "Se encontró 'initialProperties', pero no se encontró el array JSON."
        )

    try:
        properties, _ = json.JSONDecoder().raw_decode(rsc_text[array_start:])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"No se pudo parsear el array 'initialProperties': {exc}"
        ) from exc

    if not isinstance(properties, list):
        raise ValueError(
            f"'initialProperties' no es una lista JSON: {type(properties).__name__}"
        )

    return properties


# ---------------------------------------------------------------------------
# Normalización al esquema del proyecto (ver doc original)
# ---------------------------------------------------------------------------

def normalize_property(
    raw: dict, marketplace: str = "venta", source_url: str | None = None
) -> dict:
    """
    Convierte un elemento de initialProperties al esquema plano que
    necesita la base histórica: ID_PROPIEDAD, precio, estado, etc.
    """
    item = raw.get("item", {})
    is_project = raw.get("type") == "project"  # preventas tienen otro shape
    detail_url = build_detail_url(item, marketplace=marketplace, is_project=is_project)
    public_status = get_public_status(item, is_project=is_project)
    internal_status = item.get("status") or item.get("work_progress")
    extracted_at = datetime.now(timezone.utc).isoformat()

    if is_project:
        return {
            "id_propiedad": raw.get("id"),
            "operacion": marketplace,
            "tipo": "proyecto_preventa",
            "titulo": item.get("project_name"),
            "precio": item.get("price_value"),
            "estado": public_status,
            "estado_interno": internal_status,
            "direccion": item.get("project_address"),
            "habitaciones": item.get("min_bedrooms"),
            "banos": item.get("min_bathrooms"),
            "parqueos": None,
            "area_m2": item.get("min_area"),
            "url": detail_url,
            "publicado_en": None,
            "fecha_extraccion": extracted_at,
            "fuente_url": source_url,
        }

    return {
        "id_propiedad": raw.get("id"),
        "operacion": marketplace,
        "tipo": "propiedad",
        "titulo": item.get("name"),
        "precio": item.get("price_value"),
        "estado": public_status,
        "estado_interno": internal_status,
        "direccion": item.get("address"),
        "habitaciones": item.get("bedrooms"),
        "banos": item.get("bathrooms"),
        "parqueos": item.get("parkings"),
        "area_m2": item.get("area"),
        "url": detail_url,
        "publicado_en": item.get("published_at"),
        "fecha_extraccion": extracted_at,
        "fuente_url": source_url,
    }


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def scrape_zone(zona_slug: str, marketplace: str = "venta") -> list[dict]:
    url = build_search_url(zona_slug, marketplace=marketplace)
    print(f"[+] Descargando: {url}")
    html = fetch_html(url)

    rsc_text = extract_rsc_text(html)
    raw_properties = extract_initial_properties(rsc_text)
    print(f"[+] {len(raw_properties)} elementos encontrados en initialProperties")

    return [normalize_property(p, marketplace=marketplace, source_url=url) for p in raw_properties]


def scrape_marketplace(marketplace: str = "venta") -> list[dict]:
    url = build_marketplace_url(marketplace=marketplace)
    print(f"[+] Descargando catálogo {marketplace}: {url}")
    html = fetch_html(url)

    rsc_text = extract_rsc_text(html)
    raw_properties = extract_initial_properties(rsc_text)
    print(f"[+] {len(raw_properties)} elementos encontrados en catálogo {marketplace}")

    return [normalize_property(p, marketplace=marketplace, source_url=url) for p in raw_properties]


def scrape_catalog(marketplaces: tuple[str, ...] = ("venta", "alquiler")) -> list[dict]:
    properties_by_key: dict[str, dict] = {}

    for marketplace in marketplaces:
        for prop in scrape_marketplace(marketplace=marketplace):
            unique_id = prop.get("id_propiedad") or prop.get("url")
            key = f"{marketplace}:{unique_id}"
            properties_by_key[key] = prop

    properties = list(properties_by_key.values())
    print(f"[+] Total catálogo deduplicado: {len(properties)} propiedades")
    return properties


def save_snapshot(properties: list[dict], zona_slug: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"{zona_slug}_{timestamp}.csv"

    if not properties:
        print("[!] No hay propiedades para guardar.")
        return out_path

    fieldnames = CSV_FIELDNAMES
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(properties)

    print(f"[+] Guardado: {out_path} ({len(properties)} filas)")
    return out_path


if __name__ == "__main__":
    try:
        props = scrape_catalog()
        save_snapshot(props, "catalogo_sv")
    except Exception as exc:
        print(f"[ERROR] Falló el escaneo del catálogo: {exc}")
