"""
exploration_value.py  v1.0
──────────────────────────
Estimates the exploration payout for a star system based on its bodies,
using the Elite Dangerous 3.3+ exploration value formula.

Reference: https://forums.frontier.co.uk/threads/exploration-value-formulae.232000/

This module is used by the /api/system/{id64} endpoint to add an
`exploration_value` field to system detail responses.
"""


# ── Body type base values ─────────────────────────────────────────────────────
# These are the k-values from the ED 3.3 formula
_BASE_VALUE: dict[str, float] = {
    # Stars
    "Neutron Star":                   22628,
    "Black Hole":                     22628,
    "White Dwarf (D)":                14057,
    "White Dwarf (DA)":               14057,
    "White Dwarf (DAB)":              14057,
    "White Dwarf (DAZ)":              14057,
    "White Dwarf (DB)":               14057,
    "White Dwarf (DBZ)":              14057,
    "White Dwarf (DC)":               14057,
    "White Dwarf (DQ)":               14057,
    "White Dwarf (DX)":               14057,
    # Planets
    "Earthlike body":                 116295,
    "Water world":                    64831,
    "Ammonia world":                  232619,
    "High metal content body":        9654,
    "Metal rich body":                21790,
    "Rocky body":                     300,
    "Rocky ice body":                 300,
    "Icy body":                       250,
    "Gas giant with water based life": 2000,
    "Gas giant with ammonia based life": 2000,
    "Sudarsky class I gas giant":     3974,
    "Sudarsky class II gas giant":    9654,
    "Sudarsky class III gas giant":   3974,
    "Sudarsky class IV gas giant":    3974,
    "Sudarsky class V gas giant":     3974,
    "Helium gas giant":               3974,
    "Helium rich gas giant":          3974,
    "Water giant":                    2000,
}

# Terraformable bonus multiplier
_TERRA_BONUS: dict[str, float] = {
    "High metal content body": 93328,
    "Rocky body":              93328,
    "Water world":             116295,
}

# Bonus for first discovery (applied by the game, not calculated here)
_FIRST_DISCOVERY_MULTIPLIER = 2.6

# Bonus for first mapped
_FIRST_MAPPED_MULTIPLIER = 3.699622554

# Efficiency bonus threshold (map within 3 jumps of discovery)
_EFFICIENCY_BONUS = 1.25


def estimate_body_value(
    body_type: str,
    mass_em: float,
    terraformable: bool = False,
    first_discovered: bool = False,
    first_mapped: bool = False,
    efficient_mapping: bool = False,
) -> int:
    """
    Estimate the exploration credit value of a single body.

    Args:
        body_type:       Body type string (e.g. "Earthlike body")
        mass_em:         Body mass in Earth masses
        terraformable:   Whether the body is terraformable
        first_discovered: Whether this is a first discovery
        first_mapped:    Whether this is a first mapping
        efficient_mapping: Whether the efficient mapping bonus applies

    Returns:
        Estimated credits value (integer)
    """
    k = _BASE_VALUE.get(body_type, 300)

    # Base value formula: k + (k * q * mass^0.2)
    # q = 0.56591828 (constant from the formula)
    q = 0.56591828
    base = k + (k * q * (mass_em ** 0.2))

    # Terraformable bonus
    if terraformable and body_type in _TERRA_BONUS:
        base += _TERRA_BONUS[body_type]

    # First discovery multiplier
    if first_discovered:
        base *= _FIRST_DISCOVERY_MULTIPLIER

    # Mapping value
    map_value = 0
    if first_mapped:
        map_value = base * _FIRST_MAPPED_MULTIPLIER
        if efficient_mapping:
            map_value *= _EFFICIENCY_BONUS

    total = base + map_value
    return max(500, int(round(total)))


def estimate_system_value(
    bodies: list[dict],
    first_discovered: bool = False,
    first_mapped: bool = False,
) -> dict:
    """
    Estimate the total exploration value of a star system.

    Args:
        bodies: List of body dicts from the DB (fields: type, mass_em, is_terraformable)
        first_discovered: Whether the system is first discovered
        first_mapped: Whether the bodies are first mapped

    Returns:
        Dict with keys:
          - total_cr: Total estimated credits
          - scan_cr: Credits from scanning only (no mapping)
          - map_cr: Additional credits from mapping
          - breakdown: List of {name, type, value_cr} per body
          - notable_bodies: List of high-value body types found
    """
    scan_total = 0
    map_total = 0
    breakdown = []
    notable = set()

    for body in bodies:
        btype = body.get("type") or body.get("body_type") or ""
        mass  = float(body.get("mass_em") or body.get("mass") or 1.0)
        terra = bool(body.get("is_terraformable") or body.get("terraformable") or False)
        name  = body.get("name") or body.get("body_name") or btype

        scan_val = estimate_body_value(
            btype, mass, terra,
            first_discovered=first_discovered,
            first_mapped=False,
        )
        map_val = estimate_body_value(
            btype, mass, terra,
            first_discovered=first_discovered,
            first_mapped=first_mapped,
            efficient_mapping=True,
        ) - scan_val if first_mapped else 0

        scan_total += scan_val
        map_total  += map_val

        breakdown.append({
            "name":     name,
            "type":     btype,
            "scan_cr":  scan_val,
            "map_cr":   map_val,
            "total_cr": scan_val + map_val,
        })

        # Track notable body types
        if btype in ("Earthlike body", "Water world", "Ammonia world"):
            notable.add(btype)
        if btype in ("Neutron Star", "Black Hole"):
            notable.add(btype)
        if terra:
            notable.add(f"Terraformable {btype}")

    # Sort breakdown by value descending
    breakdown.sort(key=lambda b: b["total_cr"], reverse=True)

    return {
        "total_cr":      scan_total + map_total,
        "scan_cr":       scan_total,
        "map_cr":        map_total,
        "body_count":    len(bodies),
        "breakdown":     breakdown[:10],  # Top 10 most valuable bodies
        "notable_bodies": sorted(notable),
    }


def format_credits(cr: int) -> str:
    """Format a credit value as a human-readable string (e.g. '1.23M Cr')."""
    if cr >= 1_000_000_000:
        return f"{cr / 1_000_000_000:.2f}B Cr"
    if cr >= 1_000_000:
        return f"{cr / 1_000_000:.2f}M Cr"
    if cr >= 1_000:
        return f"{cr / 1_000:.1f}K Cr"
    return f"{cr:,} Cr"
