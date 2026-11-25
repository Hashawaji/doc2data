from doc2data import config
from doc2data.cli import main


def load(tmp_path, text, environ=None):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    environ = {} if environ is None else environ
    config.load_env_file(path, environ)
    return environ


def test_reads_key_value_lines(tmp_path):
    env = load(tmp_path, "GEMINI_API_KEY=abc123\nDOC2DATA_EXTRACTOR = gemini\n")
    assert env == {"GEMINI_API_KEY": "abc123", "DOC2DATA_EXTRACTOR": "gemini"}


def test_ignores_comments_blank_lines_and_anything_that_is_not_a_pair(tmp_path):
    env = load(tmp_path, "# a comment\n\n#KEY=commented\nnot a pair\n=novalue\n1BAD=x\nGOOD=1\n")
    assert env == {"GOOD": "1"}


def test_understands_export_quotes_and_trailing_comments(tmp_path):
    text = "export A=1\nB=\"two words\"\nC='x #y'\nD=plain # note\nE=\"quoted\" # note\nF=\nG=a=b==\n"
    assert load(tmp_path, text) == {
        "A": "1", "B": "two words", "C": "x #y", "D": "plain", "E": "quoted", "F": "", "G": "a=b==",
    }


def test_a_file_saved_with_a_byte_order_mark_still_reads(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"\xef\xbb\xbfGEMINI_API_KEY=abc123\r\nOTHER=1\r\n")
    environ = {}
    config.load_env_file(path, environ)
    assert environ == {"GEMINI_API_KEY": "abc123", "OTHER": "1"}


def test_a_variable_that_is_already_set_wins(tmp_path):
    env = load(tmp_path, "GEMINI_API_KEY=from-file\n", {"GEMINI_API_KEY": "from-shell"})
    assert env == {"GEMINI_API_KEY": "from-shell"}


def test_a_missing_file_is_not_an_error(tmp_path):
    environ = {}
    config.load_env_file(tmp_path / "absent.env", environ)
    config.load_env_file(tmp_path, environ)  # a directory, not a file
    assert environ == {}


def test_the_command_line_reads_the_env_file_before_its_settings(tmp_path, monkeypatch):
    database = tmp_path / "from-env-file.db"
    env_file = tmp_path / ".env"
    env_file.write_text(f"DOC2DATA_DATABASE_URL=sqlite:///{database}\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", str(env_file))
    monkeypatch.setenv("DOC2DATA_DATABASE_URL", "placeholder")  # registers the variable for clean-up
    monkeypatch.delenv("DOC2DATA_DATABASE_URL")

    assert main(["export", "--out", str(tmp_path / "invoices.csv")]) == 0
    assert database.exists()


def test_a_later_line_replaces_an_earlier_one(tmp_path):
    # The usual way to fill in the example file: the empty placeholder stays, the key goes below it.
    env = load(tmp_path, "GEMINI_API_KEY=\nGEMINI_API_KEY=abc123\n")
    assert env == {"GEMINI_API_KEY": "abc123"}


def test_a_comment_may_be_the_whole_value_or_follow_a_tab(tmp_path):
    env = load(tmp_path, "A= # paste your key here\nB=value\t# the default\nC=a#b\n")
    assert env == {"A": "", "B": "value", "C": "a#b"}


def test_a_file_that_is_not_utf8_text_is_ignored(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes("GEMINI_API_KEY=abc123\n".encode("utf-16"))
    environ = {}
    config.load_env_file(path, environ)
    assert environ == {}
