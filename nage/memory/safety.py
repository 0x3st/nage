import re
import unicodedata
from typing import Set

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(
        r"\b(?:password|passwd|api[_-]?key|access[_-]?token|auth[_-]?token|secret)"
        r"\s*[:=]\s*[^\s,;]{4,}",
        re.I,
    ),
    re.compile(
        r"\b(?:password|passwd|api[_ -]?key|access[_ -]?token|secret)"
        r"\s+(?:is|was)\s+[^\s,;]{4,}",
        re.I,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
_HIGHLY_SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:social security|ssn|passport number)\b", re.I),
    re.compile(r"\b(?:credit card|bank account|routing number)\b", re.I),
    re.compile(r"\b(?:medical diagnosis|diagnosed with|health condition)\b", re.I),
    re.compile(r"\b(?:home address|precise location)\b", re.I),
)
_TEMPORARY_PATTERN = re.compile(
    r"\b(?:today|tonight|tomorrow|right now|currently|this time|for this task|"
    r"for now|temporarily|until later|this answer|this response|this output|"
    r"this request)\b",
    re.I,
)
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "for",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "please",
    "prefer",
    "prefers",
    "remember",
    "that",
    "the",
    "this",
    "to",
    "use",
    "user",
    "with",
}


def normalize_memory_content(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("memory content must be text")
    return " ".join(unicodedata.normalize("NFKC", content).split())


def contains_secret(content: str) -> bool:
    normalized = normalize_memory_content(content)
    return any(pattern.search(normalized) for pattern in _SECRET_PATTERNS)


def contains_highly_sensitive_data(content: str) -> bool:
    normalized = normalize_memory_content(content)
    return any(pattern.search(normalized) for pattern in _HIGHLY_SENSITIVE_PATTERNS)


def is_temporary_statement(content: str) -> bool:
    return bool(_TEMPORARY_PATTERN.search(normalize_memory_content(content)))


def significant_tokens(content: str) -> Set[str]:
    normalized = normalize_memory_content(content).casefold()
    return {
        token
        for token in _TOKEN_PATTERN.findall(normalized)
        if len(token) >= 2 and token not in _STOPWORDS
    }
