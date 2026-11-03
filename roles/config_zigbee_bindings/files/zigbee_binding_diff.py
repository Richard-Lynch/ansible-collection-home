# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalise and diff Zigbee binding tables. Tests: test_zigbee_binding_diff.py beside this file.

WHY THIS IS PYTHON AND NOT JINJA. `binds_get` and `bind_ieee` speak DIFFERENT VOCABULARIES:

    binds_get  reports  {"cluster_id": "0x0006", ...}     <- hex STRING
    bind_ieee  accepts  cluster: genOnOff                  <- NAME

and inventory is naturally written in names. So a plain `declared - live` set difference compares
"genOnOff" against "0x0006", finds nothing in common, and concludes EVERY BINDING IS MISSING --
forever. The convergence loop would then re-write on every pass, never confirm, and the drain
automation would re-issue a Bind_req on every button press for the life of the device. It presents
as a device fault, not as a bug here.

That is a diff structurally incapable of ever reporting a match -- a silent guard. In Jinja the
failure mode of a selectattr/map chain
is a silent empty list; here it is a raised exception with a test proving it raises.

NO NETWORK I/O. This module takes declared + live as plain data and returns the delta, which is
what lets the whole diff be tested without a Zigbee radio.

Stdlib only, by requirement: its test suite runs with no third-party packages installed, and
importing PyYAML or ansible here would both break that and make it un-unit-testable.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

# The COMPLETE set of bindable clusters. Zigbee binding is only defined for these five. A closed
# table rather than a partial map is the point:
# an unknown cluster must RAISE (below), never fall through to a value that matches nothing.
CLUSTERS: dict[str, int] = {
    "genScenes": 0x0005,
    "genOnOff": 0x0006,
    "genLevelCtrl": 0x0008,
    "lightingColorCtrl": 0x0300,
    "closuresWindowCovering": 0x0102,
}

CLUSTER_NAMES: dict[int, str] = {v: k for k, v in CLUSTERS.items()}

# Zigbee addrmode values as they appear in a binding table entry (ZDO Mgmt_Bind_rsp).
ADDRMODE_GROUP = 1
ADDRMODE_DEVICE = 3

_IEEE_RE = re.compile(r"\A[0-9a-f]{2}(:[0-9a-f]{2}){7}\Z")
_HEX_RE = re.compile(r"\A0x[0-9a-f]+\Z", re.IGNORECASE)


class BindingError(ValueError):
    """Raised when input cannot be normalised. DELIBERATELY not a soft failure.

    Every branch in this module either produces a correct answer or raises. There is no
    `return None`, and no `.get(k, default)` on a key whose absence means the payload is not what
    we think it is -- an invented value flows into a comparison, matches nothing, and reports
    "missing" with total confidence.
    """


class Bind(NamedTuple):
    """One normalised binding, comparable by value.

    src/dst are colon-separated lowercase IEEE. cluster is an INT. A group target has
    dst_ieee=None and group set; a device target has group=None.
    """

    src: str
    src_ep: int
    cluster: int
    dst_ieee: str | None
    dst_ep: int | None
    group: int | None

    def describe(self) -> str:
        target = (
            f"group {self.group}" if self.group is not None else f"{self.dst_ieee}:{self.dst_ep}"
        )
        return f"{self.src}:{self.src_ep} --{cluster_name(self.cluster)}--> {target}"


class Delta(NamedTuple):
    """The result of a diff.

    missing: declared, not on the device -> what converge writes.
    present: declared and on the device  -> what confirms.
    extra:   on the device, not declared -> what prune would remove, and what converge MUST NOT
             touch (ZHA binds every device to the coordinator during interview; a converge that
             pruned would tear those out network-wide on its first run).
    """

    missing: list[Bind]
    present: list[Bind]
    extra: list[Bind]

    @property
    def converged(self) -> bool:
        return not self.missing


