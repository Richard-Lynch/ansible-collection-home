# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the canon_rui SDK (module_utils/canon_rui.py).

PURE LOGIC ONLY — no printer, no network. The fixtures are replies captured from a TS8700
(firmware 4.010), trimmed of the header/menu chrome they all carry. What is asserted
is every decision the client makes about data it was handed: these are the places a wrong answer
would be silent (a write built from a partial page, a secret echoed into a log, a bad name sent and
refused with an opaque RW_NG).

Run: python -m pytest tests/unit -q (from the collection root)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "module_utils"))

from canon_rui import (  # noqa: E402
    GROUPS,
    AuthError,
    CanonRuiError,
    UnsupportedError,
    ValidationError,
    build_payload,
    parse_response,
    parse_status_model_js,
    password_hash,
    plan_write,
    validate,
)

CHROME = (
    "<ERR_DISP_STATE>HTTP_ERR_DISP_IDLE</ERR_DISP_STATE><WARNING_ID>NO_ERR</WARNING_ID>"
    "<BST_STATE>0</BST_STATE><HEADER_2PAIN><HEADER_BON_NAME>Canon TS8700 Printer</HEADER_BON_NAME>"
    "<HEADER_LOGOUT>Log out</HEADER_LOGOUT></HEADER_2PAIN><MENU_2PAIN><MENU_TITLE>Menu</MENU_TITLE>"
    "<MENU_0>Printer status</MENU_0></MENU_2PAIN>"
)
BONJOUR_XML = (
    '<?xml version="1.0" ?><response><PAGE_SYSTEMINFO><BON_MODE>ON</BON_MODE>'
    "<BONNAME>Canon TS8700 Printer</BONNAME><BONCONFLICT></BONCONFLICT><BONNOTE></BONNOTE>"
    "</PAGE_SYSTEMINFO><RESULT>RD_PAGE_SYS</RESULT>" + CHROME + "</response>"
)
DRX_XML = (
    '<?xml version="1.0" ?><response><PAGE_SYSTEMINFO><WIRELESS_INTERMITTENT>ON</WIRELESS_INTERMITTENT>'
    "</PAGE_SYSTEMINFO><RESULT>RD_PAGE_SYS</RESULT>" + CHROME + "</response>"
)
IPP_WITH_SECRET_XML = (
    '<?xml version="1.0" ?><response><PAGE_SYSTEMINFO><IPP_MODE>ON</IPP_MODE>'
    "<IPP_PASSWORD>hunter2</IPP_PASSWORD><PRX_USER_PASS></PRX_USER_PASS></PAGE_SYSTEMINFO>"
    "<RESULT>RW_OK</RESULT></response>"
)


