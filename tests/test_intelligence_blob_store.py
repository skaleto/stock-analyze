from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_analyze.intelligence.blob_store import (
    BlobConflictError,
    BlobStoreConfigurationError,
    LocalBlobStore,
    OssBlobStore,
    build_blob_store,
    parsed_object_key,
    pdf_object_key,
)


PDF = b"%PDF-1.7\nartifact\n%%EOF\n"


class FakeOssError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"fake-oss-status-{status}")
        self.status = status


class FakeOssBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class FakeOssHead:
    def __init__(self, payload: bytes, headers: dict[str, str]) -> None:
        self.content_length = len(payload)
        self.content_type = headers["Content-Type"]
        self.headers = dict(headers)


class FakeOssBucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.put_headers: list[dict[str, str]] = []

    def put_object(
        self,
        key: str,
        payload: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        actual_headers = dict(headers or {})
        self.put_headers.append(actual_headers)
        if actual_headers.get("If-None-Match") == "*" and key in self.objects:
            raise FakeOssError(412)
        self.objects[key] = (bytes(payload), actual_headers)

    def head_object(self, key: str) -> FakeOssHead:
        try:
            payload, headers = self.objects[key]
        except KeyError as exc:
            raise FakeOssError(404) from exc
        return FakeOssHead(payload, headers)

    def get_object(self, key: str) -> FakeOssBody:
        try:
            payload, _headers = self.objects[key]
        except KeyError as exc:
            raise FakeOssError(404) from exc
        return FakeOssBody(payload)


class BlobStoreContractMixin:
    store: LocalBlobStore | OssBlobStore

    def test_put_if_absent_exists_read_and_repeat_are_idempotent(self) -> None:
        key = pdf_object_key(PDF)
        uri = self.store.put_if_absent(key, PDF, "application/pdf")

        self.assertTrue(self.store.exists(uri))
        self.assertEqual(self.store.read(uri), PDF)
        self.assertEqual(
            self.store.put_if_absent(key, PDF, "application/pdf"),
            uri,
        )

    def test_same_key_with_different_payload_fails_closed(self) -> None:
        key = pdf_object_key(PDF)
        self.store.put_if_absent(key, PDF, "application/pdf")

        with self.assertRaisesRegex(
            BlobConflictError,
            "^intelligence_blob_conflict:",
        ):
            self.store.put_if_absent(
                key,
                b"%PDF-1.7\ndifferent\n%%EOF\n",
                "application/pdf",
            )

    def test_same_key_with_different_content_type_fails_closed(self) -> None:
        key = pdf_object_key(PDF)
        self.store.put_if_absent(key, PDF, "application/pdf")

        with self.assertRaisesRegex(
            BlobConflictError,
            "^intelligence_blob_conflict:",
        ):
            self.store.put_if_absent(key, PDF, "application/octet-stream")


class LocalBlobStoreTest(BlobStoreContractMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LocalBlobStore(
            Path(self.tmp.name),
            key_prefix="announcements",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_uri_parser_rejects_wrong_authority_and_path_escape(self) -> None:
        for uri in (
            "localblob://other/announcements/pdf/a.pdf",
            "localblob://artifacts/../outside",
            "localblob://artifacts/%2e%2e/outside",
            "file:///tmp/outside",
            "localblob://user:pass@artifacts/announcements/pdf/a.pdf",
        ):
            with self.subTest(uri=uri):
                with self.assertRaisesRegex(
                    ValueError,
                    "^intelligence_blob_uri_invalid",
                ):
                    self.store.exists(uri)

    def test_file_is_not_rewritten_for_idempotent_put(self) -> None:
        key = pdf_object_key(PDF)
        uri = self.store.put_if_absent(key, PDF, "application/pdf")
        path = self.store.path_for_uri(uri)
        first_mtime = path.stat().st_mtime_ns

        self.store.put_if_absent(key, PDF, "application/pdf")

        self.assertEqual(path.stat().st_mtime_ns, first_mtime)


class OssBlobStoreTest(BlobStoreContractMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.bucket = FakeOssBucket()
        self.store = OssBlobStore(
            endpoint="https://oss-cn-beijing.aliyuncs.com",
            bucket_name="stock-analyze",
            key_prefix="announcements",
            bucket_client=self.bucket,
        )

    def test_conditional_write_race_validates_existing_object(self) -> None:
        key = pdf_object_key(PDF)
        first = self.store.put_if_absent(key, PDF, "application/pdf")
        second_store = OssBlobStore(
            endpoint="https://oss-cn-beijing.aliyuncs.com",
            bucket_name="stock-analyze",
            key_prefix="announcements",
            bucket_client=self.bucket,
        )

        second = second_store.put_if_absent(key, PDF, "application/pdf")

        self.assertEqual(second, first)
        self.assertTrue(
            all(
                headers.get("If-None-Match") == "*"
                for headers in self.bucket.put_headers
            )
        )
        self.assertTrue(
            all(
                headers.get("x-oss-forbid-overwrite") == "true"
                for headers in self.bucket.put_headers
            )
        )

    def test_uri_parser_enforces_bucket_and_prefix_boundaries(self) -> None:
        for uri in (
            "oss://other/announcements/pdf/a.pdf",
            "oss://stock-analyze/other/pdf/a.pdf",
            "oss://stock-analyze/announcements/../secret",
            "oss://user:pass@stock-analyze/announcements/pdf/a.pdf",
            "oss://stock-analyze/announcements/pdf/a.pdf?version=1",
        ):
            with self.subTest(uri=uri):
                with self.assertRaisesRegex(
                    ValueError,
                    "^intelligence_blob_uri_invalid",
                ):
                    self.store.exists(uri)

    def test_existing_object_with_inconsistent_metadata_fails_closed(self) -> None:
        key = pdf_object_key(PDF)
        self.bucket.objects[key] = (
            PDF,
            {
                "Content-Type": "application/pdf",
                "x-oss-meta-sha256": "0" * 64,
            },
        )

        with self.assertRaises(BlobConflictError):
            self.store.put_if_absent(key, PDF, "application/pdf")

    def test_provider_error_does_not_preserve_sensitive_exception_chain(
        self,
    ) -> None:
        class ExplodingBucket(FakeOssBucket):
            def put_object(self, *_args, **_kwargs) -> None:
                raise RuntimeError("provider echoed never-leak-this")

        store = OssBlobStore(
            endpoint="https://oss-cn-beijing.aliyuncs.com",
            bucket_name="stock-analyze",
            key_prefix="announcements",
            bucket_client=ExplodingBucket(),
        )

        with self.assertRaises(Exception) as raised:
            store.put_if_absent(
                pdf_object_key(PDF),
                PDF,
                "application/pdf",
            )

        self.assertNotIn("never-leak-this", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)


class BlobStoreFactoryTest(unittest.TestCase):
    def test_content_addressed_key_layout_is_stable(self) -> None:
        digest = "ab" + ("0" * 62)
        self.assertEqual(
            pdf_object_key(digest),
            f"announcements/pdf/ab/{digest}.pdf",
        )
        self.assertEqual(
            parsed_object_key("announcement-layout-v1", digest),
            (
                "announcements/parsed/announcement-layout-v1/"
                f"ab/{digest}.json.gz"
            ),
        )

    def config(self, root: Path) -> dict[str, object]:
        return {
            "artifact_store": {
                "production_kind": "oss",
                "development_kind": "local",
                "endpoint": "https://oss-cn-beijing.aliyuncs.com",
                "bucket": "stock-analyze",
                "key_prefix": "announcements",
                "credential_env": {
                    "access_key_id_file": "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE",
                    "access_key_secret_file":
                        "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE",
                },
                "local_root": str(root / "artifacts"),
            }
        }

    def test_production_requires_both_file_environment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            with self.assertRaisesRegex(
                BlobStoreConfigurationError,
                (
                    "^intelligence_oss_unavailable:missing_env:"
                    "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE,"
                    "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE$"
                ),
            ):
                build_blob_store(self.config(Path(tmp)), production=True)

    def test_factory_reads_credentials_only_from_referenced_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_id_path = root / "access-key-id"
            secret_path = root / "access-key-secret"
            key_id_path.write_text("id-from-file\n", encoding="utf-8")
            secret_path.write_text("secret-from-file\n", encoding="utf-8")
            captured: dict[str, str] = {}

            def factory(
                endpoint: str,
                bucket: str,
                access_key_id: str,
                access_key_secret: str,
            ) -> FakeOssBucket:
                captured.update(
                    endpoint=endpoint,
                    bucket=bucket,
                    access_key_id=access_key_id,
                    access_key_secret=access_key_secret,
                )
                return FakeOssBucket()

            with mock.patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE":
                        str(key_id_path),
                    "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE":
                        str(secret_path),
                },
                clear=True,
            ):
                store = build_blob_store(
                    self.config(root),
                    production=True,
                    oss_bucket_factory=factory,
                )

            self.assertIsInstance(store, OssBlobStore)
            self.assertEqual(captured["access_key_id"], "id-from-file")
            self.assertEqual(
                captured["access_key_secret"],
                "secret-from-file",
            )

    def test_factory_uses_access_point_alias_without_changing_uri_bucket(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_id_path = root / "access-key-id"
            secret_path = root / "access-key-secret"
            key_id_path.write_text("id-from-file\n", encoding="utf-8")
            secret_path.write_text(
                "secret-from-file\n",
                encoding="utf-8",
            )
            config = self.config(root)
            artifact = config["artifact_store"]
            assert isinstance(artifact, dict)
            artifact["bucket"] = "stock-analyze-hz"
            artifact["access_point_alias"] = (
                "stock-analyze-hz-example-ossalias"
            )
            captured: dict[str, str] = {}

            def factory(
                endpoint: str,
                bucket: str,
                access_key_id: str,
                access_key_secret: str,
            ) -> FakeOssBucket:
                captured.update(endpoint=endpoint, bucket=bucket)
                return FakeOssBucket()

            with mock.patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE":
                        str(key_id_path),
                    "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE":
                        str(secret_path),
                },
                clear=True,
            ):
                store = build_blob_store(
                    config,
                    production=True,
                    oss_bucket_factory=factory,
                )

            self.assertEqual(
                captured["bucket"],
                "stock-analyze-hz-example-ossalias",
            )
            self.assertEqual(store.bucket_name, "stock-analyze-hz")

    def test_missing_or_empty_secret_files_use_the_missing_env_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty_secret = root / "empty-secret"
            empty_secret.write_text("\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE":
                        str(root / "does-not-exist"),
                    "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE":
                        str(empty_secret),
                },
                clear=True,
            ):
                with self.assertRaisesRegex(
                    BlobStoreConfigurationError,
                    (
                        "^intelligence_oss_unavailable:missing_env:"
                        "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE,"
                        "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE$"
                    ),
                ):
                    build_blob_store(
                        self.config(root),
                        production=True,
                    )

    def test_secret_values_are_not_in_wrapped_factory_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_id_path = root / "id"
            secret_path = root / "secret"
            key_id_path.write_text("visible-id", encoding="utf-8")
            secret_path.write_text("never-leak-this", encoding="utf-8")

            def failing_factory(*_args: object) -> FakeOssBucket:
                raise RuntimeError(
                    "provider echoed never-leak-this and visible-id"
                )

            with mock.patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE":
                        str(key_id_path),
                    "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE":
                        str(secret_path),
                },
                clear=True,
            ):
                with self.assertRaises(
                    BlobStoreConfigurationError,
                ) as raised:
                    build_blob_store(
                        self.config(root),
                        production=True,
                        oss_bucket_factory=failing_factory,
                    )

            message = str(raised.exception)
            self.assertNotIn("never-leak-this", message)
            self.assertNotIn("visible-id", message)
            self.assertIsNone(raised.exception.__cause__)

    def test_development_factory_does_not_require_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            store = build_blob_store(
                self.config(Path(tmp)),
                production=False,
            )
            self.assertIsInstance(store, LocalBlobStore)

    def test_config_rejects_non_file_credential_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            artifact = config["artifact_store"]
            assert isinstance(artifact, dict)
            credentials = artifact["credential_env"]
            assert isinstance(credentials, dict)
            credentials["access_key_id_file"] = "OSS_ACCESS_KEY_ID"

            with self.assertRaisesRegex(
                BlobStoreConfigurationError,
                "^intelligence_oss_unavailable:credential_env_not_file:",
            ):
                build_blob_store(config, production=True)


if __name__ == "__main__":
    unittest.main()