def normalise_ieee(value: Any) -> str:
    """Any accepted spelling of an IEEE address -> colon-separated lowercase.

    Accepts the three forms that actually turn up: zigpy's repr (`00:11:22:...`, what binds_get
    emits), the 0x-prefixed form used throughout the Zigbee2MQTT docs, and bare hex. Rejects
    anything else rather than guessing -- a mistyped IEEE that normalises to *something* produces
    a binding that silently never converges and reads as "the device is asleep".
    """
    if not isinstance(value, str):
        raise BindingError(f"IEEE address must be a string, got {type(value).__name__}: {value!r}")
    raw = value.strip().lower()
    if _IEEE_RE.match(raw):
        return raw
    compact = raw[2:] if raw.startswith("0x") else raw
    if len(compact) == 16 and all(c in "0123456789abcdef" for c in compact):
        return ":".join(compact[i : i + 2] for i in range(0, 16, 2))
    raise BindingError(
        f"not an IEEE address: {value!r} "
        "(expected 00:11:22:33:44:55:66:77, 0x0011223344556677, or 0011223344556677)"
    )


def normalise_cluster(value: Any) -> int:
    """A cluster name, hex string or int -> its integer cluster id.

    THE CORE OF THIS MODULE. Raises on an unknown cluster instead of returning None: a None flows
    into a set comparison, matches nothing, and the binding reports missing forever.
    """
    if isinstance(value, bool):  # bool is an int subclass; a bool here is always a mistake
        raise BindingError(f"cluster must not be a boolean: {value!r}")
    if isinstance(value, int):
        if value in CLUSTER_NAMES:
            return value
        raise BindingError(
            f"cluster id {value:#06x} is not bindable; expected one of "
            + ", ".join(f"{n} ({i:#06x})" for n, i in sorted(CLUSTERS.items()))
        )
    if not isinstance(value, str):
        raise BindingError(f"cluster must be a name, hex string or int, got {value!r}")
    raw = value.strip()
    if raw in CLUSTERS:
        return CLUSTERS[raw]
    if _HEX_RE.match(raw):
        parsed = int(raw, 16)
        if parsed in CLUSTER_NAMES:
            return parsed
        raise BindingError(f"cluster {raw} is not bindable (id {parsed:#06x})")
    raise BindingError(f"unknown cluster {value!r}; expected one of " + ", ".join(sorted(CLUSTERS)))


def cluster_name(cluster: int) -> str:
    """Integer cluster id -> the name bind_ieee expects. Raises on an unknown id."""
    try:
        return CLUSTER_NAMES[cluster]
    except KeyError:
        raise BindingError(f"no bindable cluster with id {cluster:#06x}") from None


