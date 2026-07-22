"""Minimal value-level encryption support for Git filters."""

import base64
import fnmatch
import json
import os
import re
import subprocess
from typing import Any, Dict, Optional

PREFIX = "ENCv1:"
WRAPPED_KEY = ".git-filters/key.gpg"
CACHED_KEY = ".git/line-crypt.key"


def _git(root: Optional[str], *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          check=True).stdout


def repo_root() -> str:
    return _git(None, "rev-parse", "--show-toplevel").decode().strip()


def _rules(root: str) -> Dict[str, Any]:
    path = os.path.join(root, "rules.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as file:
        value = json.load(file)
    return value if isinstance(value, dict) else {}


def _key(root: str) -> bytes:
    with open(os.path.join(root, CACHED_KEY), "rb") as file:
        return file.read()


def _crypt(key: bytes, value: bytes, decrypt: bool = False) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESSIV

    siv = AESSIV(key)
    return siv.decrypt(value, []) if decrypt else siv.encrypt(value, [])


def _replace_expression(filename: str, key: str) -> str:
    escaped = re.escape(key)
    if filename.lower().endswith(".json"):
        return '("' + escaped + r'"\s*:\s*")([^"\r\n]*)(")'
    return rf'(^\s*{escaped}\s*:\s*)([^\r\n]*?)(\s*(?:#.*)?$)'


def transform(root: str, filename: str, data: bytes, decrypt: bool = False) -> bytes:
    matched = [key for glob, rule in _rules(root).items()
               if fnmatch.fnmatchcase(filename.replace(os.sep, "/"), glob)
               for key in (rule.get("keys", []) if isinstance(rule, dict) else [])]
    if not matched:
        return data
    key = _key(root)
    text = data.decode("utf-8")
    for name in matched:
        expression = _replace_expression(filename, name)

        def replace(match: re.Match) -> str:
            value = match.group(2)
            if decrypt:
                if not value.startswith(PREFIX):
                    return match.group(0)
                plain = _crypt(key, base64.urlsafe_b64decode(value[6:]), True)
                return match.group(1) + plain.decode("utf-8") + match.group(3)
            if value.startswith(PREFIX):
                return match.group(0)
            encrypted = base64.urlsafe_b64encode(_crypt(key, value.encode())).decode()
            return match.group(1) + PREFIX + encrypted + match.group(3)

        text = re.sub(expression, replace, text, flags=re.MULTILINE)
    return text.encode("utf-8")


def filter_command(kind: str, filename: str) -> None:
    root = repo_root()
    data = os.fdopen(0, "rb", closefd=False).read()
    output = transform(root, filename, data, kind == "smudge")
    os.fdopen(1, "wb", closefd=False).write(output)


def init(root: str, recipient: str) -> None:
    key = os.urandom(64)
    wrapped = subprocess.run(
        ["gpg", "--batch", "--yes", "--encrypt", "--recipient", recipient],
        cwd=root, input=key, capture_output=True, check=True,
    ).stdout
    os.makedirs(os.path.join(root, ".git-filters"), exist_ok=True)
    with open(os.path.join(root, WRAPPED_KEY), "wb") as file:
        file.write(wrapped)
    with open(os.path.join(root, CACHED_KEY), "wb") as file:
        file.write(key)
    rules_path = os.path.join(root, "rules.json")
    if not os.path.exists(rules_path):
        with open(rules_path, "w", encoding="utf-8") as file:
            json.dump({}, file, indent=2)
            file.write("\n")
    _git(root, "config", "filter.dfm-encrypt.clean", "dfm encrypt filter clean %f")
    _git(root, "config", "filter.dfm-encrypt.smudge", "dfm encrypt filter smudge %f")
    _git(root, "config", "filter.dfm-encrypt.required", "true")
    attrs = os.path.join(root, ".gitattributes")
    lines = open(attrs, encoding="utf-8").read().splitlines() if os.path.exists(attrs) else []
    for glob in _rules(root):
        rule = f"{glob} filter=dfm-encrypt"
        if rule not in lines:
            lines.append(rule)
    with open(attrs, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines) + "\n")
    subprocess.run(["git", "add", WRAPPED_KEY, "rules.json", ".gitattributes"],
                   cwd=root, check=True)
