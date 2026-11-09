"""Request rules: conditions a channel's gateway answers itself (unitysvc/unitysvc#2947).

A channel's ``request_rules`` lists rules the gateway checks, in order, after
authentication, channel selection and rate limiting and before anything is
dispatched. The first rule whose condition holds answers the request with its
static response; when none holds the request continues unchanged::

    "request_rules": [
      {"name": "github-ping", "when": "header['x-github-event'] == 'ping'",
       "respond": {"status": 200}},
      {"when": "body.user_id == '${ customer_secrets.DISCORD_WEBHOOK_ID ?? }'",
       "respond": {"status": 204}}
    ]

A condition is one string comparison between a request value and a literal::

    when    := ref "==" literal | ref "in" literal | literal "in" ref
    ref     := root ( "." ident | "[" string "]" )*
    root    := "body" | "header" | "query" | "method"
    literal := a quoted string ('...' or "...", escapes \\\\ \\' \\")

``ref in 'lit'`` holds when the request value is a non-empty substring of the
literal, ``'lit' in ref`` when the literal is a non-empty substring of the
request value. A request value that is not a string never matches.

A literal whose whole content is one reference is a **slot**:
``'${ customer_secrets.X }'``, ``'${ customer_secrets.X ?? default }'``,
``'${ secrets.X }'``, ``'{{ params.X }}'`` or ``'{{ enrollment.code }}'``. The
gateway fills a slot with the resolved value when it evaluates the condition;
the value is never spliced into the condition's text. A slot that is unset or
resolves to an empty string never matches. A secret slot may only be compared
with ``==``: in ``ref in '${ secret }'`` the caller controls ``ref`` and would
learn the secret one character at a time.

The gateway evaluates conditions (apisix-gateways ``lib/rules.lua``); this
module validates them at upload. :func:`evaluate` exists to run the shared
conformance cases (``rules_conformance.json``) that keep the two in step.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

ROOTS = ("body", "header", "query", "method")
MAX_RULES = 32
MAX_CONDITION_LENGTH = 1024
MAX_LITERAL_LENGTH = 1024
MAX_REF_DEPTH = 16

_SECRET_NAMESPACES = ("secrets", "customer_secrets")
_SECRET_SLOT_RE = re.compile(
    r"^\$\{\s*(secrets|customer_secrets)\.([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?\?\s*(.*?))?\s*\}$", re.DOTALL
)
_PARAM_SLOT_RE = re.compile(r"^\{\{\s*(params\.[A-Za-z_][A-Za-z0-9_]*|enrollment\.code)\s*\}\}$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOKEN_RE = re.compile(r"\s*(?:(==)|(\[)|(\])|(\.)|('|\")|([A-Za-z_][A-Za-z0-9_]*))")


class RuleSyntaxError(ValueError):
    """A request-rule condition does not parse, or breaks a rule of the language."""


@dataclass(frozen=True)
class Ref:
    """A request value: a root and the keys looked up under it."""

    root: str
    keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Slot:
    """A literal that is one reference, filled when the gateway evaluates the condition."""

    namespace: str  # secrets, customer_secrets, params or enrollment
    name: str
    default: str | None = None  # the ``?? default`` of a secret reference

    @property
    def key(self) -> str:
        """The reference without braces, e.g. ``customer_secrets.X``."""
        return f"{self.namespace}.{self.name}"

    @property
    def is_secret(self) -> bool:
        return self.namespace in _SECRET_NAMESPACES


@dataclass(frozen=True)
class Condition:
    """A parsed condition: ``ref == literal``, ``ref in literal`` or ``literal in ref``."""

    op: Literal["==", "in"]
    ref: Ref
    literal: str | Slot
    literal_first: bool = False  # True for ``literal in ref``


def _literal(content: str) -> str | Slot:
    """A literal's value, or the slot it names. A reference mixed with other text is an error."""
    if match := _SECRET_SLOT_RE.match(content):
        return Slot(match.group(1), match.group(2), match.group(3))
    if match := _PARAM_SLOT_RE.match(content):
        namespace, name = match.group(1).split(".", 1)
        return Slot(namespace, name)
    if "${" in content or "{{" in content:
        raise RuleSyntaxError(
            f"'{content}' is not a valid reference literal: a reference must be the whole literal, one of "
            "'${ customer_secrets.NAME }', '${ customer_secrets.NAME ?? default }', '${ secrets.NAME }', "
            "'{{ params.NAME }}' or '{{ enrollment.code }}'"
        )
    if len(content.encode()) > MAX_LITERAL_LENGTH:
        raise RuleSyntaxError(f"a literal is longer than {MAX_LITERAL_LENGTH} bytes")
    return content


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def fail(self, message: str) -> RuleSyntaxError:
        return RuleSyntaxError(f"{message} at position {self.pos} in {self.text!r}")

    def peek(self) -> tuple[str, str] | None:
        """The next token as (kind, text) without consuming it; None at the end."""
        match = _TOKEN_RE.match(self.text, self.pos)
        if not match:
            if self.text[self.pos :].strip():
                raise self.fail("unexpected character")
            return None
        kinds = ("op", "lbracket", "rbracket", "dot", "quote", "ident")
        index = next(i for i, group in enumerate(match.groups()) if group is not None)
        return kinds[index], match.group(index + 1)

    def take(self) -> tuple[str, str] | None:
        token = self.peek()
        if token is not None:
            match = _TOKEN_RE.match(self.text, self.pos)
            assert match is not None
            self.pos = match.end()
        return token

    def string(self) -> str:
        """Read a quoted string; the opening quote is the next token."""
        token = self.take()
        assert token is not None and token[0] == "quote"
        quote, out = token[1], list[str]()
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return "".join(out)
            if char == "\\":
                if self.pos >= len(self.text) or self.text[self.pos] not in "\\'\"":
                    raise self.fail("unsupported escape (only \\\\, \\' and \\\")")
                char = self.text[self.pos]
                self.pos += 1
            out.append(char)
        raise self.fail("unterminated string")

    def ref(self) -> Ref:
        token = self.take()
        assert token is not None and token[0] == "ident"
        root, keys = token[1], []
        if root not in ROOTS:
            raise self.fail(f"unknown root '{root}' (expected one of {', '.join(ROOTS)})")
        while (token := self.peek()) is not None and token[0] in ("dot", "lbracket"):
            self.take()
            if token[0] == "dot":
                ident = self.take()
                if ident is None or ident[0] != "ident":
                    raise self.fail("expected a name after '.'")
                keys.append(ident[1])
            else:
                if self.text[self.pos :].lstrip()[:1] not in ("'", '"'):
                    raise self.fail("expected a quoted key inside [ ]")
                keys.append(self.string())
                if (self.take() or ("", ""))[0] != "rbracket":
                    raise self.fail("expected ']'")
        if len(keys) > MAX_REF_DEPTH:
            raise self.fail(f"a reference is deeper than {MAX_REF_DEPTH} keys")
        if root == "method" and keys:
            raise self.fail("'method' takes no keys")
        if root in ("header", "query") and len(keys) != 1:
            raise self.fail(f"'{root}' takes exactly one key, e.g. {root}['name']")
        if root == "header":
            keys = [keys[0].lower()]
        return Ref(root, tuple(keys))

    def operand(self) -> Ref | str | Slot:
        token = self.peek()
        if token is None:
            raise self.fail("expected a request value or a quoted literal")
        if token[0] == "quote":
            return _literal(self.string())
        if token[0] == "ident":
            if token[1] == "in":
                raise self.fail("expected a request value or a quoted literal")
            return self.ref()
        raise self.fail("expected a request value or a quoted literal")

    def operator(self) -> Literal["==", "in"]:
        token = self.take()
        if token == ("op", "=="):
            return "=="
        if token == ("ident", "in"):
            return "in"
        raise self.fail("expected '==' or 'in'")


