from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "importer" / "src"))

from v3_spansh_classified.classification import (
    BODY_TYPE_VOCABULARY,
    PLANET_CLASS_TO_PUBLIC_CODE,
    STAR_SUBTYPE_TO_SPECTRAL_CLASS,
    BodyClassification,
    classify_body,
    normalise_spectral_class,
)


@pytest.mark.parametrize("letter", tuple("OBAFGKMLTY"))
@pytest.mark.parametrize("field", ("spectralClass", "SpectralClass", "starType", "StarType"))
def test_main_and_brown_dwarf_classes_match_renderer_keys(letter, field):
    result = classify_body({field: f"{letter}2", "luminosity": "V"})
    assert result == BodyClassification("star", letter, "V")


@pytest.mark.parametrize("source, expected", [
    ("N", "neutron"), ("N5", "neutron"), ("Neutron Star", "neutron"),
    ("H", "black-hole"), ("H0", "black-hole"), ("Black Hole", "black-hole"),
    ("SupermassiveBlackHole", "supermassive-black-hole"),
    ("Supermassive Black Hole", "supermassive-black-hole"),
    ("D", "white-dwarf"), ("DA", "white-dwarf"), ("DAZ5", "white-dwarf"),
    ("DAB0", "white-dwarf"), ("DBV0", "white-dwarf"), ("DCV0", "white-dwarf"),
    ("DQ9", "white-dwarf"), ("White Dwarf (DA) Star", "white-dwarf"),
    ("TTS3", "T Tauri"), ("AeBe8", "AeBe"),
    ("CJ4", "carbon"), ("CN5", "carbon"), ("MS6", "carbon"), ("S7", "carbon"),
    ("WNC0", "wolf-rayet"), ("WO0", "wolf-rayet"), ("W5", "wolf-rayet"),
    ("K_OrangeGiant", "K"), ("M_RedSuperGiant", "M"),
    (" G2 V ", "G"), ("b0.5Ia", "B"),
])
def test_explicit_special_and_numbered_spectral_mappings(source, expected):
    assert normalise_spectral_class(source) == expected


@pytest.mark.parametrize("source, expected", list(STAR_SUBTYPE_TO_SPECTRAL_CLASS.items()))
@pytest.mark.parametrize("field", ("subType", "SubType", "subtype"))
def test_every_spansh_schema_star_subtype_is_classified(source, expected, field):
    assert classify_body({field: source}) == BodyClassification("star", expected, None)


@pytest.mark.parametrize("source", list(PLANET_CLASS_TO_PUBLIC_CODE))
@pytest.mark.parametrize("field", ("subType", "PlanetClass", "planetClass"))
def test_every_explicit_planet_class_uses_existing_planet_vocabulary(source, field):
    assert classify_body({field: source}) == BodyClassification("planet", None, None)


@pytest.mark.parametrize("source, code, identifier", [
    ("Star", "star", 1), ("Planet", "planet", 2),
    ("Barycentre", "barycentre", 3), ("Belt Cluster", "belt_cluster", 4),
])
def test_existing_body_type_ids_and_public_codes_are_preserved(source, code, identifier):
    assert classify_body({"type": source}).body_type_code == code
    assert BODY_TYPE_VOCABULARY[code] == (identifier, source)


def test_known_independent_sources_agree_after_normalisation():
    assert classify_body({
        "type": " star ", "spectralClass": "g2", "SpectralClass": "G2 V",
        "StarType": "G", "subType": "G (White-Yellow) Star", "Luminosity": "Va",
    }) == BodyClassification("star", "G", "Va")


def test_supermassive_subtype_refines_broad_black_hole_code():
    assert classify_body({"spectralClass": "H0", "subType": "Supermassive Black Hole"}) == (
        BodyClassification("star", "supermassive-black-hole", None)
    )


@pytest.mark.parametrize("unknown", ("Nebulous", "Dark matter", "G banana", "DARK", "Q", "G42", ""))
def test_unknown_spectral_tokens_do_not_invent_classes(unknown):
    assert normalise_spectral_class(unknown) is None


def test_unknown_spectrum_uses_known_subtype_and_records_original_token():
    result = classify_body({"type": "Star", "spectralClass": "new spectrum", "subType": "Neutron Star"})
    assert result == BodyClassification("star", "neutron", None, (("spectral_class", "new spectrum"),))


def test_unknown_subtype_preserves_known_body_type_and_evidence():
    assert classify_body({"type": "Planet", "subType": "new planet class"}) == BodyClassification(
        "planet", None, None, (("body_type", "new planet class"),),
    )


def test_unknown_type_and_missing_values_stay_unknown():
    assert classify_body({}) == BodyClassification(None, None, None)
    assert classify_body({"type": "Mystery", "spectralClass": None}) == BodyClassification(
        None, None, None, (("body_type", "Mystery"),),
    )
    assert classify_body({"type": " ", "subType": None, "SpectralClass": ""}) == BodyClassification(None, None, None)


@pytest.mark.parametrize("body", [
    {"type": "Planet", "StarType": "G"},
    {"type": "Star", "PlanetClass": "Rocky body"},
    {"type": "Barycentre", "subType": "Neutron Star"},
    {"type": "Star", "Type": "Planet"},
    {"SpectralClass": "G2", "spectralClass": "K1"},
    {"StarType": "N", "subType": "Black Hole"},
    {"type": "Planet", "luminosity": "V"},
    {"luminosity": "V", "Luminosity": "III"},
    {"spectralClass": ["G"]},
    {"PlanetClass": False},
])
def test_conflicting_or_malformed_classification_fails_closed(body):
    with pytest.raises(ValueError):
        classify_body(body)


def test_mapping_tables_are_bounded_to_existing_vocabulary_and_renderer_inputs():
    assert len(STAR_SUBTYPE_TO_SPECTRAL_CLASS) == 43
    assert len(PLANET_CLASS_TO_PUBLIC_CODE) == 30
    assert set(PLANET_CLASS_TO_PUBLIC_CODE.values()) == {"planet"}
    assert set(STAR_SUBTYPE_TO_SPECTRAL_CLASS.values()) == {
        "O", "B", "A", "F", "G", "K", "M", "L", "T", "Y", "neutron",
        "white-dwarf", "black-hole", "supermassive-black-hole", "carbon", "wolf-rayet", "AeBe", "T Tauri",
    }


def test_classification_does_not_modify_source_evidence():
    body = {"type": "Star", "spectralClass": "G2", "subType": "G (White-Yellow) Star"}
    before = dict(body)
    classify_body(body)
    assert body == before
