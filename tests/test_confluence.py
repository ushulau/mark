"""Confluence client tests with a mocked HTTP layer (no network)."""

from __future__ import annotations

import base64
import io
import json
import urllib.error

import pytest

from mark.confluence import ConfluenceClient, ConfluenceError


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Harness:
    """Scripted urlopen replacement recording every request."""

    def __init__(self, monkeypatch, script):
        self.requests = []
        self._script = list(script)
        monkeypatch.setattr("mark.confluence.urllib.request.urlopen", self._open)
        monkeypatch.setattr("mark.confluence.time.sleep", lambda s: None)

    def _open(self, request, timeout=None, context=None):
        self.requests.append(request)
        if not self._script:
            raise AssertionError("unexpected extra HTTP request")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def assert_done(self):
        assert self._script == []


def json_resp(payload, status=200, headers=None):
    return FakeResponse(status, json.dumps(payload).encode(), headers)


def http_error(url, code, message="oops"):
    return urllib.error.HTTPError(
        url, code, "err", {"Content-Type": "application/json"},
        io.BytesIO(json.dumps({"message": message}).encode()),
    )


def request_json(request):
    return json.loads(request.data.decode("utf-8"))


# -- construction / auth --------------------------------------------------------

def test_base_url_required():
    with pytest.raises(ConfluenceError, match="base-url is required"):
        ConfluenceClient("")


def test_auth_basic_bearer_and_none():
    basic = ConfluenceClient("https://x", "user", "tok")._auth_headers()
    assert basic == {
        "Authorization": "Basic " + base64.b64encode(b"user:tok").decode()
    }
    assert ConfluenceClient("https://x", "", "pat")._auth_headers() == {
        "Authorization": "Bearer pat"
    }
    assert ConfluenceClient("https://x")._auth_headers() == {}


def test_gateway_fails_fast():
    client = ConfluenceClient("https://tenant.api.atlassian.com/ex/confluence/1")
    with pytest.raises(ConfluenceError, match="v2 API"):
        client.find_page("S", "T")


# -- request plumbing -------------------------------------------------------------

def test_request_retries_429_then_succeeds(monkeypatch):
    client = ConfluenceClient("https://x", max_retries=2)
    h = Harness(monkeypatch, [
        FakeResponse(429, b"{}", {"Retry-After": "0"}),
        json_resp({"ok": True}),
    ])
    status, result, _ = client._request("GET", "/content/", what="probe")
    assert (status, result) == (200, {"ok": True})
    assert len(h.requests) == 2
    h.assert_done()


def test_request_http_error_wraps(monkeypatch):
    client = ConfluenceClient("https://x", max_retries=0)
    url = "https://x/rest/api/content/"
    h = Harness(monkeypatch, [http_error(url, 403, "forbidden")])
    with pytest.raises(ConfluenceError, match="HTTP 403: forbidden") as exc:
        client._request("GET", "/content/", what="probe")
    assert exc.value.status == 403
    assert exc.value.url == url
    h.assert_done()


def test_request_url_error_retries_then_raises(monkeypatch):
    client = ConfluenceClient("https://x", max_retries=1)
    h = Harness(monkeypatch, [
        urllib.error.URLError("down"),
        urllib.error.URLError("down"),
    ])
    with pytest.raises(ConfluenceError, match="probe failed"):
        client._request("GET", "/content/", what="probe")
    assert len(h.requests) == 2
    h.assert_done()


def test_non_json_body_returns_bytes(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [FakeResponse(200, b"raw", {"Content-Type": "text/plain"})])
    status, result, _ = client._request("GET", "/x", what="probe")
    assert (status, result) == (200, b"raw")
    h.assert_done()


# -- pages ------------------------------------------------------------------------

PAGE = {
    "id": "7", "type": "page", "title": "T",
    "version": {"number": 3, "message": ""},
    "ancestors": [{"id": "1"}],
    "_links": {"base": "https://x", "webui": "/pages/7"},
    "body": {"storage": {"value": "<p>old</p>"}},
}


