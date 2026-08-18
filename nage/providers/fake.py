from collections import deque
from typing import Deque, List, Sequence, Union

from ..errors import ProviderError
from .base import BaseProvider, ProviderRequest, ProviderResult


class FakeProvider(BaseProvider):
    """Deterministic provider used by unit and orchestration tests."""

    def __init__(self, responses: Sequence[Union[ProviderResult, Exception]]) -> None:
        self._responses: Deque[Union[ProviderResult, Exception]] = deque(responses)
        self.requests: List[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        if not self._responses:
            raise ProviderError("Fake provider has no queued response.")
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response
