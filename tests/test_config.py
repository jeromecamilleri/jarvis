"""Chargeur `.env` : uniquement des fichiers temporaires, jamais le vrai `.env` de la machine."""
from app.config import load_env_file


def write(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_simple_assignments_comments_export_and_quotes(tmp_path, monkeypatch):
    for k in ("T_A", "T_B", "T_C", "T_D", "T_E"):
        monkeypatch.delenv(k, raising=False)
    path = write(tmp_path, '# commentaire\nT_A=1\nexport T_B="deux mots"\nT_C=\'x\'\n\nT_D=a=b\nT_E=\n')
    assert load_env_file(path) == ["T_A", "T_B", "T_C", "T_D", "T_E"]
    import os
    assert (os.environ["T_A"], os.environ["T_B"], os.environ["T_C"], os.environ["T_D"], os.environ["T_E"]) == (
        "1", "deux mots", "x", "a=b", "")
    for k in ("T_A", "T_B", "T_C", "T_D", "T_E"):
        monkeypatch.delenv(k)


def test_environment_has_priority_including_empty_values(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("T_SET", "env")
    monkeypatch.setenv("T_EMPTY", "")
    path = write(tmp_path, "T_SET=fichier\nT_EMPTY=fichier\n")
    assert load_env_file(path) == []
    assert os.environ["T_SET"] == "env" and os.environ["T_EMPTY"] == ""


def test_no_expansion_substitution_or_code_execution(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("T_HOME", "/h")
    monkeypatch.delenv("T_LIT", raising=False)
    marker = tmp_path / "pwned"
    load_env_file(write(tmp_path, f"T_LIT=$T_HOME/$(touch {marker})\n"))
    assert os.environ["T_LIT"] == f"$T_HOME/$(touch {marker})" and not marker.exists()
    monkeypatch.delenv("T_LIT")


def test_missing_unreadable_or_invalid_file_is_harmless(tmp_path):
    assert load_env_file(tmp_path / "absent.env") == []
    assert load_env_file(tmp_path) == []  # un répertoire
    path = tmp_path / "bin.env"
    path.write_bytes(b"\xff\xfe\x00")
    assert load_env_file(path) == []
    assert load_env_file(write(tmp_path, "pas une affectation\n=vide\n1BAD=x\n")) == []


def test_jarvis_env_file_variable_selects_or_disables_loading(tmp_path, monkeypatch):
    import os
    monkeypatch.delenv("T_SEL", raising=False)
    monkeypatch.setenv("JARVIS_ENV_FILE", "")
    assert load_env_file() == []
    monkeypatch.setenv("JARVIS_ENV_FILE", str(write(tmp_path, "T_SEL=ok\n")))
    assert load_env_file() == ["T_SEL"] and os.environ["T_SEL"] == "ok"
    monkeypatch.delenv("T_SEL")
