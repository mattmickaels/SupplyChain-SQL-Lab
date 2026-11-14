from dataclasses import dataclass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    total_rows: int
    truncated: bool
    elapsed_ms: int


class QueryError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
