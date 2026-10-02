"""A scripted provider for tests: returns prepared responses and remembers what it was asked."""

from mtg_deck_advisor.llm.types import ProviderRequest, ProviderResponse


class FakeProvider:
    def __init__(self, *replies: ProviderResponse | Exception, bills: bool = True) -> None:
        self._replies = list(replies)
        self._bills = bills
        self.calls: list[ProviderRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def bills(self) -> bool:
        return self._bills

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        self.calls.append(request)
        if not self._replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
