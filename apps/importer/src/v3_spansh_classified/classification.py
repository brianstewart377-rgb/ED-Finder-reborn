"""Explicit Spansh classification mappings for a new canonical ingest version.

Source: https://docs.spansh.co.uk/galaxy.schema.json (checked 2026-09-20).
Spansh supplies the source classifications; ED-Finder normalises their spelling
for its existing vocabulary and renderer. The source artifact retains the exact
subtype, numeric spectral subclass and other source evidence for replay.

The frozen Ratings V4 adapter requires the broad body-type public codes below.
Do not replace ``planet`` with fine-grained planet codes without a separate
versioned vocabulary/consumer change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re


BODY_TYPE_VOCABULARY: dict[str, tuple[int, str]] = {
    "star": (1, "Star"),
    "planet": (2, "Planet"),
    "barycentre": (3, "Barycentre"),
    "belt_cluster": (4, "Belt Cluster"),
}

# All 18 planet subtypes in Spansh's galaxy schema, plus explicitly named
# Frontier PlanetClass compatibility spellings already used by ED-Finder.
PLANET_CLASS_TO_PUBLIC_CODE: dict[str, str] = {
    "Ammonia world": "planet",
    "Class I gas giant": "planet",
    "Class II gas giant": "planet",
    "Class III gas giant": "planet",
    "Class IV gas giant": "planet",
    "Class V gas giant": "planet",
    "Earth-like world": "planet",
    "Gas giant with ammonia-based life": "planet",
    "Gas giant with water-based life": "planet",
    "Helium gas giant": "planet",
    "Helium-rich gas giant": "planet",
    "High metal content world": "planet",
    "Icy body": "planet",
    "Metal-rich body": "planet",
    "Rocky Ice world": "planet",
    "Rocky body": "planet",
    "Water giant": "planet",
    "Water world": "planet",
    "Earthlike body": "planet",
    "High metal content body": "planet",
    "Metal rich body": "planet",
    "Rocky ice body": "planet",
    "Gas giant with water based life": "planet",
    "Gas giant with ammonia based life": "planet",
    "Sudarsky class I gas giant": "planet",
    "Sudarsky class II gas giant": "planet",
    "Sudarsky class III gas giant": "planet",
    "Sudarsky class IV gas giant": "planet",
    "Sudarsky class V gas giant": "planet",
    "Helium rich gas giant": "planet",
}

# Every star subtype in Spansh's galaxy schema is explicit. Values are accepted
# by apps/web/src/lib/spatial/stellar-presentation.ts, including its remnant
# precedence. A single letter preserves the colour for G2, M4 etc.; forwarding
# those numbered source values verbatim would select the renderer's unknown key.
STAR_SUBTYPE_TO_SPECTRAL_CLASS: dict[str, str] = {
    "A (Blue-White super giant) Star": "A",
    "A (Blue-White) Star": "A",
    "B (Blue-White super giant) Star": "B",
    "B (Blue-White) Star": "B",
    "Black Hole": "black-hole",
    "C Star": "carbon",
    "CJ Star": "carbon",
    "CN Star": "carbon",
    "F (White super giant) Star": "F",
    "F (White) Star": "F",
    "G (White-Yellow super giant) Star": "G",
    "G (White-Yellow) Star": "G",
    "Herbig Ae/Be Star": "AeBe",
    "K (Yellow-Orange giant) Star": "K",
    "K (Yellow-Orange) Star": "K",
    "L (Brown dwarf) Star": "L",
    "M (Red dwarf) Star": "M",
    "M (Red giant) Star": "M",
    "M (Red super giant) Star": "M",
    "MS-type Star": "carbon",
    "Neutron Star": "neutron",
    "O (Blue-White) Star": "O",
    "S-type Star": "carbon",
    "Supermassive Black Hole": "supermassive-black-hole",
    "T (Brown dwarf) Star": "T",
    "T Tauri Star": "T Tauri",
    "White Dwarf (D) Star": "white-dwarf",
    "White Dwarf (DA) Star": "white-dwarf",
    "White Dwarf (DAB) Star": "white-dwarf",
    "White Dwarf (DAV) Star": "white-dwarf",
    "White Dwarf (DAZ) Star": "white-dwarf",
    "White Dwarf (DB) Star": "white-dwarf",
    "White Dwarf (DBV) Star": "white-dwarf",
    "White Dwarf (DBZ) Star": "white-dwarf",
    "White Dwarf (DC) Star": "white-dwarf",
    "White Dwarf (DCV) Star": "white-dwarf",
    "White Dwarf (DQ) Star": "white-dwarf",
    "Wolf-Rayet C Star": "wolf-rayet",
    "Wolf-Rayet N Star": "wolf-rayet",
    "Wolf-Rayet NC Star": "wolf-rayet",
    "Wolf-Rayet O Star": "wolf-rayet",
    "Wolf-Rayet Star": "wolf-rayet",
    "Y (Brown dwarf) Star": "Y",
}

STAR_TYPE_TO_SPECTRAL_CLASS: dict[str, str] = {
    **{letter: letter for letter in "OBAFGKMLTY"},
    "N": "neutron",
    "Neutron": "neutron",
    "H": "black-hole",
    "Black-hole": "black-hole",
    "SupermassiveBlackHole": "supermassive-black-hole",
    "Supermassive-black-hole": "supermassive-black-hole",
    "D": "white-dwarf",
    "DA": "white-dwarf",
    "DAB": "white-dwarf",
    "DAV": "white-dwarf",
    "DAZ": "white-dwarf",
    "DB": "white-dwarf",
    "DBV": "white-dwarf",
    "DBZ": "white-dwarf",
    "DC": "white-dwarf",
    "DCV": "white-dwarf",
    "DO": "white-dwarf",
    "DQ": "white-dwarf",
    "DX": "white-dwarf",
    "White-dwarf": "white-dwarf",
    "C": "carbon",
    "CJ": "carbon",
    "CN": "carbon",
    "MS": "carbon",
    "S": "carbon",
    "Carbon": "carbon",
    "W": "wolf-rayet",
    "WC": "wolf-rayet",
    "WN": "wolf-rayet",
    "WNC": "wolf-rayet",
    "WO": "wolf-rayet",
    "Wolf-rayet": "wolf-rayet",
    "TTS": "T Tauri",
    "T Tauri": "T Tauri",
    "AeBe": "AeBe",
    "K_OrangeGiant": "K",
    "M_RedGiant": "M",
    "M_RedSuperGiant": "M",
    "A_BlueWhiteSuperGiant": "A",
    "B_BlueWhiteSuperGiant": "B",
    "F_WhiteSuperGiant": "F",
    "G_WhiteYellowSuperGiant": "G",
}


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


_BODY_TYPES = {_key(display): code for code, (_, display) in BODY_TYPE_VOCABULARY.items()}
_BODY_TYPES["belt_cluster"] = "belt_cluster"
_PLANETS = {_key(raw): code for raw, code in PLANET_CLASS_TO_PUBLIC_CODE.items()}
_STARS = {_key(raw): value for raw, value in STAR_SUBTYPE_TO_SPECTRAL_CLASS.items()}
_STAR_CODES = {_key(raw): value for raw, value in STAR_TYPE_TO_SPECTRAL_CLASS.items()}
_NUMBERED_CLASS = re.compile(
    r"(?P<code>AeBe|TTS|WNC|DAB|DAV|DAZ|DBV|DBZ|DCV|CJ|CN|MS|WC|WN|WO|DA|DB|DC|DO|DQ|DX|[OBAFGKMLTYNHDCSW])"
    r"(?:[0-9](?:\.[0-9]+)?)?(?:\s*(?:VII|VI|IV|III|II|V|I)(?:ab|a0|a|b|z)?)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BodyClassification:
    body_type_code: str | None
    spectral_class: str | None
    luminosity_class: str | None
    unmapped: tuple[tuple[str, str], ...] = ()


def _observations(body: Mapping[str, object], fields: tuple[str, ...]) -> list[tuple[str, str]]:
    observations = []
    for field in fields:
        value = body.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"body classification {field} must be a string")
        if value.strip():
            observations.append((field, value.strip()))
    return observations


def normalise_spectral_class(value: str) -> str | None:
    """Map a supported class/subtype to an existing renderer input; never guess."""
    direct = _STARS.get(_key(value)) or _STAR_CODES.get(_key(value))
    if direct:
        return direct
    match = _NUMBERED_CLASS.fullmatch(value.strip())
    return _STAR_CODES.get(_key(match["code"])) if match else None


def classify_body(body: Mapping[str, object]) -> BodyClassification:
    """Classify source fields, rejecting contradictory recognised evidence.

    Current Spansh lower-camel fields and explicit Frontier/PascalCase aliases
    are supported. Unknown nonempty values are returned as replayable unmapped
    vocabulary; they never become a fabricated spectral class. Unknown subtype
    does not erase a separately known broad body type. Alias conflicts fail
    closed instead of selecting whichever happens to appear first.
    """
    types: set[str] = set()
    spectra: set[str] = set()
    unmapped: set[tuple[str, str]] = set()

    for _, value in _observations(body, ("type", "Type")):
        broad = _BODY_TYPES.get(_key(value))
        if broad is None:
            unmapped.add(("body_type", value))
        else:
            types.add(broad)

    for field, value in _observations(
        body, ("spectralClass", "SpectralClass", "starType", "StarType"),
    ):
        spectrum = normalise_spectral_class(value)
        # These source field names themselves assert a stellar body, even when
        # their spectral value is new. The class remains unknown in that case.
        types.add("star")
        if spectrum is None:
            unmapped.add(("spectral_class", value))
        else:
            spectra.add(spectrum)

    for _, value in _observations(body, ("planetClass", "PlanetClass")):
        types.add("planet")
        if _key(value) not in _PLANETS:
            unmapped.add(("body_type", value))

    for _, value in _observations(body, ("subType", "SubType", "subtype")):
        if _key(value) in _PLANETS:
            types.add("planet")
        else:
            spectrum = normalise_spectral_class(value)
            if spectrum is None:
                unmapped.add(("body_type", value))
            else:
                types.add("star")
                spectra.add(spectrum)

    if len(types) > 1:
        raise ValueError(f"conflicting body classification types: {sorted(types)}")
    # H/H0 is the broad black-hole spectral code; the explicit Spansh subtype
    # can refine it to the renderer's existing supermassive black-hole family.
    if spectra == {"black-hole", "supermassive-black-hole"}:
        spectra.remove("black-hole")
    if len(spectra) > 1:
        raise ValueError(f"conflicting body spectral classes: {sorted(spectra)}")

    luminosities = _observations(body, ("luminosity", "Luminosity", "luminosityClass", "LuminosityClass"))
    if len({_key(value) for _, value in luminosities}) > 1:
        raise ValueError("conflicting body luminosity classes")
    broad = next(iter(types), None)
    if luminosities and broad not in {"star", None}:
        raise ValueError("stellar luminosity on a non-star body")
    return BodyClassification(
        body_type_code=broad,
        spectral_class=next(iter(spectra), None),
        luminosity_class=luminosities[0][1] if luminosities else None,
        unmapped=tuple(sorted(unmapped)),
    )
