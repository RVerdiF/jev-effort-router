"""Resolution of the opt-in CommandCode trio — and what "configured" is allowed to mean.

Three settings, no defaults. The distinctions matter more than the happy path:

* empty means *off* and must look exactly like a deployment that never knew the setting;
* a half-written trio, or a URL that is not a plain HTTPS endpoint, disables the fallback and
  nothing else — the legacy chain is never blocked by a configuration mistake made in the fallback;
* an unusable URL is dropped from the resolved value, so a URL that carried credentials can never
  be echoed by a status line or a log record.
"""

from __future__ import annotations

import logging

import pytest

from config import (
    COMMANDCODE_BASE_URL_SETTING,
    COMMANDCODE_KEY_ENV_SETTING,
    COMMANDCODE_MODEL_SETTING,
    CommandCodeFallback,
    Settings,
    load_settings,
    resolve_commandcode_fallback,
)

CC_URL = "https://api.commandcode.ai/provider/v1/systemone"
CC_MODEL = "typesafe/jev"
CC_KEY_ENV = "COMMANDCODE_API_KEY"


def resolve(**kwargs):
    values = {"base_url": CC_URL, "model": CC_MODEL, "api_key_env": CC_KEY_ENV}
    values.update(kwargs)
    return resolve_commandcode_fallback(**values)


def test_an_empty_trio_is_disabled_and_not_configured():
    fallback = resolve(base_url="", model="", api_key_env="")

    assert fallback == CommandCodeFallback()
    assert fallback.configured is False
    assert fallback.enabled is False
    assert fallback.reason == ""


def test_a_complete_trio_is_enabled():
    fallback = resolve()

    assert fallback.configured is True
    assert fallback.enabled is True
    assert (fallback.url, fallback.model, fallback.key_env) == (CC_URL, CC_MODEL, CC_KEY_ENV)
    assert fallback.reason == ""


def test_surrounding_whitespace_is_trimmed():
    fallback = resolve(base_url=f"  {CC_URL}  ", model=f" {CC_MODEL} ", api_key_env=" COMMANDCODE_API_KEY\n")

    assert (fallback.url, fallback.model, fallback.key_env) == (CC_URL, CC_MODEL, CC_KEY_ENV)


@pytest.mark.parametrize(
    "missing_setting, kwargs",
    [
        (COMMANDCODE_BASE_URL_SETTING, {"base_url": ""}),
        (COMMANDCODE_MODEL_SETTING, {"model": ""}),
        (COMMANDCODE_KEY_ENV_SETTING, {"api_key_env": ""}),
    ],
)
def test_any_missing_field_disables_the_fallback_and_names_it(missing_setting, kwargs):
    fallback = resolve(**kwargs)

    assert fallback.configured is True
    assert fallback.enabled is False
    assert missing_setting in fallback.reason


@pytest.mark.parametrize(
    "url",
    [
        "http://api.commandcode.ai/provider/v1/systemone",
        "ftp://api.commandcode.ai/provider/v1/systemone",
        "https://user:secret@api.commandcode.ai/provider/v1/systemone",
        "https://api.commandcode.ai/provider/v1/systemone?token=secret",
        "https://api.commandcode.ai/provider/v1/systemone#fragment",
        "https:///provider/v1/systemone",
        "api.commandcode.ai/provider/v1/systemone",
        "not a url at all",
    ],
)
def test_an_unusable_url_disables_the_fallback_without_keeping_the_value(url):
    fallback = resolve(base_url=url)

    assert fallback.enabled is False
    assert fallback.configured is True
    assert COMMANDCODE_BASE_URL_SETTING in fallback.reason
    # Nothing that could hold a credential survives into the resolved value.
    assert fallback.url == ""
    assert "secret" not in fallback.reason


def test_an_invalid_url_is_dropped_even_when_the_trio_is_also_incomplete():
    # The rejection path must not keep the value: an incomplete trio whose URL embedded a
    # credential would otherwise hand it straight to the status line.
    fallback = resolve_commandcode_fallback(
        base_url="https://user:secret@api.commandcode.ai/provider/v1/systemone",
        model="",
        api_key_env="",
    )

    assert fallback.configured is True
    assert fallback.enabled is False
    assert fallback.url == ""
    assert "secret" not in fallback.reason


def test_a_url_with_an_ip_and_port_is_accepted():
    fallback = resolve(base_url="https://127.0.0.1:8443/provider/v1/systemone")

    assert fallback.enabled is True
    assert fallback.url == "https://127.0.0.1:8443/provider/v1/systemone"


# -- how the snapshot exposes it ------------------------------------------------------


def settings_for(config):
    return load_settings(lambda key, default=None: config.get(key, default))


def test_the_three_settings_default_to_empty_and_keep_settings_equality():
    settings = settings_for({})

    assert settings.console_jev_commandcode_base_url == ""
    assert settings.console_jev_commandcode_api_key_env == ""
    assert settings.console_jev_commandcode_model == ""
    assert settings == Settings()


def test_the_loader_reads_the_trio():
    settings = settings_for(
        {
            "console_jev_commandcode_base_url": CC_URL,
            "console_jev_commandcode_api_key_env": CC_KEY_ENV,
            "console_jev_commandcode_model": CC_MODEL,
        }
    )

    fallback = settings.commandcode_fallback()
    assert fallback.enabled is True
    assert fallback.url == CC_URL


def test_only_the_console_decider_can_use_the_trio():
    config = {
        "console_jev_commandcode_base_url": CC_URL,
        "console_jev_commandcode_api_key_env": CC_KEY_ENV,
        "console_jev_commandcode_model": CC_MODEL,
    }

    assert settings_for({**config, "decider": "jev_console"}).active_commandcode_fallback().enabled is True
    for decider in ("jev", "openai_compatible"):
        settings = settings_for({**config, "decider": decider})
        # Configured, yes — but invisible to a decider that does not own it.
        assert settings.commandcode_fallback().enabled is True
        assert settings.active_commandcode_fallback() == CommandCodeFallback()


def test_a_partial_trio_warns_once_without_naming_a_value(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        settings_for({"console_jev_commandcode_base_url": CC_URL})

    assert "console_jev_commandcode" in caplog.text
    assert COMMANDCODE_MODEL_SETTING in caplog.text
    assert caplog.text.count("disabled") == 1
