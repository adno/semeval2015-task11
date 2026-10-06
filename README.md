# SemEval-2015 Task 11 data reconstruction

This project reconstructs the released annotations for **SemEval-2015 Task
11: Sentiment Analysis of Figurative Language in Twitter** and attempts to
rehydrate the corresponding tweet text. If a post is unavailable, its
annotation is retained with an explicit hydration status rather than dropped.

The annotation sources are the organizers' [Data and Tools page][official]
and the authors' [test-set supplement on ResearchGate][test-data]. The task
itself is described in the [SemEval paper][paper].

## Quick start

Install the command in a Python 3.10+ environment:

```console
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Place the ResearchGate `TestGoldIdwithCategory.tsv` file in the project root,
then run the recommended free syndication workflow:

```console
semeval-task11 all \
  --test-file TestGoldIdwithCategory.tsv \
  --backend syndication
```

If ResearchGate permits automatic download, `--test-file` can be omitted.
When the command finishes, the primary result is:

```text
data/processed/hydrated.tsv
```

This TSV contains every annotation plus the recovered text, the ID used for
hydration, the backend, and the availability status. A summary is written to
`data/processed/reconstruction_report.json`. Both files are local artifacts
ignored by Git.

## Requirements

- Python 3.10 or newer
- Network access to the source sites and the selected hydration service
- An X API bearer token (only when using the `x` backend)

For development and tests, install `python -m pip install -e '.[dev]'`.

## Workflow details

The `all` command performs four operations: source download, normalization,
validation, and hydration. Syndication is the recommended default because it
is free to call and requires no developer account or credentials. In our
October 2026 reconstruction, it recovered every organizer-provided training
replacement ID and every exact test ID.

Here, **normalization** is project-specific terminology for converting the
released training TSV files, trial spreadsheet, test TSV, and replacement-ID
mapping into one consistent tabular schema. It preserves the published gold
scores and IDs while adding explicit split, category, original-ID, and
replacement-ID columns. It does **not** normalize or preprocess tweet text,
change sentiment labels, tokenize content, or remove records. The original
task paper refers to these data as the training, trial, and test datasets and
their gold-standard annotations; it does not call this conversion
"normalization."

The commands can also be run separately:

```console
semeval-task11 download
semeval-task11 validate data/processed/annotations.tsv
semeval-task11 rehydrate --backend syndication
semeval-task11 validate data/processed/hydrated.tsv
```

The ResearchGate download is sometimes replaced by an anti-bot or login page.
The command detects and rejects that HTML instead of silently treating it as
data. If this occurs, download `TestGoldIdwithCategory.tsv` from the
[supplementary resource][test-data] in a browser and run:

```console
semeval-task11 all \
  --test-file /path/to/TestGoldIdwithCategory.tsv \
  --backend syndication
