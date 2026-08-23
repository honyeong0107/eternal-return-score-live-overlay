# Powered by Honyeong
from __future__ import annotations

import json
import re
import threading
import unicodedata
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from .secret_store import SecretProtector


DEFAULT_TEAMS = [""] * 8
DEFAULT_TEAM_NAMES_CONFIGURED = [False] * 8
DEFAULT_THEME = {
    "title": "LEADERBOARD",
    "accent": "#a8d8f0",
    "surface": "#ffffff",
    "text": "#111c22",
    "muted": "#52656f",
    "ks": "#3e718a",
    "rank": "#a8d8f0",
    "rankText": "#0e1a20",
    "line": "#000000",
    "elimination": "#86aabd",
}
DEFAULT_PROFILE = {
    "id": "default",
    "name": "기본 대회",
    "checkpointEnabled": False,
    "teams": DEFAULT_TEAMS,
    "teamNamesConfigured": DEFAULT_TEAM_NAMES_CONFIGURED,
    "theme": DEFAULT_THEME,
}
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
VIEW_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
REGISTRY_KEY = r"Software\EternalReturnScoreOverlay"
REGISTRY_VALUE = "Config"
DEFAULT_REMOTE_SYNC_ENDPOINT = "https://api.etercut.com/api/live-score"


