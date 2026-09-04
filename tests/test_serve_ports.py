import pytest

import serve


def test_ports_libres(monkeypatch):
    monkeypatch.setattr(serve, "_port_open", lambda port: False)
    assert serve._ensure_app_ports_available(8001, 3001) is None


def test_port_occupe_refuse_un_decalage_silencieux(monkeypatch):
    monkeypatch.setattr(serve, "_port_open", lambda port: port == 3001)
    with pytest.raises(RuntimeError, match=r"3001.*Aucun port de remplacement"):
        serve._ensure_app_ports_available(8001, 3001)
