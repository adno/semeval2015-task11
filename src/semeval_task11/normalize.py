"""Normalize all released annotation formats into one stable TSV."""

import csv
import re
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

from .model import ANNOTATION_FIELDS, Annotation
from .tabular import atomic_dict_writer


EXPECTED_COUNTS = {
    'train': 8000,
    'trial': 1025,
    'test': 4000,
    }
SCIENTIFIC_ID = re.compile(r'^\d(?:\.\d+)?[Ee]\+?\d+$')


class NormalizationError(ValueError):
    """Raised when a source file is structurally invalid."""


def integer_score(value):
    """Round a task score to its published 11-point integer scale."""
    try:
        score = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise NormalizationError(f'invalid sentiment score: {value!r}') from error
    rounded = (score + Decimal('0.5')).to_integral_value(
        rounding=ROUND_FLOOR
        )
    return str(int(rounded))


def canonical_score(value):
    """Return a plain decimal representation for a sentiment score."""
    try:
        score = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise NormalizationError(f'invalid sentiment score: {value!r}') from error
    if not Decimal('-5') <= score <= Decimal('5'):
        raise NormalizationError(f'sentiment score is outside [-5, 5]: {score}')
    rendered = format(score, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


def integer_score_consistent(real_value, integer_value):
    score = Decimal(real_value)
    published = Decimal(integer_value)
    if score % 1 in {Decimal('0.5'), Decimal('-0.5')}:
        lower = score.to_integral_value(rounding=ROUND_FLOOR)
        return published in {lower, lower + 1}
    return published == Decimal(integer_score(score))


def _valid_id(value):
    return value.isdigit() and 5 <= len(value) <= 25


def _read_simple_tsv(path, columns):
    rows = []
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        reader = csv.reader(file, delimiter='\t')
        for line_number, row in enumerate(reader, 1):
            if not row or not any(field.strip() for field in row):
                continue
            if len(row) < columns:
                raise NormalizationError(
                    f'{path}:{line_number}: expected {columns} columns'
                    )
            rows.append([field.strip() for field in row[:columns]])
    return rows


def _read_training(paths):
    integer_rows = _read_simple_tsv(paths['train_integer.tsv'], 2)
    real_rows = _read_simple_tsv(paths['train_real.tsv'], 2)
    mapping_rows = _read_simple_tsv(paths['train_id_mapping.tsv'], 3)
    integer = dict(integer_rows)
    real = dict(real_rows)
    mapping = {row[0]: (row[1], row[2]) for row in mapping_rows}
    if len(integer) != len(integer_rows):
        raise NormalizationError('duplicate ID in integer training data')
    if len(real) != len(real_rows):
        raise NormalizationError('duplicate ID in real-valued training data')
    if len(mapping) != len(mapping_rows):
        raise NormalizationError('duplicate ID in training ID mapping')
    if set(integer) != set(real) or set(integer) != set(mapping):
        raise NormalizationError('training source files contain different IDs')

    annotations = []
    for tweet_id, published_integer in integer_rows:
        replacement_id, mapped_score = mapping[tweet_id]
        if not _valid_id(tweet_id) or not _valid_id(replacement_id):
            raise NormalizationError(f'invalid training tweet ID: {tweet_id}')
        sentiment = canonical_score(real[tweet_id])
        if canonical_score(mapped_score) != sentiment:
            raise NormalizationError(
                f'mapping score disagrees for tweet {tweet_id}'
                )
        published_integer = canonical_score(published_integer)
        if not integer_score_consistent(sentiment, published_integer):
            raise NormalizationError(
                f'integer score disagrees for tweet {tweet_id}'
                )
        annotations.append(Annotation(
            tweet_id=tweet_id,
            original_tweet_id=tweet_id,
            replacement_tweet_id=replacement_id,
            sentiment=sentiment,
            sentiment_integer=published_integer,
            category='',
            split='train',
            ))
    return annotations


def _id_cell_text(cell):
    value = cell.value
    if cell.ctype == 2:
        if value != int(value):
            raise NormalizationError(f'non-integral numeric tweet ID: {value}')
        value = int(value)
    return str(value).strip()


def _read_trial(path):
    try:
        import xlrd
    except ImportError as error:
        raise NormalizationError(
            'reading trial.xls requires xlrd; install the project dependencies'
            ) from error
    workbook = xlrd.open_workbook(path)
    rows = []
    for sheet in workbook.sheets():
        for index in range(sheet.nrows):
            if sheet.ncols < 2:
                continue
            tweet_id = _id_cell_text(sheet.cell(index, 0))
            score = str(sheet.cell(index, 1).value).strip()
            if not tweet_id and not score:
                continue
            if not _valid_id(tweet_id):
                if index == 0:
                    continue
                raise NormalizationError(
                    f'{path}: row {index + 1}: invalid tweet ID {tweet_id!r}'
                    )
            sentiment = canonical_score(score)
            rows.append(Annotation(
                tweet_id=tweet_id,
                original_tweet_id=tweet_id,
                replacement_tweet_id='',
                sentiment=sentiment,
                sentiment_integer=integer_score(sentiment),
                category='',
                split='trial',
                ))
    return rows


def _parse_test_row(row, path, line_number):
    fields = [field.strip() for field in row if field.strip()]
    tweet_ids = [field for field in fields if _valid_id(field)]
    if not tweet_ids:
        tweet_ids = [field for field in fields if SCIENTIFIC_ID.fullmatch(field)]
    if not tweet_ids:
        return None
    tweet_id = tweet_ids[0]
    score = None
    category = ''
    for field in fields:
        if field == tweet_id:
            continue
        try:
            possible = Decimal(field)
        except InvalidOperation:
            if not category:
                category = field
            continue
        if Decimal('-5') <= possible <= Decimal('5') and score is None:
            score = field
    if score is None:
        raise NormalizationError(
            f'{path}:{line_number}: no sentiment score found'
            )
    sentiment = canonical_score(score)
    return Annotation(
        tweet_id=tweet_id,
        original_tweet_id=tweet_id,
        replacement_tweet_id='',
        sentiment=sentiment,
        sentiment_integer=integer_score(sentiment),
        category=category,
        split='test',
        )


def _read_test(path):
    rows = []
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        for line_number, row in enumerate(csv.reader(file, delimiter='\t'), 1):
            annotation = _parse_test_row(row, path, line_number)
            if annotation:
                rows.append(annotation)
    return rows


def normalize_sources(paths, output_path):
    """Normalize available source paths and write the combined TSV."""
    annotations = _read_training(paths)
    annotations.extend(_read_trial(paths['trial.xls']))
    if 'test.tsv' in paths:
        annotations.extend(_read_test(paths['test.tsv']))
    order = {'train': 0, 'trial': 1, 'test': 2}
    annotations.sort(key=lambda item: (
        order[item.split],
        0 if item.tweet_id.isdigit() else 1,
        int(item.tweet_id) if item.tweet_id.isdigit() else item.tweet_id,
        ))
    atomic_dict_writer(
        output_path,
        ANNOTATION_FIELDS,
        (annotation.as_row() for annotation in annotations),
        )
    return annotations
