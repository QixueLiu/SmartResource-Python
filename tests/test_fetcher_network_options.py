"""Network options and redirect classification should remain explicit."""
import pytest

from smartresource.fetcher import client_network_options, redirect_error


def test_proxy_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SMARTRESOURCE_USE_ENV_PROXY", raising=False)
    monkeypatch.delenv("SMARTRESOURCE_HTTP_PROXY", raising=False)
    options = client_network_options()
    assert options["proxy"] is None
    assert options["trust_env"] is False


def test_env_proxy_is_opt_in(monkeypatch):
    monkeypatch.delenv("SMARTRESOURCE_HTTP_PROXY", raising=False)
    monkeypatch.setenv("SMARTRESOURCE_USE_ENV_PROXY", "1")
    assert client_network_options()["trust_env"] is True


def test_explicit_proxy_disables_env_fallback(monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("SMARTRESOURCE_USE_ENV_PROXY", "1")
    assert client_network_options() == {
        "proxy": "http://127.0.0.1:7890", "trust_env": False,
    }


def test_invalid_proxy_rejected(monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_HTTP_PROXY", "socks://127.0.0.1:7890")
    with pytest.raises(ValueError):
        client_network_options()


def test_home_redirect_not_treated_as_a_search_result():
    message = redirect_error("https://books.example.org/search?q=hi", "books.example.org", 302, "/")
    assert "跳回首页" in message
    assert "跨域" in redirect_error(
        "https://books.example.org/search?q=hi", "books.example.org", 302,
        "https://elsewhere.example.org/",
    )
