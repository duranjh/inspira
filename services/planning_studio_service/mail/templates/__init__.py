"""Template registry for Inspira transactional email.

This subpackage plays two roles, and Python's import machinery lets us do
both from one place:

1. It is the **module** callers import as
   ``planning_studio_service.mail.templates`` — exposing :class:`Template`,
   the :data:`registry` dict, and :func:`render`.
2. It is the **resource package** that holds the ``*.html`` and ``*.txt``
   template bodies sitting alongside this file. Bodies are loaded lazily
   via :mod:`importlib.resources` so the package stays portable across
   filesystem layouts (wheels, zip imports, containers).

Rendering uses plain ``str.format`` — no Jinja, no MarkupSafe, no new
dependencies. The trade-offs:

- Any literal ``{`` or ``}`` in template bodies must be doubled (``{{``
  / ``}}``). Relevant for inline CSS (``font-family: {{ ... }}``) but the
  current templates don't use CSS rule blocks, so this is theoretical.
- ``str.format`` has no conditional blocks. Templates that need optional
  copy (account_deleted's data export, budget_warning's Pro upgrade
  link) use a pre-composed placeholder such as ``{export_block}`` or
  ``{upgrade_block}``. The caller composes the snippet (a full sentence
  or the empty string) and passes it in — the template file just drops
  it in. This keeps the conditional in Python where we can test it,
  instead of sprinkled through copy.

Validation: :func:`render` verifies that every placeholder the template
references is present in the caller's ``context`` dict. A missing key
raises ``ValueError`` so the wiring bug shows up in tests rather than in
a half-rendered email that reaches a user.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Mapping

# Matches single-braced ``{name}`` placeholders. Any literal ``{`` must be
# doubled (``{{``) in the template source so the regex (and str.format)
# skip it — same rule str.format itself enforces.
_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")

# Default sender identity. Override via the ``INSPIRA_EMAIL_FROM`` env
# var, which accepts either a bare address or ``"Name <address>"``.
# Per-template ``from_email`` / ``from_name`` on the :class:`Template`
# entry still take precedence over the env-level default.
DEFAULT_FROM_EMAIL = "hello@example.com"
DEFAULT_FROM_NAME = "Inspira"


def _parse_from_header(raw: str) -> tuple[str, str] | None:
    """Parse ``"Name <addr@host>"`` or a bare ``addr@host`` into (name, email).

    Returns ``None`` for an unparseable string so callers can fall back
    to the hard-coded defaults. Whitespace around the pieces is stripped.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    # ``Name <address>`` form.
    if "<" in raw and raw.endswith(">"):
        name, _, rest = raw.partition("<")
        addr = rest[:-1].strip()
        return name.strip() or DEFAULT_FROM_NAME, addr or DEFAULT_FROM_EMAIL
    # Bare address.
    if "@" in raw:
        return DEFAULT_FROM_NAME, raw
    return None


def _env_from_identity() -> tuple[str, str]:
    """Current ``(from_name, from_email)`` after applying env overrides.

    Read on every call so tests that mutate ``os.environ`` between cases
    see the new value without having to reload the module.
    """
    override = _parse_from_header(os.environ.get("INSPIRA_EMAIL_FROM", ""))
    if override is None:
        return DEFAULT_FROM_NAME, DEFAULT_FROM_EMAIL
    return override


@dataclass(frozen=True)
class Template:
    """One transactional email template.

    Attributes:
        template_id: Stable string id callers pass to
            :meth:`EmailSender.send`. Snake_case, e.g. ``"welcome"``.
        subject: Subject line. May contain ``{name}`` placeholders
            resolved at render time.
        from_name: Display name on the ``From`` header.
        from_email: Address on the ``From`` header. Must match an
            address the chosen provider has verified.
        html_path: Filename of the HTML body, resolved under this
            subpackage. Loaded lazily.
        text_path: Filename of the plain-text body, resolved under this
            subpackage.
    """

    template_id: str
    subject: str
    from_name: str
    from_email: str
    html_path: str
    text_path: str


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# Add new templates here. Each entry needs a matching ``<name>.html`` and
# ``<name>.txt`` sitting next to this file. The template_id doubles as
# the key in the dict AND inside the Template dataclass so lookup and
# self-description stay in sync.

