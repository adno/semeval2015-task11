"""Validation for normalized and hydrated reconstruction outputs."""

import csv
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .model import ANNOTATION_FIELDS, HYDRATION_FIELDS, TERMINAL_STATUSES
from .normalize import EXPECTED_COUNTS, SCIENTIFIC_ID, integer_score_consistent


class ValidationError(ValueError):
    """Raised when reconstructed data violates an expected invariant."""


def validate_file(path, allow_missing_test=False):
    """Validate a normalized or hydrated TSV and return summary counts."""
    path = Path(path)
    with path.open(encoding='utf-8', newline='') as file:
        reader = csv.DictReader(file, delimiter='\t')
        fields = reader.fieldnames or []
        missing = set(ANNOTATION_FIELDS) - set(fields)
        if missing:
            names = ', '.join(sorted(missing))
            raise ValidationError(f'missing required columns: {names}')
        hydrated = set(HYDRATION_FIELDS).issubset(fields)
        rows = list(reader)

    split_counts = Counter()
    seen = set()
    replacement_ids = set()
    status_counts = Counter()
    for line_number, row in enumerate(rows, 2):
        tweet_id = row['tweet_id']
        split = row['split']
        malformed_test_id = (
            split == 'test' and SCIENTIFIC_ID.fullmatch(tweet_id)
            )
        if not tweet_id.isdigit() and not malformed_test_id:
            raise ValidationError(
                f'line {line_number}: invalid tweet_id {tweet_id!r}'
                )
        key = (split, tweet_id)
        if key in seen and not malformed_test_id:
            raise ValidationError(
                f'line {line_number}: duplicate {split} ID {tweet_id}'
                )
        seen.add(key)
        if split not in EXPECTED_COUNTS:
            raise ValidationError(
                f'line {line_number}: unknown split {split!r}'
                )
        split_counts[split] += 1
        try:
            score = Decimal(row['sentiment'])
        except InvalidOperation as error:
            raise ValidationError(
                f'line {line_number}: invalid sentiment value'
                ) from error
        if not Decimal('-5') <= score <= Decimal('5'):
            raise ValidationError(
                f'line {line_number}: sentiment outside [-5, 5]'
                )
        if not integer_score_consistent(score, row['sentiment_integer']):
            raise ValidationError(
                f'line {line_number}: inconsistent integer sentiment'
                )
        replacement = row['replacement_tweet_id']
        if split == 'train':
            if not replacement.isdigit():
                raise ValidationError(
                    f'line {line_number}: missing training replacement ID'
                    )
            if replacement in replacement_ids:
                raise ValidationError(
                    f'line {line_number}: duplicate replacement ID '
                    f'{replacement}'
                    )
            replacement_ids.add(replacement)
        elif replacement:
            raise ValidationError(
                f'line {line_number}: unexpected replacement ID in {split}'
                )
        if hydrated:
            status = row['hydration_status']
            if status not in TERMINAL_STATUSES:
                raise ValidationError(
                    f'line {line_number}: unknown hydration status {status!r}'
                    )
            if status == 'available' and not row['text']:
                raise ValidationError(
                    f'line {line_number}: available row has no text'
                    )
            status_counts[status] += 1

    required = {'train', 'trial'}
    if not allow_missing_test:
        required.add('test')
    for split in required:
        expected = EXPECTED_COUNTS[split]
        if split_counts[split] != expected:
            raise ValidationError(
                f'{split}: expected {expected} rows, got {split_counts[split]}'
                )
    if 'test' in split_counts and split_counts['test'] != EXPECTED_COUNTS['test']:
        raise ValidationError(
            f"test: expected {EXPECTED_COUNTS['test']} rows, "
            f"got {split_counts['test']}"
            )
    return {
        'rows': len(rows),
        'splits': dict(sorted(split_counts.items())),
        'hydration_statuses': dict(sorted(status_counts.items())),
        }