class TestPasswordHash:
    def test_matches_cryptojs_sha256_hex(self):
        # SHA-256("abc"), the FIPS 180-2 test vector — lowercase hex, as CryptoJS emits.
        assert (
            password_hash("abc")
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_empty_password_is_refused_not_hashed(self):
        # sha256("") is a valid-looking hash; sending it would read as "wrong password".
        with pytest.raises(AuthError):
            password_hash("")


class TestParseResponse:
    def test_values_and_result(self):
        result, values = parse_response(BONJOUR_XML)
        assert result == "RD_PAGE_SYS"
        assert values == {
            "BON_MODE": "ON",
            "BONNAME": "Canon TS8700 Printer",
            "BONCONFLICT": "",
            "BONNOTE": "",
        }

    def test_header_and_menu_chrome_are_not_settings(self):
        _result, values = parse_response(BONJOUR_XML)
        assert not any(k.startswith(("HEADER_", "MENU_")) for k in values)
        assert "ERR_DISP_STATE" not in values and "RESULT" not in values

    def test_secrets_echoed_by_the_device_are_redacted(self):
        _result, values = parse_response(IPP_WITH_SECRET_XML)
        assert values["IPP_PASSWORD"] == "<redacted>"
        assert (
            values["PRX_USER_PASS"] == ""
        )  # empty stays empty: nothing to hide, and "unset" is information
        assert values["IPP_MODE"] == "ON"

    def test_session_expiry_is_an_auth_error(self):
        with pytest.raises(AuthError):
            parse_response("<response><SES_ERR_URL>login_manager.html</SES_ERR_URL></response>")

    def test_html_instead_of_xml_is_named(self):
        with pytest.raises(CanonRuiError, match="not XML"):
            parse_response("<html><body>login</body")


class TestValidate:
    def test_read_only_group_cannot_be_written(self):
        with pytest.raises(ValidationError, match="read-only"):
            validate(GROUPS["ipv4"], {"IPV4_MODE": "MANUAL"})

    def test_unknown_field_lists_the_writable_ones(self):
        with pytest.raises(ValidationError, match="WIRELESS_INTERMITTENT"):
            validate(GROUPS["wifi_power_save"], {"DRX": "OFF"})

    def test_enum_values(self):
        validate(GROUPS["ipv6"], {"CONNECT_TYPE": "IPV4_MODE"})
        with pytest.raises(ValidationError):
            validate(GROUPS["ipv6"], {"CONNECT_TYPE": "IPV6_ONLY"})

    @pytest.mark.parametrize("name", ["printer", "ab", "p-1", "A" * 15])
    def test_printer_name_accepted(self, name):
        validate(GROUPS["printer_name"], {"PRINTER_NAME": name})

    @pytest.mark.parametrize(
        "name", ["p", "A" * 16, "-printer", "printer-", "print er", "printer.local"]
    )
    def test_printer_name_rejected_like_the_device_would(self, name):
        with pytest.raises(ValidationError):
            validate(GROUPS["printer_name"], {"PRINTER_NAME": name})

    def test_bonjour_name_rules(self):
        validate(GROUPS["bonjour"], {"BONNAME": "Printer"})
        for bad in (" Printer", "x" * 49, "Prïnter"):
            with pytest.raises(ValidationError):
                validate(GROUPS["bonjour"], {"BONNAME": bad})


class TestPlanWrite:
    def test_page_is_what_the_device_reported(self):
        # The TS8700 is Wi-Fi only: it never reports WIRED_INTERMITTENT and the UI never sends it.
        _result, current = parse_response(DRX_XML)
        changes, merged = plan_write(
            GROUPS["wifi_power_save"], current, {"WIRELESS_INTERMITTENT": "OFF"}
        )
        assert merged == {"WIRELESS_INTERMITTENT": "OFF"}
        assert changes == {"WIRELESS_INTERMITTENT": {"before": "ON", "after": "OFF"}}

    def test_field_this_model_lacks_is_unsupported_not_guessed(self):
        _result, current = parse_response(DRX_XML)
        with pytest.raises(UnsupportedError, match="WIRED_INTERMITTENT"):
            plan_write(GROUPS["wifi_power_save"], current, {"WIRED_INTERMITTENT": "OFF"})

    def test_whole_page_is_sent_with_unchanged_fields_preserved(self):
        _result, current = parse_response(BONJOUR_XML)
        changes, merged = plan_write(GROUPS["bonjour"], current, {"BONNAME": "Printer"})
        assert merged == {
            "BON_MODE": "ON",
            "BONNAME": "Printer",
        }  # BON_MODE carried, not dropped
        assert list(changes) == ["BONNAME"]

    def test_already_converged_plans_nothing(self):
        _result, current = parse_response(BONJOUR_XML)
        changes, _rest = plan_write(GROUPS["bonjour"], current, {"BON_MODE": "ON"})
        assert changes == {}

    def test_values_compare_as_strings(self):
        # inventory YAML may give an int; the device always reports strings.
        current = {
            "WSD_MODE": "ON",
            "WSD_OPTIMIZE": "ON",
            "WSD_SCAN_MODE": "OFF",
            "WSD_TIMEOUT": "15",
        }
        changes, _rest = plan_write(GROUPS["wsd"], current, {"WSD_TIMEOUT": 15})
        assert changes == {}


class TestBuildPayload:
    def test_shape_matches_the_ui(self):
        body = build_payload({"BON_MODE": "ON", "BONNAME": "Canon TS8700 Printer"}, "abc123")
        assert body == "SETINFO=0&BON_MODE=ON&BONNAME=Canon%20TS8700%20Printer&SBID=abc123"

    def test_reserved_characters_cannot_inject_fields(self):
        body = build_payload({"BONNAME": "a&SBID=evil"}, "tok")
        assert body.count("&SBID=") == 1 and body.endswith("&SBID=tok")


class TestStatusModelJs:
    JS = (
        "var g_Administrator = 0;\nvar g_signal_strength = '60';\nvar g_link_quality = '46';\n"
        "var g_help_url = 'https://rs.ciggws.net/x';\nvar g_password_enable= 1;\n"
        "var g_wireless_intermittent = 'ENABLE';\nvar g_data_firm_ver=\"4.010\";\n"
    )

    def test_scalars_extracted(self):
        s = parse_status_model_js(self.JS)
        assert s["g_signal_strength"] == "60" and s["g_link_quality"] == "46"
        assert s["g_wireless_intermittent"] == "ENABLE" and s["g_data_firm_ver"] == "4.010"
        assert s["g_Administrator"] == "0"

    def test_urls_and_password_flags_are_dropped(self):
        s = parse_status_model_js(self.JS)
        assert "g_help_url" not in s and "g_password_enable" not in s


class TestFailsClosed:
    def test_no_ca_and_not_insecure_refuses_before_sending_anything(self):
        from canon_rui import CanonRuiClient

        c = CanonRuiClient(
            "printer.invalid", "pw"
        )  # .invalid never resolves: any request would error differently
        with pytest.raises(CanonRuiError, match="without a pinned CA"):
            c.login()
        with pytest.raises(CanonRuiError, match="without a pinned CA"):
            c.read_raw("lan_bonjour_data.cgi")


class TestJobState:
    # Captured from the device: the reply WITH SBID, and the reply when SBID was omitted.
    DONE = (
        '<?xml version="1.0" ?><response><BONNAME>Canon TS8700 Printer</BONNAME>'
        "<getJobState>2</getJobState><ERR_DISP_STATE>HTTP_ERR_DISP_IDLE</ERR_DISP_STATE>"
        "<UPDATE><ERR_INFO>0</ERR_INFO></UPDATE></response>"
    )
    NO_SBID = (
        '<?xml version="1.0" ?><response><SES_ERR_URL>ses_err_unmatch.html</SES_ERR_URL>'
        "<RESULT>RW_SES_NG</RESULT></response>"
    )

    def test_done(self):
        from canon_rui import job_state

        assert job_state(self.DONE) == "2"

    def test_nested_state_is_found(self):
        from canon_rui import job_state

        assert job_state("<response><X><getJobState>1</getJobState></X></response>") == "1"

    def test_session_error_is_raised_not_read_as_running(self):
        from canon_rui import job_state

        with pytest.raises(AuthError):
            job_state(self.NO_SBID)


class TestAdminPassword:
    @pytest.mark.parametrize("pw", ["abcd", "A1b2C3d4", "x" * 32])
    def test_accepted(self, pw):
        from canon_rui import validate_admin_password

        validate_admin_password(pw)

    @pytest.mark.parametrize("pw", ["", "abc", "x" * 33, "pass word", "pässword", "p@ssw0rd"])
    def test_rejected_like_the_device(self, pw):
        from canon_rui import validate_admin_password

        with pytest.raises(ValidationError):
            validate_admin_password(pw)


# -- maintenance jobs and power-off --------------------------------------------------------------


def _job_xml(state, warning="NO_ERR"):
    return (
        '<?xml version="1.0" ?><response><getJobState>%s</getJobState>'
        "<WARNING_ID>%s</WARNING_ID><RESULT>RW_OK</RESULT></response>" % (state, warning)
    )


KICK_OK = (
    '<?xml version="1.0" ?><response><JOB_NUM>7</JOB_NUM><RESULT>RW_POLLING</RESULT></response>'
)
KICK_NG = '<?xml version="1.0" ?><response><RESULT>RW_NG</RESULT></response>'


class TestMaintenanceBody:
    def test_no_pattern_is_an_empty_body_like_nor_cleaning(self):
        from canon_rui import MAINTENANCE, maintenance_body

        assert maintenance_body(MAINTENANCE["cleaning"]) == ""

    def test_pattern_is_sent_like_g_util_cleaning(self):
        from canon_rui import MAINTENANCE, maintenance_body

        assert (
            maintenance_body(MAINTENANCE["deep_cleaning"], "PAT1") == "SETINFO=0&CLN_PATTERN=PAT1"
        )

    def test_unknown_pattern_is_refused_before_sending(self):
        from canon_rui import MAINTENANCE, maintenance_body

        with pytest.raises(ValidationError):
            maintenance_body(MAINTENANCE["cleaning"], "BLACK")

    def test_nozzle_check_takes_no_pattern(self):
        from canon_rui import MAINTENANCE, maintenance_body

        with pytest.raises(ValidationError):
            maintenance_body(MAINTENANCE["nozzle_check"], "ALL")


class TestJobPoll:
    def test_states_are_named(self):
        from canon_rui import job_poll

        assert job_poll(_job_xml("1")) == ("in_progress", "")
        assert job_poll(_job_xml("2")) == ("done", "")

    def test_held_job_carries_the_devices_reason(self):
        from canon_rui import job_poll

        assert job_poll(_job_xml("5", "WARNING_NO_PAPER")) == (
            "waiting_on_error",
            "WARNING_NO_PAPER",
        )

    def test_service_error_is_the_reason_when_no_warning(self):
        from canon_rui import job_poll

        xml = "<response><getJobState>3</getJobState><SERVICE>WARNING_X</SERVICE></response>"
        assert job_poll(xml) == ("error", "WARNING_X")


class _FakeClient:
    """A CanonRuiClient whose transport is a scripted list of replies, recording every request."""

    def __new__(cls, replies):
        from canon_rui import CanonRuiClient

        c = CanonRuiClient("printer.invalid", "pw", ca_cert="/dev/null", poll_interval=0)
        c._sbid = "abc123"
        sent = []

        def _post(path, body, accept_xml=True):
            sent.append((path, body))
            return replies.pop(0)

        c._post = _post
        c.sent = sent
        return c


class TestRunJob:
    def test_kick_then_poll_until_done(self):
        c = _FakeClient([KICK_OK, _job_xml("1"), _job_xml("2")])
        res = c.run_job("nozzle_check")
        assert res["result"] == "done" and res["job_num"] == "7"
        assert c.sent[0] == ("/rui/ut_nozzle_check.cgi", "SBID=abc123")
        # The poll repeats the kick body plus JOB_NUM, as jobkick.js JobStatePolling does.
        assert c.sent[1] == ("/rui/get_job_status.cgi", "JOB_NUM=7&SBID=abc123")

    def test_pattern_goes_in_the_kick_and_the_poll(self):
        c = _FakeClient([KICK_OK, _job_xml("2")])
        c.run_job("cleaning", "ALL")
        assert c.sent[0] == ("/rui/ut_normal_cln.cgi", "SETINFO=0&CLN_PATTERN=ALL&SBID=abc123")
        assert c.sent[1][1] == "SETINFO=0&CLN_PATTERN=ALL&JOB_NUM=7&SBID=abc123"

    def test_rw_ng_is_refused_and_nothing_is_polled(self):
        from canon_rui import RejectedError

        c = _FakeClient([KICK_NG])
        with pytest.raises(RejectedError, match="RW_NG"):
            c.run_job("cleaning")
        assert len(c.sent) == 1

    def test_job_error_names_the_reason(self):
        from canon_rui import RejectedError

        c = _FakeClient([KICK_OK, _job_xml("3", "WARNING_INK_LOW")])
        with pytest.raises(RejectedError, match="WARNING_INK_LOW"):
            c.run_job("deep_cleaning")

    def test_held_on_error_keeps_polling_and_can_finish(self):
        c = _FakeClient([KICK_OK, _job_xml("5", "WARNING_NO_PAPER"), _job_xml("2")])
        assert c.run_job("nozzle_check")["result"] == "done"

    def test_a_session_error_mid_poll_is_not_read_as_running(self):
        c = _FakeClient(
            [KICK_OK, "<response><SES_ERR_URL>x</SES_ERR_URL><RESULT>RW_SES_NG</RESULT></response>"]
        )
        with pytest.raises(AuthError):
            c.run_job("nozzle_check")


class TestPowerOff:
    """power_off decides by READ-BACK: the printer must answer first and stop answering after."""

    def _patch(self, monkeypatch, gets, post):
        import requests

        import canon_rui

        calls = {"post": 0}
        gets = list(gets)

        def fake_get(url, timeout):
            ok = gets.pop(0) if len(gets) > 1 else gets[0]  # the last answer repeats
            if not ok:
                raise requests.ConnectionError("down")

            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

            return R()

        def fake_post(url, data, headers, timeout):
            calls["post"] += 1
            assert url.endswith("/canon/ij/command2/port1")
            assert b"<ivec:operation>PowerOff</ivec:operation>" in data
            if post is None:
                raise requests.ConnectionError("reset")

            class R:
                status_code = post

            return R()

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(canon_rui.time, "sleep", lambda s: None)
        return calls

    def test_connection_drop_then_silence_is_off(self, monkeypatch):
        from canon_rui import power_off

        calls = self._patch(monkeypatch, [True, True, False], post=None)
        res = power_off("printer.invalid")
        assert calls["post"] == 1 and "connection dropped" in res["sent"]

    def test_unreachable_before_sending_is_an_error_and_sends_nothing(self, monkeypatch):
        from canon_rui import power_off

        calls = self._patch(monkeypatch, [False], post=200)
        with pytest.raises(CanonRuiError, match="nothing sent"):
            power_off("printer.invalid")
        assert calls["post"] == 0

    def test_still_answering_is_not_off(self, monkeypatch):
        from canon_rui import power_off

        self._patch(monkeypatch, [True], post=200)
        with pytest.raises(CanonRuiError, match="not powered off"):
            power_off("printer.invalid", confirm_within=0.001)

    def test_404_means_no_chmp(self, monkeypatch):
        from canon_rui import power_off

        self._patch(monkeypatch, [True], post=404)
        with pytest.raises(UnsupportedError):
            power_off("printer.invalid")
