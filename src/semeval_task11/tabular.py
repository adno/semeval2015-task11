"""TSV input and output helpers."""

import csv
import os
import tempfile
from pathlib import Path

from .model import ANNOTATION_FIELDS, Annotation


def atomic_dict_writer(path, fieldnames, rows):
    """Write dictionaries as TSV and atomically replace the destination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f'.{path.name}.',
        suffix='.tmp',
        text=True,
        )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames,
                delimiter='\t',
                quoting=csv.QUOTE_MINIMAL,
                lineterminator='\n',
                )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_annotations(path):
    """Load normalized annotations from a TSV file."""
    with Path(path).open(encoding='utf-8', newline='') as file:
        reader = csv.DictReader(file, delimiter='\t')
        missing = set(ANNOTATION_FIELDS) - set(reader.fieldnames or [])
        if missing:
            names = ', '.join(sorted(missing))
            raise ValueError(f'annotation file is missing columns: {names}')
        return [
            Annotation(**{field: row[field] for field in ANNOTATION_FIELDS})
            for row in reader
            ]
