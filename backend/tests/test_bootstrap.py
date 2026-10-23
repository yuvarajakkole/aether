from aether.bootstrap import interpolate


def test_interpolate_keys_values_and_defaults(monkeypatch):
    monkeypatch.setenv("APP_NAME", "demo")
    monkeypatch.delenv("HEADER_NAME", raising=False)
    out = interpolate({"headers": {"${HEADER_NAME:-x-client-app}": "${APP_NAME}"}, "list": ["${APP_NAME}", 3], "url": "${MISSING_URL}"})
    assert out == {"headers": {"x-client-app": "demo"}, "list": ["demo", 3], "url": ""}
