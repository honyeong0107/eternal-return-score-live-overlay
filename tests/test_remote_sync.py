from __future__ import annotations

import base64
import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from score_overlay.live_score import LiveScoreCapture
from score_overlay.remote_sync import RemoteStateSync
from score_overlay.state import ScoreState
from score_overlay.tournaments import TournamentStore
from score_overlay.web import OverlayServer


class FakeSecretProtector:
    def protect(self, value: str) -> str:
        return "protected:" + value[::-1]

    def unprotect(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith("protected:"):
            raise RuntimeError("invalid protected value")
        return value.removeprefix("protected:")[::-1]


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None):
        self.status = status
        self.payload = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self.payload


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))


def _headers(request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def verify_signature(request, public_key_value: str) -> None:
    headers = _headers(request)
    body = request.data or b""
    content_hash = hashlib.sha256(body).hexdigest()
    if headers["x-live-score-content-sha256"] != content_hash:
        raise AssertionError("body hash mismatch")
    message = "\n".join(
        (
            "ETERCUT-LIVE-SCORE-V1",
            request.method,
            urlsplit(request.full_url).path,
            headers["x-live-score-timestamp"],
            headers["x-live-score-nonce"],
            content_hash,
        )
    ).encode("utf-8")
    Ed25519PublicKey.from_public_bytes(_decode_base64url(public_key_value)).verify(
        _decode_base64url(headers["x-live-score-signature"]),
        message,
    )


class RemoteSyncTests(unittest.TestCase):
    def test_change_during_upload_is_sent_without_waiting_for_heartbeat(self) -> None:
        class ReadyStore:
            @staticmethod
            def remote_sync() -> dict:
                return {
                    "enabled": True,
                    "paired": True,
                    "writePrivateKey": "test-private-key",
                }

        class RevisionState:
            revision = 1

        state = RevisionState()
        sync = RemoteStateSync(state, ReadyStore())
        first_upload_started = threading.Event()
        finish_first_upload = threading.Event()
        second_upload_finished = threading.Event()
        uploaded_revisions = []

        def upload(_config: dict) -> None:
            uploaded_revisions.append(state.revision)
            if len(uploaded_revisions) == 1:
                first_upload_started.set()
                self.assertTrue(finish_first_upload.wait(timeout=1))
            else:
                second_upload_finished.set()

        sync._upload = upload
        sync.start()
        try:
            self.assertTrue(first_upload_started.wait(timeout=1))
            state.revision = 2
            sync.notify_changed()
            finish_first_upload.set()
            self.assertTrue(second_upload_finished.wait(timeout=0.5))
            self.assertEqual([1, 2], uploaded_revisions[:2])
        finally:
            finish_first_upload.set()
            sync.close()

    def test_pairing_code_is_not_saved_and_private_keys_are_protected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = TournamentStore(path, secret_protector=FakeSecretProtector())
            requests = []

            def open_url(request, timeout):
                requests.append((request, timeout))
                return FakeResponse(201, {"paired": True})

            sync = RemoteStateSync(ScoreState(store.active()), store, open_url=open_url)
            status = sync.pair("one-time-pairing-code")
            saved = path.read_text(encoding="utf-8")
            request = requests[0][0]
            payload = json.loads(request.data.decode("utf-8"))

            self.assertTrue(status["paired"])
            self.assertEqual("Bearer one-time-pairing-code", _headers(request)["authorization"])
            self.assertEqual(43, len(payload["writePublicKey"]))
            self.assertEqual(43, len(payload["adminPublicKey"]))
            self.assertNotIn("one-time-pairing-code", saved)
            self.assertNotIn(store.remote_sync()["writePrivateKey"], saved)
            self.assertNotIn(store.remote_sync()["adminPrivateKey"], saved)

    def test_upload_and_admin_requests_use_separate_valid_signatures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(
                Path(directory) / "config.json",
                secret_protector=FakeSecretProtector(),
            )
            requests = []
            public_keys = {}

            def open_url(request, timeout):
                requests.append((request, timeout))
                path = urlsplit(request.full_url).path
                if path.endswith("/pair"):
                    public_keys.update(json.loads(request.data.decode("utf-8")))
                    return FakeResponse(201, {"paired": True})
                if request.method == "PUT":
                    verify_signature(request, public_keys["writePublicKey"])
                    return FakeResponse(204)
                verify_signature(request, public_keys["adminPublicKey"])
                if request.method == "GET":
                    return FakeResponse(200, {"views": [], "publicOrigin": "https://etercut.com"})
                return FakeResponse(
                    201,
                    {
                        "view": {
                            "viewId": "abcdefghijkl",
                            "name": "Caster A",
                            "active": True,
                        },
                        "publicOrigin": "https://etercut.com",
                    },
                )

            sync = RemoteStateSync(
                ScoreState(store.active()),
                store,
                open_url=open_url,
                wall_time=lambda: 1_700_000_000.0,
            )
            sync.pair("pair-code")
            store.save_remote_sync(
                {"enabled": True, "endpoint": "https://api.etercut.com/api/live-score"}
            )
            sync._upload(store.remote_sync())
            upload_request = requests[-1][0]
            self.assertNotIn("authorization", _headers(upload_request))
            self.assertEqual(8, len(json.loads(upload_request.data)["teams"]))

            self.assertEqual([], sync.list_views())
            created = sync.create_view("Caster A")
            create_request = requests[-1][0]
            create_payload = json.loads(create_request.data)
            read_token = urlsplit(created["overlayUrl"]).fragment.split(".", 1)[1]
            edit_token = urlsplit(created["settingsUrl"]).fragment.split(".", 1)[1]
            self.assertEqual(hashlib.sha256(read_token.encode()).hexdigest(), create_payload["readTokenHash"])
            self.assertEqual(hashlib.sha256(edit_token.encode()).hexdigest(), create_payload["editTokenHash"])
            self.assertNotIn(read_token, create_request.data.decode("utf-8"))
            self.assertNotIn(edit_token, create_request.data.decode("utf-8"))
            self.assertNotIn("authorization", _headers(create_request))

    def test_view_tokens_stay_local_and_missing_tokens_require_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = TournamentStore(path, secret_protector=FakeSecretProtector())
            store.save_view_tokens("abcdefghijkl", "read-secret", "edit-secret")
            saved = path.read_text(encoding="utf-8")
            self.assertNotIn("read-secret", saved)
            self.assertNotIn("edit-secret", saved)
            self.assertEqual(
                {"readToken": "read-secret", "editToken": "edit-secret"},
                store.view_tokens("abcdefghijkl"),
            )
            store.delete_view_tokens("abcdefghijkl")
            self.assertIsNone(store.view_tokens("abcdefghijkl"))

    def test_remote_endpoint_rejects_query_or_fragment_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(
                Path(directory) / "config.json",
                secret_protector=FakeSecretProtector(),
            )
            for endpoint in (
                "https://api.etercut.com/api/live-score?redirect=elsewhere",
                "https://api.etercut.com/api/live-score#fragment",
            ):
                with self.subTest(endpoint=endpoint):
                    with self.assertRaises(ValueError):
                        store.save_remote_sync({"endpoint": endpoint})

    def test_public_snapshot_excludes_local_history_and_uses_safe_team_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(
                Path(directory) / "config.json",
                secret_protector=FakeSecretProtector(),
            )
            snapshot = ScoreState(store.active()).public_snapshot()
            self.assertEqual(1, snapshot["schemaVersion"])
            self.assertEqual(8, len(snapshot["teams"]))
            self.assertEqual("TEAM 1", snapshot["teams"][0]["name"])
            self.assertNotIn("completedRounds", snapshot)
            self.assertNotIn("health", snapshot)
            self.assertNotIn("capture", snapshot)

    def test_local_status_api_never_returns_authentication_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(
                Path(directory) / "config.json",
                secret_protector=FakeSecretProtector(),
            )
            state = ScoreState(store.active())
            sync = RemoteStateSync(state, store)
            server = OverlayServer(
                ("127.0.0.1", 0),
                state,
                store,
                LiveScoreCapture(),
                remote_sync=sync,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/api/remote-sync")
                response = connection.getresponse()
                payload = response.read().decode("utf-8")
                self.assertEqual(200, response.status)
                for forbidden in ("privateKey", "publicKey", "Token", "Protected"):
                    self.assertNotIn(forbidden, payload)
                self.assertFalse(json.loads(payload)["paired"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
