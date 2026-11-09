"""Classify whole files without leaking commander context between files.

Until segment completion is durable, a mixed/ambiguous file is held in full.
Other valid files continue, and held files are never admitted to file dedupe.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .commanders import FID_PATTERN


@dataclass(frozen=True)
class FileOwnership:
    name: str
    fid: str | None
    reason: str | None
    display_name: str | None = None


def classify_files(files: list[dict], events: list[dict], owned_fids: set[str]) -> list[FileOwnership]:
    by_file: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_file[event['source_file']].append(event)
    names = [item['name'] for item in files]
    results = []
    for name in names:
        if names.count(name) != 1:
            results.append(FileOwnership(name, None, 'duplicate_file_name'))
            continue
        fid = None
        seen: set[str] = set()
        display_name = None
        reason = None
        offsets = set()
        for event in sorted(by_file[name], key=lambda item: item['source_offset']):
            offset = event['source_offset']
            if offset in offsets:
                reason = 'ambiguous_record_order'
            offsets.add(offset)
            event_type = event['event_type']
            payload = event.get('payload', event.get('event_payload', {}))
            if event_type not in ('Commander', 'LoadGame'):
                # A Fileheader preceding Commander is normal. Actual gameplay
                # before the identity record cannot be assigned retroactively.
                if fid is None and event_type != 'Fileheader':
                    reason = reason or 'missing_commander_header'
                continue
            candidate = payload.get('FID')
            if not isinstance(candidate, str) or not FID_PATTERN.fullmatch(candidate):
                fid = None
                reason = reason or 'missing_commander_identity'
                continue
            fid = candidate
            seen.add(fid)
            label = payload.get('Name' if event_type == 'Commander' else 'Commander')
            if isinstance(label, str) and label.strip():
                display_name = label.strip()[:128]
        if len(seen) > 1:
            reason = 'mixed_commanders'
        elif not seen:
            reason = reason or 'missing_commander_header'
        elif fid not in owned_fids:
            reason = reason or 'commander_not_linked'
        results.append(FileOwnership(name, fid, reason, display_name))
    return results