class TournamentStore:
    def __init__(self, path: Path | None, secret_protector: SecretProtector | None = None):
        self.path = path
        self._secret_protector = secret_protector or SecretProtector()
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        raw_text = self._read_text()
        if raw_text is None:
            return {
                "activeTournament": "default",
                "tournaments": [deepcopy(DEFAULT_PROFILE)],
                "remoteSync": self._normalize_remote_sync(None),
            }

        raw = json.loads(raw_text)
        if "tournaments" not in raw:
            teams = raw.get("teams", DEFAULT_TEAMS)
            profile = deepcopy(DEFAULT_PROFILE)
            profile["teams"] = teams
            if "teamNamesConfigured" in raw:
                profile["teamNamesConfigured"] = raw["teamNamesConfigured"]
            else:
                profile.pop("teamNamesConfigured", None)
            profile = self._validate(profile)
            data = {
                "activeTournament": "default",
                "tournaments": [profile],
                "remoteSync": self._normalize_remote_sync(raw.get("remoteSync")),
            }
            sessions = self._load_sessions(raw, {profile["id"]})
            if sessions:
                data["scoreSessions"] = sessions
            return data

        profiles = [self._validate(profile) for profile in raw.get("tournaments", [])]
        if not profiles:
            profiles = [deepcopy(DEFAULT_PROFILE)]
        ids = {profile["id"] for profile in profiles}
        active = raw.get("activeTournament")
        if active not in ids:
            active = profiles[0]["id"]
        data = {
            "activeTournament": active,
            "tournaments": profiles,
            "remoteSync": self._normalize_remote_sync(raw.get("remoteSync")),
        }
        sessions = self._load_sessions(raw, ids)
        if sessions:
            data["scoreSessions"] = sessions
        return data

    @staticmethod
    def _normalize_remote_sync(value: object) -> dict:
        source = value if isinstance(value, dict) else {}
        endpoint = str(source.get("endpoint", DEFAULT_REMOTE_SYNC_ENDPOINT)).strip()
        if not endpoint:
            endpoint = DEFAULT_REMOTE_SYNC_ENDPOINT
        raw_view_tokens = source.get("viewTokens")
        view_tokens = {}
        if isinstance(raw_view_tokens, dict):
            for view_id, tokens in raw_view_tokens.items():
                clean_view_id = str(view_id).strip()
                if not VIEW_ID_PATTERN.fullmatch(clean_view_id) or not isinstance(tokens, dict):
                    continue
                view_tokens[clean_view_id] = {
                    "readTokenProtected": str(tokens.get("readTokenProtected", "")),
                    "editTokenProtected": str(tokens.get("editTokenProtected", "")),
                }
        return {
            "enabled": bool(source.get("enabled", False)),
            "endpoint": endpoint.rstrip("/"),
            "operatorPaired": bool(source.get("operatorPaired", False)),
            "writePrivateKeyProtected": str(source.get("writePrivateKeyProtected", "")),
            "writePublicKey": str(source.get("writePublicKey", "")),
            "adminPrivateKeyProtected": str(source.get("adminPrivateKeyProtected", "")),
            "adminPublicKey": str(source.get("adminPublicKey", "")),
            "viewTokens": view_tokens,
            "legacyWriteTokenProtected": str(
                source.get("legacyWriteTokenProtected", source.get("writeTokenProtected", ""))
            ),
            "legacyAdminTokenProtected": str(
                source.get("legacyAdminTokenProtected", source.get("adminTokenProtected", ""))
            ),
        }

    @staticmethod
    def _load_sessions(raw: dict, tournament_ids: set[str]) -> dict[str, dict]:
        sessions = {}
        raw_sessions = raw.get("scoreSessions")
        if isinstance(raw_sessions, dict):
            for tournament_id, session in raw_sessions.items():
                if (
                    tournament_id in tournament_ids
                    and isinstance(session, dict)
                    and session.get("tournamentId") == tournament_id
                ):
                    sessions[tournament_id] = deepcopy(session)

        legacy_session = raw.get("scoreSession")
        if isinstance(legacy_session, dict):
            tournament_id = legacy_session.get("tournamentId")
            if tournament_id in tournament_ids and tournament_id not in sessions:
                sessions[tournament_id] = deepcopy(legacy_session)
        return sessions

    @staticmethod
    def _slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return slug or "tournament"

    @classmethod
    def _validate(cls, profile: dict) -> dict:
        name = str(profile.get("name", "")).strip()
        if not 1 <= len(name) <= 50:
            raise ValueError("대회 이름은 1~50자로 입력하세요.")

        profile_id = str(profile.get("id", "")).strip() or cls._slug(name)
        if not re.fullmatch(r"[a-z0-9-]{1,50}", profile_id):
            profile_id = cls._slug(profile_id)[:50]

        teams = profile.get("teams")
        if not isinstance(teams, list) or len(teams) != 8:
            raise ValueError("팀 이름은 정확히 8개가 필요합니다.")
        clean_teams = [str(team).strip() for team in teams]
        raw_configured = profile.get("teamNamesConfigured")
        if isinstance(raw_configured, list) and len(raw_configured) == 8:
            team_names_configured = [bool(configured) for configured in raw_configured]
        else:
            generated_labels = [f"TEAM {team}" for team in range(1, 9)]
            team_names_configured = [
                bool(name) and name.casefold() != generated.casefold()
                for name, generated in zip(clean_teams, generated_labels)
            ]
        if any(len(team) > 40 for team in clean_teams) or any(
            configured and not name
            for name, configured in zip(clean_teams, team_names_configured)
        ):
            raise ValueError("각 팀 이름은 40자 이내로 입력하세요.")
        clean_teams = [
            name if configured else ""
            for name, configured in zip(clean_teams, team_names_configured)
        ]

        theme = profile.get("theme") or {}
        title = str(theme.get("title", DEFAULT_THEME["title"])).strip()
        if not 1 <= len(title) <= 30:
            raise ValueError("점수판 제목은 1~30자로 입력하세요.")
        editable_colors = {
            key: str(theme.get(key, DEFAULT_THEME[key])).lower()
            for key in ("accent", "text", "muted", "ks", "rank", "rankText", "elimination")
        }
        if any(not COLOR_PATTERN.fullmatch(color) for color in editable_colors.values()):
            raise ValueError("색상은 #65d9ff 형식으로 입력하세요.")
        colors = {
            "accent": editable_colors["accent"],
            "surface": "#ffffff",
            "text": editable_colors["text"],
            "muted": editable_colors["muted"],
            "ks": editable_colors["ks"],
            "rank": editable_colors["rank"],
            "rankText": editable_colors["rankText"],
            "line": "#000000",
            "elimination": editable_colors["elimination"],
        }

        return {
            "id": profile_id,
            "name": name,
            "checkpointEnabled": bool(profile.get("checkpointEnabled", False)),
            "teams": clean_teams,
            "teamNamesConfigured": team_names_configured,
            "theme": {"title": title, **colors},
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "activeTournament": self._data["activeTournament"],
                "tournaments": deepcopy(self._data["tournaments"]),
            }

    def remote_sync(self) -> dict:
        with self._lock:
            value = deepcopy(self._data["remoteSync"])
        credential_error = ""
        try:
            write_private_key = self._secret_protector.unprotect(
                value["writePrivateKeyProtected"]
            )
            admin_private_key = self._secret_protector.unprotect(
                value["adminPrivateKeyProtected"]
            )
        except RuntimeError as error:
            write_private_key = ""
            admin_private_key = ""
            credential_error = str(error)
        return {
            "enabled": value["enabled"],
            "endpoint": value["endpoint"],
            "paired": bool(value["operatorPaired"]),
            "writePrivateKey": write_private_key,
            "writePublicKey": value["writePublicKey"],
            "adminPrivateKey": admin_private_key,
            "adminPublicKey": value["adminPublicKey"],
            "credentialError": credential_error,
        }

    def remote_sync_public(self) -> dict:
        with self._lock:
            value = self._data["remoteSync"]
            return {
                "enabled": value["enabled"],
                "endpoint": value["endpoint"],
                "paired": bool(
                    value["operatorPaired"]
                    and value["writePrivateKeyProtected"]
                    and value["adminPrivateKeyProtected"]
                ),
            }

    def save_remote_sync(self, value: dict) -> dict:
        endpoint = str(value.get("endpoint", DEFAULT_REMOTE_SYNC_ENDPOINT)).strip().rstrip("/")
        parsed = urlparse(endpoint)
        local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            not (parsed.scheme == "https" or local_http)
            or not parsed.netloc
            or parsed.path != "/api/live-score"
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API 주소는 https://.../api/live-score 형식이어야 합니다.")
        with self._lock:
            current = self._data["remoteSync"]
            updated = deepcopy(current)
            updated["enabled"] = bool(value.get("enabled", current["enabled"]))
            updated["endpoint"] = endpoint
            self._data["remoteSync"] = updated
            self._save_locked()
        return self.remote_sync_public()

    def save_operator_identities(
        self,
        write_identity: dict[str, str],
        admin_identity: dict[str, str],
    ) -> None:
        with self._lock:
            remote = self._data["remoteSync"]
            remote["writePrivateKeyProtected"] = self._secret_protector.protect(
                write_identity["privateKey"]
            )
            remote["writePublicKey"] = write_identity["publicKey"]
            remote["adminPrivateKeyProtected"] = self._secret_protector.protect(
                admin_identity["privateKey"]
            )
            remote["adminPublicKey"] = admin_identity["publicKey"]
            remote["operatorPaired"] = False
            self._save_locked()

    def mark_operator_paired(self) -> None:
        with self._lock:
            remote = self._data["remoteSync"]
            if not remote["writePrivateKeyProtected"] or not remote["adminPrivateKeyProtected"]:
                raise RuntimeError("이 컴퓨터의 ETERCUT 인증 정보를 찾을 수 없습니다.")
            remote["operatorPaired"] = True
            remote["legacyWriteTokenProtected"] = ""
            remote["legacyAdminTokenProtected"] = ""
            self._save_locked()

    def legacy_pairing_token(self) -> str:
        with self._lock:
            protected = self._data["remoteSync"]["legacyAdminTokenProtected"]
        try:
            return self._secret_protector.unprotect(protected)
        except RuntimeError:
            return ""

    def view_tokens(self, view_id: str) -> dict[str, str] | None:
        clean_view_id = str(view_id).strip()
        if not VIEW_ID_PATTERN.fullmatch(clean_view_id):
            return None
        with self._lock:
            protected = deepcopy(
                self._data["remoteSync"]["viewTokens"].get(clean_view_id)
            )
        if not protected:
            return None
        try:
            read_token = self._secret_protector.unprotect(protected["readTokenProtected"])
            edit_token = self._secret_protector.unprotect(protected["editTokenProtected"])
        except RuntimeError:
            return None
        if not read_token or not edit_token:
            return None
        return {"readToken": read_token, "editToken": edit_token}

    def save_view_tokens(self, view_id: str, read_token: str, edit_token: str) -> None:
        clean_view_id = str(view_id).strip()
        if not VIEW_ID_PATTERN.fullmatch(clean_view_id):
            raise ValueError("방송인 링크 식별자가 올바르지 않습니다.")
        if not read_token or not edit_token:
            raise ValueError("방송인 링크 토큰이 비어 있습니다.")
        with self._lock:
            self._data["remoteSync"]["viewTokens"][clean_view_id] = {
                "readTokenProtected": self._secret_protector.protect(read_token),
                "editTokenProtected": self._secret_protector.protect(edit_token),
            }
            self._save_locked()

    def delete_view_tokens(self, view_id: str) -> None:
        with self._lock:
            removed = self._data["remoteSync"]["viewTokens"].pop(str(view_id), None)
            if removed is not None:
                self._save_locked()

    def load_session(self) -> dict | None:
        with self._lock:
            sessions = self._data.get("scoreSessions", {})
            session = sessions.get(self._data["activeTournament"])
            if not isinstance(session, dict):
                return None
            return deepcopy(session)

    def save_session(self, session: dict) -> None:
        with self._lock:
            if session.get("tournamentId") != self._data["activeTournament"]:
                raise ValueError("활성 대회와 라운드 기록이 일치하지 않습니다.")
            sessions = self._data.setdefault("scoreSessions", {})
            sessions[self._data["activeTournament"]] = deepcopy(session)
            self._save_locked()

    def active(self) -> dict:
        with self._lock:
            active_id = self._data["activeTournament"]
            profile = next(profile for profile in self._data["tournaments"] if profile["id"] == active_id)
            return deepcopy(profile)

    def select(self, profile_id: str) -> dict:
        with self._lock:
            if not any(profile["id"] == profile_id for profile in self._data["tournaments"]):
                raise ValueError("선택한 대회를 찾을 수 없습니다.")
            self._data["activeTournament"] = profile_id
            self._save_locked()
            return deepcopy(next(profile for profile in self._data["tournaments"] if profile["id"] == profile_id))

    def save(self, value: dict) -> dict:
        is_new = not str(value.get("id", "")).strip()
        profile = self._validate(value)
        with self._lock:
            existing_ids = {item["id"] for item in self._data["tournaments"]}
            requested_id = profile["id"]
            if is_new and requested_id in existing_ids:
                base = requested_id
                suffix = 2
                while profile["id"] in existing_ids:
                    profile["id"] = f"{base}-{suffix}"
                    suffix += 1

            for index, item in enumerate(self._data["tournaments"]):
                if item["id"] == profile["id"]:
                    self._data["tournaments"][index] = profile
                    break
            else:
                self._data["tournaments"].append(profile)
            self._data["activeTournament"] = profile["id"]
            self._save_locked()
            return deepcopy(profile)

    def delete(self, profile_id: str) -> dict:
        with self._lock:
            index = next(
                (index for index, profile in enumerate(self._data["tournaments"]) if profile["id"] == profile_id),
                None,
            )
            if index is None:
                raise ValueError("삭제할 대회를 찾을 수 없습니다.")
            if len(self._data["tournaments"]) == 1:
                self._data = {
                    "activeTournament": "default",
                    "tournaments": [deepcopy(DEFAULT_PROFILE)],
                    "remoteSync": deepcopy(self._data["remoteSync"]),
                }
                self._save_locked()
                return deepcopy(DEFAULT_PROFILE)
            self._data["tournaments"].pop(index)
            sessions = self._data.get("scoreSessions")
            if isinstance(sessions, dict):
                sessions.pop(profile_id, None)
            if self._data["activeTournament"] == profile_id:
                self._data["activeTournament"] = self._data["tournaments"][0]["id"]
            active_id = self._data["activeTournament"]
            active = next(profile for profile in self._data["tournaments"] if profile["id"] == active_id)
            self._save_locked()
            return deepcopy(active)

    def _save_locked(self) -> None:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.path)
            return

        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            winreg.SetValueEx(key, REGISTRY_VALUE, 0, winreg.REG_SZ, payload)

    def _read_text(self) -> str | None:
        if self.path is not None:
            if not self.path.exists():
                return None
            return self.path.read_text(encoding="utf-8")

        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
                value, _ = winreg.QueryValueEx(key, REGISTRY_VALUE)
        except FileNotFoundError:
            return None
        return str(value)