def test_find_page_caches(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [json_resp({"results": [dict(PAGE)]})])
    first = client.find_page("S", "T")
    second = client.find_page("S", "T")
    assert first and first["id"] == "7"
    assert second == first and second is not first
    assert len(h.requests) == 1  # second call served from cache
    h.assert_done()
    client.invalidate("S", "T")
    h2 = Harness(monkeypatch, [json_resp({"results": [dict(PAGE)]})])
    client.find_page("S", "T")
    assert len(h2.requests) == 1


def test_find_page_none_on_404_and_empty(monkeypatch):
    client = ConfluenceClient("https://x", max_retries=0)
    h = Harness(monkeypatch, [
        http_error("https://x/rest/api/content/", 404, "nope"),
        json_resp({"results": []}),
    ])
    assert client.find_page("S", "Missing") is None
    assert client.find_page("S", "Empty") is None
    h.assert_done()


def test_create_page_payload_and_cache(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [json_resp(dict(PAGE))])
    page = client.create_page("S", "T", "<p>new</p>", "page", parent_id="1")
    assert page["id"] == "7"
    payload = request_json(h.requests[0])
    assert payload["ancestors"] == [{"id": "1"}]
    assert payload["body"]["storage"]["value"] == "<p>new</p>"
    assert payload["metadata"]["properties"]["editor"] == {"value": "v2"}
    assert h.requests[0].get_method() == "POST"
    # Cached: a find does not hit HTTP again.
    assert client.find_page("S", "T")["id"] == "7"
    assert len(h.requests) == 1
    h.assert_done()


def test_update_page_payload_with_emoji(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [json_resp({"id": "7"})])
    client.update_page(
        dict(PAGE), "<p>new</p>", minor_edit=True,
        version_message="msg", appearance="fixed", emoji="🚀",
    )
    payload = request_json(h.requests[0])
    assert h.requests[0].get_method() == "PUT"
    assert payload["version"] == {"number": 4, "minorEdit": True, "message": "msg"}
    props = payload["metadata"]["properties"]
    assert props["content-appearance-published"] == {"value": "fixed"}
    codepoint = f"{ord('🚀'):x}"
    assert props["emoji-title-draft"] == {"value": codepoint}
    assert props["emoji-title-published"] == {"value": codepoint}
    assert payload["ancestors"] == [{"id": "1"}]
    h.assert_done()


def test_update_page_retries_conflict_once(monkeypatch):
    client = ConfluenceClient("https://x", max_retries=0)
    h = Harness(monkeypatch, [
        http_error("https://x/rest/api/content/7", 409, "conflict"),
        json_resp({"id": "7", "version": {"number": 9}}),
        json_resp({"id": "7"}),
    ])
    client.update_page(dict(PAGE), "<p>new</p>")
    assert len(h.requests) == 3
    assert request_json(h.requests[2])["version"]["number"] == 10
    h.assert_done()


def test_update_page_double_conflict_friendly(monkeypatch):
    client = ConfluenceClient("https://x", max_retries=0)
    h = Harness(monkeypatch, [
        http_error("https://x/rest/api/content/7", 409, "c1"),
        json_resp({"id": "7", "version": {"number": 9}}),
        http_error("https://x/rest/api/content/7", 409, "c2"),
    ])
    with pytest.raises(ConfluenceError, match="conflicted twice"):
        client.update_page(dict(PAGE), "<p>new</p>")
    h.assert_done()


def test_update_page_respects_concurrent_edits(monkeypatch):
    client = ConfluenceClient("https://x", max_retries=0)
    client.keep_concurrent_edits = True
    h = Harness(monkeypatch, [
        http_error("https://x/rest/api/content/7", 409, "c1"),
        json_resp({"id": "7", "version": {"number": 9}}),
    ])
    with pytest.raises(ConfluenceError, match="refusing to overwrite"):
        client.update_page(dict(PAGE), "<p>new</p>")
    h.assert_done()


def test_delete_page_statuses(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [FakeResponse(204, b""), FakeResponse(200, b"")])
    client.delete_page("7")
    client.delete_page("8")
    h.assert_done()


def test_get_page_by_id_unexpected_status(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [json_resp({}, status=302)])
    with pytest.raises(ConfluenceError, match="unexpected response HTTP 302"):
        client.get_page_by_id("7")
    h.assert_done()


