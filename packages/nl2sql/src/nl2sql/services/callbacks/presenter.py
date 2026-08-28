from typing import Protocol


class PresenterProtocol(Protocol):
    def update_interactive_status(self, message: str) -> None:
        ...

    def print_success(self, message: str) -> None:
        ...

    def print_error(self, message: str) -> None:
        ...
