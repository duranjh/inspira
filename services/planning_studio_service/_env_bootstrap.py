"""Load variables from a repo-root ``.env`` file into ``os.environ``.

Intended for local development and tests. Production deployments inject
secrets via their platform (Vercel / Fly / AWS / GCP secret managers), not
via a checked-in or local .env.

Behavior:

- Idempotent — safe to call multiple times. Existing ``os.environ`` values
  are NOT overridden (so a caller who explicitly set ``OPENAI_API_KEY``
  in their shell still wins over any stale value in ``.env``).
- Walks up from the current working directory looking for the first
  ``.env``. That's the repo root in our layout.
- Silent no-op when ``python-dotenv`` is not installed or no ``.env`` is
  found — tests still run, they just won't see the key if it wasn't
  provided another way.

Call ``ensure_loaded()`` once at the top of a test file or the service
entrypoint — NOT from library modules. Libraries should read ``os.environ``
directly; bootstrap is a caller concern.
"""

from __future__ import annotations

_loaded: bool = False


def ensure_loaded() -> None:
    """Idempotently load the nearest ``.env`` into os.environ, if present."""
    global _loaded
    if _loaded:
        return
    try:
        from dotenv import find_dotenv, load_dotenv  # type: ignore[import-not-found]
    except ImportError:
        # dotenv isn't installed — silently skip. The caller either set env
        # vars explicitly in their shell or will get a clear "env var missing"
        # error later when it matters.
        _loaded = True
        return

    path = find_dotenv(usecwd=True)
    if path:
        # override=False: an explicitly-set shell env var wins over .env.
        load_dotenv(path, override=False)
    _loaded = True
