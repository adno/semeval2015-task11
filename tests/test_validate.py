import csv

import pytest

from semeval_task11.model import ANNOTATION_FIELDS
from semeval_task11.validate import ValidationError, validate_file


def write_rows(path, rows):
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, ANNOTATION_FIELDS, delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def row(tweet_id, split, replacement=''):
    return {
        'tweet_id': tweet_id,
        'original_tweet_id': tweet_id,
        'replacement_tweet_id': replacement,
        'sentiment': '-1.2',
        'sentiment_integer': '-1',
        'category': '',
        'split': split,
        }


def test_validation_detects_duplicate_ids(tmp_path):
    path = tmp_path / 'annotations.tsv'
    item = row('123456', 'train', '654321')
    write_rows(path, [item, item])
    with pytest.raises(ValidationError, match='duplicate train ID'):
        validate_file(path, allow_missing_test=True)


def test_validation_detects_bad_integer_score(tmp_path):
    path = tmp_path / 'annotations.tsv'
    item = row('123456', 'train', '654321')
    item['sentiment_integer'] = '-2'
    write_rows(path, [item])
    with pytest.raises(ValidationError, match='inconsistent'):
        validate_file(path, allow_missing_test=True)
