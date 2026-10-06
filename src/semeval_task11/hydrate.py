"""Tweet hydration backends and reconstruction orchestration."""

import concurrent.futures
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .model import AVAILABLE, HYDRATION_FIELDS, HydrationResult
from .tabular import atomic_dict_writer


SYNDICATION_URL = 'https://cdn.syndication.twimg.com/tweet-result'
X_API_URL = 'https://api.x.com/2/tweets'
USER_AGENT = 'semeval2015-task11-reconstruction/1.0'
VALID_BACKENDS = {'syndication', 'x'}


class HydrationError(RuntimeError):
    """Raised for invalid hydration configuration."""


class HttpResponse:
    """Small transport-neutral HTTP response."""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = {key.lower(): value for key, value in headers.items()}
        self.body = body


def http_get(url, headers, timeout):
    """Fetch a URL while retaining HTTP error bodies and headers."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                response.status,
                dict(response.headers.items()),
                response.read(),
                )
    except urllib.error.HTTPError as error:
        return HttpResponse(
            error.code,
            dict(error.headers.items()),
            error.read(),
            )


def parse_backend_order(value):
    """Parse and validate an ordered comma-separated backend selection."""
    backends = [item.strip().lower() for item in value.split(',') if item.strip()]
    if not backends:
        raise HydrationError('at least one hydration backend is required')
    unknown = set(backends) - VALID_BACKENDS
    if unknown:
        raise HydrationError(
            'unknown hydration backend: ' + ', '.join(sorted(unknown))
            )
    if len(set(backends)) != len(backends):
        raise HydrationError('hydration backends must not be repeated')
    return backends


class ResultCache:
    """Persistent per-ID JSON result cache."""

    def __init__(self, root, refresh=False):
        self.root = Path(root)
        self.refresh = refresh
        self.lock = threading.Lock()

    def load(self, backend, tweet_id):
        if self.refresh:
            return None
        path = self.root / backend / f'{tweet_id}.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return HydrationResult(**data)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return None

    def save(self, result):
        directory = self.root / result.backend
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{result.tweet_id}.json'
        payload = json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=directory,
            prefix=f'.{result.tweet_id}.',
            suffix='.tmp',
            text=True,
            )
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
                file.write(payload + '\n')
            with self.lock:
                os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def _status_from_detail(detail, default='missing'):
    lowered = detail.lower()
    if 'deleted' in lowered:
        return 'deleted'
    if 'protected' in lowered or 'not authorized' in lowered:
        return 'protected'
    if 'withheld' in lowered:
        return 'withheld'
    if 'authenticat' in lowered or 'unauthorized' in lowered:
        return 'auth_error'
    return default


class SyndicationBackend:
    """Credential-free client for X's undocumented syndication endpoint."""

    name = 'syndication'

    def __init__(self, cache, workers=4, pace=0.1, retries=3,
                 backoff=1.0, timeout=20.0, transport=http_get,
                 sleep=time.sleep):
        self.cache = cache
        self.workers = workers
        self.pace = pace
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.transport = transport
        self.sleep = sleep
        self.pace_lock = threading.Lock()
        self.last_request = 0.0

    def _pace_request(self):
        with self.pace_lock:
            delay = self.pace - (time.monotonic() - self.last_request)
            if delay > 0:
                self.sleep(delay)
            self.last_request = time.monotonic()

    def _parse(self, tweet_id, response):
        if response.status in {401, 403}:
            return HydrationResult(
                tweet_id, backend=self.name, status='auth_error',
                detail=f'HTTP {response.status}',
                )
        if response.status == 404:
            return HydrationResult(
                tweet_id, backend=self.name, status='missing',
                detail='HTTP 404',
                )
        try:
            payload = json.loads(response.body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HydrationResult(
                tweet_id, backend=self.name, status='malformed',
                detail='response was not valid JSON',
                )
        typename = payload.get('__typename', '')
        text = payload.get('text')
        if typename == 'Tweet' and isinstance(text, str):
            return HydrationResult(
                tweet_id, text=text, backend=self.name, status=AVAILABLE,
                )
        if typename == 'TweetTombstone':
            detail = (
                payload.get('tombstone', {}).get('text', {}).get('text', '')
                )
            return HydrationResult(
                tweet_id,
                backend=self.name,
                status=_status_from_detail(detail),
                detail=detail,
                )
        detail = payload.get('detail') or payload.get('message') or typename
        return HydrationResult(
            tweet_id,
            backend=self.name,
            status=_status_from_detail(str(detail), 'malformed'),
            detail=str(detail),
            )

    def _lookup_one(self, tweet_id):
        cached = self.cache.load(self.name, tweet_id)
        if cached:
            return cached
        query = urllib.parse.urlencode({'id': tweet_id, 'token': '0'})
        url = f'{SYNDICATION_URL}?{query}'
        for attempt in range(self.retries + 1):
            try:
                self._pace_request()
                response = self.transport(
                    url,
                    {'User-Agent': USER_AGENT},
                    self.timeout,
                    )
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                if attempt < self.retries:
                    self.sleep(self.backoff * (2 ** attempt))
                    continue
                result = HydrationResult(
                    tweet_id,
                    backend=self.name,
                    status='transient_error',
                    detail=str(error),
                    )
                self.cache.save(result)
                return result
            if response.status == 429 or response.status >= 500:
                if attempt < self.retries:
                    retry_after = response.headers.get('retry-after')
                    delay = float(retry_after) if retry_after else (
                        self.backoff * (2 ** attempt)
                        )
                    self.sleep(delay)
                    continue
                result = HydrationResult(
                    tweet_id,
                    backend=self.name,
                    status='transient_error',
                    detail=f'HTTP {response.status}',
                    )
            else:
                result = self._parse(tweet_id, response)
            self.cache.save(result)
            return result

    def lookup_many(self, tweet_ids):
        """Look up IDs concurrently while preserving input mapping."""
        unique = list(dict.fromkeys(tweet_ids))
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.workers) as executor:
            results = executor.map(self._lookup_one, unique)
        return {result.tweet_id: result for result in results}