def _as_int(value: Any, what: str) -> int:
    """Coerce an endpoint/group id to int, accepting the hex strings binds_get emits.

    Deliberately explicit rather than `| int`-style coercion: "3" == 3 is False in Jinja and the
    STRINT-01 trap is exactly this, so the conversion happens once, here, with a real error.
    """
    if isinstance(value, bool):
        raise BindingError(f"{what} must not be a boolean: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip()
        try:
            return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
        except ValueError:
            raise BindingError(f"{what} is not a number: {value!r}") from None
    raise BindingError(f"{what} must be an int or numeric string, got {value!r}")


def parse_live(payload: Any, source_ieee: str | None = None) -> list[Bind]:
    """A zha_toolkit `binds_get` result -> normalised bindings.

    RAISES when the payload is not parseable, and that is the single most important behaviour in
    this module. An unreadable table must NEVER present as an empty one: `binds_get` against a
    sleeping device can return nothing useful, and if that reads as "this device has no bindings"
    then sweep re-opens every confirmed binding and writes to the device on every wake, forever.

    It is the same trap as a `uri` read-back whose `.json` is absent and defaults to `[]`, with
    worse consequences, because here it causes writes rather than just a wrong report.

    An EMPTY-but-present table is legitimate and returns []: that is a device with no bindings.
    The two cases are distinguished by `success` plus the presence of the result key, never by the
    length of the list.
    """
    if not isinstance(payload, dict):
        raise BindingError(f"binds_get payload is not a mapping: {type(payload).__name__}")

    # zha_toolkit returns its event_data shape; HA's ?return_response wraps it in service_response.
    body = payload.get("service_response", payload)
    if not isinstance(body, dict):
        raise BindingError("binds_get service_response is not a mapping")

    if body.get("success") is False:
        raise BindingError(
            "binds_get reported success=false "
            f"(device unreachable or asleep): {body.get('result') or body.get('error') or body}"
        )

    if "result" not in body:
        raise BindingError(
            "binds_get payload has no 'result' key -- the table was NOT read. "
            "Treating this as 'no bindings' would be wrong. Keys present: "
            + ", ".join(sorted(map(str, body)))
        )

    result = body["result"]
    if result is None:
        raise BindingError("binds_get returned result=null -- the table was not read")
    if not isinstance(result, list):
        raise BindingError(f"binds_get result is not a list: {type(result).__name__}")

    out: list[Bind] = []
    for i, entry in enumerate(result):
        if not isinstance(entry, dict):
            raise BindingError(f"binding[{i}] is not a mapping: {entry!r}")
        for key in ("src", "src_ep", "cluster_id", "dst"):
            if key not in entry:
                raise BindingError(f"binding[{i}] has no {key!r}: {entry!r}")
        dst = entry["dst"]
        if not isinstance(dst, dict):
            raise BindingError(f"binding[{i}] dst is not a mapping: {dst!r}")
        if "addrmode" not in dst:
            raise BindingError(f"binding[{i}] dst has no addrmode: {dst!r}")
        addrmode = _as_int(dst["addrmode"], f"binding[{i}] addrmode")

        try:
            cluster = normalise_cluster(entry["cluster_id"])
        except BindingError:
            # A device's table may legitimately hold a NON-bindable cluster (ZHA binds reporting
            # clusters to the coordinator during interview). That is not an error in the payload --
            # it is simply not something this role manages, so it is skipped rather than raised.
            # It is also never written, so it can never appear in `missing`.
            continue

        if addrmode == ADDRMODE_GROUP:
            if "group" not in dst:
                raise BindingError(f"binding[{i}] group target has no group id: {dst!r}")
            out.append(
                Bind(
                    src=normalise_ieee(entry["src"]),
                    src_ep=_as_int(entry["src_ep"], f"binding[{i}] src_ep"),
                    cluster=cluster,
                    dst_ieee=None,
                    dst_ep=None,
                    group=_as_int(dst["group"], f"binding[{i}] group"),
                )
            )
        elif addrmode == ADDRMODE_DEVICE:
            for key in ("dst_ieee", "dst_ep"):
                if key not in dst:
                    raise BindingError(f"binding[{i}] device target has no {key!r}: {dst!r}")
            out.append(
                Bind(
                    src=normalise_ieee(entry["src"]),
                    src_ep=_as_int(entry["src_ep"], f"binding[{i}] src_ep"),
                    cluster=cluster,
                    dst_ieee=normalise_ieee(dst["dst_ieee"]),
                    dst_ep=_as_int(dst["dst_ep"], f"binding[{i}] dst_ep"),
                    group=None,
                )
            )
        else:
            # addrmode 0/2 (group-with-no-endpoint, 16-bit NWK) are not emitted for a binding
            # table in practice. Raise rather than skip: an unrecognised target silently dropped
            # would make an existing binding look missing, and we would write a duplicate.
            raise BindingError(f"binding[{i}] has unsupported addrmode {addrmode}: {dst!r}")

    if source_ieee is not None:
        want = normalise_ieee(source_ieee)
        mismatched = sorted({b.src for b in out if b.src != want})
        if mismatched:
            # Guards against pairing one device's declaration with another's table, which would
            # otherwise diff two unrelated devices and write to the wrong one.
            raise BindingError(
                f"binds_get for {want} returned bindings sourced from {', '.join(mismatched)}"
            )
    return out


def parse_declared(entries: Any) -> list[Bind]:
    """The inventory `zigbee_bindings` list -> normalised bindings, one per cluster.

    Fans each declaration out across its `clusters`, because the device's binding table has one
    row per cluster and the diff must compare like with like.

    Entries with `state: absent` are EXCLUDED: "this must not exist" is prune's business, never
    converge's, so an absent declaration can never end up in `missing`.
    """
    if not isinstance(entries, list):
        raise BindingError(f"zigbee_bindings must be a list, got {type(entries).__name__}")

    out: list[Bind] = []
    for i, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise BindingError(f"zigbee_bindings[{i}] is not a mapping: {raw!r}")
        label = raw.get("name") or f"zigbee_bindings[{i}]"

        state = raw.get("state", "present")
        if state not in ("present", "absent"):
            raise BindingError(f"{label}: state must be 'present' or 'absent', got {state!r}")
        if state == "absent":
            continue

        if "from" not in raw:
            raise BindingError(f"{label}: no 'from' (source IEEE)")
        has_to, has_group = "to" in raw, "to_group" in raw
        if has_to == has_group:
            # Neither is the dangerous case: bind_ieee's documented default with no target is the
            # COORDINATOR, so a declaration missing both would quietly add a real binding nobody
            # asked for. Both is simply ambiguous. Checked here as well as in _preflight.yml
            # because a role argspec cannot express mutual exclusion of sub-keys at all.
            raise BindingError(
                f"{label}: exactly one of 'to' (device IEEE) or 'to_group' (group id) is required"
                + (" -- both were given" if has_to else " -- neither was given")
            )

        clusters = raw.get("clusters")
        if not isinstance(clusters, list) or not clusters:
            raise BindingError(f"{label}: 'clusters' must be a non-empty list")

        src = normalise_ieee(raw["from"])
        src_ep = _as_int(raw.get("from_endpoint", 1), f"{label}: from_endpoint")
        for cluster_raw in clusters:
            cluster = normalise_cluster(cluster_raw)
            if has_group:
                bind = Bind(
                    src, src_ep, cluster, None, None, _as_int(raw["to_group"], f"{label}: to_group")
                )
            else:
                bind = Bind(
                    src,
                    src_ep,
                    cluster,
                    normalise_ieee(raw["to"]),
                    _as_int(raw.get("to_endpoint", 1), f"{label}: to_endpoint"),
                    None,
                )
            if bind in out:
                continue  # declared twice -- idempotent, never a duplicate write
            out.append(bind)
    return out


def diff(declared: list[Bind], live: list[Bind]) -> Delta:
    """Compare normalised declared against normalised live.

    TOTAL and symmetric: every live binding is either `present` or `extra`, and every declared one
    either `present` or `missing`. Nothing is silently dropped, so prune sees a complete picture
    and status can report undeclared bindings as information rather than hiding them.
    """
    want, have = set(declared), set(live)
    return Delta(
        missing=[b for b in declared if b not in have],
        present=[b for b in declared if b in have],
        extra=[b for b in live if b not in want],
    )


def to_declaration(bind: Bind, name: str = "") -> dict[str, Any]:
    """A normalised Bind -> an inventory declaration stanza for it.

    Used by `probe` to emit a paste-ready zigbee_bindings block, so nobody has to hand-type a
    16-hex-digit IEEE -- a mistyped one produces a binding that never converges and reads as "the
    device is asleep", which is the most expensive failure in this system.

    Emits one cluster per stanza rather than grouping: grouping requires deciding which bindings
    "belong together", and getting that wrong silently merges two different targets.
    """
    out: dict[str, Any] = {}
    if name:
        out["name"] = name
    out["from"] = bind.src
    out["from_endpoint"] = bind.src_ep
    if bind.group is not None:
        out["to_group"] = bind.group
    else:
        out["to"] = bind.dst_ieee
        out["to_endpoint"] = bind.dst_ep
    out["clusters"] = [cluster_name(bind.cluster)]
    return out


def to_bind_call(bind: Bind) -> dict[str, Any]:
    """A normalised Bind -> the zha_toolkit service arguments that create it.

    Converts back to the NAME vocabulary, which is the half of the asymmetry this module exists to
    bridge. `command_data` carries the target: the group id for a group bind, the IEEE for a
    device bind. It is never omitted -- omitting it binds to the coordinator.
    """
    args: dict[str, Any] = {
        "ieee": bind.src,
        "endpoint": bind.src_ep,
        "cluster": bind.cluster,
        "command_data": bind.group if bind.group is not None else bind.dst_ieee,
    }
    if bind.group is None:
        args["dst_endpoint"] = bind.dst_ep
    return args
