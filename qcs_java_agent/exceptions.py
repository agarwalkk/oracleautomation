"""Typed exceptions for the local QCS Java Forms agent client."""
from __future__ import annotations


class JavaAgentError(RuntimeError):
    """Base class for Java agent failures."""


class JavaNotFoundError(JavaAgentError):
    """Java or JDK tooling could not be found."""


class ProcessNotFoundError(JavaAgentError):
    """The Oracle Forms JVM process could not be found."""


class AttachError(JavaAgentError):
    """The Java Attach API command failed."""


class AgentOutputError(JavaAgentError):
    """The Java agent did not produce a valid output file."""


class CommandError(JavaAgentError):
    """The Java agent returned a JSON error envelope."""

    def __init__(self, message: str, response: dict | None = None):
        super().__init__(message)
        self.response = response or {}