class XApiBackend:
    """Batch client for the official X API v2 tweet lookup endpoint."""

    name = 'x'

    def __init__(self, cache, bearer_token, retries=3, backoff=1.0,
                 timeout=30.0, transport=http_get, sleep=time.sleep):
        if not bearer_token:
            raise HydrationError(
                'the x backend requires the X_BEARER_TOKEN environment variable'
                )
        self.cache = cache
        self.bearer_token = bearer_token
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.transport = transport
        self.sleep = sleep

    def _request_batch(self, tweet_ids):
        query = urllib.parse.urlencode({
            'ids': ','.join(tweet_ids),
            'tweet.fields': 'created_at,lang',
            })
        url = f'{X_API_URL}?{query}'
        headers = {
            'Authorization': f'Bearer {self.bearer_token}',
            'User-Agent': USER_AGENT,
            }
        for attempt in range(self.retries + 1):
            try:
                response = self.transport(url, headers, self.timeout)
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                if attempt < self.retries:
                    self.sleep(self.backoff * (2 ** attempt))
                    continue
                return None, str(error)
            if response.status == 429 or response.status >= 500:
                if attempt < self.retries:
                    reset = response.headers.get('x-rate-limit-reset')
                    retry_after = response.headers.get('retry-after')
                    if retry_after:
                        delay = float(retry_after)
                    elif reset:
                        delay = max(0.0, float(reset) - time.time())
                    else:
                        delay = self.backoff * (2 ** attempt)
                    self.sleep(delay)
                    continue
                return None, f'HTTP {response.status}'
            try:
                return json.loads(response.body.decode('utf-8')), None
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}, 'response was not valid JSON'

    def _parse_batch(self, tweet_ids, payload, request_error):
        if request_error:
            return {
                tweet_id: HydrationResult(
                    tweet_id,
                    backend=self.name,
                    status='transient_error',
                    detail=request_error,
                    )
                for tweet_id in tweet_ids
                }
        results = {}
        for tweet in payload.get('data', []):
            tweet_id = str(tweet.get('id', ''))
            text = tweet.get('text')
            if tweet_id and isinstance(text, str):
                results[tweet_id] = HydrationResult(
                    tweet_id,
                    text=text,
                    backend=self.name,
                    status=AVAILABLE,
                    )
        for error in payload.get('errors', []):
            tweet_id = str(
                error.get('resource_id') or error.get('value') or ''
                )
            detail = str(error.get('detail') or error.get('title') or '')
            if tweet_id in tweet_ids:
                results[tweet_id] = HydrationResult(
                    tweet_id,
                    backend=self.name,
                    status=_status_from_detail(detail),
                    detail=detail,
                    )
        default_status = 'auth_error' if payload.get('status') in {401, 403} \
            else 'missing'
        detail = str(payload.get('detail') or payload.get('title') or '')
        for tweet_id in tweet_ids:
            results.setdefault(tweet_id, HydrationResult(
                tweet_id,
                backend=self.name,
                status=_status_from_detail(detail, default_status),
                detail=detail,
                ))
        return results

    def lookup_many(self, tweet_ids):
        """Look up IDs in API batches of at most 100."""
        unique = list(dict.fromkeys(tweet_ids))
        results = {}
        pending = []
        for tweet_id in unique:
            cached = self.cache.load(self.name, tweet_id)
            if cached:
                results[tweet_id] = cached
            else:
                pending.append(tweet_id)
        for offset in range(0, len(pending), 100):
            batch = pending[offset:offset + 100]
            payload, request_error = self._request_batch(batch)
            parsed = self._parse_batch(batch, payload or {}, request_error)
            for result in parsed.values():
                self.cache.save(result)
            results.update(parsed)
        return results


