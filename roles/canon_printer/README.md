# canon_printer

Manage a Canon PIXMA printer through its **Remote UI** admin API — read every
setting, converge the ones declared in inventory, run its maintenance jobs
(nozzle check, cleaning, deep cleaning), and power it off properly. Built for
the TS8700 (firmware 4.010); the protocol is shared across Canon's inkjet range,
and pages a model lacks answer 404 and are reported as unsupported.

The printer has no shell. Its inventory host is API-only
(`ansible_connection: local`, with `ansible_python_interpreter` pointing at a
controller Python that has `requests`); every task runs on the controller.

## Layers

| layer                                    | where                            | use it for                                                                            |
| ---------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------- |
| SDK `canon_rui`                          | `plugins/module_utils/canon_rui.py` | ad-hoc scripts, exporters, other glue; also a CLI (`python canon_rui.py --host … read`) |
| module `richard_lynch.home.canon_printer_settings` | `plugins/modules/`               | any play: read, plan, apply, unauthenticated status                                   |
| this role                                | `roles/canon_printer`            | the inventory-driven entrypoint                                                       |

## Usage

```yaml
- hosts: printer
  gather_facts: false
  roles:
    - role: richard_lynch.home.canon_printer
```

```
ansible-playbook printer.yml -e canon_printer_action=probe
... -e canon_printer_action=read
... -e canon_printer_action=converge                              # PLAN against inventory
... -e 'canon_printer_action=converge canon_printer_apply=true'   # write + read back
... -e 'canon_printer_action=nozzle_check canon_printer_apply=true'  # prints one page
... -e 'canon_printer_action=cleaning canon_printer_apply=true'      # uses ink
... -e 'canon_printer_action=deep_cleaning canon_printer_apply=true' # uses a lot of ink
... -e 'canon_printer_action=power_off canon_printer_apply=true'     # caps the head, shuts down
```

Every maintenance/power action without `canon_printer_apply=true` is a PLAN: it
says what it would do and sends nothing.

Inputs: `meta/argument_specs.py`. Installation config (host, password lookup,
CA, desired settings) lives in the printer's inventory host_vars, never here.

## The protocol (derived from the firmware's JS, verified against a device)

- **Login** `POST /rui/sendpw.cgi` `NAMAE=<sha256 hex of password>&IDTYPE=2` →
  cookie `SCID` + a self-posting form carrying **SBID** (CSRF token), appended
  to every later request. The hash is unsalted, i.e. password-equivalent.
- **Read** `POST /rui/<page>.cgi` `GETINFO=0&SBID=…` → XML leaf values.
- **Write** `POST /rui/<page>.cgi` `SETINFO=0&<every field on the page>&SBID=…`
  → `<RESULT>` `RW_OK` | `RW_NG` | `RW_POLLING` (poll `get_job_status.cgi`) |
  `LOCKED`. Which fields form "the page" depends on the model (a Wi-Fi-only
  printer never reports or accepts `WIRED_INTERMITTENT`), so the client writes
  exactly the writable fields the device reported, merged with the desired ones.
- **Unauthenticated status**: `/JS_MDL/model.js` — signal %, link quality %,
  error state, flags.

Settings groups and their writable fields: `GROUPS` in the SDK. `ipv4`, `power`,
`firmware*`, `airprint` and `capabilities` are read-only by design (addressing
belongs to the DHCP server; the rest are not yet write-verified).

## Safety

- **Generate-don't-apply.** `converge` without `canon_printer_apply=true` reads
  the live device and reports the diff; nothing is sent. Every desired value is
  validated (the device's own name rules, enums) before the first write, so a
  typo cannot leave the printer half-converged.
- **Read-back is the result.** After a write the module re-reads the page and
  reports what the device says; the role then asserts every desired value
  against that, not against the write's `RW_OK`.
- **Disruptive groups** (`ipv6`, `printer_name`, `bonjour`) make the printer
  re-register on the network — expect it to vanish for a few seconds. Renaming
  the Bonjour service orphans any AirPrint (`dnssd://`) queue keyed on the old
  name; a client queue addressed as IPP by DNS name does not depend on it.
- **TLS fails closed.** The printer's server cert names only its current IP, so
  the client pins the printer's OWN CA (`/cert_root.der`, kept beside the
  inventory and passed as `canon_printer_ca_cert`) with hostname checks off, and refuses any
  HTTPS call without it. After a factory reset the device mints a new CA:
  re-fetch with `python canon_rui.py --host <printer> fetch-ca` (sends no
  credentials).

