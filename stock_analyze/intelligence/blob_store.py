"""Immutable content-addressed storage for intelligence artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.parse import urlsplit


_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_BUCKET_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BlobStoreError(RuntimeError):
    """Base class for object-store failures safe to expose to operators."""


class BlobStoreConfigurationError(BlobStoreError):
    """Raised for fail-closed production configuration errors."""


class BlobConflictError(BlobStoreError):
    """Raised when an immutable key already contains different content."""


class BlobStore(Protocol):
    def put_if_absent(
        self,
        key: str,
        payload: bytes,
        content_type: str,
    ) -> str:
        ...

    def exists(self, uri: str) -> bool:
        ...

    def read(self, uri: str) -> bytes:
        ...


def _content_hash(payload: bytes) -> str:
    return hashlib.sha256(bytes(payload)).hexdigest()


def _validated_sha256(value: str) -> str:
    normalized = str(value).strip().casefold()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("intelligence_blob_sha256_invalid")
    return normalized


def pdf_object_key(payload_or_hash: bytes | str) -> str:
    digest = (
        _content_hash(payload_or_hash)
        if isinstance(payload_or_hash, bytes)
        else _validated_sha256(payload_or_hash)
    )
    return f"announcements/pdf/{digest[:2]}/{digest}.pdf"


def parsed_object_key(parser_version: str, content_hash: str) -> str:
    version = str(parser_version).strip()
    if (
        not version
        or "/" in version
        or not re.fullmatch(r"[A-Za-z0-9._-]+", version)
    ):
        raise ValueError("intelligence_blob_parser_version_invalid")
    digest = _validated_sha256(content_hash)
    return (
        f"announcements/parsed/{version}/"
        f"{digest[:2]}/{digest}.json.gz"
    )


def _validate_prefix(value: str) -> str:
    prefix = str(value).strip().strip("/")
    if not prefix:
        raise ValueError("intelligence_blob_prefix_invalid")
    _validate_key(f"{prefix}/placeholder", prefix=None)
    return prefix


def _validate_key(key: str, *, prefix: str | None) -> str:
    normalized = str(key).strip()
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.endswith("/")
        or "\\" in normalized
        or "%" in normalized
        or "?" in normalized
        or "#" in normalized
        or not _KEY_PATTERN.fullmatch(normalized)
    ):
        raise ValueError("intelligence_blob_key_invalid")
    parts = PurePosixPath(normalized).parts
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or str(PurePosixPath(*parts)) != normalized
    ):
        raise ValueError("intelligence_blob_key_invalid")
    if prefix is not None and not (
        normalized == prefix or normalized.startswith(f"{prefix}/")
    ):
        raise ValueError("intelligence_blob_key_outside_prefix")
    return normalized


def _validate_content_type(value: str) -> str:
    normalized = str(value).strip().casefold()
    if (
        not normalized
        or "\r" in normalized
        or "\n" in normalized
        or len(normalized) > 255
    ):
        raise ValueError("intelligence_blob_content_type_invalid")
    return normalized


class LocalBlobStore:
    """Filesystem implementation with immutable, atomically published blobs."""

    _AUTHORITY = "artifacts"

    def __init__(
        self,
        root: str | Path,
        *,
        key_prefix: str = "announcements",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.key_prefix = _validate_prefix(key_prefix)
        self._lock_root = self.root / ".locks"
        self._lock_root.mkdir(parents=True, exist_ok=True)

    def _uri(self, key: str) -> str:
        return f"localblob://{self._AUTHORITY}/{key}"

    def _key_from_uri(self, uri: str) -> str:
        try:
            parsed = urlsplit(str(uri))
        except ValueError as exc:
            raise ValueError("intelligence_blob_uri_invalid") from exc
        if (
            parsed.scheme != "localblob"
            or parsed.netloc != self._AUTHORITY
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
            or "%" in parsed.path
        ):
            raise ValueError("intelligence_blob_uri_invalid")
        try:
            return _validate_key(
                parsed.path[1:],
                prefix=self.key_prefix,
            )
        except ValueError as exc:
            raise ValueError("intelligence_blob_uri_invalid") from exc

    def path_for_uri(self, uri: str) -> Path:
        key = self._key_from_uri(uri)
        candidate = (self.root / key).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("intelligence_blob_uri_invalid") from exc
        return candidate

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.metadata.json")

    @contextmanager
    def _key_lock(self, key: str) -> Iterator[None]:
        lock_name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        lock_path = self._lock_root / f"{lock_name}.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _metadata_payload(
        self,
        payload: bytes,
        content_type: str,
    ) -> bytes:
        return json.dumps(
            {
                "byte_size": len(payload),
                "content_type": content_type,
                "sha256": _content_hash(payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _validate_existing(
        self,
        path: Path,
        *,
        expected_payload: bytes | None = None,
        expected_content_type: str | None = None,
        repair_missing_metadata: bool = False,
    ) -> bytes:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise BlobStoreError(
                "intelligence_blob_local_error:read"
            ) from exc
        metadata_path = self._metadata_path(path)
        try:
            metadata = json.loads(metadata_path.read_text("utf-8"))
        except FileNotFoundError:
            if (
                repair_missing_metadata
                and expected_payload is not None
                and expected_content_type is not None
                and payload == expected_payload
            ):
                self._atomic_write(
                    metadata_path,
                    self._metadata_payload(
                        payload,
                        expected_content_type,
                    ),
                )
                metadata = {
                    "byte_size": len(payload),
                    "content_type": expected_content_type,
                    "sha256": _content_hash(payload),
                }
            else:
                raise BlobConflictError(
                    "intelligence_blob_conflict:metadata_missing"
                )
        except (OSError, ValueError, TypeError) as exc:
            raise BlobConflictError(
                "intelligence_blob_conflict:metadata_invalid"
            ) from exc
        actual_hash = _content_hash(payload)
        if (
            not isinstance(metadata, dict)
            or metadata.get("sha256") != actual_hash
            or metadata.get("byte_size") != len(payload)
            or not isinstance(metadata.get("content_type"), str)
        ):
            raise BlobConflictError(
                "intelligence_blob_conflict:metadata_mismatch"
            )
        if expected_payload is not None and payload != expected_payload:
            raise BlobConflictError(
                "intelligence_blob_conflict:payload_mismatch"
            )
        if (
            expected_content_type is not None
            and metadata["content_type"] != expected_content_type
        ):
            raise BlobConflictError(
                "intelligence_blob_conflict:content_type_mismatch"
            )
        return payload

    def put_if_absent(
        self,
        key: str,
        payload: bytes,
        content_type: str,
    ) -> str:
        normalized_key = _validate_key(key, prefix=self.key_prefix)
        normalized_payload = bytes(payload)
        normalized_type = _validate_content_type(content_type)
        uri = self._uri(normalized_key)
        path = self.path_for_uri(uri)
        with self._key_lock(normalized_key):
            if path.exists():
                self._validate_existing(
                    path,
                    expected_payload=normalized_payload,
                    expected_content_type=normalized_type,
                    repair_missing_metadata=True,
                )
                return uri
            path.parent.mkdir(parents=True, exist_ok=True)
            blob_handle = tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            )
            temporary = Path(blob_handle.name)
            try:
                with blob_handle:
                    blob_handle.write(normalized_payload)
                    blob_handle.flush()
                    os.fsync(blob_handle.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    self._validate_existing(
                        path,
                        expected_payload=normalized_payload,
                        expected_content_type=normalized_type,
                        repair_missing_metadata=True,
                    )
                    return uri
                self._atomic_write(
                    self._metadata_path(path),
                    self._metadata_payload(
                        normalized_payload,
                        normalized_type,
                    ),
                )
            except BlobStoreError:
                raise
            except OSError as exc:
                raise BlobStoreError(
                    "intelligence_blob_local_error:put"
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
        return uri

    def exists(self, uri: str) -> bool:
        path = self.path_for_uri(uri)
        if not path.exists():
            return False
        self._validate_existing(path)
        return True

    def read(self, uri: str) -> bytes:
        path = self.path_for_uri(uri)
        if not path.exists():
            raise BlobStoreError("intelligence_blob_not_found")
        return self._validate_existing(path)


def _exception_status(error: Exception) -> int | None:
    for attribute in ("status", "status_code", "http_status"):
        value = getattr(error, attribute, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _header(headers: Mapping[str, Any], name: str) -> str:
    target = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == target:
            return str(value)
    return ""


class OssBlobStore:
    """Aliyun OSS implementation using conditional immutable writes."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket_name: str,
        key_prefix: str,
        bucket_client: Any,
    ) -> None:
        parsed_endpoint = urlsplit(str(endpoint))
        try:
            endpoint_port = parsed_endpoint.port
        except ValueError as exc:
            raise ValueError("intelligence_oss_endpoint_invalid") from exc
        if (
            parsed_endpoint.scheme != "https"
            or not parsed_endpoint.hostname
            or parsed_endpoint.username is not None
            or parsed_endpoint.password is not None
            or endpoint_port not in {None, 443}
            or parsed_endpoint.path not in {"", "/"}
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise ValueError("intelligence_oss_endpoint_invalid")
        normalized_bucket = str(bucket_name).strip()
        if not _BUCKET_PATTERN.fullmatch(normalized_bucket):
            raise ValueError("intelligence_oss_bucket_invalid")
        self.endpoint = str(endpoint).rstrip("/")
        self.bucket_name = normalized_bucket
        self.key_prefix = _validate_prefix(key_prefix)
        self._bucket = bucket_client

    def _uri(self, key: str) -> str:
        return f"oss://{self.bucket_name}/{key}"

    def _key_from_uri(self, uri: str) -> str:
        try:
            parsed = urlsplit(str(uri))
        except ValueError as exc:
            raise ValueError("intelligence_blob_uri_invalid") from exc
        if (
            parsed.scheme != "oss"
            or parsed.netloc != self.bucket_name
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
            or "%" in parsed.path
        ):
            raise ValueError("intelligence_blob_uri_invalid")
        try:
            return _validate_key(
                parsed.path[1:],
                prefix=self.key_prefix,
            )
        except ValueError as exc:
            raise ValueError("intelligence_blob_uri_invalid") from exc

    @staticmethod
    def _safe_error(operation: str, error: Exception) -> BlobStoreError:
        status = _exception_status(error)
        suffix = f":status_{status}" if status is not None else ""
        return BlobStoreError(
            f"intelligence_oss_error:{operation}{suffix}"
        )

    def _get_payload(self, key: str) -> bytes:
        try:
            result = self._bucket.get_object(key)
            return bytes(result.read())
        except Exception as exc:
            if isinstance(exc, BlobStoreError):
                raise
            if _exception_status(exc) == 404:
                raise BlobStoreError(
                    "intelligence_blob_not_found"
                ) from None
            raise self._safe_error("read", exc) from None

    def _validate_existing(
        self,
        key: str,
        *,
        expected_payload: bytes | None = None,
        expected_content_type: str | None = None,
    ) -> bytes:
        try:
            head = self._bucket.head_object(key)
        except Exception as exc:
            if _exception_status(exc) == 404:
                raise BlobConflictError(
                    "intelligence_blob_conflict:missing_after_write"
                ) from None
            raise self._safe_error("head", exc) from None
        payload = self._get_payload(key)
        headers = getattr(head, "headers", {}) or {}
        content_type = str(
            getattr(head, "content_type", "")
            or _header(headers, "Content-Type")
        ).strip().casefold()
        content_length = getattr(head, "content_length", None)
        sha256 = _header(headers, "x-oss-meta-sha256").casefold()
        actual_hash = _content_hash(payload)
        try:
            normalized_length = int(content_length)
        except (TypeError, ValueError):
            normalized_length = -1
        if (
            normalized_length != len(payload)
            or sha256 != actual_hash
            or not content_type
        ):
            raise BlobConflictError(
                "intelligence_blob_conflict:metadata_mismatch"
            )
        if expected_payload is not None and payload != expected_payload:
            raise BlobConflictError(
                "intelligence_blob_conflict:payload_mismatch"
            )
        if (
            expected_content_type is not None
            and content_type != expected_content_type
        ):
            raise BlobConflictError(
                "intelligence_blob_conflict:content_type_mismatch"
            )
        return payload

    def put_if_absent(
        self,
        key: str,
        payload: bytes,
        content_type: str,
    ) -> str:
        normalized_key = _validate_key(key, prefix=self.key_prefix)
        normalized_payload = bytes(payload)
        normalized_type = _validate_content_type(content_type)
        digest = _content_hash(normalized_payload)
        headers = {
            "Content-Type": normalized_type,
            "If-None-Match": "*",
            "x-oss-forbid-overwrite": "true",
            "x-oss-meta-byte-size": str(len(normalized_payload)),
            "x-oss-meta-sha256": digest,
        }
        try:
            self._bucket.put_object(
                normalized_key,
                normalized_payload,
                headers=headers,
            )
        except Exception as exc:
            if _exception_status(exc) not in {409, 412}:
                raise self._safe_error("put", exc) from None
        self._validate_existing(
            normalized_key,
            expected_payload=normalized_payload,
            expected_content_type=normalized_type,
        )
        return self._uri(normalized_key)

    def exists(self, uri: str) -> bool:
        key = self._key_from_uri(uri)
        try:
            self._bucket.head_object(key)
            return True
        except Exception as exc:
            if _exception_status(exc) == 404:
                return False
            raise self._safe_error("head", exc) from None

    def read(self, uri: str) -> bytes:
        key = self._key_from_uri(uri)
        return self._validate_existing(key)


OssBucketFactory = Callable[[str, str, str, str], Any]


def _artifact_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    candidate = config.get("artifact_store", config)
    if not isinstance(candidate, Mapping):
        raise BlobStoreConfigurationError(
            "intelligence_blob_config_invalid:artifact_store"
        )
    return candidate


def _read_secret_file(environment_name: str) -> str | None:
    file_value = os.environ.get(environment_name, "").strip()
    if not file_value:
        return None
    try:
        value = Path(file_value).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value:
        return None
    return value


def _default_oss_bucket_factory(
    endpoint: str,
    bucket: str,
    access_key_id: str,
    access_key_secret: str,
) -> Any:
    try:
        import oss2

        return oss2.Bucket(
            oss2.Auth(access_key_id, access_key_secret),
            endpoint,
            bucket,
        )
    except Exception:
        raise BlobStoreConfigurationError(
            "intelligence_oss_unavailable:client_initialization_failed"
        ) from None


def build_blob_store(
    config: Mapping[str, Any],
    production: bool = True,
    *,
    oss_bucket_factory: OssBucketFactory | None = None,
) -> BlobStore:
    artifact = _artifact_config(config)
    kind_key = "production_kind" if production else "development_kind"
    kind = str(artifact.get(kind_key) or "").strip().casefold()
    key_prefix = str(
        artifact.get("key_prefix") or "announcements"
    ).strip()
    if not production and kind == "local":
        local_root = str(artifact.get("local_root") or "").strip()
        if not local_root:
            raise BlobStoreConfigurationError(
                "intelligence_blob_config_invalid:local_root"
            )
        return LocalBlobStore(local_root, key_prefix=key_prefix)
    if kind != "oss":
        raise BlobStoreConfigurationError(
            f"intelligence_blob_config_invalid:kind:{kind or 'missing'}"
        )
    credential_config = artifact.get("credential_env")
    if not isinstance(credential_config, Mapping):
        raise BlobStoreConfigurationError(
            "intelligence_blob_config_invalid:credential_env"
        )
    names = [
        str(credential_config.get("access_key_id_file") or "").strip(),
        str(
            credential_config.get("access_key_secret_file") or ""
        ).strip(),
    ]
    invalid_names = [
        name or "missing"
        for name in names
        if not name or not name.endswith("_FILE")
    ]
    if invalid_names:
        raise BlobStoreConfigurationError(
            "intelligence_oss_unavailable:credential_env_not_file:"
            + ",".join(invalid_names)
        )
    secret_values = [_read_secret_file(name) for name in names]
    missing = [
        name
        for name, value in zip(names, secret_values)
        if value is None
    ]
    if missing:
        raise BlobStoreConfigurationError(
            "intelligence_oss_unavailable:missing_env:"
            + ",".join(missing)
        )
    access_key_id, access_key_secret = secret_values
    assert access_key_id is not None
    assert access_key_secret is not None
    factory = oss_bucket_factory or _default_oss_bucket_factory
    logical_bucket = str(artifact.get("bucket") or "").strip()
    client_bucket = str(
        artifact.get("access_point_alias") or logical_bucket
    ).strip()
    if not _BUCKET_PATTERN.fullmatch(client_bucket):
        raise BlobStoreConfigurationError(
            "intelligence_oss_unavailable:access_point_alias_invalid"
        )
    try:
        bucket_client = factory(
            str(artifact.get("endpoint") or "").strip(),
            client_bucket,
            access_key_id,
            access_key_secret,
        )
    except BlobStoreConfigurationError:
        raise
    except Exception:
        raise BlobStoreConfigurationError(
            "intelligence_oss_unavailable:client_initialization_failed"
        ) from None
    return OssBlobStore(
        endpoint=str(artifact.get("endpoint") or "").strip(),
        bucket_name=logical_bucket,
        key_prefix=key_prefix,
        bucket_client=bucket_client,
    )