def parse_condition(text: str) -> Condition:
    """Parse and check one condition.

    Raises:
        RuleSyntaxError: anything outside the language, a secret slot under
            ``in``, or a condition longer than :data:`MAX_CONDITION_LENGTH` bytes.
    """
    if not isinstance(text, str) or not text.strip():
        raise RuleSyntaxError("a condition must be a non-empty string")
    if len(text.encode()) > MAX_CONDITION_LENGTH:
        raise RuleSyntaxError(f"a condition is longer than {MAX_CONDITION_LENGTH} bytes (UTF-8)")
    parser = _Parser(text)
    left = parser.operand()
    op = parser.operator()
    right = parser.operand()
    if parser.peek() is not None:
        raise parser.fail("unexpected text after the condition")

    if isinstance(left, Ref) and not isinstance(right, Ref):
        condition = Condition(op, left, right)
    elif not isinstance(left, Ref) and isinstance(right, Ref):
        if op == "==":
            raise RuleSyntaxError(f"write the request value first: '{text}' compares a literal == a request value")
        condition = Condition(op, right, left, literal_first=True)
    else:
        raise RuleSyntaxError(f"'{text}' must compare one request value with one quoted literal")

    if isinstance(condition.literal, Slot) and condition.literal.is_secret and op != "==":
        raise RuleSyntaxError(
            f"'{text}': a secret may only be compared with '==' (a substring test would reveal it "
            "one character at a time)"
        )
    return condition


# ---------------------------------------------------------------------------
# Evaluation, for the conformance cases. The gateway is the real evaluator.
# ---------------------------------------------------------------------------

_MISSING = object()


def _request_value(ref: Ref, request: Mapping[str, Any]) -> Any:
    if ref.root == "method":
        return request.get("method", _MISSING)
    if ref.root in ("header", "query"):
        table = request.get("headers" if ref.root == "header" else "query") or {}
        if ref.root == "header":
            table = {str(name).lower(): value for name, value in table.items()}
        return table.get(ref.keys[0], _MISSING)
    value = request.get("body", _MISSING)
    for key in ref.keys:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _literal_value(literal: str | Slot, slots: Mapping[str, str]) -> Any:
    if isinstance(literal, str):
        return literal
    value = slots.get(literal.key)
    if value is None:
        value = literal.default
    return value if isinstance(value, str) and value != "" else _MISSING


def evaluate(condition: Condition, request: Mapping[str, Any], slots: Mapping[str, str] | None = None) -> bool:
    """Whether ``condition`` holds for ``request``.

    ``request`` has ``body`` (the decoded JSON body; absent when the body is
    not JSON), ``headers``, ``query`` and ``method``. ``slots`` maps a slot's
    :attr:`Slot.key` to its resolved value; an absent key uses the slot's
    ``?? default``.
    """
    value = _request_value(condition.ref, request)
    literal = _literal_value(condition.literal, slots or {})
    if not isinstance(value, str) or literal is _MISSING:
        return False
    if condition.op == "==":
        return hmac.compare_digest(value.encode(), literal.encode())
    if condition.literal_first:
        return literal != "" and literal in value
    return value != "" and value in literal
