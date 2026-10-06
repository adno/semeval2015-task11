"""Download released annotation sources."""

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


OFFICIAL_BASE = (
    'https://alt.qcri.org/semeval2015/task11/data/uploads/'
    )
SOURCES = {
    'train_integer.tsv': OFFICIAL_BASE + 'weightedtweetdata_int.tsv',
    'train_real.tsv': OFFICIAL_BASE + 'weightedtweetdata_real.tsv',
    'train_id_mapping.tsv': OFFICIAL_BASE + 'newid_weightedtweetdata.tsv',
    'trial.xls': OFFICIAL_BASE + 'task-11-trial-data.xls',
    }
TEST_URL = (
    'https://www.researchgate.net/publication/profile/Aniruddha-Ghosh-7/'
    'publication/318570500_Semeval_2015_Task_11_Tweets_ids_of_Test_data_set_'
    'with_Gold_Sentiment_annotations_and_category/data/'
    '5970c6b3458515fa8de6e238/TestGoldIdwithCategory.tsv'
    '?origin=publication_list'
    )
USER_AGENT = 'semeval2015-task11-reconstruction/1.0'


class DownloadError(RuntimeError):
    """Raised when a source cannot be downloaded or validated."""


def _looks_like_html(data):
    sample = data[:1024].lstrip().lower()
    return sample.startswith(b'<!doctype html') or sample.startswith(b'<html')


def _validate_download(name, data):
    if not data:
        raise DownloadError(f'{name}: downloaded an empty response')
    if _looks_like_html(data):
        raise DownloadError(
            f'{name}: received HTML instead of dataset content; '
            'the provider may require an interactive download'
            )
    if name.endswith('.tsv') and b'\t' not in data[:8192]:
        raise DownloadError(f'{name}: response does not look like TSV data')
    if name.endswith('.xls') and not data.startswith(b'\xd0\xcf\x11\xe0'):
        raise DownloadError(f'{name}: response is not a legacy Excel workbook')


def _fetch(url, timeout):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise DownloadError(f'could not download {url}: {error}') from error


def _atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f'.{path.name}.',
        suffix='.tmp',
        )
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(data)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _copy_source(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f'.{destination.name}.',
        suffix='.tmp',
        )
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def download_sources(raw_dir, test_file=None, skip_test=False,
                     refresh=False, timeout=30.0):
    """Download official sources and return their local paths."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in SOURCES.items():
        path = raw_dir / name
        if refresh or not path.exists():
            data = _fetch(url, timeout)
            _validate_download(name, data)
            _atomic_bytes(path, data)
        paths[name] = path

    test_path = raw_dir / 'test.tsv'
    if test_file:
        source = Path(test_file)
        if not source.is_file():
            raise DownloadError(f'test file does not exist: {source}')
        data = source.read_bytes()
        _validate_download('test.tsv', data)
        _copy_source(source, test_path)
        paths['test.tsv'] = test_path
    elif not skip_test:
        if refresh or not test_path.exists():
            try:
                data = _fetch(TEST_URL, timeout)
                _validate_download('test.tsv', data)
                _atomic_bytes(test_path, data)
            except DownloadError as error:
                raise DownloadError(
                    f'{error}\nDownload TestGoldIdwithCategory.tsv from '
                    'the ResearchGate supplementary resource and rerun with '
                    '--test-file PATH, or use --skip-test.'
                    ) from error
        paths['test.tsv'] = test_path

    manifest = {}
    for name, path in sorted(paths.items()):
        if name == 'test.tsv':
            source = str(Path(test_file).resolve()) if test_file else TEST_URL
        else:
            source = SOURCES[name]
        manifest[name] = {
            'path': path.name,
            'source': source,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size,
            }
    manifest_path = raw_dir / 'source_manifest.json'
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
        )
    return paths
