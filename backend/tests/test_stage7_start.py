"""启动入口显式控制代理信任，测试不构建前端也不启动服务器。"""

import pytest
import uvicorn

import start


@pytest.fixture
def startup(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist/index.html").write_text("<html>isolated</html>", encoding="utf-8")
    monkeypatch.setattr(start, "ROOT", tmp_path)
    monkeypatch.setattr(start, "FRONTEND", frontend)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    def invoke(arguments):
        monkeypatch.setattr(start.sys, "argv", ["start.py", "--skip-build", *arguments])
        return start.main()

    return invoke, calls


def test_default_start_ignores_inherited_proxy_trust(startup):
    invoke, calls = startup
    assert invoke([]) == 0
    assert calls == [(("backend.app.main:app",), {"host": "127.0.0.1", "port": 8000, "workers": 1,
                                                    "proxy_headers": False, "forwarded_allow_ips": ""})]


@pytest.mark.parametrize("argument,expected", [
    ("127.0.0.1", "127.0.0.1"),
    ("127.0.0.1, ::1,192.0.2.0/24,2001:db8::/64", "127.0.0.1,::1,192.0.2.0/24,2001:db8::/64"),
    ("192.0.2.10/24", "192.0.2.0/24"),
])
def test_explicit_proxy_ips_enable_only_the_validated_addresses(startup, argument, expected):
    invoke, calls = startup
    assert invoke(["--trusted-proxy-ips", argument, "--port", "8010"]) == 0
    assert calls[0][1]["proxy_headers"] is True
    assert calls[0][1]["forwarded_allow_ips"] == expected
    assert calls[0][1]["port"] == 8010


@pytest.mark.parametrize("argument", [
    "*", "127.0.0.1,*", "", " ", "127.0.0.1,", ",::1", "127.0.0.1,,::1",
    "localhost", "proxy.example.com", "127.0.0.1:8000", "192.0.2.0/33", "https://127.0.0.1",
])
def test_invalid_proxy_trust_is_rejected_before_server_start(startup, argument):
    invoke, calls = startup
    with pytest.raises(SystemExit) as rejected:
        invoke(["--trusted-proxy-ips", argument])
    assert rejected.value.code == 2 and not calls
