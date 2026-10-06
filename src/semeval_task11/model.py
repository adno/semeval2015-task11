"""Shared data models and tabular schemas."""

from dataclasses import asdict, dataclass


ANNOTATION_FIELDS = [
    'tweet_id',
    'original_tweet_id',
    'replacement_tweet_id',
    'sentiment',
    'sentiment_integer',
    'category',
    'split',
    ]

HYDRATION_FIELDS = ANNOTATION_FIELDS + [
    'hydrated_tweet_id',
    'text',
    'hydration_backend',
    'hydration_status',
    ]

AVAILABLE = 'available'
TERMINAL_STATUSES = {
    AVAILABLE,
    'deleted',
    'protected',
    'withheld',
    'missing',
    'malformed',
    'auth_error',
    'transient_error',
    }


@dataclass(frozen=True)
class Annotation:
    """One normalized task annotation."""

    tweet_id: str
    original_tweet_id: str
    replacement_tweet_id: str
    sentiment: str
    sentiment_integer: str
    category: str
    split: str

    def as_row(self):
        """Return a dictionary suitable for a DictWriter."""
        return asdict(self)


@dataclass(frozen=True)
class HydrationResult:
    """A backend lookup result for one tweet ID."""

    tweet_id: str
    text: str = ''
    backend: str = ''
    status: str = 'missing'
    detail: str = ''

    @property
    def available(self):
        """Whether this result contains usable tweet text."""
        return self.status == AVAILABLE
