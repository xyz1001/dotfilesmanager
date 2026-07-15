"""Shared structural types for the configuration and filesystem layers."""

from typing import Any, Callable, Dict, List, Literal, TypedDict


class SystemConfig(TypedDict):
    path: str


RawConfig = Dict[str, Any]


class Config(TypedDict):
    """The shape consumed after ``validate_config`` has succeeded."""

    dotfiles: Dict[str, Dict[str, SystemConfig]]


class Target(TypedDict):
    path: str


Targets = Dict[str, str]
ConfirmCallback = Callable[[str], bool]
LinkState = Literal["missing", "dangling", "conflict", "correct", "sync"]
InstallApprovalMap = Dict[str, LinkState]


class OperationMessages(TypedDict):
    config: Config
    messages: List[str]
