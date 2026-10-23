# Post Queue: a free, personal social publishing queue

A small, original tool that does the useful core of a social media scheduler for one person, at about $0 a month:
you add posts to a calendar (one workspace per brand), they wait in a queue stored as files in this
repository, and a GitHub Actions workflow publishes them through each platform's official API and records what happened.

It is a standalone repository, independent of any other project.

```
dashboard/index.html     Content Desk, the web page (static; talks to GitHub's API with your token)
queue/posts/<id>.json    the content you wrote           (written by you / the dashboard)
queue/state/<id>.json    what happened to each account   (written only by the publisher)
queue/commands/*.json    requests: cancel, retry, publish now, mark published
queue/log.jsonl         append-only record of every attempt
config/*.json            accounts (secret NAMES only), brand voice, settings
socialpub/               the publisher (Python)
.github/workflows/social-publish.yml   hourly run + manual run
tests/                   120 automated tests (mocked platforms, fake GitHub, real browser)
docs/                    setup, platforms, security, troubleshooting, status, cost
```

**Read `docs/STATUS.md` first**: it says exactly what works, what is partial and what does not exist.
Setup is in `docs/SETUP.md` (about 30 minutes the first time; Bluesky is the quickest platform to start with).

Common commands (run from this folder; the workflow runs the same code):

```
python -m socialpub validate [--strict]     check config and posts
python -m socialpub status                   one line per post
python -m socialpub run --dry-run            show what would be published, send nothing
python -m socialpub run                      publish what is due (needs SOCIAL_SECRETS)
python -m socialpub check-setup              log in / read identity on each account; never posts
python -m pytest -q                          run the tests
```
