'''Canonical, database-free V3 system-archetype scoring model.

Economy ordinals: 1=Agriculture, 2=Refinery, 3=Industrial, 4=HighTech,
5=Military, 6=Tourism and 7=Extraction.
'''
from __future__ import annotations

import ast
import builtins
import dataclasses
import hashlib
import math
from pathlib import Path


ARCHETYPE_VERSION = 'v3-archetype-4'

_ANCHORS: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {
    'paradise': ((1, 6), ()),
    'mining_hub': ((7, 2), ()),
    'manufacturing_hub': ((2, 3), ()),
    'megacomplex': ((7, 2, 3), ()),
    'research_hub': ((4,), (3,)),
    'stronghold': ((5, 3), ()),
    'population_capital': ((1, 4), ()),
    'flexible': ((), ()),
}
ARCHETYPE_KEYS = tuple(_ANCHORS)

ALPHA = 0.6
SPEC_FLOOR, SPEC_SPAN = 0.85, 0.15
CAP_FLOOR, CAP_SPAN, CAPACITY_TARGET = 0.6, 0.4, 8
SYNERGY_BONUS, SYNERGY_THRESHOLD = 5, 60
SPEC_UNKNOWN_CONF = 0.85
BREADTH_POT, BREADTH_QUAL, BREADTH_TARGET = 55, 40, 4

_TIERS = ((88, 'S'), (76, 'A'), (60, 'B'), (45, 'C'))

# Both selectors are present so the same query contract can be used for a
# builder chunk and for the on-demand single-system explanation. Exactly one
# must be non-NULL. The database schema supplies all vector bounds.
VECTOR_READ_SQL = '''
SELECT v.system_id64, v.potential, v.quality, v.quality_min, v.quality_max,
       v.completeness, v.confidence
  FROM v3_derived.system_rating_vector v
 WHERE v.derived_generation_id = %(generation_id)s
   AND (
       (%(chunk_ordinal)s::bigint IS NOT NULL
        AND %(system_id64)s::bigint IS NULL
        AND v.chunk_ordinal = %(chunk_ordinal)s)
       OR
       (%(system_id64)s::bigint IS NOT NULL
        AND %(chunk_ordinal)s::bigint IS NULL
        AND v.system_id64 = %(system_id64)s)
   )
 ORDER BY v.system_id64
'''

OPPORTUNITY_READ_SQL = '''
SELECT o.system_id64, o.economy_ordinal, count(*)::int,
       sum(o.local_score)::bigint
  FROM v3_derived.economy_opportunity o
  JOIN v3_derived.system_rating_vector v
    ON v.derived_generation_id = o.derived_generation_id
   AND v.system_id64 = o.system_id64
 WHERE o.derived_generation_id = %(generation_id)s
   AND (
       (%(chunk_ordinal)s::bigint IS NOT NULL
        AND %(system_id64)s::bigint IS NULL
        AND v.chunk_ordinal = %(chunk_ordinal)s)
       OR
       (%(system_id64)s::bigint IS NOT NULL
        AND %(chunk_ordinal)s::bigint IS NULL
        AND o.system_id64 = %(system_id64)s)
   )
 GROUP BY o.system_id64, o.economy_ordinal
 ORDER BY o.system_id64, o.economy_ordinal
'''


@dataclasses.dataclass(frozen=True)
class SystemVectorRow:
    '''The seven columns returned by ``VECTOR_READ_SQL``, without scaling.'''

    system_id64: int
    potential: tuple[int, ...]
    quality: tuple[int | None, ...]
    quality_min: tuple[int, ...]
    quality_max: tuple[int, ...]
    completeness: tuple[int, ...]
    confidence: tuple[int, ...]

    @classmethod
    def from_row(cls, row) -> SystemVectorRow:
        return cls(
            system_id64=int(row[0]),
            potential=tuple(row[1]),
            quality=tuple(row[2]),
            quality_min=tuple(row[3]),
            quality_max=tuple(row[4]),
            completeness=tuple(row[5]),
            confidence=tuple(row[6]),
        )


