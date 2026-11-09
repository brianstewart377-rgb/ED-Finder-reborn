"""Import facility template data from DaftMav's colonisation workbook.

The importer intentionally uses only the Python standard library so it can run
on a clean Windows install without adding spreadsheet dependencies.  The XLSX
file is read as OpenXML zipped XML and the Stats sheet is converted into the
runtime catalogue shape consumed by ``domain.facilities``.
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET  # nosemgrep: use-defused-xml


NS = {
    'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'rel': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'pkgrel': 'http://schemas.openxmlformats.org/package/2006/relationships',
}

SOURCE_NAME = 'Copy of Colonization Construction v3 (By DaftMav).xlsx'
STATS_SHEET = 'Stats'

HEADERS = {
    'A': 'structure',
    'B': 'max_pad',
    'C': 'prerequisites',
    'D': 'system_unlock',
    'E': 'strong_link_unlock',
    'F': 't2_cp_effect',
    'G': 't3_cp_effect',
    'I': 'security',
    'J': 'tech_level',
    'K': 'wealth',
    'L': 'standard_of_living',
    'M': 'development_level',
    'N': 'facility_economy',
}

ID_ALIASES = {
    'Orbital - Starport - Coriolis': 'coriolis_station',
    'Orbital - Starport - Asteroid Base': 'asteroid_base',
    'Orbital - Starport - Ocellus': 'ocellus_station',
    'Orbital - Starport - Orbis': 'orbis_t3',
    'Orbital - Starport - Dodecahedron': 'dodecahedron_t3',
    'Surface - Planetary Port - Port': 'planetary_port',
    'Surface - Hub - Refinery': 'refinery',
    'Surface - Hub - Industrial': 'industrial_facility',
    'Surface - Hub - Extraction': 'extraction_facility',
    'Surface - Hub - Military': 'military_installation',
    'Surface - Hub - High Tech': 'hightech_research',
    'Orbital - Installation - Tourist': 'tourism_installation',
}

ECONOMY_NORMALISATION = {
    'Agricultural': 'Agriculture',
    'High Tech': 'HighTech',
    'Extraction': 'Extraction',
    'Industrial': 'Industrial',
    'Refinery': 'Refinery',
    'Military': 'Military',
    'Tourism': 'Tourism',
    'Colony': 'Colony',
    'Contraband': 'Contraband',
}

PORT_FAMILIES = {'Starport', 'Outpost', 'Planetary Port'}


@dataclass(frozen=True)
class CellRow:
    index: int
    values: dict[str, Any]


def import_catalogue(workbook_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(workbook_path) as workbook:
        shared_strings = _read_shared_strings(workbook)
        sheet_paths = _sheet_paths(workbook)
        rows = _read_sheet(workbook, sheet_paths[STATS_SHEET], shared_strings)

    templates = [
        _row_to_template(row)
        for row in rows
        if row.index > 1 and row.values.get('A')
    ]
    templates.extend(_bootstrap_templates())
    templates.sort(key=lambda item: (item['allowed_location'], item['category'], item['tier'], item['name']))

    return {
        'catalogue_version': 1,
        'source': {
            'name': SOURCE_NAME,
            'sheet': STATS_SHEET,
            'importer': 'apps/importer/src/import_facility_templates_from_daftmav.py',
        },
        'templates': templates,
    }


def _bootstrap_templates() -> list[dict[str, Any]]:
    """Small app bootstrap entries not represented as rows on the Stats sheet."""
    return [
        {
            'id': 'colony_ship',
            'name': 'Colony Ship',
            'source_structure': 'ED Finder bootstrap - Colony Ship',
            'category': 'port',
            'tier': 1,
            'economy': 'Colony',
            'is_port': True,
            'is_colony_port': True,
            'is_support_facility': False,
            'yellow_cp_generated': 0,
            'green_cp_generated': 0,
            'yellow_cp_cost': 0,
            'green_cp_cost': 0,
            'strong_link_value': 0.4,
            'weak_link_value': 0.05,
            'allowed_location': 'orbital',
            'pad_size': 'L',
            'prerequisites': [],
            'economy_effects': {'primary': 'Colony', 'facility_economy_raw': 'Colony', 'is_modifier_economy': False},
            'stat_effects': {
                'data_confidence': 'confirmed',
                'source': 'ED Finder bootstrap',
                'note': 'Initial colony placement used by the planner; DaftMav Stats starts with constructed structures.',
            },
        },
    ]


def _row_to_template(row: CellRow) -> dict[str, Any]:
    source = {name: _clean(row.values.get(col)) for col, name in HEADERS.items()}
    structure = str(source['structure'])
    parts = structure.split(' - ')
    location_family = parts[0] if parts else ''
    structure_family = parts[1] if len(parts) > 1 else ''
    display_name = ' - '.join(parts[2:]) if len(parts) > 2 else structure
    economy_raw = _clean(source.get('facility_economy'))
    economy = _normalise_economy(economy_raw) or _inferred_economy_from_structure(structure)
    is_port = structure_family in PORT_FAMILIES

    t2_effect = _maybe_number(source.get('t2_cp_effect'))
    t3_effect = _maybe_number(source.get('t3_cp_effect'))
    tier = _tier_from_cp_effects(structure, structure_family, t2_effect, t3_effect)
    yellow_generated = int(max(0, round(t2_effect or 0)))
    green_generated = int(max(0, round(t3_effect or 0)))
    yellow_cost = int(max(0, round(-(t2_effect or 0))))
    green_cost = int(max(0, round(-(t3_effect or 0))))

    return {
        'id': ID_ALIASES.get(structure, _slugify(structure)),
        'name': display_name,
        'source_structure': structure,
        'category': _category(structure_family, economy),
        'tier': tier,
        'economy': economy,
        'is_port': is_port,
        # Mega Guide v2.3.0 defines colony-type ports by their Colony economy.
        # Dodecahedron is not named there; Raven Colonial's src/site-data.ts
        # also lists its economy as colony.
        'is_colony_port': is_port and economy == 'Colony',
        'is_support_facility': not is_port,
        'yellow_cp_generated': yellow_generated,
        'green_cp_generated': green_generated,
        'yellow_cp_cost': yellow_cost,
        'green_cp_cost': green_cost,
        'strong_link_value': _strong_link_value(tier),
        'weak_link_value': 0.05,
        'allowed_location': _allowed_location(location_family, structure),
        'pad_size': _pad_size(source.get('max_pad')),
        'prerequisites': _rules(source.get('prerequisites'), 'Prerequisites'),
        'economy_effects': {
            'primary': economy,
            'facility_economy_raw': economy_raw,
            'is_modifier_economy': bool(economy_raw and economy_raw.startswith('*')),
        },
        'stat_effects': _stat_effects(row.index, source),
    }


def _stat_effects(row_index: int, source: dict[str, Any]) -> dict[str, Any]:
    effects: dict[str, Any] = {
        'data_confidence': 'observed',
        'source': 'DaftMav Colonization Construction v3',
        'source_sheet': STATS_SHEET,
        'source_row': row_index,
        'source_fields': source,
    }
    for key in ('t2_cp_effect', 't3_cp_effect', 'security', 'tech_level', 'wealth', 'standard_of_living', 'development_level'):
        number = _maybe_number(source.get(key))
        if number is not None:
            effects[key] = number
    unlocks = []
    for key, label in (('system_unlock', 'System Unlock'), ('strong_link_unlock', 'Strong Link Unlock')):
        value = _clean(source.get(key))
        if value:
            unlocks.append({'type': label, 'description': value})
    if unlocks:
        effects['unlocks'] = unlocks
    return effects


def _read_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(workbook.read('xl/sharedStrings.xml'))
    except KeyError:
        return []
    strings: list[str] = []
    for item in root.findall('main:si', NS):
        text_parts = [node.text or '' for node in item.findall('.//main:t', NS)]
        strings.append(''.join(text_parts))
    return strings


def _sheet_paths(workbook: zipfile.ZipFile) -> dict[str, str]:
    workbook_root = ET.fromstring(workbook.read('xl/workbook.xml'))
    rel_root = ET.fromstring(workbook.read('xl/_rels/workbook.xml.rels'))
    rels = {
        rel.attrib['Id']: rel.attrib['Target'].lstrip('/')
        for rel in rel_root.findall('pkgrel:Relationship', NS)
    }
    sheet_paths: dict[str, str] = {}
    for sheet in workbook_root.findall('main:sheets/main:sheet', NS):
        rel_id = sheet.attrib[f'{{{NS["rel"]}}}id']
        target = rels[rel_id]
        sheet_paths[sheet.attrib['name']] = target if target.startswith('xl/') else f'xl/{target}'
    return sheet_paths


def _read_sheet(workbook: zipfile.ZipFile, path: str, shared_strings: list[str]) -> list[CellRow]:
    root = ET.fromstring(workbook.read(path))
    rows: list[CellRow] = []
    for row in root.findall('main:sheetData/main:row', NS):
        values: dict[str, Any] = {}
        for cell in row.findall('main:c', NS):
            ref = cell.attrib.get('r', '')
            column = re.sub(r'\d+', '', ref)
            value = _cell_value(cell, shared_strings)
            if value not in (None, ''):
                values[column] = value
        rows.append(CellRow(index=int(row.attrib['r']), values=values))
    return rows


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get('t')
    if cell_type == 'inlineStr':
        return ''.join(node.text or '' for node in cell.findall('.//main:t', NS))
    value = cell.find('main:v', NS)
    if value is None or value.text is None:
        return None
    if cell_type == 's':
        return shared_strings[int(value.text)]
    return value.text


def _rules(value: Any, source_column: str) -> list[dict[str, str]]:
    text = _clean(value)
    if not text:
        return []
    return [{'source_column': source_column, 'description': text}]


def _normalise_economy(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    text = text.lstrip('*').strip()
    return ECONOMY_NORMALISATION.get(text, text)


def _inferred_economy_from_structure(structure: str) -> str | None:
    for label, economy in (
        ('Refinery', 'Refinery'),
        ('Industrial', 'Industrial'),
        ('Extraction', 'Extraction'),
        ('Military', 'Military'),
        ('High Tech', 'HighTech'),
        ('Tourism', 'Tourism'),
        ('Agriculture', 'Agriculture'),
    ):
        if f' - {label}' in structure or f' {label} ' in structure:
            return economy
    return None


def _tier_from_cp_effects(
    structure: str, family: str, t2_effect: float | None, t3_effect: float | None,
) -> int:
    """Tier follows the points spent, per Mega Guide v2.3.0 PDF p26."""
    # Research Bio/Tourism small and medium settlements cost T2 points: Raven
    # Colonial also calls them T2, despite their T1 names and the guide's p55
    # example calling small/medium settlements T1. Preserve the source CP data.
    if t3_effect is not None and t3_effect < 0:
        return 3
    if t2_effect is not None and t2_effect < 0:
        return 2
    if t2_effect is not None or t3_effect is not None:
        return 1

    # Retain the legacy name-based fallback only when both CP effects are absent.
    if ' T3' in structure or any(name in structure for name in ('Ocellus', 'Orbis', 'Dodecahedron')):
        return 3
    if ' T2' in structure or family in {'Starport', 'Outpost', 'Planetary Port'}:
        return 2
    return 1


def _category(family: str, economy: str | None) -> str:
    if family in PORT_FAMILIES:
        return 'port'
    if economy:
        return economy.lower()
    return family.lower().replace(' ', '_') or 'support'


def _allowed_location(location_family: str, structure: str) -> str:
    if 'Asteroid Base' in structure:
        return 'ringed_orbital'
    if location_family == 'Orbital':
        return 'orbital'
    if location_family == 'Surface':
        return 'surface'
    return 'orbital_or_surface'


def _pad_size(value: Any) -> str | None:
    text = _clean(value)
    if not text or text == '❌':
        return None
    return text


def _strong_link_value(tier: int) -> float:
    if tier == 3:
        return 1.2
    if tier == 2:
        return 0.8
    return 0.4


def _maybe_number(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace('\r\n', ' ').replace('\n', ' ').strip()
    return text or None


def _slugify(value: str) -> str:
    text = value.lower().replace('&', ' and ')
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook', type=Path)
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('apps/api/src/domain/facility_catalogue_v1.json'),
    )
    args = parser.parse_args()

    catalogue = import_catalogue(args.workbook)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalogue, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'Wrote {len(catalogue["templates"])} templates to {args.output}')


if __name__ == '__main__':
    main()
