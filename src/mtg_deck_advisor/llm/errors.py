"""Errors from model calls. Providers raise the specific ones; ModelClient raises the rest."""


class ModelError(Exception):
    """A model call did not produce a usable result."""


class ModelCallError(ModelError):
    """The provider failed: network, timeout, or an API error."""


class SpendLimitError(ModelCallError):
    """The provider refused for money reasons: a spend limit or an empty credit balance.

    Retrying won't help until the limit resets or is raised.
    """


class ProviderAuthError(ModelCallError):
    """The provider rejected the credentials."""


class RefusalError(ModelError):
    """The model declined to answer."""


class InvalidOutputError(ModelError):
    """The output did not match the required schema, even after a retry."""


class BudgetExceededError(ModelError):
    """The call could push this run's spending over its cost cap, so it was not made."""
