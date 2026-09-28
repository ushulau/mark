"""Confluence REST API client (v1) for mark.

Mirrors the Go implementation's ``confluence`` package
(``mark-research/confluence/api.go``) for the core operations: finding,
creating and updating pages, attachments, and labels. Authentication follows
the same rules: ``username`` + ``password``/API-token uses HTTP Basic auth,
while a token *without* a username (a Personal Access Token) uses
``Authorization: Bearer``.

Only the standard library is used (``urllib``), so the client works without
installing ``requests``.

Currently out of scope: the ``api.atlassian.com`` gateway / v2 API (scoped
tokens), folders, content properties, comments, page restrictions and moves.
Gateway base URLs fail fast with an explanatory error.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


RETRY_STATUSES = (429, 503)


class ConfluenceError(Exception):
    """A Confluence API call failed."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str = "",
        url: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url


def _cache_key(space: str, title: str, page_type: str) -> str:
    return f"{space}\x00{title}\x00{page_type}"


def _encode_multipart(
    fields: dict[str, str],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = "----mark" + hashlib.sha256(os.urandom(16)).hexdigest()[:24]
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    for field_name, filename, data, content_type in files:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class ConfluenceClient:
    """Minimal Confluence v1 client used by ``mark sync``."""

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        *,
        insecure_skip_tls_verify: bool = False,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        if not base_url:
            raise ConfluenceError("base-url is required")
        self.base_url = base_url.rstrip("/")
        self.v1 = self.base_url + "/rest/api"
        self.username = username
        self.password = password
        self.timeout = timeout
        self.max_retries = max_retries
        self.gateway = "api.atlassian.com" in self.base_url
        self.keep_concurrent_edits = False
        self._ssl_context = (
            ssl._create_unverified_context()  # noqa: S323
            if insecure_skip_tls_verify
            else None
        )
        self._page_cache: dict[str, dict[str, Any] | None] = {}

    # -- low-level HTTP -----------------------------------------------------
    def _auth_headers(self) -> dict[str, str]:
        if self.username:
            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            return {"Authorization": f"Basic {token}"}
        if self.password:
            return {"Authorization": f"Bearer {self.password}"}
        return {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        what: str = "request",
    ) -> tuple[int, Any, dict[str, str]]:
        url = self.v1 + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = data
        request_headers = dict(self._auth_headers())
        if headers:
            request_headers.update(headers)
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        last_error: ConfluenceError | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url, data=body, headers=request_headers, method=method
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self._ssl_context
                ) as response:
                    status = response.status
                    payload = response.read()
                    response_headers = dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    payload = exc.read()
                except OSError:
                    payload = b""
                response_headers = dict((exc.headers or {}).items())
                if status in RETRY_STATUSES and attempt < self.max_retries:
                    delay = self._retry_delay(response_headers, attempt)
                    time.sleep(delay)
                    continue
                raise self._as_error(
                    status, payload, url, f"{what} failed", response_headers
                ) from None
            except (urllib.error.URLError, OSError) as exc:
                last_error = ConfluenceError(
                    f"{what} failed: {exc}", url=url
                )
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise last_error from None

            if status in RETRY_STATUSES and attempt < self.max_retries:
                time.sleep(self._retry_delay(response_headers, attempt))
                continue
            return status, self._decode(payload, response_headers), response_headers
        raise last_error or ConfluenceError(f"{what} failed", url=url)  # pragma: no cover

    @staticmethod
    def _retry_delay(headers: dict[str, str], attempt: int) -> float:
        for name, value in headers.items():
            if name.lower() == "retry-after":
                try:
                    return max(0.0, float(value))
                except ValueError:
                    break
        return float(2 ** attempt)

    @staticmethod
    def _decode(payload: bytes, headers: dict[str, str]) -> Any:
        content_type = ""
        for name, value in headers.items():
            if name.lower() == "content-type":
                content_type = value
                break
        if "json" in content_type:
            try:
                return json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
        return payload

    @staticmethod
    def _as_error(
        status: int,
        payload: bytes,
        url: str,
        what: str,
        headers: dict[str, str],
    ) -> ConfluenceError:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = repr(payload[:500])
        detail = text
        if "json" in str(headers.get("Content-Type", "")).lower():
            try:
                parsed = json.loads(text)
                detail = parsed.get("message", text) if isinstance(parsed, dict) else text
            except json.JSONDecodeError:
                pass
        return ConfluenceError(
            f"{what}: HTTP {status}: {detail[:500]}",
            status=status,
            body=text[:2000],
            url=url,
        )

    def _require_v1(self, what: str) -> None:
        if self.gateway:
            raise ConfluenceError(
                f"{what} through the api.atlassian.com gateway needs the v2 API, "
                "which this client does not implement yet; use a site base URL "
                "(https://<tenant>.atlassian.net/wiki) with an API token instead"
            )

    # -- pages ----------------------------------------------------------------
    def find_page(
        self, space: str, title: str, page_type: str = "page"
    ) -> dict[str, Any] | None:
        """Live page by title, or None. Results are cached per run."""
        self._require_v1(f"find page {title!r} in space {space}")
        key = _cache_key(space, title, page_type)
        if key in self._page_cache:
            cached = self._page_cache[key]
            return dict(cached) if cached is not None else None
        params = {
            "spaceKey": space,
            "expand": "ancestors,version",
            "type": page_type,
            "status": "current",
        }
        if title:
            params["title"] = title
        try:
            status, result, _ = self._request(
                "GET", "/content/",
                params=params,
                what=f"find page {title!r} in space {space}",
            )
        except ConfluenceError as exc:
            if exc.status == 404:
                self._page_cache[key] = None
                return None
            raise
        if status == 404 or not isinstance(result, dict) or not result.get("results"):
            self._page_cache[key] = None
            return None
        page = result["results"][0]
        if not page.get("_links", {}).get("base"):
            page.setdefault("_links", {})["base"] = self.base_url
        self._page_cache[key] = page
        # Canonical key: Confluence matches titles case-insensitively.
        canonical = _cache_key(space, page.get("title", title), page.get("type", page_type))
        self._page_cache.setdefault(canonical, page)
        return dict(page)

    def invalidate(self, space: str, title: str, page_type: str = "page") -> None:
        self._page_cache.pop(_cache_key(space, title, page_type), None)

    def get_page_by_id(
        self, page_id: str, expand: str = "ancestors,version,body.storage"
    ) -> dict[str, Any]:
        self._require_v1(f"read page {page_id}")
        status, result, _ = self._request(
            "GET", f"/content/{page_id}",
            params={"expand": expand},
            what=f"read page {page_id}",
        )
        if status != 200 or not isinstance(result, dict):
            raise ConfluenceError(
                f"read page {page_id}: unexpected response HTTP {status}",
                status=status,
                url=self.v1 + f"/content/{page_id}",
            )
        return result

    def create_page(
        self,
        space: str,
        title: str,
        body: str,
        page_type: str = "page",
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_v1(f"create page {title!r} in space {space}")
        payload: dict[str, Any] = {
            "type": page_type,
            "title": title,
            "space": {"key": space},
            "body": {"storage": {"representation": "storage", "value": body}},
            "metadata": {"properties": {"editor": {"value": "v2"}}},
        }
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]
        status, result, _ = self._request(
            "POST", "/content/",
            json_body=payload,
            what=f"create page {title!r} in space {space}",
        )
        if status != 200 or not isinstance(result, dict):
            raise ConfluenceError(
                f"create page {title!r} in space {space}: "
                f"unexpected response HTTP {status}",
                status=status,
                url=self.v1 + "/content/",
            )
        self._page_cache[_cache_key(space, title, page_type)] = result
        return result

    def update_page(
        self,
        page: dict[str, Any],
        content: str,
        *,
        minor_edit: bool = False,
        version_message: str = "",
        appearance: str = "full-width",
        emoji: str = "",
    ) -> dict[str, Any]:
        """Update a page's content; retries a 409 conflict once, afresh."""
        self._require_v1(f"update page {page.get('title')!r} ({page.get('id')})")
        version = int(((page.get("version") or {}).get("number")) or 0)
        try:
            return self._put_page(
                page, content, version + 1, minor_edit, version_message,
                appearance, emoji,
            )
        except ConfluenceError as exc:
            if exc.status != 409:
                raise
            current = self.get_page_by_id(str(page["id"]), expand="version")
            current_version = int((current.get("version") or {}).get("number") or 0)
            if self.keep_concurrent_edits and current_version != version:
                raise ConfluenceError(
                    f"page {page.get('title')!r} ({page.get('id')}) was edited in "
                    f"Confluence while publishing (now at version {current_version}, "
                    f"mark read version {version}); refusing to overwrite"
                ) from exc
            try:
                return self._put_page(
                    page, content, current_version + 1, minor_edit,
                    version_message, appearance, emoji,
                )
            except ConfluenceError as retry_exc:
                if retry_exc.status == 409:
                    raise ConfluenceError(
                        f"update of page {page.get('title')!r} ({page.get('id')}) "
                        "conflicted twice; something else is writing to the page, "
                        "run mark again once it has stopped"
                    ) from retry_exc
                raise

    def _put_page(
        self,
        page: dict[str, Any],
        content: str,
        next_version: int,
        minor_edit: bool,
        version_message: str,
        appearance: str,
        emoji: str = "",
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "content-appearance-published": {"value": appearance},
        }
        if emoji:
            codepoint = f"{ord(emoji[0]):x}"
            properties["emoji-title-draft"] = {"value": codepoint}
            properties["emoji-title-published"] = {"value": codepoint}
        payload: dict[str, Any] = {
            "id": page["id"],
            "type": page.get("type", "page"),
            "title": page["title"],
            "version": {
                "number": next_version,
                "minorEdit": minor_edit,
                "message": version_message,
            },
            "body": {"storage": {"value": content, "representation": "storage"}},
            "metadata": {"properties": properties},
        }
        ancestors = page.get("ancestors") or []
        if page.get("type") != "blogpost" and ancestors:
            payload["ancestors"] = [{"id": ancestors[-1]["id"]}]
        status, result, _ = self._request(
            "PUT", f"/content/{page['id']}",
            json_body=payload,
            what=f"update page {page.get('title')!r} ({page.get('id')})",
        )
        if status != 200:
            raise ConfluenceError(
                f"update page {page.get('title')!r} ({page.get('id')}): "
                f"unexpected response HTTP {status}",
                status=status,
                url=self.v1 + f"/content/{page['id']}",
            )
        return result if isinstance(result, dict) else {}

    def delete_page(self, page_id: str) -> None:
        """Move a page to the trash."""
        self._require_v1(f"delete page {page_id}")
        status, _, _ = self._request(
            "DELETE", f"/content/{page_id}", what=f"delete page {page_id}"
        )
        if status not in (200, 204):
            raise ConfluenceError(
                f"delete page {page_id}: unexpected response HTTP {status}",
                status=status,
                url=self.v1 + f"/content/{page_id}",
            )

    # -- attachments --------------------------------------------------------------
    def get_attachments(self, page_id: str) -> list[dict[str, Any]]:
        self._require_v1(f"list attachments of page {page_id}")
        found: list[dict[str, Any]] = []
        start = 0
        page_size = 100
        while True:
            status, result, _ = self._request(
                "GET", f"/content/{page_id}/child/attachment",
                params={
                    "expand": "version,container",
                    "limit": str(page_size),
                    "start": str(start),
                },
                what=f"list attachments of page {page_id}",
            )
            if status != 200 or not isinstance(result, dict):
                raise ConfluenceError(
                    f"list attachments of page {page_id}: "
                    f"unexpected response HTTP {status}",
                    status=status,
                    url=self.v1 + f"/content/{page_id}/child/attachment",
                )
            results = result.get("results", [])
            found.extend(results)
            links = result.get("_links", {}) or {}
            if not results or (not links.get("next") and len(results) < page_size):
                break
            start += len(results)
        return found

    def _attachment_request(
        self, path: str, filename: str, comment: str, data: bytes, what: str
    ) -> dict[str, Any]:
        content_type = (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        body, multipart_type = _encode_multipart(
            {"comment": comment, "minorEdit": "true"},
            [("file", os.path.basename(filename), data, content_type)],
        )
        status, result, _ = self._request(
            "POST", path,
            data=body,
            headers={
                "Content-Type": multipart_type,
                "X-Atlassian-Token": "no-check",
            },
            what=what,
        )
        if status != 200:
            raise ConfluenceError(
                f"{what}: unexpected response HTTP {status}",
                status=status,
                url=self.v1 + path,
            )
        if isinstance(result, dict) and result.get("results"):
            return result["results"][0]
        if isinstance(result, dict):
            return result  # short variant some instances return
        raise ConfluenceError(
            f"{what}: the Confluence REST API returned 0 json objects, "
            "expected at least 1",
            status=status,
            url=self.v1 + path,
        )

    def create_attachment(
        self, page_id: str, filename: str, comment: str, data: bytes
    ) -> dict[str, Any]:
        self._require_v1(f"attach {filename!r} to page {page_id}")
        return self._attachment_request(
            f"/content/{page_id}/child/attachment",
            filename,
            comment,
            data,
            f"attach {filename!r} to page {page_id}",
        )

    def update_attachment(
        self, page_id: str, attachment_id: str, filename: str, comment: str, data: bytes
    ) -> dict[str, Any]:
        self._require_v1(
            f"upload a new version of attachment {filename!r} of page {page_id}"
        )
        return self._attachment_request(
            f"/content/{page_id}/child/attachment/{attachment_id}/data",
            filename,
            comment,
            data,
            f"upload a new version of attachment {filename!r} of page {page_id}",
        )

    def download_attachment(self, attachment: dict[str, Any]) -> bytes:
        links = attachment.get("_links", {}) or {}
        download = links.get("download", "")
        if not download:
            raise ConfluenceError(
                f"attachment {attachment.get('title')!r} has no download link"
            )
        url = download if download.startswith("http") else self.base_url + download
        request = urllib.request.Request(url, headers=self._auth_headers(), method="GET")
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self._ssl_context
            ) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise ConfluenceError(
                f"download attachment {attachment.get('title')!r}: HTTP {exc.code}",
                status=exc.code,
                url=url,
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ConfluenceError(
                f"download attachment {attachment.get('title')!r}: {exc}", url=url
            ) from None

    def ensure_attachment(
        self, page_id: str, local_path: str, comment: str = ""
    ) -> tuple[dict[str, Any], bool]:
        """Upload ``local_path`` unless an identical attachment exists.

        Returns ``(info, changed)``; ``changed`` is False when the remote
        bytes already match (sha256), so runs stay quiet and version-clean.
        """
        filename = os.path.basename(local_path)
        with open(local_path, "rb") as handle:
            data = handle.read()
        for existing in self.get_attachments(page_id):
            if existing.get("title") != filename:
                continue
            try:
                remote = self.download_attachment(existing)
            except ConfluenceError:
                remote = None
            if remote is not None and hashlib.sha256(remote).digest() == hashlib.sha256(
                data
            ).digest():
                return existing, False
            info = self.update_attachment(
                page_id, str(existing.get("id")), filename, comment, data
            )
            return info, True
        info = self.create_attachment(page_id, filename, comment, data)
        return info, True

    # -- labels ------------------------------------------------------------------
    def get_labels(self, page_id: str, prefix: str = "global") -> list[str]:
        self._require_v1(f"read labels of page {page_id}")
        names: list[str] = []
        start = 0
        page_size = 50
        while True:
            status, result, _ = self._request(
                "GET", f"/content/{page_id}/label",
                params={
                    "prefix": prefix,
                    "limit": str(page_size),
                    "start": str(start),
                },
                what=f"read labels of page {page_id}",
            )
            if status != 200 or not isinstance(result, dict):
                raise ConfluenceError(
                    f"read labels of page {page_id}: unexpected response HTTP {status}",
                    status=status,
                    url=self.v1 + f"/content/{page_id}/label",
                )
            results = result.get("results", [])
            names.extend(label.get("name", "") for label in results)
            links = result.get("_links", {}) or {}
            if not results or (not links.get("next") and len(results) < page_size):
                break
            start += len(results)
        return [name for name in names if name]

    def add_labels(self, page_id: str, labels: list[str]) -> None:
        labels = [label for label in labels if label]
        if not labels:
            return
        self._require_v1(f"add labels to page {page_id}")
        payload = [{"prefix": "global", "name": label} for label in labels]
        status, _, _ = self._request(
            "POST", f"/content/{page_id}/label",
            json_body=payload,
            what=f"add labels to page {page_id}",
        )
        if status != 200:
            raise ConfluenceError(
                f"add labels to page {page_id}: unexpected response HTTP {status}",
                status=status,
                url=self.v1 + f"/content/{page_id}/label",
            )

    def delete_label(self, page_id: str, label: str) -> None:
        self._require_v1(f"remove label {label!r} from page {page_id}")
        status, _, _ = self._request(
            "DELETE", f"/content/{page_id}/label",
            params={"name": label},
            what=f"remove label {label!r} from page {page_id}",
        )
        if status not in (200, 204):
            raise ConfluenceError(
                f"remove label {label!r} from page {page_id}: "
                f"unexpected response HTTP {status}",
                status=status,
                url=self.v1 + f"/content/{page_id}/label",
            )

    def sync_labels(
        self, page_id: str, labels: list[str], *, append: bool = False
    ) -> None:
        """Make the page carry exactly ``labels`` (or add them with append)."""
        wanted = [label for label in dict.fromkeys(labels) if label]
        if append:
            current = set(self.get_labels(page_id))
            self.add_labels(page_id, [label for label in wanted if label not in current])
            return
        current = self.get_labels(page_id)
        self.add_labels(page_id, [label for label in wanted if label not in current])
        for label in current:
            if label not in set(wanted):
                self.delete_label(page_id, label)
