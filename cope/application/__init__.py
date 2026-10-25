from .contracts import CommandRequest
from .errors import ApplicationError
from .identity import Principal

__all__ = ["ApplicationError", "CommandRequest", "Principal", "execute_command", "list_commands"]


def execute_command(*args, **kwargs):
    from .commands import execute_command as execute

    return execute(*args, **kwargs)


def list_commands(*args, **kwargs):
    from .commands import list_commands as describe

    return describe(*args, **kwargs)