# -- attachments ---------------------------------------------------------------------

def test_get_attachments_paginates(monkeypatch):
    client = ConfluenceClient("https://x")
    first = {"results": [{"id": "a1", "title": "f.png"}],
             "_links": {"next": "/more"}}
    second = {"results": [{"id": "a2", "title": "g.png"}], "_links": {}}
    h = Harness(monkeypatch, [json_resp(first), json_resp(second)])
    found = client.get_attachments("7")
    assert [a["id"] for a in found] == ["a1", "a2"]
    h.assert_done()


def test_create_attachment_multipart(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [json_resp({"results": [{"id": "a1"}]})])
    info = client.create_attachment("7", "f.png", "", b"data")
    assert info == {"id": "a1"}
    request = h.requests[0]
    assert request.get_header("X-atlassian-token") == "no-check"
    assert "multipart/form-data" in request.get_header("Content-type")
    assert b"data" in request.data
    h.assert_done()


def test_download_attachment_needs_link():
    client = ConfluenceClient("https://x")
    with pytest.raises(ConfluenceError, match="no download link"):
        client.download_attachment({"title": "f.png"})


def test_ensure_attachment_skips_identical(monkeypatch, tmp_path):
    local = tmp_path / "f.png"
    local.write_bytes(b"same")
    client = ConfluenceClient("https://x")
    existing = {"id": "a1", "title": "f.png",
                "_links": {"download": "/download/f.png"}}
    h = Harness(monkeypatch, [json_resp({"results": [existing], "_links": {}})])
    monkeypatch.setattr(client, "download_attachment", lambda a: b"same")
    created = []
    monkeypatch.setattr(client, "create_attachment",
                        lambda *a: created.append(a) or {})
    monkeypatch.setattr(client, "update_attachment",
                        lambda *a: created.append(a) or {})
    info, changed = client.ensure_attachment("7", str(local))
    assert (info, changed) == (existing, False)
    assert created == []
    h.assert_done()


def test_ensure_attachment_updates_and_creates(monkeypatch, tmp_path):
    local = tmp_path / "f.png"
    local.write_bytes(b"new")
    client = ConfluenceClient("https://x")
    existing = {"id": "a1", "title": "f.png",
                "_links": {"download": "/download/f.png"}}
    h = Harness(monkeypatch, [
        json_resp({"results": [existing], "_links": {}}),
        json_resp({"results": [], "_links": {}}),
    ])
    monkeypatch.setattr(client, "download_attachment", lambda a: b"old")
    calls = []
    monkeypatch.setattr(client, "update_attachment",
                        lambda *a: calls.append(("update", a)) or {"id": "a1"})
    monkeypatch.setattr(client, "create_attachment",
                        lambda *a: calls.append(("create", a)) or {"id": "a2"})
    _, changed = client.ensure_attachment("7", str(local))
    assert changed is True
    _, changed = client.ensure_attachment("7", str(local))
    assert changed is True
    assert [kind for kind, _ in calls] == ["update", "create"]
    h.assert_done()


# -- labels ----------------------------------------------------------------------------

def test_sync_labels_replace_and_append(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [
        json_resp({"results": [{"name": "keep"}, {"name": "drop"}], "_links": {}}),
        json_resp({}),  # add "add"
        FakeResponse(204, b""),
    ])
    client.sync_labels("7", ["keep", "add"])
    methods = [r.get_method() for r in h.requests]
    assert methods == ["GET", "POST", "DELETE"]
    assert request_json(h.requests[1]) == [{"prefix": "global", "name": "add"}]
    assert "name=drop" in h.requests[2].full_url
    h.assert_done()

    h2 = Harness(monkeypatch, [
        json_resp({"results": [{"name": "keep"}], "_links": {}}),
        json_resp({}),
    ])
    client.sync_labels("7", ["keep", "add"], append=True)
    assert [r.get_method() for r in h2.requests] == ["GET", "POST"]
    h2.assert_done()


def test_add_labels_empty_is_noop(monkeypatch):
    client = ConfluenceClient("https://x")
    h = Harness(monkeypatch, [])
    client.add_labels("7", [])
    assert h.requests == []
