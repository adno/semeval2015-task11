import json
import csv

import pytest

from semeval_task11.hydrate import (
    HttpResponse,
    HydrationError,
    ResultCache,
    SyndicationBackend,
    XApiBackend,
    hydrate_annotations,
    parse_backend_order,
    )
from semeval_task11.model import Annotation, HydrationResult


def response(payload, status=200, headers=None):
    return HttpResponse(
        status,
        headers or {},
        json.dumps(payload).encode('utf-8'),
        )


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('syndication', ['syndication']),
        ('x', ['x']),
        ('syndication,x', ['syndication', 'x']),
        ('x,syndication', ['x', 'syndication']),
        ],
    )
def test_parse_backend_order(value, expected):
    assert parse_backend_order(value) == expected


def test_parse_backend_order_rejects_duplicates():
    with pytest.raises(HydrationError, match='repeated'):
        parse_backend_order('x,x')


def test_syndication_parses_tweet(tmp_path):
    def transport(url, headers, timeout):
        return response({
            '__typename': 'Tweet',
            'id_str': '123456',
            'text': 'hello\tworld\nagain',
            })

    backend = SyndicationBackend(
        ResultCache(tmp_path),
        workers=1,
        pace=0,
        retries=0,
        transport=transport,
        )
    result = backend.lookup_many(['123456'])['123456']
    assert result.status == 'available'
    assert result.text == 'hello\tworld\nagain'


@pytest.mark.parametrize(
    ('message', 'status'),
    [
        ('This Post was deleted by the Post author.', 'deleted'),
        ('These Posts are protected.', 'protected'),
        ('This Post has been withheld.', 'withheld'),
        ],
    )
def test_syndication_parses_tombstones(tmp_path, message, status):
    def transport(url, headers, timeout):
        return response({
            '__typename': 'TweetTombstone',
            'tombstone': {'text': {'text': message}},
            })

    backend = SyndicationBackend(
        ResultCache(tmp_path),
        workers=1,
        pace=0,
        retries=0,
        transport=transport,
        )
    assert backend.lookup_many(['123456'])['123456'].status == status


def test_syndication_retries_throttling(tmp_path):
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        if len(calls) == 1:
            return response({}, status=429, headers={'Retry-After': '0'})
        return response({'__typename': 'Tweet', 'text': 'recovered'})

    backend = SyndicationBackend(
        ResultCache(tmp_path),
        workers=1,
        pace=0,
        retries=1,
        transport=transport,
        sleep=lambda delay: None,
        )
    result = backend.lookup_many(['123456'])['123456']
    assert result.available
    assert len(calls) == 2


def test_syndication_handles_malformed_json(tmp_path):
    def transport(url, headers, timeout):
        return HttpResponse(200, {}, b'<html>not json</html>')

    backend = SyndicationBackend(
        ResultCache(tmp_path),
        workers=1,
        pace=0,
        retries=0,
        transport=transport,
        )
    assert backend.lookup_many(['123456'])['123456'].status == 'malformed'


def test_syndication_resumes_from_cache(tmp_path):
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return response({'__typename': 'Tweet', 'text': 'cached'})

    cache = ResultCache(tmp_path)
    backend = SyndicationBackend(
        cache,
        workers=1,
        pace=0,
        retries=0,
        transport=transport,
        )
    assert backend.lookup_many(['123456'])['123456'].available
    assert backend.lookup_many(['123456'])['123456'].available
    assert len(calls) == 1


def test_x_api_parses_partial_batch(tmp_path):
    def transport(url, headers, timeout):
        assert headers['Authorization'] == 'Bearer secret'
        return response({
            'data': [{'id': '123456', 'text': 'available'}],
            'errors': [{
                'resource_id': '654321',
                'detail': 'This post was deleted.',
                }],
            })

    backend = XApiBackend(
        ResultCache(tmp_path),
        'secret',
        retries=0,
        transport=transport,
        )
    results = backend.lookup_many(['123456', '654321'])
    assert results['123456'].available
    assert results['654321'].status == 'deleted'


def test_x_api_requires_bearer_token(tmp_path):
    with pytest.raises(HydrationError, match='X_BEARER_TOKEN'):
        XApiBackend(ResultCache(tmp_path), '')


