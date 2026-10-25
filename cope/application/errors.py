from __future__ import annotations


class ApplicationError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}
