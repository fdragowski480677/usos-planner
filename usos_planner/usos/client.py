"""Minimal USOS API client (OAuth 1.0a, out-of-band PIN flow).

Every USOS installation has its own base URL and consumer keys; register yours at
`<base_url>developers/`.  Tokens are kept in `<data_dir>/usos_token.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests
from requests_oauthlib import OAuth1Session

from ..config import UsosConfig


class UsosError(RuntimeError):
    def __init__(self, status: int, message: str, payload: dict | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload or {}

    @property
    def user_message(self) -> str:
        um = self.payload.get("user_messages") or {}
        if isinstance(um, dict):
            fields = um.get("fields") or {}
            generic = um.get("generic_message")
            if fields:
                return "; ".join(f"{k}: {_lang(v)}" for k, v in fields.items())
            if generic:
                return _lang(generic)
        return self.message


def _lang(value) -> str:
    if isinstance(value, dict):
        return value.get("pl") or value.get("en") or json.dumps(value, ensure_ascii=False)
    return str(value)


class UsosClient:
    def __init__(self, cfg: UsosConfig, token_path: Path):
        if not cfg.consumer_key or not cfg.consumer_secret:
            raise UsosError(0, "consumer key/secret missing in config.yaml (usos section)")
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/") + "/"
        self.token_path = token_path
        self.token: dict[str, str] | None = None
        if token_path.exists():
            self.token = json.loads(token_path.read_text())

    # ----- OAuth ----------------------------------------------------------
    def begin_login(self) -> tuple[str, dict]:
        """Returns (authorize_url, request_token). Show the URL, ask the user for the PIN."""
        session = OAuth1Session(self.cfg.consumer_key, self.cfg.consumer_secret, callback_uri="oob")
        req = session.fetch_request_token(
            self.base + "services/oauth/request_token",
            params={"scopes": "|".join(self.cfg.scopes)},
        )
        url = session.authorization_url(self.base + "services/oauth/authorize")
        return url, dict(req)

    def finish_login(self, request_token: dict, pin: str) -> None:
        session = OAuth1Session(
            self.cfg.consumer_key,
            self.cfg.consumer_secret,
            resource_owner_key=request_token["oauth_token"],
            resource_owner_secret=request_token["oauth_token_secret"],
            verifier=pin.strip(),
        )
        access = session.fetch_access_token(self.base + "services/oauth/access_token")
        self.token = {"oauth_token": access["oauth_token"], "oauth_token_secret": access["oauth_token_secret"]}
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(self.token))
        self.token_path.chmod(0o600)

    @property
    def logged_in(self) -> bool:
        return self.token is not None

    def _session(self) -> OAuth1Session:
        if self.token:
            return OAuth1Session(
                self.cfg.consumer_key,
                self.cfg.consumer_secret,
                resource_owner_key=self.token["oauth_token"],
                resource_owner_secret=self.token["oauth_token_secret"],
            )
        return OAuth1Session(self.cfg.consumer_key, self.cfg.consumer_secret)

    # ----- generic call ---------------------------------------------------
    def call(self, method: str, **params: Any) -> Any:
        """POST `services/<method>` with the given parameters; returns decoded JSON."""
        clean = {k: _param(v) for k, v in params.items() if v is not None}
        url = self.base + "services/" + method.strip("/")
        try:
            resp = self._session().post(url, data=clean, timeout=30)
        except requests.RequestException as e:
            raise UsosError(0, f"network error: {e}") from e
        if resp.status_code != 200:
            try:
                payload = resp.json()
            except ValueError:
                payload = {"message": resp.text[:500]}
            raise UsosError(resp.status_code, str(payload.get("message", resp.reason)), payload)
        if not resp.content:
            return {}
        return resp.json()

    # ----- convenience ----------------------------------------------------
    def whoami(self) -> dict:
        return self.call("users/user", fields="id|first_name|last_name|student_number|email|student_programmes")

    def api_installation(self) -> dict:
        return self.call("apisrv/installation", fields="base_url|version|institution_name")


def _param(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple, set)):
        return "|".join(str(x) for x in v)
    return str(v)
