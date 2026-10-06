from pathlib import Path

import pytest

from semeval_task11.normalize import (
    NormalizationError,
    _parse_test_row,
    _read_training,
    canonical_score,
    integer_score,
    )


def test_integer_score_matches_official_scorer():
    assert integer_score('2.50') == '3'
    assert integer_score('-2.50') == '-2'
    assert integer_score('0.49') == '0'


def test_canonical_score_validates_range():
    assert canonical_score('-.70') == '-0.7'
    assert canonical_score('3.00') == '3'
    with pytest.raises(NormalizationError, match='outside'):
        canonical_score('5.1')


def test_parse_test_row_finds_fields_in_any_order():
    item = _parse_test_row(
        ['Sarcasm', '519632796449378304', '-3.5'],
        Path('test.tsv'),
        1,
        )
    assert item.tweet_id == '519632796449378304'
    assert item.sentiment == '-3.5'
    assert item.sentiment_integer == '-3'
    assert item.category == 'Sarcasm'


def test_parse_test_row_preserves_scientific_notation_id():
    item = _parse_test_row(
        ['5.39436E+17', '-1', 'sarcasm'],
        Path('test.tsv'),
        29,
        )
    assert item.tweet_id == '5.39436E+17'
    assert item.sentiment == '-1'


def test_read_training_uses_replacement_id(tmp_path):
    sources = {
        'train_integer.tsv': tmp_path / 'integer.tsv',
        'train_real.tsv': tmp_path / 'real.tsv',
        'train_id_mapping.tsv': tmp_path / 'mapping.tsv',
        }
    sources['train_integer.tsv'].write_text(
        '472189928340606976\t-4\n', encoding='utf-8'
        )
    sources['train_real.tsv'].write_text(
        '472189928340606976\t-3.99\n', encoding='utf-8'
        )
    sources['train_id_mapping.tsv'].write_text(
        '472189928340606976\t519632796449378304\t-3.99\n',
        encoding='utf-8',
        )
    rows = _read_training(sources)
    assert rows[0].original_tweet_id == '472189928340606976'
    assert rows[0].replacement_tweet_id == '519632796449378304'


def test_read_training_rejects_score_disagreement(tmp_path):
    sources = {
        'train_integer.tsv': tmp_path / 'integer.tsv',
        'train_real.tsv': tmp_path / 'real.tsv',
        'train_id_mapping.tsv': tmp_path / 'mapping.tsv',
        }
    sources['train_integer.tsv'].write_text('123456\t-4\n', encoding='utf-8')
    sources['train_real.tsv'].write_text('123456\t-3.99\n', encoding='utf-8')
    sources['train_id_mapping.tsv'].write_text(
        '123456\t654321\t-2.0\n', encoding='utf-8'
        )
    with pytest.raises(NormalizationError, match='disagrees'):
        _read_training(sources)
