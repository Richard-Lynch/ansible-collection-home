# SPDX-License-Identifier: GPL-3.0-or-later
"""Ansible filters over zigbee_binding_diff.py.

A THIN BRIDGE, holding no logic of its own. Everything these filters do lives in
`files/zigbee_binding_diff.py`, which is pure, stdlib-only and unit-tested by its own suite
(`test_zigbee_binding_diff.py` beside it) without an Ansible or a Zigbee radio in sight. Putting
the normalisation here instead would make it testable only through a molecule run.

The filters return PLAIN DICTS rather than the module's NamedTuples: Ansible has to serialise
whatever a filter returns, and a namedtuple round-trips through its JSON encoder as a bare list,
which would make `item.cluster` silently unavailable in a template.

A collection filter rather than the role's own filter_plugins/ because a role-local filter is only
loaded while that ROLE is executing: a playbook that merely renders the role's template (as the
molecule scenario does) cannot see it, and fails with "No filter named ...".
Referenced as `richard_lynch.home.zigbee_*`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

from ansible.errors import AnsibleFilterError

# <collection>/plugins/filter/ -> the collection root is 2 levels up.
# The diff module stays in the ROLE's files/ so its test suite and molecule both reach it as a
# plain file; duplicating it here would create two copies that drift.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DIFF_PY = _REPO_ROOT / "roles" / "config_zigbee_bindings" / "files" / "zigbee_binding_diff.py"


def _load() -> Any:
    """Import the role's diff module by path.

    By path rather than as an installed module because it lives in the role's files/ (where its
    test suite and molecule can both reach it) and roles are not importable.
    Cached in sys.modules so a play templating hundreds of expressions pays this once.
    """
    cached = sys.modules.get("_zigbee_binding_diff")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("_zigbee_binding_diff", _DIFF_PY)
    if spec is None or spec.loader is None:
        raise AnsibleFilterError(f"cannot load the binding diff module from {_DIFF_PY}")
    module = importlib.util.module_from_spec(spec)
    # Required BEFORE exec_module: NamedTuple resolves its annotations through
    # sys.modules[cls.__module__], which is None for an unregistered importlib module.
    sys.modules["_zigbee_binding_diff"] = module
    spec.loader.exec_module(module)
    return module


def _as_dict(bind: Any) -> dict[str, Any]:
    m = _load()
    return {
        "src": bind.src,
        "src_ep": bind.src_ep,
        "cluster": bind.cluster,
        "cluster_name": m.cluster_name(bind.cluster),
        "dst_ieee": bind.dst_ieee,
        "dst_ep": bind.dst_ep,
        "group": bind.group,
        "describe": bind.describe(),
    }


def zigbee_parse_declared(value: Any) -> list[dict[str, Any]]:
    """The inventory `zigbee_bindings` list -> one normalised dict per (binding, cluster)."""
    m = _load()
    try:
        return [_as_dict(b) for b in m.parse_declared(value)]
    except m.BindingError as exc:
        # Surfaced as a filter error so the play STOPS here, naming the offending declaration,
        # rather than carrying an empty list forward and reporting "nothing to do".
        raise AnsibleFilterError(f"zigbee_bindings declaration is invalid: {exc}") from exc


def zigbee_parse_live(value: Any, source_ieee: str | None = None) -> list[dict[str, Any]]:
    """A `binds_get` response -> normalised dicts.

    RAISES when the table could not be read, which is the whole point: an unreadable table must
    never present as an empty one. If it did, `sweep` would re-open every confirmed binding on a
    merely-offline device and write to it on every wake.
    """
    m = _load()
    try:
        return [_as_dict(b) for b in m.parse_live(value, source_ieee)]
    except m.BindingError as exc:
        raise AnsibleFilterError(f"could not read the binding table: {exc}") from exc


def zigbee_binding_delta(
    declared: Any, live: Any, source_ieee: str | None = None
) -> dict[str, Any]:
    """Diff a declaration against a raw `binds_get` response. The role's main entry point.

    Takes the RAW response rather than pre-parsed bindings so the parse and the diff cannot drift
    apart across two separate task invocations.
    """
    m = _load()
    try:
        want = m.parse_declared(declared)
        have = m.parse_live(live, source_ieee)
        delta = m.diff(want, have)
    except m.BindingError as exc:
        raise AnsibleFilterError(f"binding diff failed: {exc}") from exc
    return {
        "missing": [_as_dict(b) for b in delta.missing],
        "present": [_as_dict(b) for b in delta.present],
        "extra": [_as_dict(b) for b in delta.extra],
        "converged": delta.converged,
        "counts": {
            "declared": len(want),
            "missing": len(delta.missing),
            "present": len(delta.present),
            "extra": len(delta.extra),
        },
    }


def _bind_from_dict(m: Any, value: Any, what: str) -> Any:
    if not isinstance(value, dict):
        raise AnsibleFilterError(
            f"{what} expects normalised binding mappings, got {type(value).__name__}"
        )
    try:
        return m.Bind(
            src=value["src"],
            src_ep=value["src_ep"],
            cluster=value["cluster"],
            dst_ieee=value.get("dst_ieee"),
            dst_ep=value.get("dst_ep"),
            group=value.get("group"),
        )
    except KeyError as exc:
        raise AnsibleFilterError(
            f"{what}: binding is missing {exc}. Expected the output of zigbee_parse_declared, "
            "not a raw inventory declaration."
        ) from exc


def zigbee_delta_normalised(
    declared: Any, live: Any, source_ieee: str | None = None
) -> dict[str, Any]:
    """Diff ALREADY-NORMALISED declared bindings against a raw `binds_get` response.

    Separate from zigbee_binding_delta because the role parses the whole declaration ONCE in
    _preflight (so a bad entry fails early, naming itself) and then slices that per device. Feeding
    those normalised dicts back through the raw parser raises "no 'from'" -- they no longer carry
    inventory keys. Caught before this ever ran; the two entry points now differ by name so the
    mistake cannot be made silently.
    """
    m = _load()
    if not isinstance(declared, list):
        raise AnsibleFilterError(
            f"zigbee_delta_normalised expects a list, got {type(declared).__name__}"
        )
    try:
        want = [_bind_from_dict(m, d, "zigbee_delta_normalised") for d in declared]
        have = m.parse_live(live, source_ieee)
        delta = m.diff(want, have)
    except m.BindingError as exc:
        raise AnsibleFilterError(f"binding diff failed: {exc}") from exc
    return {
        "missing": [_as_dict(b) for b in delta.missing],
        "present": [_as_dict(b) for b in delta.present],
        "extra": [_as_dict(b) for b in delta.extra],
        "converged": delta.converged,
        "counts": {
            "declared": len(want),
            "missing": len(delta.missing),
            "present": len(delta.present),
            "extra": len(delta.extra),
        },
    }


def zigbee_normalise_ieee(value: Any) -> str:
    """Any accepted IEEE spelling -> colon-separated lowercase.

    Needed because `zha_devices` and the declaration may not agree on format: a bare `| lower` is
    NOT sufficient, since "0x0011223344556677" lowercased still does not equal
    "00:11:22:33:44:55:66:77". Comparing those two with `lower` would make an
    "is this device known to ZHA?" assert reject every perfectly valid device.
    """
    m = _load()
    try:
        return str(m.normalise_ieee(value))
    except m.BindingError as exc:
        raise AnsibleFilterError(str(exc)) from exc


def zigbee_devices_by_ieee(value: Any) -> list[dict[str, Any]]:
    """A zha_devices list -> the same list with `ieee` normalised.

    So every later lookup compares like with like. Matching a declaration's
    "00:11:22:..." against a reported "0x001122..." finds nothing and yields an empty device
    dict, which would make `status` report "never seen" for a perfectly healthy device and send
    someone to check its battery. A devices entry with no usable ieee is dropped rather than
    guessed at, and the count difference is visible in the report.
    """
    m = _load()
    if not isinstance(value, list):
        raise AnsibleFilterError(
            f"zigbee_devices_by_ieee expects a list, got {type(value).__name__}"
        )
    out: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict) or "ieee" not in entry:
            continue
        try:
            normalised = m.normalise_ieee(entry["ieee"])
        except m.BindingError:
            continue
        out.append({**entry, "ieee": normalised})
    return out


def zigbee_bind_args(value: Any) -> dict[str, Any]:
    """A normalised binding dict -> the zha_toolkit service arguments that create it.

    Converts back to the vocabulary bind_ieee wants. `command_data` is ALWAYS populated: omitting
    it makes zha_toolkit bind to the coordinator, which is a real binding nobody asked for.
    """
    m = _load()
    if not isinstance(value, dict):
        raise AnsibleFilterError(f"zigbee_bind_args expects a mapping, got {type(value).__name__}")
    try:
        bind = m.Bind(
            src=value["src"],
            src_ep=value["src_ep"],
            cluster=value["cluster"],
            dst_ieee=value.get("dst_ieee"),
            dst_ep=value.get("dst_ep"),
            group=value.get("group"),
        )
        return m.to_bind_call(bind)
    except KeyError as exc:
        raise AnsibleFilterError(
            f"binding is missing {exc}; expected a normalised binding"
        ) from exc
    except m.BindingError as exc:
        raise AnsibleFilterError(f"cannot build bind arguments: {exc}") from exc


def zigbee_declaration_stanza(value: Any, name: str = "") -> dict[str, Any]:
    """A normalised binding dict -> an inventory declaration stanza (probe's paste-ready output)."""
    m = _load()
    try:
        return m.to_declaration(_bind_from_dict(m, value, "zigbee_declaration_stanza"), name)
    except m.BindingError as exc:
        raise AnsibleFilterError(f"cannot build a declaration: {exc}") from exc


class FilterModule:
    """Zigbee binding normalisation and diff filters."""

    def filters(self) -> dict[str, Any]:
        return {
            "zigbee_parse_declared": zigbee_parse_declared,
            "zigbee_parse_live": zigbee_parse_live,
            "zigbee_binding_delta": zigbee_binding_delta,
            "zigbee_delta_normalised": zigbee_delta_normalised,
            "zigbee_bind_args": zigbee_bind_args,
            "zigbee_normalise_ieee": zigbee_normalise_ieee,
            "zigbee_devices_by_ieee": zigbee_devices_by_ieee,
            "zigbee_declaration_stanza": zigbee_declaration_stanza,
        }
