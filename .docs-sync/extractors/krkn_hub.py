"""Extractor for krkn-hub upstream.

Reads `llms-full.txt` at head and base refs (already fetched by
`github_ops.fetch_upstream_digest`), parses both into structured
per-scenario data, then diffs to produce a `ChangeSet`.

The ChangeSet describes WHAT to change, not HOW. The mechanical regen
module (regen/parameter_table.py) consumes it to produce file edits.

Pure deterministic Python — no LLM. Bit-identical output for same input.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Parameter:
    name: str
    variable: str
    type: str
    default: str
    required: bool
    description: str

    @classmethod
    def all_fields(cls) -> list[str]:
        return ["name", "variable", "type", "default", "required", "description"]


@dataclass
class Scenario:
    name: str
    scenario_type: str | None
    parameters: list[Parameter] = field(default_factory=list)


@dataclass
class ParameterChange:
    name: str
    head: Parameter
    base: Parameter
    fields_changed: list[str]


@dataclass
class ModifiedScenario:
    name: str
    head: Scenario
    base: Scenario
    parameters_added: list[Parameter] = field(default_factory=list)
    parameters_removed: list[Parameter] = field(default_factory=list)
    parameters_modified: list[ParameterChange] = field(default_factory=list)
    fields_changed: list[str] = field(default_factory=list)  # scenario-level changes


@dataclass
class ChangeSet:
    scenarios_added: list[Scenario] = field(default_factory=list)
    scenarios_removed: list[Scenario] = field(default_factory=list)
    scenarios_modified: list[ModifiedScenario] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

_SCENARIO_HEADER_RE = re.compile(r"^## scenario:\s*(.+?)\s*$", re.MULTILINE)
_SCENARIO_TYPE_RE = re.compile(r"^scenario_type:\s*(.+?)\s*$", re.MULTILINE)


def _split_into_scenario_blocks(text: str) -> list[tuple[str, str]]:
    """Yield (name, body) for each `## scenario: <name>` section in text.

    Body runs from after the scenario header to (but not including) the
    next scenario header or end of input.
    """
    matches = list(_SCENARIO_HEADER_RE.finditer(text))
    if not matches:
        return []
    blocks = []
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        blocks.append((name, body))
    return blocks


def _unescape_pipe(s: str) -> str:
    """The renderer escapes `|` as `\\|` so table rows parse cleanly. Reverse it."""
    return s.replace("\\|", "|")


def _parse_param_table(body: str) -> list[Parameter]:
    """Find a markdown table inside `body` and parse it into Parameters.

    The renderer emits a fixed column order:
      | name | variable | type | default | required | description |
    We parse defensively (use the header to map columns), but assume the
    column NAMES are stable since we control the renderer.
    """
    lines = body.splitlines()
    # Find the header row — first line that looks like our table header
    header_idx = None
    for i, line in enumerate(lines):
        if "|" in line and "name" in line.lower() and "variable" in line.lower():
            header_idx = i
            break

    if header_idx is None or header_idx + 1 >= len(lines):
        return []

    # Header row → list of column names
    header_cells = [c.strip().lower() for c in _split_table_row(lines[header_idx])]

    # Skip the separator row (line after header)
    data_start = header_idx + 2
    if data_start > len(lines):
        return []

    params = []
    for line in lines[data_start:]:
        if "|" not in line:
            break  # table ended
        cells = _split_table_row(line)
        if len(cells) != len(header_cells):
            # Misaligned row — skip rather than crash
            continue
        row = dict(zip(header_cells, cells))

        params.append(Parameter(
            name=_unescape_pipe(row.get("name", "")),
            variable=_unescape_pipe(row.get("variable", "")),
            type=_unescape_pipe(row.get("type", "")),
            default=_unescape_pipe(row.get("default", "")),
            required=row.get("required", "false").strip().lower() == "true",
            description=_unescape_pipe(row.get("description", "")),
        ))
    return params


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row on `|`, respecting `\\|` escapes.

    Strips outer pipes (if present) and trims each cell.
    """
    # Replace escaped pipes with a sentinel, split, then restore.
    sentinel = "\x00ESC_PIPE\x00"
    safe = line.replace("\\|", sentinel)
    cells = [c.strip().replace(sentinel, "\\|") for c in safe.split("|")]
    # Strip leading and trailing empty cells from outer pipes
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def parse_llms_full_txt(text: str) -> dict[str, Scenario]:
    """Parse the full digest text into a dict of scenario_name → Scenario."""
    if not text or not text.strip():
        return {}

    result: dict[str, Scenario] = {}
    for name, body in _split_into_scenario_blocks(text):
        type_match = _SCENARIO_TYPE_RE.search(body)
        scenario_type = type_match.group(1).strip() if type_match else None
        # Sentinel `(unknown)` from renderer means "no SCENARIO_TYPE in source"
        if scenario_type == "(unknown)":
            scenario_type = None

        params = _parse_param_table(body)
        result[name] = Scenario(
            name=name,
            scenario_type=scenario_type,
            parameters=params,
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Diffing
# ─────────────────────────────────────────────────────────────────────────────

def _parameter_to_dict(p: Parameter) -> dict:
    """Param → dict for value-equality comparison."""
    return {
        "name": p.name,
        "variable": p.variable,
        "type": p.type,
        "default": p.default,
        "required": p.required,
        "description": p.description,
    }


def _diff_parameters(
    head_params: list[Parameter],
    base_params: list[Parameter],
) -> tuple[list[Parameter], list[Parameter], list[ParameterChange]]:
    """Compare two lists of params keyed by `name`.

    Returns: (added, removed, modified).
    """
    head_by_name = {p.name: p for p in head_params}
    base_by_name = {p.name: p for p in base_params}

    added = [head_by_name[n] for n in head_by_name if n not in base_by_name]
    removed = [base_by_name[n] for n in base_by_name if n not in head_by_name]

    modified = []
    for name in head_by_name:
        if name not in base_by_name:
            continue
        h = head_by_name[name]
        b = base_by_name[name]
        h_dict = _parameter_to_dict(h)
        b_dict = _parameter_to_dict(b)
        fields_changed = [k for k in h_dict if h_dict[k] != b_dict[k]]
        if fields_changed:
            modified.append(ParameterChange(
                name=name, head=h, base=b, fields_changed=fields_changed,
            ))

    # Sort all outputs by name for determinism
    return (
        sorted(added, key=lambda p: p.name),
        sorted(removed, key=lambda p: p.name),
        sorted(modified, key=lambda c: c.name),
    )


def diff_scenarios(
    head: dict[str, Scenario],
    base: dict[str, Scenario],
) -> ChangeSet:
    """Compute the ChangeSet between two parsed scenario maps."""
    cs = ChangeSet()

    head_names = set(head.keys())
    base_names = set(base.keys())

    cs.scenarios_added = sorted(
        (head[n] for n in head_names - base_names),
        key=lambda s: s.name,
    )
    cs.scenarios_removed = sorted(
        (base[n] for n in base_names - head_names),
        key=lambda s: s.name,
    )

    for name in sorted(head_names & base_names):
        h_scen = head[name]
        b_scen = base[name]

        added_p, removed_p, modified_p = _diff_parameters(
            h_scen.parameters, b_scen.parameters,
        )

        # Scenario-level field changes (e.g., scenario_type changed)
        scenario_fields_changed = []
        if h_scen.scenario_type != b_scen.scenario_type:
            scenario_fields_changed.append("scenario_type")

        if added_p or removed_p or modified_p or scenario_fields_changed:
            cs.scenarios_modified.append(ModifiedScenario(
                name=name,
                head=h_scen,
                base=b_scen,
                parameters_added=added_p,
                parameters_removed=removed_p,
                parameters_modified=modified_p,
                fields_changed=scenario_fields_changed,
            ))

    return cs


def extract(head_digest: str, base_digest: str) -> ChangeSet:
    """Top-level: parse both digests and produce a ChangeSet."""
    head_scenarios = parse_llms_full_txt(head_digest)
    base_scenarios = parse_llms_full_txt(base_digest)
    return diff_scenarios(head_scenarios, base_scenarios)
