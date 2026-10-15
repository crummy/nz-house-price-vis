#!/usr/bin/env python3
"""Build the static house-price dataset from official source files.

The script uses only the Python standard library. By default, it downloads the
latest MCERT workbook and the BIS New Zealand nominal and real price series.
Local source files can be supplied for repeatable or offline builds.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


HUD_ABOUT_URL = (
    "https://www.hud.govt.nz/stats-and-insights/"
    "property-and-sales-statistics/about-the-dashboard"
)
BIS_REAL_URL = (
    "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_SPP/1.0/"
    "Q.NZ.R.628?format=csv&labels=both"
)
BIS_NOMINAL_URL = (
    "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_SPP/1.0/"
    "Q.NZ.N.628?format=csv&labels=both"
)
USER_AGENT = "house-price-vis/1.0 (static data build)"
XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def latest_hud_workbook_url() -> str:
    page = fetch_bytes(HUD_ABOUT_URL).decode("utf-8")
    matches = re.findall(
        r'href=["\']([^"\']*Property-and-Sales[^"\']*\.xlsx[^"\']*)["\']',
        page,
        flags=re.IGNORECASE,
    )
    if not matches:
        raise RuntimeError("Could not find the MCERT property workbook link")
    return urllib.parse.urljoin(HUD_ABOUT_URL, html.unescape(matches[0]))


def read_source(path: str | None, url: str) -> tuple[bytes, str]:
    if path:
        return Path(path).read_bytes(), url
    return fetch_bytes(url), url


def column_index(cell_reference: str) -> int:
    letters = "".join(character for character in cell_reference if character.isalpha())
    result = 0
    for character in letters:
        result = result * 26 + ord(character.upper()) - 64
    return result - 1


def shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{XML_NS}t"))
        for item in root.findall(f"{XML_NS}si")
    ]


def parse_hud_hpi(workbook_bytes: bytes) -> dict[tuple[str, str], dict[int, float]]:
    observations: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as workbook:
        strings = shared_strings(workbook)
        with workbook.open("xl/worksheets/sheet4.xml") as sheet:
            for _, row in ET.iterparse(sheet, events=("end",)):
                if row.tag != f"{XML_NS}row":
                    continue

                values = [""] * 8
                for cell in row.findall(f"{XML_NS}c"):
                    index = column_index(cell.attrib.get("r", "A1"))
                    if index >= len(values):
                        continue
                    value_node = cell.find(f"{XML_NS}v")
                    value = "" if value_node is None else value_node.text or ""
                    if cell.attrib.get("t") == "s" and value:
                        value = strings[int(value)]
                    values[index] = value

                stat_type, area_type, area, period, quartile, _, hpi, _ = values
                if (
                    stat_type == "Monthly"
                    and area_type
                    in {"National", "Region", "Territorial Authority", "Auckland Local Board"}
                    and quartile == "All"
                    and period
                    and hpi
                ):
                    date = datetime(1899, 12, 30) + timedelta(days=float(period))
                    if date.month == 12 and date.year >= 1980:
                        observations[(area_type, area)][date.year] = float(hpi)
                row.clear()
    return observations


def parse_bis_csv(contents: bytes) -> dict[str, float]:
    text = contents.decode("utf-8-sig")
    return {
        row["TIME_PERIOD"]: float(row["OBS_VALUE"])
        for row in csv.DictReader(io.StringIO(text))
        if row.get("TIME_PERIOD") and row.get("OBS_VALUE")
    }


def round_value(value: float) -> float:
    return round(value, 5)


def build_dataset(
    hud_bytes: bytes,
    bis_real_bytes: bytes,
    bis_nominal_bytes: bytes,
    hud_source_url: str,
) -> dict:
    hud = parse_hud_hpi(hud_bytes)
    bis_real = parse_bis_csv(bis_real_bytes)
    bis_nominal = parse_bis_csv(bis_nominal_bytes)

    q4_years = sorted(
        int(period[:4])
        for period in set(bis_real) & set(bis_nominal)
        if period.endswith("Q4")
    )
    hud_years = sorted(year for values in hud.values() for year in values)
    latest_complete_year = min(max(q4_years), max(hud_years))

    cpi_factor = {
        year: bis_nominal[f"{year}-Q4"] / bis_real[f"{year}-Q4"]
        for year in q4_years
        if year <= latest_complete_year
    }

    group_order = {
        "National": 0,
        "Region": 1,
        "Territorial Authority": 2,
        "Auckland Local Board": 3,
    }
    locations = []
    for (area_type, area), values in sorted(
        hud.items(), key=lambda item: (group_order[item[0][0]], item[0][1])
    ):
        points = []
        for year, nominal_index in sorted(values.items()):
            if year > latest_complete_year or year not in cpi_factor:
                continue
            points.append(
                [
                    year,
                    round_value(nominal_index),
                    round_value(nominal_index / cpi_factor[year]),
                ]
            )
        if len(points) >= 10:
            locations.append(
                {"name": area, "group": area_type, "points": points}
            )

    extended_points = []
    for year in q4_years:
        if year > latest_complete_year:
            continue
        extended_points.append(
            [
                year,
                round_value(bis_nominal[f"{year}-Q4"]),
                round_value(bis_real[f"{year}-Q4"]),
            ]
        )

    return {
        "metadata": {
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "latestCompleteYear": latest_complete_year,
            "defaultStartYear": 1980,
            "pointFormat": ["year", "nominalIndex", "realIndex"],
            "sources": {
                "hud": hud_source_url,
                "bisReal": BIS_REAL_URL,
                "bisNominal": BIS_NOMINAL_URL,
            },
            "method": (
                "December house-price index observations are deflated with the CPI "
                "factor implied by the BIS nominal and real New Zealand series."
            ),
        },
        "locations": locations,
        "nationalExtended": {
            "name": "New Zealand — extended series",
            "group": "National",
            "points": extended_points,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hud-file")
    parser.add_argument("--hud-url")
    parser.add_argument("--bis-real-file")
    parser.add_argument("--bis-nominal-file")
    parser.add_argument(
        "--output",
        default="src/data/housing.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    hud_url = args.hud_url or latest_hud_workbook_url()
    hud_bytes, _ = read_source(args.hud_file, hud_url)
    real_bytes, _ = read_source(args.bis_real_file, BIS_REAL_URL)
    nominal_bytes, _ = read_source(args.bis_nominal_file, BIS_NOMINAL_URL)
    dataset = build_dataset(hud_bytes, real_bytes, nominal_bytes, hud_url)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dataset, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {output} with {len(dataset['locations'])} locations "
        f"through {dataset['metadata']['latestCompleteYear']}"
    )


if __name__ == "__main__":
    main()