def make_backends(names, cache, bearer_token=None, workers=4, pace=0.1,
                  retries=3, backoff=1.0, timeout=20.0):
    """Construct the configured ordered backend list."""
    backends = []
    for name in names:
        if name == 'syndication':
            backends.append(SyndicationBackend(
                cache,
                workers=workers,
                pace=pace,
                retries=retries,
                backoff=backoff,
                timeout=timeout,
                ))
        elif name == 'x':
            backends.append(XApiBackend(
                cache,
                bearer_token=bearer_token,
                retries=retries,
                backoff=backoff,
                timeout=timeout,
                ))
    return backends


def _hydrate_ids(tweet_ids, backends):
    unique = list(dict.fromkeys(tweet_ids))
    unresolved = [tweet_id for tweet_id in unique if tweet_id.isdigit()]
    final = {
        tweet_id: HydrationResult(
            tweet_id,
            backend='source',
            status='malformed',
            detail='source tweet ID is not an exact integer',
            )
        for tweet_id in unique if not tweet_id.isdigit()
        }
    for backend in backends:
        if not unresolved:
            break
        results = backend.lookup_many(unresolved)
        next_unresolved = []
        for tweet_id in unresolved:
            result = results[tweet_id]
            final[tweet_id] = result
            if not result.available:
                next_unresolved.append(tweet_id)
        unresolved = next_unresolved
    return final


def hydrate_annotations(annotations, output_path, report_path, backends,
                        try_original=False):
    """Hydrate annotations and write a TSV plus JSON reconstruction report."""
    primary_ids = [
        item.replacement_tweet_id or item.tweet_id for item in annotations
        ]
    primary_results = _hydrate_ids(primary_ids, backends)
    original_results = {}
    if try_original:
        original_ids = []
        for item, primary_id in zip(annotations, primary_ids):
            if (not primary_results[primary_id].available and
                    item.original_tweet_id != primary_id):
                original_ids.append(item.original_tweet_id)
        original_results = _hydrate_ids(original_ids, backends)

    rows = []
    statuses = Counter()
    backend_counts = Counter()
    recovered = Counter()
    unresolved = []
    for item, primary_id in zip(annotations, primary_ids):
        result = primary_results[primary_id]
        source = 'replacement' if item.replacement_tweet_id else 'released'
        if not result.available and item.original_tweet_id in original_results:
            original = original_results[item.original_tweet_id]
            if original.available:
                result = original
                source = 'original'
        row = item.as_row()
        row.update({
            'hydrated_tweet_id': result.tweet_id,
            'text': result.text,
            'hydration_backend': result.backend,
            'hydration_status': result.status,
            })
        rows.append(row)
        statuses[result.status] += 1
        backend_counts[result.backend] += 1
        if result.available:
            recovered[source] += 1
        else:
            unresolved.append(item.tweet_id)
    atomic_dict_writer(output_path, HYDRATION_FIELDS, rows)

    split_counts = Counter(item.split for item in annotations)
    report = {
        'annotations': len(annotations),
        'backend_order': [backend.name for backend in backends],
        'backend_results': dict(sorted(backend_counts.items())),
        'hydration_statuses': dict(sorted(statuses.items())),
        'recovered_by_id_type': dict(sorted(recovered.items())),
        'source_counts': dict(sorted(split_counts.items())),
        'try_original': try_original,
        'unresolved_count': len(unresolved),
        'unresolved_tweet_ids': unresolved,
        }
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
        )
    return report
