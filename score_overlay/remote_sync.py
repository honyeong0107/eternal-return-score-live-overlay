from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .operator_auth import generate_identity, generate_view_token, hash_token, sign_headers
from .state import ScoreState
from .tournaments import TournamentStore


HEARTBEAT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 3.0
RETRY_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)


class RemoteSyncError(RuntimeError):
    pass


def _iso_time(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


class RemoteStateSync:
    def __init__(
        self,
        state: ScoreState,
        store: TournamentStore,
        *,
        open_url=urlopen,
        monotonic=time.monotonic,
        wall_time=time.time,
    ):
        self.state = state
        self.store = store
        self._open_url = open_url
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._condition = threading.Condition()
        self._stop = False
        self._pending = True
        self._next_attempt = 0.0
        self._retry_index = 0
        self._last_success: float | None = None
        self._last_attempt: float | None = None
        self._last_error = ""
        self._thread = threading.Thread(target=self._run, name="etercut-sync", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def close(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=REQUEST_TIMEOUT_SECONDS + 1.0)

    def notify_changed(self) -> None:
        with self._condition:
            self._pending = True
            if self._retry_index == 0:
                self._next_attempt = self._monotonic()
            self._condition.notify_all()

    def configuration_changed(self) -> None:
        with self._condition:
            self._pending = True
            self._retry_index = 0
            self._next_attempt = self._monotonic()
            self._last_error = ""
            self._condition.notify_all()

    def status(self) -> dict:
        with self._condition:
            status = {
                "connected": bool(self._last_success and not self._last_error),
                "lastSuccessAt": _iso_time(self._last_success),
                "lastAttemptAt": _iso_time(self._last_attempt),
                "error": self._last_error,
            }
        return {**self.store.remote_sync_public(), **status}

    def pair(self, pairing_token: str) -> dict:
        pairing_token = str(pairing_token).strip()
        if not pairing_token:
            raise RemoteSyncError("연결 코드를 입력하세요.")
        config = self.store.remote_sync()
        if config["paired"]:
            return self.status()
        if not all(
            (
                config["writePrivateKey"],
                config["writePublicKey"],
                config["adminPrivateKey"],
                config["adminPublicKey"],
            )
        ):
            self.store.save_operator_identities(generate_identity(), generate_identity())
            config = self.store.remote_sync()

        endpoint = config["endpoint"].rstrip("/") + "/pair"
        payload = json.dumps(
            {
                "writePublicKey": config["writePublicKey"],
                "adminPublicKey": config["adminPublicKey"],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {pairing_token}",
                "Content-Type": "application/json",
                "User-Agent": "EternalReturnScoreOverlay/1",
            },
        )
        try:
            with self._open_url(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", response.getcode()))
        except HTTPError as error:
            if error.code == 409:
                try:
                    self._signed_request("GET", "/views", role="admin", config=config)
                except RemoteSyncError as verify_error:
                    raise RemoteSyncError(
                        "이미 다른 컴퓨터가 연결되어 있습니다. 서버 운영자에게 연결 초기화를 요청하세요."
                    ) from verify_error
            elif error.code in (401, 404):
                raise RemoteSyncError("연결 코드가 올바르지 않거나 만료되었습니다.") from error
            else:
                raise RemoteSyncError(f"ETERCUT 연결에 실패했습니다. (HTTP {error.code})") from error
        except URLError as error:
            raise RemoteSyncError("ETERCUT 서버에 연결할 수 없습니다.") from error
        else:
            if status != 201:
                raise RemoteSyncError(f"ETERCUT 연결 응답이 올바르지 않습니다. (HTTP {status})")

        self.store.mark_operator_paired()
        self.configuration_changed()
        return self.status()

    def sync_now(self) -> dict:
        config = self.store.remote_sync()
        if not config["paired"]:
            raise RemoteSyncError("이 컴퓨터를 ETERCUT에 먼저 연결하세요.")
        self.notify_changed()
        return self.status()

    def list_views(self) -> list[dict]:
        _, result = self._admin_request("GET", "/views")
        origin = str(result.get("publicOrigin", "")).rstrip("/")
        return [self._with_links(dict(view), origin) for view in result.get("views", [])]

    def create_view(self, name: str) -> dict:
        read_token = generate_view_token()
        edit_token = generate_view_token()
        _, result = self._admin_request(
            "POST",
            "/views",
            {
                "name": name,
                "readTokenHash": hash_token(read_token),
                "editTokenHash": hash_token(edit_token),
            },
        )
        view = dict(result["view"])
        self.store.save_view_tokens(view["viewId"], read_token, edit_token)
        return self._with_links(view, str(result.get("publicOrigin", "")).rstrip("/"))

    def rotate_view(self, view_id: str) -> dict:
        read_token = generate_view_token()
        edit_token = generate_view_token()
        _, result = self._admin_request(
            "POST",
            f"/views/{quote(view_id, safe='')}",
            {
                "action": "rotate",
                "readTokenHash": hash_token(read_token),
                "editTokenHash": hash_token(edit_token),
            },
        )
        view = dict(result["view"])
        self.store.save_view_tokens(view["viewId"], read_token, edit_token)
        return self._with_links(view, str(result.get("publicOrigin", "")).rstrip("/"))

    def set_view_active(self, view_id: str, active: bool) -> dict:
        _, result = self._admin_request(
            "POST",
            f"/views/{quote(view_id, safe='')}",
            {"action": "set-active", "active": bool(active)},
        )
        return self._with_links(
            dict(result["view"]),
            str(result.get("publicOrigin", "")).rstrip("/"),
        )

    def delete_view(self, view_id: str) -> None:
        self._admin_request("DELETE", f"/views/{quote(view_id, safe='')}")
        self.store.delete_view_tokens(view_id)

    def _with_links(self, view: dict, origin: str) -> dict:
        tokens = self.store.view_tokens(str(view.get("viewId", "")))
        links_available = bool(tokens and origin)
        return {
            **view,
            "linksAvailable": links_available,
            "overlayUrl": (
                f"{origin}/live-score#{view['viewId']}.{tokens['readToken']}"
                if links_available
                else ""
            ),
            "settingsUrl": (
                f"{origin}/live-score-settings#{view['viewId']}.{tokens['editToken']}"
                if links_available
                else ""
            ),
        }

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stop:
                    return
                config = self.store.remote_sync()
                ready = bool(
                    config["enabled"]
                    and config["paired"]
                    and config["writePrivateKey"]
                )
                now = self._monotonic()
                if not ready:
                    self._condition.wait(timeout=HEARTBEAT_SECONDS)
                    continue
                wait_seconds = max(0.0, self._next_attempt - now)
                if not self._pending and wait_seconds <= 0:
                    self._pending = True
                if not self._pending or wait_seconds > 0:
                    self._condition.wait(timeout=wait_seconds or HEARTBEAT_SECONDS)
                    continue
                self._pending = False

            try:
                self._upload(config)
            except (RemoteSyncError, OSError, ValueError) as error:
                with self._condition:
                    self._last_attempt = self._wall_time()
                    self._last_error = str(error)
                    delay = RETRY_SECONDS[min(self._retry_index, len(RETRY_SECONDS) - 1)]
                    self._retry_index = min(self._retry_index + 1, len(RETRY_SECONDS) - 1)
                    self._pending = True
                    self._next_attempt = self._monotonic() + delay
            else:
                with self._condition:
                    completed_at = self._wall_time()
                    self._last_attempt = completed_at
                    self._last_success = completed_at
                    self._last_error = ""
                    self._retry_index = 0
                    if not self._pending:
                        self._next_attempt = self._monotonic() + HEARTBEAT_SECONDS

    def _upload(self, config: dict) -> None:
        status, _ = self._signed_request(
            "PUT",
            "",
            self.state.public_snapshot(),
            role="write",
            config=config,
        )
        if status != 204:
            raise RemoteSyncError(f"ETERCUT 업로드 응답이 올바르지 않습니다. (HTTP {status})")

    def _admin_request(
        self,
        method: str,
        suffix: str,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        config = self.store.remote_sync()
        if not config["paired"] or not config["adminPrivateKey"]:
            raise RemoteSyncError("이 컴퓨터를 ETERCUT에 먼저 연결하세요.")
        return self._signed_request(method, suffix, payload, role="admin", config=config)

    def _signed_request(
        self,
        method: str,
        suffix: str,
        payload: dict | None = None,
        *,
        role: str,
        config: dict,
    ) -> tuple[int, dict]:
        endpoint = config["endpoint"].rstrip("/") + suffix
        data = (
            None
            if payload is None
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        private_key = config[f"{role}PrivateKey"]
        if not private_key:
            raise RemoteSyncError("이 컴퓨터의 ETERCUT 인증 정보를 찾을 수 없습니다.")
        headers = {
            "User-Agent": "EternalReturnScoreOverlay/1",
            **sign_headers(method, endpoint, data, private_key, wall_time=self._wall_time),
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(endpoint, data=data, method=method, headers=headers)
        try:
            with self._open_url(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", response.getcode()))
                body = response.read()
        except HTTPError as error:
            if error.code == 404:
                raise RemoteSyncError("권한이 없거나 대상 방송인 링크를 찾을 수 없습니다.") from error
            raise RemoteSyncError(f"ETERCUT 요청이 실패했습니다. (HTTP {error.code})") from error
        except URLError as error:
            raise RemoteSyncError("ETERCUT 서버에 연결할 수 없습니다.") from error
        if status == 204 or not body:
            return status, {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RemoteSyncError("ETERCUT 서버 응답을 읽을 수 없습니다.") from error
        if not isinstance(value, dict):
            raise RemoteSyncError("ETERCUT 서버 응답 형식이 올바르지 않습니다.")
        return status, value