registry: dict[str, Template] = {
    "welcome": Template(
        template_id="welcome",
        subject="Welcome to Inspira.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="welcome.html",
        text_path="welcome.txt",
    ),
    "password_reset": Template(
        template_id="password_reset",
        subject="Reset your Inspira password.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="password_reset.html",
        text_path="password_reset.txt",
    ),
    "account_deleted": Template(
        template_id="account_deleted",
        subject="Your Inspira account is deleted.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="account_deleted.html",
        text_path="account_deleted.txt",
    ),
    "budget_warning": Template(
        template_id="budget_warning",
        subject="You've used most of today's planner budget.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="budget_warning.html",
        text_path="budget_warning.txt",
    ),
    "verify_email": Template(
        template_id="verify_email",
        subject="Confirm your Inspira email.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="verify_email.html",
        text_path="verify_email.txt",
    ),
    "password_changed": Template(
        template_id="password_changed",
        subject="Your Inspira password was changed.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="password_changed.html",
        text_path="password_changed.txt",
    ),
    "new_signin": Template(
        template_id="new_signin",
        subject="New sign-in to your Inspira account.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="new_signin.html",
        text_path="new_signin.txt",
    ),
    "trial_ending": Template(
        template_id="trial_ending",
        subject="Your Inspira trial ends in 3 days.",
        from_name=DEFAULT_FROM_NAME,
        from_email=DEFAULT_FROM_EMAIL,
        html_path="trial_ending.html",
        text_path="trial_ending.txt",
    ),
}


# ---------------------------------------------------------------------------
# Loading + rendering
# ---------------------------------------------------------------------------


def _load_template_file(filename: str) -> str:
    """Read a template body from this subpackage.

    importlib.resources handles wheel / zip / filesystem layouts uniformly,
    so the same code path works in dev (source checkout) and in a
    container image that bundles the package as a zip.
    """
    return (
        files("planning_studio_service.mail.templates")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def _required_placeholders(raw: str) -> set[str]:
    """Return every ``{name}`` placeholder referenced in ``raw``.

    Uses the same rule as :meth:`str.format`: single braces are
    placeholders, doubled braces (``{{``, ``}}``) are literals.
    """
    return set(_PLACEHOLDER_RE.findall(raw))


def _render_one(raw: str, context: Mapping[str, object], *, label: str) -> str:
    """Render a single template string with validation.

    Raises:
        ValueError: when ``context`` is missing any placeholder the
            template references. The error includes every missing key
            so the caller can fix them in one pass.
    """
    required = _required_placeholders(raw)
    missing = sorted(required - set(context.keys()))
    if missing:
        raise ValueError(
            f"{label}: missing required context key(s): {', '.join(missing)}",
        )
    try:
        return raw.format(**context)
    except KeyError as exc:
        # Defense in depth — _required_placeholders should have caught
        # this already, but KeyError from ``.format`` is opaque, so
        # surface it as ValueError too.
        raise ValueError(f"{label}: unresolved placeholder {exc}") from exc


def render(template_id: str, context: Mapping[str, object]) -> tuple[str, str, str]:
    """Render ``(subject, html_body, text_body)`` for the given template.

    Args:
        template_id: key into :data:`registry`.
        context: dict of placeholder values. Every ``{name}`` referenced
            in the subject, HTML body, or text body must have a matching
            key. Missing keys raise ``ValueError``.

    Raises:
        KeyError: when ``template_id`` is not in :data:`registry`.
        ValueError: when ``context`` is missing a required placeholder.

    Returns:
        A 3-tuple. The HTML and text bodies are independent renderings;
        callers should send both so the recipient's client picks the one
        it prefers.
    """
    tpl = registry[template_id]  # KeyError surfaces to caller by design.
    html_raw = _load_template_file(tpl.html_path)
    text_raw = _load_template_file(tpl.text_path)
    subject = _render_one(tpl.subject, context, label=f"{template_id}.subject")
    html = _render_one(html_raw, context, label=f"{template_id}.html")
    text = _render_one(text_raw, context, label=f"{template_id}.text")
    return subject, html, text


def resolve_from_identity(template_id: str) -> tuple[str, str]:
    """Return ``(from_name, from_email)`` for the given template.

    Resolution order, highest priority first:

    1. ``INSPIRA_EMAIL_FROM`` env var (applies to every template). Parsed
       as either ``"Name <addr>"`` or a bare ``addr`` — malformed values
       are ignored.
    2. The template's own ``from_name`` / ``from_email`` fields (only
       honored when they differ from the module-level defaults — this
       gives templates a way to pin a specialised sender but doesn't
       block the env-wide override for typical cases).
    3. The module defaults.

    The function is idempotent and safe to call on every send — it reads
    ``os.environ`` each time so tests that swap the env var between
    cases see the new value immediately.
    """
    tpl = registry[template_id]  # KeyError surfaces to caller.
    env_name, env_email = _env_from_identity()
    # If a template pinned a non-default identity, keep it. Otherwise
    # let the env override win.
    if (
        tpl.from_name != DEFAULT_FROM_NAME
        or tpl.from_email != DEFAULT_FROM_EMAIL
    ):
        return tpl.from_name, tpl.from_email
    return env_name, env_email


__all__ = [
    "DEFAULT_FROM_EMAIL",
    "DEFAULT_FROM_NAME",
    "Template",
    "registry",
    "render",
    "resolve_from_identity",
]
