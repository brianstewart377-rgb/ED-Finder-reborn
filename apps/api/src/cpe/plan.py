"""Immutable inputs and bounded validation for one system's construction plan."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .catalogue import planner_facilities


@dataclass(frozen=True)
class Body:
    body_ref: str
    orbital_slots: int | None
    surface_slots: int | None
    is_landable: bool | None
    is_ringed: bool | None


@dataclass(frozen=True)
class Site:
    site_id: str
    facility_id: str
    body_ref: str | None
    slot_kind: Literal['orbital', 'surface']
    status: Literal['planned', 'building', 'complete', 'demolished']
    name: str | None = None


@dataclass(frozen=True)
class Plan:
    system_id64: int
    bodies: tuple[Body, ...]
    sites: tuple[Site, ...]


@dataclass(frozen=True)
class PlanIssue:
    code: str
    site_id: str | None
    message: str


class PlanInvalid(ValueError):
    """Evaluation was refused; issues contains every input validation failure."""

    def __init__(self, issues: list[PlanIssue]):
        self.issues = tuple(issues)
        super().__init__('; '.join(issue.message for issue in issues))


def validate_plan(plan: Plan) -> list[PlanIssue]:
    issues: list[PlanIssue] = []

    def issue(code: str, message: str, site_id: str | None = None) -> None:
        issues.append(PlanIssue(code, site_id, message))

    if type(plan.system_id64) is not int or not 1 <= plan.system_id64 <= 2**63 - 1:
        issue('BAD_SYSTEM', 'System id64 must be an integer in 1..2**63-1.')
    if not plan.sites:
        issue('NO_SITES', 'A plan needs a Primary Port.')
    if len(plan.sites) > 300:
        issue('TOO_MANY_SITES', 'A plan may contain at most 300 sites.')
    if len(plan.bodies) > 1000:
        issue('TOO_MANY_BODIES', 'A plan may contain at most 1000 bodies.')

    body_refs: set[str] = set()
    for body in plan.bodies:
        if not isinstance(body.body_ref, str) or not body.body_ref or body.body_ref in body_refs:
            issue('UNKNOWN_BODY', 'Body references must be nonempty, unique strings.')
        else:
            body_refs.add(body.body_ref)
        for count in (body.orbital_slots, body.surface_slots):
            if count is not None and (type(count) is not int or not 0 <= count <= 64):
                issue('BAD_SLOTS', f'Body {body.body_ref!r} slot counts must be unknown or integers in 0..64.')

    catalogue = planner_facilities()
    seen: set[str] = set()
    for index, site in enumerate(plan.sites):
        if not isinstance(site.site_id, str) or not 1 <= len(site.site_id) <= 64 or site.site_id in seen:
            issue('BAD_SITE_ID', 'Site ids must be unique strings of 1..64 characters.', site.site_id)
        else:
            seen.add(site.site_id)
        if site.name is not None and (not isinstance(site.name, str) or len(site.name) > 64):
            issue('BAD_NAME', 'Site names must be unknown or at most 64 characters.', site.site_id)
        if site.status not in ('planned', 'building', 'complete', 'demolished'):
            issue('BAD_STATUS', 'Site status must be planned, building, complete or demolished.', site.site_id)
        if site.body_ref is not None and (not isinstance(site.body_ref, str) or site.body_ref not in body_refs):
            issue('UNKNOWN_BODY', 'Site body reference must name a body in this plan.', site.site_id)
        template = catalogue.get(site.facility_id) if isinstance(site.facility_id, str) else None
        if template is None:
            issue('UNKNOWN_FACILITY', 'Facility id must be in the planner catalogue.', site.site_id)
        else:
            location_matches = (
                site.slot_kind == 'orbital' and template.can_go_orbital
                or site.slot_kind == 'surface' and template.can_go_surface
            )
            if not location_matches:
                issue('LOCATION_MISMATCH', 'Slot kind does not suit this facility.', site.site_id)
        if index == 0:
            if template is not None and not template.is_port:
                issue('PRIMARY_NOT_PORT', 'The first site must be a port.', site.site_id)
            if site.status == 'demolished':
                issue('PRIMARY_DEMOLISHED', 'The Primary Port must not be demolished.', site.site_id)
    return issues
