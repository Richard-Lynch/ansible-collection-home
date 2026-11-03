# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for zigbee_binding_diff.py. Run: python3 test_zigbee_binding_diff.py

EVERY TEST PROVES A FAILURE IS POSSIBLE, not just that the happy path passes. Two of the cases
here exist because the obvious implementation is silently wrong in a way no happy-path test would
catch:

  * THE VOCABULARY LANDMINE. binds_get emits cluster_id "0x0006"; bind_ieee takes "genOnOff". An
    un-normalised diff finds nothing in common and reports every binding missing FOREVER --
    re-writing on every pass, never confirming. `name matches hex` is the test that fails if the
    normalisation is ever dropped.
  * UNREADABLE != EMPTY. A binds_get that could not read the table must RAISE, not return []. If
    it returned [], sweep would re-open every confirmed binding on a merely-offline device and
    write to it on every wake. Several cases assert a raise, and `empty table parses` asserts the
    legitimate empty case still works -- so the refusal cannot be implemented as "raise always".

Stdlib only, no network, no Zigbee radio.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent


def load() -> Any:
    spec = importlib.util.spec_from_file_location("zbd", HERE / "zigbee_binding_diff.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registering in sys.modules is REQUIRED: NamedTuple resolves annotations via
    # sys.modules[cls.__module__], which is None for an unregistered importlib module.
    sys.modules["zbd"] = module
    spec.loader.exec_module(module)
    return module


m = load()

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    (PASS if condition else FAIL).append(name)
    suffix = f"  -- {detail}" if detail else ""
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{suffix}")


def raises(name: str, fn: Any, *args: Any, **kwargs: Any) -> None:
    """Assert fn raises BindingError. A refusal is only credible if something proves it fires."""
    try:
        result = fn(*args, **kwargs)
    except m.BindingError as exc:
        check(name, True, str(exc)[:70])
    except Exception as exc:  # noqa: BLE001 - a non-BindingError is itself a failure
        check(name, False, f"raised {type(exc).__name__}, expected BindingError: {exc}")
    else:
        check(name, False, f"did NOT raise; returned {result!r}")


REMOTE = "00:11:22:33:44:55:66:77"
LAMP = "00:17:88:01:02:03:04:05"


def live(*entries: dict[str, Any], success: bool = True) -> dict[str, Any]:
    """Wrap entries the way HA's ?return_response wraps a zha_toolkit result."""
    return {"service_response": {"success": success, "result": list(entries)}}


def device_entry(
    cluster_id: str, src: str = REMOTE, dst: str = LAMP, src_ep: int = 1, dst_ep: int = 1
):
    return {
        "src": src,
        "src_ep": src_ep,
        "cluster_id": cluster_id,
        "dst": {"addrmode": 3, "dst_ieee": dst, "dst_ep": dst_ep},
    }


def group_entry(cluster_id: str, group: int = 12, src: str = REMOTE, src_ep: int = 1):
    return {
        "src": src,
        "src_ep": src_ep,
        "cluster_id": cluster_id,
        "dst": {"addrmode": 1, "group": group},
    }


print("\n-- cluster normalisation: THE VOCABULARY LANDMINE --")

check("name -> id", m.normalise_cluster("genOnOff") == 0x0006)
check("hex string -> id", m.normalise_cluster("0x0006") == 0x0006)
check("uppercase hex -> id", m.normalise_cluster("0X0006") == 0x0006)
check("int -> id", m.normalise_cluster(6) == 0x0006)
check(
    "name and hex normalise EQUAL (the landmine)",
    m.normalise_cluster("genOnOff") == m.normalise_cluster("0x0006"),
)
check("round-trips back to the name bind_ieee wants", m.cluster_name(0x0006) == "genOnOff")
check("all five bindable clusters are mapped", len(m.CLUSTERS) == 5, sorted(m.CLUSTERS))
check(
    "every cluster round-trips",
    all(m.cluster_name(m.normalise_cluster(n)) == n for n in m.CLUSTERS),
)
raises("typo'd cluster name raises (not silently missing)", m.normalise_cluster, "genOnoff")
raises("non-bindable cluster id raises", m.normalise_cluster, 0x0402)
raises("non-bindable hex raises", m.normalise_cluster, "0x0402")
raises("bool is rejected", m.normalise_cluster, True)
raises("None is rejected", m.normalise_cluster, None)
raises("unknown id has no name", m.cluster_name, 0x0402)

print("\n-- IEEE normalisation --")

check("colon form passes through", m.normalise_ieee(REMOTE) == REMOTE)
check("0x form normalises", m.normalise_ieee("0x0011223344556677") == REMOTE)
check("bare hex normalises", m.normalise_ieee("0011223344556677") == REMOTE)
check("uppercase normalises", m.normalise_ieee(REMOTE.upper()) == REMOTE)
check(
    "0x and colon forms are EQUAL",
    m.normalise_ieee("0x0011223344556677") == m.normalise_ieee(REMOTE),
)
raises("too short raises", m.normalise_ieee, "0x001122")
raises("non-hex raises", m.normalise_ieee, "00:11:22:33:44:55:66:zz")
raises("friendly name raises (names are UI state, not identity)", m.normalise_ieee, "Bedside left")
raises("int is rejected", m.normalise_ieee, 12345)

print("\n-- parse_live: UNREADABLE MUST NOT LOOK EMPTY --")

check("empty but present table parses as []", m.parse_live(live()) == [])
raises("no 'result' key raises", m.parse_live, {"service_response": {"success": True}})
raises("result=null raises", m.parse_live, {"service_response": {"success": True, "result": None}})
raises("success=false raises", m.parse_live, live(device_entry("0x0006"), success=False))
raises("result not a list raises", m.parse_live, {"service_response": {"result": {}}})
raises("payload not a mapping raises", m.parse_live, ["nope"])
raises(
    "entry missing cluster_id raises",
    m.parse_live,
    {"service_response": {"result": [{"src": REMOTE, "src_ep": 1, "dst": {}}]}},
)
raises(
    "device target missing dst_ep raises",
    m.parse_live,
    live(
        {
            "src": REMOTE,
            "src_ep": 1,
            "cluster_id": "0x0006",
            "dst": {"addrmode": 3, "dst_ieee": LAMP},
        }
    ),
)
raises(
    "group target missing group id raises",
    m.parse_live,
    live({"src": REMOTE, "src_ep": 1, "cluster_id": "0x0006", "dst": {"addrmode": 1}}),
)
raises(
    "unsupported addrmode raises (never silently dropped)",
    m.parse_live,
    live(
        {
            "src": REMOTE,
            "src_ep": 1,
            "cluster_id": "0x0006",
            "dst": {"addrmode": 2, "nwk": "0x1234"},
        }
    ),
)
raises(
    "a table sourced from the WRONG device raises",
    m.parse_live,
    live(device_entry("0x0006", src=LAMP)),
    REMOTE,
)

_parsed = m.parse_live(live(device_entry("0x0006")), REMOTE)
check("device binding parses", len(_parsed) == 1 and _parsed[0].cluster == 0x0006)
check("device binding carries dst", _parsed[0].dst_ieee == LAMP and _parsed[0].group is None)
_g = m.parse_live(live(group_entry("0x0006")), REMOTE)
check("group binding parses", _g[0].group == 12 and _g[0].dst_ieee is None)
check(
    "a non-bindable cluster in the table is SKIPPED, not fatal",
    m.parse_live(live(device_entry("0x0402"), device_entry("0x0006")))
    == m.parse_live(live(device_entry("0x0006"))),
    "ZHA binds reporting clusters to the coordinator during interview",
)
check(
    "bare event_data (no service_response wrapper) also parses",
    m.parse_live({"success": True, "result": [device_entry("0x0006")]}) == _parsed,
)

print("\n-- parse_declared --")

DECL = [{"name": "bedside", "from": REMOTE, "to": LAMP, "clusters": ["genOnOff", "genLevelCtrl"]}]
_d = m.parse_declared(DECL)
check("one declaration fans out per cluster", len(_d) == 2, [m.cluster_name(b.cluster) for b in _d])
check("from_endpoint defaults to 1", _d[0].src_ep == 1)
check("to_endpoint defaults to 1", _d[0].dst_ep == 1)
check(
    "state: absent is EXCLUDED (prune's business, never converge's)",
    m.parse_declared([{**DECL[0], "state": "absent"}]) == [],
)
check("state: present is included", len(m.parse_declared([{**DECL[0], "state": "present"}])) == 2)
check(
    "declared twice is idempotent (no duplicate write)",
    len(m.parse_declared(DECL + DECL)) == 2,
)
check(
    "group declaration parses",
    m.parse_declared([{"from": REMOTE, "to_group": 12, "clusters": ["genOnOff"]}])[0].group == 12,
)
raises(
    "NEITHER to nor to_group raises (would bind to the COORDINATOR)",
    m.parse_declared,
    [{"name": "x", "from": REMOTE, "clusters": ["genOnOff"]}],
)
raises(
    "BOTH to and to_group raises (ambiguous)",
    m.parse_declared,
    [{"name": "x", "from": REMOTE, "to": LAMP, "to_group": 12, "clusters": ["genOnOff"]}],
)
raises("no 'from' raises", m.parse_declared, [{"to": LAMP, "clusters": ["genOnOff"]}])
raises("empty clusters raises", m.parse_declared, [{"from": REMOTE, "to": LAMP, "clusters": []}])
raises("missing clusters raises", m.parse_declared, [{"from": REMOTE, "to": LAMP}])
raises("bad state raises", m.parse_declared, [{**DECL[0], "state": "maybe"}])
raises("not a list raises", m.parse_declared, {"from": REMOTE})

print("\n-- diff --")

_declared = m.parse_declared(DECL)
_both = m.parse_live(live(device_entry("0x0006"), device_entry("0x0008")), REMOTE)

_all_present = m.diff(_declared, _both)
check("fully bound -> nothing missing", _all_present.missing == [])
check("fully bound -> converged", _all_present.converged is True)
check("fully bound -> both present", len(_all_present.present) == 2)
check("fully bound -> no extras", _all_present.extra == [])

_none = m.diff(_declared, m.parse_live(live(), REMOTE))
check("nothing bound -> all missing", len(_none.missing) == 2)
check("nothing bound -> NOT converged", _none.converged is False)

_partial = m.diff(_declared, m.parse_live(live(device_entry("0x0006")), REMOTE))
check("half bound -> one missing", len(_partial.missing) == 1)
check("half bound -> the MISSING one is genLevelCtrl", _partial.missing[0].cluster == 0x0008)
check("half bound -> one present", len(_partial.present) == 1)

_extra = m.diff(_declared, _both + m.parse_live(live(device_entry("0x0005")), REMOTE))
check("undeclared binding shows as extra", len(_extra.extra) == 1)
check("extra is genScenes", _extra.extra[0].cluster == 0x0005)
check("extra does NOT make it unconverged (converge is additive only)", _extra.converged is True)

check(
    "a GROUP binding does not satisfy a DEVICE declaration",
    len(
        m.diff(
            _declared, m.parse_live(live(group_entry("0x0006"), group_entry("0x0008")), REMOTE)
        ).missing
    )
    == 2,
)
check(
    "a different endpoint does not satisfy the declaration",
    len(m.diff(_declared, m.parse_live(live(device_entry("0x0006", dst_ep=2)), REMOTE)).missing)
    == 2,
)
check(
    "diff is total: every live binding is present or extra",
    len(_extra.present) + len(_extra.extra) == len(_both) + 1,
)

print("\n-- cross-vocabulary end-to-end: the regression this module exists to prevent --")

# Inventory in NAMES, device table in HEX. This is the real-world shape, and the whole point.
_e2e = m.diff(
    m.parse_declared(
        [{"from": "0x0011223344556677", "to": LAMP.upper(), "clusters": ["genOnOff"]}]
    ),
    m.parse_live(live(device_entry("0x0006")), REMOTE),
)
check("names vs hex, 0x vs colon, upper vs lower -> CONVERGED", _e2e.converged is True)
check("...and reports it present, not missing", len(_e2e.present) == 1)

print("\n-- to_bind_call: back to the NAME vocabulary --")

_call = m.to_bind_call(_declared[0])
check("passes IEEE as ieee", _call["ieee"] == REMOTE)
check("passes the cluster as an INT id", _call["cluster"] == 0x0006)
check("device target goes in command_data", _call["command_data"] == LAMP)
check("device target carries dst_endpoint", _call["dst_endpoint"] == 1)
_gcall = m.to_bind_call(
    m.parse_declared([{"from": REMOTE, "to_group": 12, "clusters": ["genOnOff"]}])[0]
)
check("group target goes in command_data as the id", _gcall["command_data"] == 12)
check("group target has NO dst_endpoint", "dst_endpoint" not in _gcall)
check(
    "command_data is ALWAYS set (omitting it binds to the coordinator)",
    all(
        "command_data" in m.to_bind_call(b) and m.to_bind_call(b)["command_data"] is not None
        for b in _declared
    ),
)

print("\n-- describe (used by status output) --")
check(
    "describes a device bind readably",
    "genOnOff" in _declared[0].describe() and LAMP in _declared[0].describe(),
)
check(
    "describes a group bind readably",
    "group 12" in _gcall
    and True
    or "group 12"
    in m.parse_declared([{"from": REMOTE, "to_group": 12, "clusters": ["genOnOff"]}])[0].describe(),
)

print("\n-- Bind round-trip through a plain dict (what the filter layer does) --")

# The filters hand Ansible plain dicts (a namedtuple serialises as a bare list, losing attribute
# access), then rebuild Bind from them. This asserts that round-trip is lossless -- the bug it
# guards is real: _czb_declared holds ALREADY-NORMALISED dicts, and feeding those back through the
# RAW parser raises "no 'from'". The role therefore has two differently-named entry points.
_as_plain = {
    "src": _declared[0].src,
    "src_ep": _declared[0].src_ep,
    "cluster": _declared[0].cluster,
    "dst_ieee": _declared[0].dst_ieee,
    "dst_ep": _declared[0].dst_ep,
    "group": _declared[0].group,
}
check("a normalised dict rebuilds the identical Bind", m.Bind(**_as_plain) == _declared[0])
raises(
    "feeding a NORMALISED dict to the raw declaration parser raises",
    m.parse_declared,
    [_as_plain],
)
check(
    "...and the rebuilt Bind still diffs correctly",
    m.diff([m.Bind(**_as_plain)], m.parse_live(live(device_entry("0x0006")), REMOTE)).converged
    is True,
)

print()
print("\n-- to_declaration: probe's paste-ready output must ROUND-TRIP --")

# The whole value of probe's suggestion is that pasting it back produces the same bindings. If the
# stanza it emits does not re-parse to the identical Bind, the output is worse than useless --
# it looks authoritative and declares something subtly different.
for _orig in (
    _declared[0],
    _gcall_bind := m.parse_declared(
        [{"from": REMOTE, "to_group": 12, "clusters": ["genLevelCtrl"]}]
    )[0],
):
    _stanza = m.to_declaration(_orig, "probed")
    _back = m.parse_declared([_stanza])
    check(
        f"stanza round-trips ({m.cluster_name(_orig.cluster)}, "
        f"{'group' if _orig.group is not None else 'device'})",
        _back == [_orig],
        f"{_stanza}",
    )

_dev_stanza = m.to_declaration(_declared[0])
check(
    "device stanza carries `to` and `to_endpoint`",
    "to" in _dev_stanza and "to_endpoint" in _dev_stanza,
)
check("device stanza has NO to_group", "to_group" not in _dev_stanza)
_grp_stanza = m.to_declaration(_gcall_bind)
check("group stanza carries `to_group`", _grp_stanza["to_group"] == 12)
check(
    "group stanza has NO `to`/`to_endpoint`",
    "to" not in _grp_stanza and "to_endpoint" not in _grp_stanza,
)
check("stanza names the cluster, not its id", _dev_stanza["clusters"] == ["genOnOff"])
check("name is omitted when not supplied", "name" not in _dev_stanza)
check("name is included when supplied", m.to_declaration(_declared[0], "x")["name"] == "x")

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\nFAILED:")
    for name in FAIL:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)