@dataclasses.dataclass(frozen=True)
class SystemVectors:
    pot: tuple[int, ...]
    qual: tuple[int | None, ...]
    qual_min: tuple[int, ...]
    qual_max: tuple[int, ...]
    completeness: tuple[float, ...]
    confidence: tuple[float, ...]
    opp: dict[int, tuple[int, float]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ArchetypeFit:
    key: str
    score: int
    tier: str
    confidence: float
    explanation: dict


def tier_of(score: int) -> str:
    for threshold, name in _TIERS:
        if score >= threshold:
            return name
    return 'D'


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _quality(v: SystemVectors, ordinal: int) -> tuple[float, bool]:
    '''Return the quality value and whether bounded-null midpointing was used.'''
    index = ordinal - 1
    quality = v.qual[index]
    if quality is not None:
        return float(quality), False
    return (v.qual_min[index] + v.qual_max[index]) / 2.0, True


def _explain_anchored(
    v: SystemVectors,
    required: tuple[int, ...],
    supporting: tuple[int, ...],
    core: float,
    spec: float,
    cap: float,
    synergy: int,
) -> dict:
    pool = tuple(required) + tuple(supporting)
    return {
        'anchors': list(required),
        'supporting': list(supporting),
        'core': round(core, 3),
        'spec': round(spec, 4),
        'cap': round(cap, 4),
        'synergy': synergy,
        'components': {
            'pot': {ordinal: v.pot[ordinal - 1] for ordinal in pool},
            'qual': {ordinal: v.qual[ordinal - 1] for ordinal in required},
            'opp_count': {
                ordinal: v.opp.get(ordinal, (0, 0.0))[0]
                for ordinal in required
            },
        },
    }


def _explain_flexible(breadth: int) -> dict:
    return {
        'anchors': [],
        'breadth': breadth,
        'breadth_target': BREADTH_TARGET,
    }


def _fit_anchored(v: SystemVectors, key: str) -> ArchetypeFit:
    required, supporting = _ANCHORS[key]
    potentials = [v.pot[ordinal - 1] for ordinal in required]
    core = ALPHA * min(potentials) + (1 - ALPHA) * (
        sum(potentials) / len(potentials)
    )

    quality_values = []
    unknown = False
    for ordinal in required:
        quality, was_unknown = _quality(v, ordinal)
        quality_values.append(quality)
        unknown = unknown or was_unknown
    spec = SPEC_FLOOR + SPEC_SPAN * (
        sum(quality_values) / len(quality_values) / 100.0
    )

    counts = []
    local_means = []
    for ordinal in required:
        count, local_mean = v.opp.get(ordinal, (0, 0.0))
        counts.append(count)
        local_means.append(local_mean)
    mean_count = sum(counts) / len(counts)
    mean_local = sum(local_means) / len(local_means)
    cap = CAP_FLOOR + CAP_SPAN * min(
        1.0, mean_count / CAPACITY_TARGET
    ) * (mean_local / 100.0)

    pool = tuple(required) + tuple(supporting)
    synergy = (
        SYNERGY_BONUS
        if all(v.pot[ordinal - 1] >= SYNERGY_THRESHOLD for ordinal in pool)
        else 0
    )
    score = round(_clamp(core * spec * cap + synergy, 0, 100))
    confidence = (
        min(v.confidence[ordinal - 1] for ordinal in required)
        * min(v.completeness[ordinal - 1] for ordinal in required)
        * (SPEC_UNKNOWN_CONF if unknown else 1.0)
    )
    return ArchetypeFit(
        key,
        score,
        tier_of(score),
        round(confidence, 6),
        _explain_anchored(v, required, supporting, core, spec, cap, synergy),
    )


def _fit_flexible(v: SystemVectors) -> ArchetypeFit:
    breadth = 0
    for ordinal in range(1, 8):
        quality, _ = _quality(v, ordinal)
        if v.pot[ordinal - 1] >= BREADTH_POT and quality >= BREADTH_QUAL:
            breadth += 1
    score = round(
        _clamp(100.0 * min(1.0, breadth / BREADTH_TARGET), 0, 100)
    )
    confidence = (sum(v.confidence) / len(v.confidence)) * (
        sum(v.completeness) / len(v.completeness)
    )
    return ArchetypeFit(
        'flexible',
        score,
        tier_of(score),
        round(confidence, 6),
        _explain_flexible(breadth),
    )


def fit_all(v: SystemVectors) -> list[ArchetypeFit]:
    return [
        _fit_flexible(v) if key == 'flexible' else _fit_anchored(v, key)
        for key in ARCHETYPE_KEYS
    ]


def search_uncertainty_factor(v: SystemVectors) -> float:
    return min(v.confidence) * min(v.completeness)


def weighted_potential(best_colony_potential: int, v: SystemVectors) -> float:
    raw = best_colony_potential * search_uncertainty_factor(v)
    return _clamp(raw, 0.0, 100.0)


def _fit_sort_key(fit: ArchetypeFit) -> tuple[int, int]:
    return -fit.score, ARCHETYPE_KEYS.index(fit.key)


def summarise(fits: list[ArchetypeFit]) -> dict:
    ordered = sorted(fits, key=_fit_sort_key)
    first, second = ordered[0], ordered[1]
    first_score, second_score = first.score, second.score
    archetype_confidence = min(
        (first_score - second_score) / max(first_score, 1) * 2,
        1.0,
    )
    return {
        'primary_archetype': first.key,
        'secondary_archetype': second.key if second_score > 0 else None,
        'best_colony_potential': first_score,
        'best_tier': first.tier,
        'archetype_confidence': round(archetype_confidence, 6),
    }


def confidence_to_ppm(confidence: float) -> int:
    '''Convert an already-six-decimal model confidence with shared half-up.'''
    return int(math.floor(confidence * 1_000_000 + 0.5))


def completeness_to_ppm(completeness: tuple[int, ...]) -> int:
    '''Derive exact ppm from the unscaled Ratings V4 smallint array.'''
    return min(completeness) * 100


def weighted_key_e4(
    score: int,
    confidence_ppm: int,
    completeness_ppm: int,
) -> int:
    numerator = score * confidence_ppm * completeness_ppm
    return (numerator + 50_000_000) // 100_000_000


def _scaled_vectors(
    vector_row: SystemVectorRow,
    opportunity_groups: tuple[tuple[int, int, int], ...],
) -> SystemVectors:
    opportunities = {}
    for economy_ordinal, row_count, local_score_sum in opportunity_groups:
        opportunities[economy_ordinal] = (
            row_count,
            local_score_sum / row_count,
        )
    return SystemVectors(
        pot=tuple(vector_row.potential),
        qual=tuple(vector_row.quality),
        qual_min=tuple(vector_row.quality_min),
        qual_max=tuple(vector_row.quality_max),
        completeness=tuple(value / 10_000.0 for value in vector_row.completeness),
        confidence=tuple(value / 10_000.0 for value in vector_row.confidence),
        opp=opportunities,
    )


def score_raw_system(
    vector_row: SystemVectorRow,
    opportunity_groups: tuple[tuple[int, int, int], ...],
) -> dict:
    '''Score one database-shaped row and return every persisted model column.'''
    vectors = _scaled_vectors(vector_row, opportunity_groups)
    completeness_ppm = completeness_to_ppm(vector_row.completeness)
    fits = fit_all(vectors)
    summary = summarise(fits)
    result = {}
    for fit in fits:
        confidence_ppm = confidence_to_ppm(fit.confidence)
        result[f'{fit.key}_score'] = fit.score
        result[f'{fit.key}_tier'] = fit.tier
        result[f'{fit.key}_confidence_ppm'] = confidence_ppm
        result[f'{fit.key}_weighted_e4'] = weighted_key_e4(
            fit.score,
            confidence_ppm,
            completeness_ppm,
        )
    result.update({
        'primary_archetype': summary['primary_archetype'],
        'secondary_archetype': summary['secondary_archetype'],
        'best_colony_potential': summary['best_colony_potential'],
        'best_tier': summary['best_tier'],
        'archetype_confidence_ppm': confidence_to_ppm(
            summary['archetype_confidence']
        ),
        'weighted_potential': weighted_potential(
            summary['best_colony_potential'], vectors
        ),
        'completeness_ppm': completeness_ppm,
    })
    return result


def explain_raw_system(
    vector_row: SystemVectorRow,
    opportunity_groups: tuple[tuple[int, int, int], ...],
) -> dict[str, dict]:
    '''Build the eight on-demand explanations from database-shaped inputs.

    Reuses the fits themselves (``fit_all`` attaches each explanation to its
    ``ArchetypeFit``) so the explanation can never describe inputs other than
    the ones that produced the score; this module holds exactly one copy of
    the scoring arithmetic. Not reachable from ``score_raw_system``, so it
    does not enter ``archetype_model_sha256``.
    '''
    fits = fit_all(_scaled_vectors(vector_row, opportunity_groups))
    return {fit.key: fit.explanation for fit in fits}


class _DigestError(ValueError):
    pass


def _without_docstring(node):
    clean = ast.fix_missing_locations(ast.parse(ast.unparse(node)).body[0])
    if (
        isinstance(clean, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and clean.body
        and isinstance(clean.body[0], ast.Expr)
        and isinstance(clean.body[0].value, ast.Constant)
        and isinstance(clean.body[0].value.value, str)
    ):
        clean.body = clean.body[1:]
    return clean


def _target_names(node) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
    }


def _annotation_nodes(function) -> set[ast.AST]:
    nodes = set()
    arguments = (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    )
    if function.args.vararg is not None:
        arguments = (*arguments, function.args.vararg)
    if function.args.kwarg is not None:
        arguments = (*arguments, function.args.kwarg)
    for argument in arguments:
        if argument.annotation is not None:
            nodes.add(argument.annotation)
    if function.returns is not None:
        nodes.add(function.returns)
    return nodes


def _annotation_references(function) -> set[str]:
    references = set()
    for annotation in _annotation_nodes(function):
        references.update(_expression_references(annotation))
    return references


def _function_references(function) -> set[str]:
    local = {
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    if function.args.vararg is not None:
        local.add(function.args.vararg.arg)
    if function.args.kwarg is not None:
        local.add(function.args.kwarg.arg)
    for statement in function.body:
        local.update(_target_names(statement))

    annotations = _annotation_nodes(function)
    references = set()

    class Visitor(ast.NodeVisitor):
        def visit(self, node):
            if node in annotations:
                return None
            return super().visit(node)

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load) and node.id not in local:
                references.add(node.id)

        def visit_FunctionDef(self, node):
            if node is function:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            if node is function:
                self.generic_visit(node)

    Visitor().visit(function)
    return references


def _expression_references(node) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _module_definitions(tree):
    functions = {}
    classes = {}
    assignments = {}
    imports = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                for name in _target_names(target):
                    assignments[name] = node
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split('.')[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            for alias in node.names:
                imports[alias.asname or alias.name] = f'{module}.{alias.name}'
    return functions, classes, assignments, imports


def _class_references(node) -> set[str]:
    references = set()
    for decorator in node.decorator_list:
        references.update(_expression_references(decorator))
    for base in node.bases:
        references.update(_expression_references(base))
    for statement in node.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            references.update(_function_references(statement))
        elif isinstance(statement, ast.Assign):
            references.update(_expression_references(statement.value))
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            references.update(_expression_references(statement.value))
    return references


def _allowed_import(name: str, imports: dict[str, str], source_node) -> bool:
    origin = imports.get(name)
    if origin == 'math':
        attributes = {
            child.attr
            for child in ast.walk(source_node)
            if isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == name
        }
        return bool(attributes) and all(hasattr(math, member) for member in attributes)
    if origin == 'dataclasses':
        attributes = {
            child.attr
            for child in ast.walk(source_node)
            if isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == name
        }
        return bool(attributes) and attributes <= {'dataclass', 'field'}
    if origin is not None and origin.startswith('math.'):
        return hasattr(math, origin.removeprefix('math.'))
    return origin in {'dataclasses.dataclass', 'dataclasses.field'}


def archetype_model_sha256() -> str:
    '''Return the fail-closed semantic identity of scoring and source reads.'''
    source = Path(__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    functions, classes, assignments, imports = _module_definitions(tree)
    if 'score_raw_system' not in functions:
        raise _DigestError('score_raw_system is not defined in the model module')

    covered_functions = set()
    covered_classes = set()
    covered_assignments = set()
    pending = [('function', 'score_raw_system')]
    payload = []

    while pending:
        kind, name = pending.pop()
        if kind == 'function':
            if name in covered_functions or name.startswith('_explain_'):
                continue
            covered_functions.add(name)
            node = functions[name]
            payload.append(('function', name, ast.dump(
                _without_docstring(node), include_attributes=False
            )))
            references = _function_references(node)
            for annotation_reference in _annotation_references(node):
                if annotation_reference in classes:
                    pending.append(('class', annotation_reference))
        elif kind == 'class':
            if name in covered_classes:
                continue
            covered_classes.add(name)
            node = classes[name]
            payload.append(('class', name, ast.dump(
                _without_docstring(node), include_attributes=False
            )))
            references = _class_references(node)
        else:
            if name in covered_assignments:
                continue
            covered_assignments.add(name)
            node = assignments[name]
            payload.append(('assignment', name, ast.dump(
                node, include_attributes=False
            )))
            value = node.value
            references = _expression_references(value)

        for reference in references:
            if reference in functions:
                if not reference.startswith('_explain_'):
                    pending.append(('function', reference))
            elif reference in classes:
                pending.append(('class', reference))
            elif reference in assignments:
                if reference not in { 'VECTOR_READ_SQL', 'OPPORTUNITY_READ_SQL'}:
                    pending.append(('assignment', reference))
            elif reference in imports:
                if not _allowed_import(reference, imports, node):
                    raise _DigestError(f'unsupported imported reference: {reference}')
            elif not hasattr(builtins, reference):
                raise _DigestError(f'undefined scoring reference: {reference}')

    sql_values = {
        'VECTOR_READ_SQL': VECTOR_READ_SQL,
        'OPPORTUNITY_READ_SQL': OPPORTUNITY_READ_SQL,
    }
    for name, value in sql_values.items():
        payload.append(('sql', name, ' '.join(value.split())))
    canonical = repr(sorted(payload)).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


__all__ = (
    'ALPHA',
    'ARCHETYPE_KEYS',
    'ARCHETYPE_VERSION',
    'ArchetypeFit',
    'BREADTH_POT',
    'BREADTH_QUAL',
    'BREADTH_TARGET',
    'CAPACITY_TARGET',
    'CAP_FLOOR',
    'CAP_SPAN',
    'OPPORTUNITY_READ_SQL',
    'SPEC_FLOOR',
    'SPEC_SPAN',
    'SPEC_UNKNOWN_CONF',
    'SYNERGY_BONUS',
    'SYNERGY_THRESHOLD',
    'SystemVectorRow',
    'SystemVectors',
    'VECTOR_READ_SQL',
    'archetype_model_sha256',
    'completeness_to_ppm',
    'confidence_to_ppm',
    'explain_raw_system',
    'fit_all',
    'score_raw_system',
    'search_uncertainty_factor',
    'summarise',
    'tier_of',
    'weighted_key_e4',
    'weighted_potential',
)