def test_x_api_retries_rate_limit(tmp_path):
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        if len(calls) == 1:
            return response({}, status=429, headers={'Retry-After': '0'})
        return response({'data': [{'id': '123456', 'text': 'available'}]})

    backend = XApiBackend(
        ResultCache(tmp_path),
        'secret',
        retries=1,
        transport=transport,
        sleep=lambda delay: None,
        )
    assert backend.lookup_many(['123456'])['123456'].available
    assert len(calls) == 2


def test_x_api_maps_authentication_failure(tmp_path):
    def transport(url, headers, timeout):
        return response(
            {'title': 'Unauthorized', 'detail': 'Authentication failed',
             'status': 401},
            status=401,
            )

    backend = XApiBackend(
        ResultCache(tmp_path),
        'invalid',
        retries=0,
        transport=transport,
        )
    assert backend.lookup_many(['123456'])['123456'].status == 'auth_error'


class FakeBackend:
    def __init__(self, name, results, calls):
        self.name = name
        self.results = results
        self.calls = calls

    def lookup_many(self, tweet_ids):
        self.calls.append((self.name, list(tweet_ids)))
        return {
            tweet_id: self.results.get(
                tweet_id,
                HydrationResult(
                    tweet_id, backend=self.name, status='missing'
                    ),
                )
            for tweet_id in tweet_ids
            }


def test_explicit_fallback_and_original_id(tmp_path):
    annotation = Annotation(
        tweet_id='111111',
        original_tweet_id='111111',
        replacement_tweet_id='222222',
        sentiment='-1',
        sentiment_integer='-1',
        category='',
        split='train',
        )
    calls = []
    first = FakeBackend('syndication', {}, calls)
    second = FakeBackend('x', {
        '111111': HydrationResult(
            '111111', text='original', backend='x', status='available'
            ),
        }, calls)
    report = hydrate_annotations(
        [annotation],
        tmp_path / 'hydrated.tsv',
        tmp_path / 'report.json',
        [first, second],
        try_original=True,
        )
    assert calls == [
        ('syndication', ['222222']),
        ('x', ['222222']),
        ('syndication', ['111111']),
        ('x', ['111111']),
        ]
    assert report['recovered_by_id_type'] == {'original': 1}


def test_no_implicit_fallback(tmp_path):
    annotation = Annotation(
        tweet_id='111111',
        original_tweet_id='111111',
        replacement_tweet_id='222222',
        sentiment='-1',
        sentiment_integer='-1',
        category='',
        split='train',
        )
    calls = []
    backend = FakeBackend('syndication', {}, calls)
    report = hydrate_annotations(
        [annotation],
        tmp_path / 'hydrated.tsv',
        tmp_path / 'report.json',
        [backend],
        try_original=False,
        )
    assert calls == [('syndication', ['222222'])]
    assert report['unresolved_count'] == 1


def test_hydrated_tsv_quotes_tabs_and_newlines(tmp_path):
    annotation = Annotation(
        tweet_id='111111',
        original_tweet_id='111111',
        replacement_tweet_id='',
        sentiment='0',
        sentiment_integer='0',
        category='Other',
        split='test',
        )
    backend = FakeBackend('syndication', {
        '111111': HydrationResult(
            '111111',
            text='tab\tand\nnewline',
            backend='syndication',
            status='available',
            ),
        }, [])
    output = tmp_path / 'hydrated.tsv'
    hydrate_annotations(
        [annotation],
        output,
        tmp_path / 'report.json',
        [backend],
        )
    with output.open(encoding='utf-8', newline='') as file:
        rows = list(csv.DictReader(file, delimiter='\t'))
    assert rows[0]['text'] == 'tab\tand\nnewline'


def test_malformed_source_id_is_not_sent_to_backend(tmp_path):
    annotation = Annotation(
        tweet_id='5.39436E+17',
        original_tweet_id='5.39436E+17',
        replacement_tweet_id='',
        sentiment='-1',
        sentiment_integer='-1',
        category='sarcasm',
        split='test',
        )
    calls = []
    backend = FakeBackend('syndication', {}, calls)
    report = hydrate_annotations(
        [annotation],
        tmp_path / 'hydrated.tsv',
        tmp_path / 'report.json',
        [backend],
        )
    assert calls == []
    assert report['hydration_statuses'] == {'malformed': 1}
