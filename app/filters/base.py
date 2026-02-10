"""Typed base filter for fastapi-filter integration.

fastapi-filter's ``.filter()`` / ``.sort()`` accept ``Union[Query, Select]``
and omit a return-type annotation, so type checkers infer ``Query | Select``.
Since we always use the modern ``select()`` API, the runtime return is always
``Select``.  This subclass adds the missing annotations in one place so
repositories and pagination calls stay clean.
"""

from fastapi_filter.contrib.sqlalchemy import Filter as _UntypedFilter
from sqlalchemy import Select


class Filter(_UntypedFilter):
    """Typed shim over fastapi-filter's ``Filter``."""

    def filter(self, query: Select) -> Select:  # type: ignore[override]
        return super().filter(query)  # type: ignore[return-value]

    def sort(self, query: Select) -> Select:  # type: ignore[override]
        return super().sort(query)  # type: ignore[return-value]
