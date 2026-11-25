import shutil

from doc2data.cli import _pdfs, main


def test_pdf_discovery_ignores_suffix_case(tmp_path, samples_dir):
    shutil.copy(samples_dir / "us-01.pdf", tmp_path / "SCAN0001.PDF")
    shutil.copy(samples_dir / "us-02.pdf", tmp_path / "second.pdf")
    (tmp_path / "notes.txt").write_text("not an invoice")
    assert [path.name for path in _pdfs([tmp_path])] == ["SCAN0001.PDF", "second.pdf"]


def test_ingest_reads_once_then_skips_unless_forced(tmp_path, samples_dir, monkeypatch, capsys):
    monkeypatch.setenv("DOC2DATA_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    target = str(samples_dir / "us-01.pdf")

    assert main(["ingest", target, "--extractor", "rules"]) == 0
    assert "1 accepted" in capsys.readouterr().out
    assert main(["ingest", target, "--extractor", "rules"]) == 0
    assert "1 already read" in capsys.readouterr().out
    assert main(["ingest", target, "--extractor", "rules", "--force"]) == 0
    assert "1 accepted" in capsys.readouterr().out


def test_a_missing_api_key_is_a_clear_error_not_a_traceback(monkeypatch, samples_dir, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert main(["eval", "--samples", str(samples_dir), "--extractor", "gemini"]) == 2
    assert "doc2data:" in capsys.readouterr().err


def test_a_mistyped_extractor_setting_is_a_clear_error_not_a_traceback(monkeypatch, samples_dir, capsys):
    monkeypatch.setenv("DOC2DATA_EXTRACTOR", "Gemni")
    assert main(["eval", "--samples", str(samples_dir)]) == 2
    assert "doc2data: Unknown extractor 'Gemni'" in capsys.readouterr().err


def test_eval_exits_with_an_error_when_no_document_could_be_read(monkeypatch, samples_dir, capsys):
    from conftest import FakeExtractor
    from doc2data import cli
    from doc2data.extractors.base import ExtractionError

    monkeypatch.setattr(cli, "get_extractor", lambda name: FakeExtractor(error=ExtractionError("HTTP 401")))
    assert main(["eval", "--samples", str(samples_dir), "--extractor", "rules"]) == 1
    captured = capsys.readouterr()
    assert "| Documents the extractor could not read | 20 of 20 |" in captured.out
    assert "could not read any of the documents" in captured.err


def test_eval_succeeds_when_only_some_documents_could_not_be_read(monkeypatch, samples_dir, truth, capsys):
    from conftest import TruthExtractor
    from doc2data import cli
    from doc2data.extractors.base import ExtractionError

    unlucky = (samples_dir / "eu-01.pdf").read_bytes()

    class FailsOnOne(TruthExtractor):
        def extract(self, *, text, pdf_bytes=None):
            if pdf_bytes == unlucky:
                raise ExtractionError("HTTP 503")
            return super().extract(text=text, pdf_bytes=pdf_bytes)

    monkeypatch.setattr(cli, "get_extractor", lambda name: FailsOnOne(samples_dir, truth))
    assert main(["eval", "--samples", str(samples_dir), "--extractor", "rules"]) == 0
    assert "| Documents the extractor could not read | 1 of 20 |" in capsys.readouterr().out