## Maintenance and power

Power-off is deliberately IN scope. Canon's manual says to switch the printer off with the ON button and wait for the lamp
to go out, because unplugging it while it is lit "may cause drying or clogging
of the print head". So a proper soft power-off, which caps (parks) the head, is
the safe way to turn it off remotely. There is no separate "park the head"
operation; capping is part of the shutdown.

- **Jobs** (`nozzle_check`, `cleaning`, `deep_cleaning`) are the printer's own
  Maintenance-menu kicks, derived from firmware 4.010's JS (`impl.js`
  `nozzle_chk` / `nor_cleaning` / `deep_cleaning`, `jobkick.js`
  `Jobkick_Closure`): `POST /rui/ut_<job>.cgi` with SBID → `RW_OK` /
  `RW_POLLING` + `JOB_NUM`, then poll `get_job_status.cgi` (same body +
  `JOB_NUM`) until `getJobState` is `2` (done). `3` error and `4` cancelled
  fail; `5` means the job is held on an error at the printer (e.g. no paper) and
  fails at the timeout with the device's `WARNING_ID`. The device answers
  `RW_NG` when it cannot start (busy, no paper, ink too low), with no reason.
  `canon_printer_cleaning_pattern` (`ALL`/`PAT1`/`PAT2`) sends the newer UI's
  `CLN_PATTERN`; unset sends the kick with no body, as `nor_cleaning()` does.
- **Power off** is not in the Remote UI. It is CHMP, the XML protocol Canon's IJ
  Printer Assistant Tool speaks: `POST http://<printer>/canon/ij/command2/port1`
  with the `PowerOff` command (`canon_rui.CHMP_POWER_OFF`). The printer drops
  the connection while it shuts down, so the reply is not the result: the role
  first checks the printer answers, then requires it to STOP answering within 90
  s. Once off it leaves the network (its DNS name lapses with the DHCP lease)
  and comes back only with the ON button unless the printer's auto power-on
  setting is enabled.
- **CHMP is unauthenticated plain HTTP.** Anything on the printer's network can
  power it off with that one request. Nothing here makes that worse, but it is
  worth knowing.
- Not built yet: print head alignment (`ut_auto_regi.cgi`). On the TS8700 it
  prints a pattern the panel then asks you to handle, so it is not a hands-off
  job; roller and bottom-plate cleaning (also need paper loaded by hand).

Prior art:
[gapipro/canon-pixma-printers-mac-assistant](https://github.com/gapipro/canon-pixma-printers-mac-assistant)
(macOS app, tested on a G3010; the source of the CHMP `PowerOff` body).

## Admin password

The password lives in 1Password and is read with `richard_lynch.common.onepassword_cached`
(`canon_printer_password`, by item ID: `canon_printer_password_item`).

- **`rotate_password`** (needs `canon_printer_rotate_confirm=true`): freezes the current value,
  rolls the 1Password item FIRST through `richard_lynch.common.secrets_manager` (which backs the
  old value up), then changes the device. If the device refuses, the item is rolled back — but only
  while the device still accepts the old password. Done only when the new password logs in, the old
  one is refused, and a fresh read of the item equals what the device accepted.
- **`sync_password`**: make the item (its website URL, `canon_printer_admin_url`), the credentials
  cache and the device agree. Idempotent; it finishes a rotation that stopped part-way.

## Deliberately NOT done

- No writes to IPv4 addressing (the DHCP server owns it), firmware update or
  LAN reset.

## Tests

Pure SDK logic: `tests/unit/test_canon_rui.py` (fixtures captured from a
device). The molecule scenario is
**check-only**: a printer cannot exist in CI, so it asserts the role's argspec
and gates, not a converge.