```

To reconstruct the official training and trial releases while obtaining the
test file separately, use:

```console
semeval-task11 all --skip-test --backend syndication
```

### Hydration backend choices

`--backend` accepts one backend or an explicit fallback order:

- `syndication` (**recommended and default**) uses the public endpoint at
  `cdn.syndication.twimg.com`. It is free to call, needs no credentials, and
  makes one request per ID. Because the endpoint is undocumented, it may
  change or disappear.
- `x` uses the official X API v2 batch lookup. X API access is paid and billed
  per use, with post reads charged per resource fetched. Pricing can change,
  so consult the [official X API pricing page][x-pricing] before a large
  reconstruction. Export the bearer token only in the current environment:

  ```console
  export X_BEARER_TOKEN='your-token'
  semeval-task11 rehydrate --backend x
  ```

- `syndication,x` or `x,syndication` uses the second backend only for IDs not
  recovered by the first. Any sequence containing `x` can incur X API charges
  for the IDs sent to that backend.

There is **no automatic X fallback** when `--backend syndication` is selected.
The order recorded in the reconstruction report is therefore reproducible.

Syndication defaults to four workers with at least 0.1 seconds between request
starts. Use `--workers`, `--pace`, `--retries`, `--backoff`, and
`--hydration-timeout` to adjust this conservatively. Successful and failed
lookups are cached under `data/cache/`, so an interrupted command can be run
again. Use `--refresh-cache` to retry every lookup.

## The replacement ("non-perishable") IDs

The original training release contains 8,000 IDs. In October 2014 the
organizers published a mapping from each original ID to a copy posted by a
dedicated account because roughly 15% of the originals had already become
unavailable. The unified project dataset preserves both identifiers:

- `original_tweet_id` is the ID in the annotation release;
- `replacement_tweet_id` is the organizer-provided copy ID;
- hydration tries the replacement ID first for training rows.

The description "non-perishable" reflects the organizers' intent at release;
it is not a present-day availability guarantee. By default an unavailable
replacement stays unavailable. Pass `--try-original` to make an additional
attempt with the original training ID after all selected backends fail for the
replacement. Trial and test rows use their released IDs because no separate
mapping is published for them.

## Expected inputs and outputs

The source releases should yield the following row counts:

| Split | Rows | Source |
|---|---:|---|
| Train | 8,000 | Official integer TSV, real-valued TSV, and ID mapping |
| Trial | 1,025 | Official legacy Excel workbook |
| Test | 4,000 | ResearchGate author supplement |

The validator checks these counts, score ranges, agreement between the three
training sources, and ID uniqueness wherever exact IDs are available.

The ResearchGate supplement contains 21 IDs stored in lossy scientific
notation. Their exact integer IDs cannot be recovered from the file. These
rows are preserved, are not sent to either hydration service, and receive the
`malformed` status rather than being silently discarded or assigned invented
IDs.

### Rehydration results as of October 6, 2026

The completed syndication run retained all 13,025 annotations and recovered
12,418 unique posts, for an overall recovery rate of **95.34%**.

| Split | Total | Available | Deleted | Missing | Malformed | Recovery |
|---|---:|---:|---:|---:|---:|---:|
| Train | 8,000 | 8,000 | 0 | 0 | 0 | 100% |
| Trial | 1,025 | 439 | 412 | 174 | 0 | 42.83% |
| Test | 4,000 | 3,979 | 0 | 0 | 21 | 99.48% |
| **Overall** | **13,025** | **12,418** | **412** | **174** | **21** | **95.34%** |

All organizer-provided training replacement IDs and all 3,979 exact test IDs
were recovered. The test results by figurative-language category were:

| Test category | Total | Available | Malformed | Recovery |
|---|---:|---:|---:|---:|
| Sarcasm | 1,200 | 1,192 | 8 | 99.33% |
| Other | 1,200 | 1,198 | 2 | 99.83% |
| Irony | 800 | 794 | 6 | 99.25% |
| Metaphor | 800 | 795 | 5 | 99.38% |

Among the successfully rehydrated records, sentiment counts were:

| Split | Negative | Neutral | Positive | Available total |
|---|---:|---:|---:|---:|
| Train | 7,344 | 12 | 644 | 8,000 |
| Trial | 360 | 3 | 76 | 439 |
| Test | 3,044 | 297 | 638 | 3,979 |
| **Overall** | **10,748** | **312** | **1,358** | **12,418** |

Availability is time-dependent, so future runs may produce different totals.
The machine-readable results for a run are stored in
`data/processed/reconstruction_report.json`.

Generated files are deliberately ignored by Git:

```text
data/
  raw/                         downloaded source files and SHA-256 manifest
  cache/
    syndication/               resumable syndication results by ID
    x/                         resumable X API results by ID
  processed/annotations.tsv   unified annotations
  processed/hydrated.tsv      annotations plus recovered text and status
  processed/reconstruction_report.json
```

`annotations.tsv` has these stable columns:

| Column | Meaning |
|---|---|
| `tweet_id` | ID in the annotation source |
| `original_tweet_id` | Original/released ID retained for provenance |
| `replacement_tweet_id` | Organizer replacement ID for training rows |
| `sentiment` | Canonical real-valued gold score |
| `sentiment_integer` | Gold score rounded to the 11-point integer scale |
| `category` | Test category when supplied; otherwise empty |
| `split` | `train`, `trial`, or `test` |

`hydrated.tsv` adds `hydrated_tweet_id`, `text`, `hydration_backend`, and
`hydration_status`. TSV fields are quoted when necessary, so tabs and newlines
inside post text remain valid data. Status values are:

- `available`: text was recovered.
- `deleted`, `protected`, or `withheld`: the service gave that reason.
- `missing`: the service did not return the requested ID.
- `malformed`: a source ID is not exact or a response cannot be interpreted.
- `auth_error`: credentials or authorization were rejected.
- `transient_error`: a network, rate-limit, or server error exhausted the
  configured retries.

The JSON report summarizes source counts, selected backend order, status totals,
recovery via replacement versus original IDs, and every unresolved annotation
ID. It contains no credentials.

## Reproducibility and responsible use

The repository contains scripts, not tweet text. Hydrated text, source files,
caches, and `.env` files are ignored to reduce the risk of accidental
redistribution or credential disclosure. Do not commit or redistribute
hydrated text without confirming that doing so is permitted. Users are
responsible for complying with X's current terms, the source authors' terms,
and applicable privacy and research-ethics requirements.

Complete reconstruction means preserving each released annotation and its
hydration outcome. Deleted, protected, withheld, or otherwise inaccessible
historical posts cannot be recreated by these scripts.

## Development checks

```console
pytest
flake8 src tests
```

[official]: https://alt.qcri.org/semeval2015/task11/index.php?id=data-and-tools
[test-data]: https://www.researchgate.net/publication/275959495_SemEval-2015_Task_11_Sentiment_Analysis_of_Figurative_Language_in_Twitter
[paper]: https://aclanthology.org/S15-2080/
[x-pricing]: https://developer.x.com/
