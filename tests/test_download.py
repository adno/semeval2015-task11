import pytest

from semeval_task11.download import DownloadError, _validate_download


def test_rejects_html_instead_of_test_data():
    with pytest.raises(DownloadError, match='received HTML'):
        _validate_download('test.tsv', b'<!doctype html><title>blocked</title>')


def test_rejects_non_tsv_response():
    with pytest.raises(DownloadError, match='does not look like TSV'):
        _validate_download('test.tsv', b'plain error response')


def test_accepts_tsv_response():
    _validate_download('test.tsv', b'123456789\t-2\tsarcasm\n')
