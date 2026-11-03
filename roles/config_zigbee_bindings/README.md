# config_zigbee_bindings

Declares Zigbee **direct bindings** (switch → light) as desired state and converges them.

A binding lets a switch drive a light with no coordinator, no broker and no automation engine in
the path. It survives Home Assistant being down or upgrading, and costs no round-trip latency.

## What makes this unlike every other config role

**The desired state does not live in a file this role can write.** A binding is stored in the
*source device's own binding table*, in NVRAM, reachable only over the air. There is nothing to
template and restart. So the role asks each device what it currently has (ZDO `Mgmt_Bind_req`, via
`zha_toolkit`'s `binds_get`), diffs that against the declaration, and writes only the difference.

**And the device is usually asleep.** A battery remote wakes for a few seconds when its button is
pressed. Zigbee's parent buffering holds a message for just **7.68 s**
(`nwkTransactionPersistenceTime`) and nothing above that layer queues. So **a write failing is the
normal case, not an error** — and `zha_toolkit`'s `tries` does not help: its `retry_wrapper`
defaults to 3 attempts 0.1 s apart, i.e. 0.3 s total.

Hence two paths:

| | For | Mechanism |
|---|---|---|
| `converge` | a cutover, or someone holding the remote | retries with real patience, then asserts |
| `install` | everything else | an HA automation triggered by the device's own `zha_event` — the wake signal itself |

`install` is what makes "it lands at the next opportunity" true. Pressing the button a few times is
simply the fast path, because **the press is the trigger**.

Confirmation always comes from re-reading the device, never from a write returning OK, and
confirmation is what disarms the loop.

## Actions

Read by default: a bare run performs `status` and changes nothing.

| `config_zigbee_bindings_action` | Writes? | Gate |
|---|---|---|
| `probe` | no | — |
| `status` *(default)* | no | — |
| `converge` | yes | `config_zigbee_bindings_apply=true` |
| `sweep` | yes | `config_zigbee_bindings_apply=true` |
| `install` | yes (HA config) | `config_zigbee_bindings_apply=true` |
| `prune` | **removes** | `config_zigbee_bindings_prune=true` |

`status` **exits 0 whether or not everything is converged.** A device being switched off is a
normal state of the world; a status command that failed on it would be noise everyone learns to
ignore.

`converge` is **additive only**, and `prune` has its own separate flag. ZHA binds every device to
the coordinator during its interview, so a converge that pruned would tear those out network-wide on
its first run and break state reporting for every bound device.

## Declaring a binding

Installation config, so it lives in inventory (`config_zigbee_bindings_bindings`) — not in this
role's defaults:

```yaml
config_zigbee_bindings_bindings:
  - name: "Bedside left dimmer -> bedroom lamp"
    from: "00:11:22:33:44:55:66:77"   # IEEE of the SOURCE (the remote)
    from_endpoint: 1                  # optional, defaults to 1
    to: "00:17:88:01:02:03:04:05"     # ...or to_group: 12. Exactly one.
    to_endpoint: 1
    clusters: [genOnOff, genLevelCtrl]
    wake: dormant                     # mains | sleepy | dormant
    state: present                    # present (default) | absent
```

**Keyed on IEEE, deliberately.** Friendly names and HA registry UUIDs are both UI state:
renameable, and regenerated on re-pair. The IEEE is the device. Run `probe` to get a paste-ready
block so nobody hand-types one.

**Only five clusters are bindable:** `genScenes`, `genOnOff`, `genLevelCtrl`, `lightingColorCtrl`,
`closuresWindowCovering`. On/off, dim, colour, blinds. Anything conditional — a sensor driving a
light, time of day — is not a binding and stays an automation.

## The landmine this role exists to contain

`binds_get` reports clusters as **hex strings** (`"0x0006"`); `bind_ieee` takes **names**
(`genOnOff`); inventory is written in names. A plain set difference finds nothing in common and
concludes *every binding is missing — forever*, re-writing on every pass and never confirming. It
presents as a device fault.

That is why the diff is `files/zigbee_binding_diff.py` — pure, stdlib-only, no network — with
`files/test_zigbee_binding_diff.py` asserting the normalisation in both directions, rather than a
Jinja `selectattr` chain whose failure mode is a silent empty list.

## Testing

| What | How | Covers |
|---|---|---|
| the diff | `python3 files/test_zigbee_binding_diff.py` | normalisation, refusals, round-trips |
| the role's decisions | `molecule test` in this role | real filters + template over recorded fixtures |
| a live network | `probe` then `status` against a real coordinator | the half that needs a radio |

**There is usually no staging for this role**: a USB Zigbee coordinator is a physical singleton.
So the acceptance test is a live one — `probe` and `status` are read-only and prove the read and
diff path against the real network, and a first `converge` is best pointed at one named device.
