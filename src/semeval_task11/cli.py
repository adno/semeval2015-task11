"""Command-line entry point for dataset reconstruction."""

import argparse
import json
import os
import sys
from pathlib import Path

from .download import DownloadError, download_sources
from .hydrate import (
    HydrationError,
    ResultCache,
    hydrate_annotations,
    make_backends,
    parse_backend_order,
    )
from .normalize import NormalizationError, normalize_sources
from .tabular import read_annotations
from .validate import ValidationError, validate_file


def _data_paths(data_dir):
    root = Path(data_dir)
    return {
        'raw': root / 'raw',
        'cache': root / 'cache',
        'annotations': root / 'processed' / 'annotations.tsv',
        'hydrated': root / 'processed' / 'hydrated.tsv',
        'report': root / 'processed' / 'reconstruction_report.json',
        }


def _add_source_arguments(parser):
    parser.add_argument('--data-dir', default='data')
    parser.add_argument(
        '--test-file',
        help='manually downloaded TestGoldIdwithCategory.tsv',
        )
    parser.add_argument(
        '--skip-test',
        action='store_true',
        help='reconstruct only the public training and trial splits',
        )
    parser.add_argument('--refresh-downloads', action='store_true')
    parser.add_argument('--timeout', type=float, default=30.0)


def _add_hydration_arguments(parser, include_data_dir=True):
    if include_data_dir:
        parser.add_argument('--data-dir', default='data')
    parser.add_argument(
        '--backend',
        default='syndication',
        help='ordered backend choice: syndication, x, syndication,x, or '
             'x,syndication',
        )
    parser.add_argument('--try-original', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--pace', type=float, default=0.1)
    parser.add_argument('--retries', type=int, default=3)
    parser.add_argument('--backoff', type=float, default=1.0)
    parser.add_argument('--refresh-cache', action='store_true')
    parser.add_argument('--hydration-timeout', type=float, default=20.0)


def build_parser():
    """Build the public CLI parser."""
    parser = argparse.ArgumentParser(
        prog='semeval-task11',
        description='Reconstruct SemEval-2015 Task 11 data.',
        )
    subparsers = parser.add_subparsers(dest='command', required=True)

    download_parser = subparsers.add_parser(
        'download',
        help='download and normalize annotation sources',
        )
    _add_source_arguments(download_parser)

    rehydrate_parser = subparsers.add_parser(
        'rehydrate',
        help='rehydrate normalized annotations',
        )
    _add_hydration_arguments(rehydrate_parser)

    validate_parser = subparsers.add_parser(
        'validate',
        help='validate a reconstructed TSV',
        )
    validate_parser.add_argument(
        'path',
        nargs='?',
        default='data/processed/annotations.tsv',
        )
    validate_parser.add_argument('--allow-missing-test', action='store_true')

    all_parser = subparsers.add_parser(
        'all',
        help='download, normalize, validate, and rehydrate',
        )
    _add_source_arguments(all_parser)
    _add_hydration_arguments(all_parser, include_data_dir=False)
    return parser


def _download(args, paths):
    sources = download_sources(
        paths['raw'],
        test_file=args.test_file,
        skip_test=args.skip_test,
        refresh=args.refresh_downloads,
        timeout=args.timeout,
        )
    annotations = normalize_sources(sources, paths['annotations'])
    summary = validate_file(
        paths['annotations'],
        allow_missing_test=args.skip_test,
        )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return annotations


def _rehydrate(args, paths, annotations=None):
    if args.workers < 1:
        raise HydrationError('--workers must be at least 1')
    if args.pace < 0 or args.retries < 0 or args.backoff < 0:
        raise HydrationError('pace, retries, and backoff must be non-negative')
    names = parse_backend_order(args.backend)
    cache = ResultCache(paths['cache'], refresh=args.refresh_cache)
    backends = make_backends(
        names,
        cache,
        bearer_token=os.environ.get('X_BEARER_TOKEN'),
        workers=args.workers,
        pace=args.pace,
        retries=args.retries,
        backoff=args.backoff,
        timeout=args.hydration_timeout,
        )
    if annotations is None:
        annotations = read_annotations(paths['annotations'])
    report = hydrate_annotations(
        annotations,
        paths['hydrated'],
        paths['report'],
        backends,
        try_original=args.try_original,
        )
    print(json.dumps(report, indent=2, sort_keys=True))


def main(argv=None):
    """Run the reconstruction CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            summary = validate_file(args.path, args.allow_missing_test)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
        paths = _data_paths(args.data_dir)
        if args.command == 'download':
            _download(args, paths)
        elif args.command == 'rehydrate':
            _rehydrate(args, paths)
        elif args.command == 'all':
            annotations = _download(args, paths)
            _rehydrate(args, paths, annotations)
            validate_file(
                paths['hydrated'],
                allow_missing_test=args.skip_test,
                )
        return 0
    except (
            DownloadError,
            HydrationError,
            NormalizationError,
            ValidationError,
            FileNotFoundError,
            ValueError,
            ) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
