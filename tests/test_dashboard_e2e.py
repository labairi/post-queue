"""Drives the real dashboard page in Chromium against a fake GitHub API (no network, no real repo)."""
import base64
import json
import re
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
PAGE = (Path(__file__).resolve().parent.parent / "dashboard" / "index.html").as_uri()
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeGitHub:
    """Two repositories: 'repo' (the queue) and 'post-queue-dashboard' (public media). Files are keyed by path;
    media files are stored under 'MEDIA:<path>'."""

    def __init__(self):
        self.files, self.dispatches, self.requests, self.fail_status = {}, [], [], None
        self.n = 0

    def put(self, path, obj):
        text = obj if isinstance(obj, str) else json.dumps(obj)
        self.n += 1
        self.files[path] = (text, f"sha{self.n}")

    def handle(self, route, request):
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        url = request.url.split("api.github.com", 1)[1]
        self.requests.append((request.method, url, request.headers.get("authorization")))
        send = lambda status, body: route.fulfill(status=status, headers=CORS, content_type="application/json", body=json.dumps(body))  # noqa: E731
        if self.fail_status:
            return send(self.fail_status, {"message": "Bad credentials"})
        m = re.match(r"/repos/me/(repo|post-queue-dashboard)/contents/(.+?)(\?.*)?$", url)
        if m:
            repo, path = m.group(1), m.group(2)
            key = path if repo == "repo" else "MEDIA:" + path
            if request.method == "GET":
                if key in self.files:
                    t, sha = self.files[key]
                    return send(200, {"content": base64.b64encode(t.encode()).decode(), "sha": sha, "name": path.rsplit("/", 1)[-1]})
                kids = [{"name": p[len(path) + 1:], "sha": s} for p, (t, s) in self.files.items() if p.startswith(path + "/") and "/" not in p[len(path) + 1:]]
                return send(200, kids) if kids else send(404, {"message": "Not Found"})
            body = request.post_data_json
            if request.method == "PUT":
                if key in self.files and body.get("sha") != self.files[key][1]:
                    return send(409, {"message": "sha mismatch"})
                raw = base64.b64decode(body["content"])
                self.n += 1
                self.files[key] = (raw.decode() if repo == "repo" else raw, f"sha{self.n}")
                return send(201, {"content": {"sha": self.files[key][1]}})
            if request.method == "DELETE":
                if key not in self.files or body.get("sha") != self.files[key][1]:
                    return send(409, {"message": "sha mismatch"})
                del self.files[key]
                return send(200, {})
        if "/dispatches" in url:
            self.dispatches.append(request.post_data_json)
            return route.fulfill(status=204, headers=CORS)
        if "/runs" in url:
            return send(200, {"workflow_runs": [{"conclusion": "success", "status": "completed", "created_at": "2026-10-25T10:07:00Z", "event": "schedule", "html_url": "https://github.com/me/repo/actions/runs/1"}]})
        return send(404, {"message": "unhandled " + url})

    def posts(self):
        return {p.split("/")[-1][:-5]: json.loads(t) for p, (t, _) in self.files.items() if p.startswith("queue/posts/") and p.endswith(".json")}

    def commands(self):
        return [json.loads(t) for p, (t, _) in self.files.items() if p.startswith("queue/commands/")]


@pytest.fixture
def gh():
    g = FakeGitHub()
    g.put("config/workspaces.json", {"workspaces": [{"id": "insta", "name": "Instagramaps", "status": "active", "tz": "UTC"},
                                                    {"id": "other", "name": "Other Brand", "status": "active", "tz": "UTC"}]})
    g.put("config/accounts.json", {"accounts": [{"id": "insta-bsky", "platform": "bluesky", "workspace": "insta", "secrets": {}},
                                                {"id": "other-bsky", "platform": "bluesky", "workspace": "other", "secrets": {}}]})
    g.put("status/setup.json", {"checked_at": "2026-10-23T14:00:00Z", "accounts": [{"account": "insta-bsky", "platform": "bluesky", "ok": True, "identity": "insta.bsky.social", "checked_at": "2026-10-23T14:00:00Z"}]})
    g.put("queue/posts/draft-one.json", {"id": "draft-one", "title": "My draft", "primary": "hello", "intent": "draft", "targets": [{"account": "insta-bsky"}]})
    g.put("queue/posts/sent-one.json", {"id": "sent-one", "title": "Sent post", "primary": "done", "intent": "scheduled", "scheduled": {"local": "2026-10-20T10:00", "tz": "UTC"}, "targets": [{"account": "insta-bsky"}]})
    g.put("queue/state/sent-one.json", {"id": "sent-one", "cancelled": False, "targets": {"insta-bsky": {"status": "published", "url": "https://bsky.app/profile/me/post/1", "attempts": 1}}})
    g.put("queue/posts/bad-one.json", {"id": "bad-one", "title": "<img src=x onerror=window.__xss=1>", "primary": "oops", "intent": "scheduled", "scheduled": {"local": "2026-10-21T10:00", "tz": "UTC"}, "targets": [{"account": "insta-bsky"}]})
    g.put("queue/state/bad-one.json", {"id": "bad-one", "cancelled": False, "targets": {"insta-bsky": {"status": "failed", "attempts": 3, "error_kind": "permanent", "last_error": "Bluesky login: rejected (HTTP 401)"}}})
    g.put("queue/posts/other-one.json", {"id": "other-one", "title": "Other brand post", "primary": "x", "intent": "draft", "targets": [{"account": "other-bsky"}]})
    g.put("queue/log.jsonl", '{"ts":"2026-10-21T10:00:00Z","event":"failed","post_id":"bad-one","account":"insta-bsky","error":"rejected"}\n')
    return g


@pytest.fixture
def page(gh):
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(viewport={"width": 1000, "height": 900}).new_page()
        pg.route("https://api.github.com/**", gh.handle)
        pg.on("pageerror", lambda e: pytest.fail(f"page error: {e}"))
        yield pg
        b.close()


def connect(pg, token="github_pat_TESTTOKEN"):
    pg.goto(PAGE)
    pg.fill("#s_owner", "me"); pg.fill("#s_repo", "repo"); pg.fill("#s_token", token)
    pg.click('[data-act="savecfg"]')
    pg.wait_for_selector(".cal")


def test_first_visit_asks_for_setup_and_makes_no_request(page, gh):
    page.goto(PAGE)
    assert page.locator("#s_token").is_visible() and gh.requests == []


def test_bad_token_shows_a_clear_error(page, gh):
    gh.fail_status = 401
    page.goto(PAGE)
    page.fill("#s_owner", "me"); page.fill("#s_repo", "repo"); page.fill("#s_token", "bad")
    page.click('[data-act="savecfg"]')
    page.wait_for_selector("#msg .err")
    assert "rejected" in page.inner_text("#msg")


def test_calendar_is_home_and_shows_real_counts(page, gh):
    connect(page)
    t = page.inner_text("#view")
    assert re.search(r"1\s*published", t) and re.search(r"1\s*drafts", t) and re.search(r"1\s*need attention", t)
    assert "Last run: success" in t
    assert page.input_value("#wsSel") == "insta"


def test_post_content_is_escaped_not_executed(page, gh):
    connect(page)
    page.click('[data-go="posts"]')
    assert "<img src=x" in page.inner_text("#view") and page.evaluate("window.__xss") is None
    assert "Bluesky: Bluesky login: rejected" in page.inner_text("#view") or "rejected (HTTP 401)" in page.inner_text("#view")


def test_workspaces_keep_posts_and_accounts_separate(page, gh):
    connect(page)
    page.click('[data-go="posts"]')
    assert "Other brand post" not in page.inner_text("#view") and "My draft" in page.inner_text("#view")
    page.select_option("#wsSel", "other")
    assert "Other brand post" in page.inner_text("#view") and "My draft" not in page.inner_text("#view")
    page.click('[data-go="accounts"]')
    assert "other-bsky" in page.inner_text("#view") and "insta-bsky" not in page.inner_text("#view")


def test_create_workspace_when_none_exists(page, gh):
    del gh.files["config/workspaces.json"]
    page.goto(PAGE)
    page.fill("#s_owner", "me"); page.fill("#s_repo", "repo"); page.fill("#s_token", "t")
    page.click('[data-act="savecfg"]')
    page.wait_for_selector("text=Create your first workspace")
    page.click('[data-act="newws"]')
    page.fill("#w_name", "Instagramaps"); page.fill("#w_site", "https://instagramaps.com"); page.select_option("#w_tz", "Africa/Casablanca")
    page.click("#wsForm button[type=submit]")
    page.wait_for_selector("text=Workspace “Instagramaps” created")
    ws = json.loads(gh.files["config/workspaces.json"][0])["workspaces"]
    assert ws == [{"id": "instagramaps", "name": "Instagramaps", "website": "https://instagramaps.com", "status": "active", "tz": "Africa/Casablanca"}]
    assert page.input_value("#wsSel") == "instagramaps"


def test_add_post_to_calendar_from_a_day_posts_to_all_connected_accounts(page, gh):
    gh.put("config/accounts.json", {"accounts": [{"id": "insta-bsky", "platform": "bluesky", "workspace": "insta", "secrets": {}},
                                                 {"id": "insta-mast", "platform": "mastodon", "workspace": "insta", "instance": "https://m.example", "secrets": {}}]})
    connect(page)
    page.click('.day [data-act="newon"] >> nth=14')
    assert page.is_checked('[data-acc="insta-bsky"]') and page.is_checked('[data-acc="insta-mast"]')
    day = re.search(r"\d{4}-\d\d-\d\d", page.input_value("#f_local")).group(0)
    page.fill("#f_primary", "Big news today https://instagramaps.com"); page.fill("#f_hashtags", "#maps, instagram")
    page.fill("#f_local", f"{day}T11:30")
    assert "Ready to add to the calendar" in page.inner_text("#problems")
    page.click('button[type=submit]')
    page.wait_for_selector("text=Added to the calendar")
    saved = [p for i, p in gh.posts().items() if p["primary"].startswith("Big news")][0]
    assert saved["intent"] == "scheduled" and saved["scheduled"] == {"local": f"{day}T11:30", "tz": "UTC"}
    assert sorted(t["account"] for t in saved["targets"]) == ["insta-bsky", "insta-mast"] and saved["hashtags"] == ["maps", "instagram"]
    assert saved["title"].startswith("Big news") and re.match(r"^[a-z0-9-]{3,80}$", saved["id"])
    assert page.locator(".ev", has_text="Big news").count() == 1


def test_char_count_blocks_calendar_but_not_drafts(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_primary", "x" * 301)
    assert "Bluesky 301/300" in page.inner_text("#counts") and page.locator(".cnt.over").count() == 1
    page.click('button[type=submit]')
    assert "Fix the problems" in page.inner_text("#msg") and not [p for p in gh.posts().values() if p["primary"] == "x" * 301]
    page.click('[data-act="savedraft"]'); page.wait_for_selector("text=Draft saved")
    assert [p for p in gh.posts().values() if p["primary"] == "x" * 301][0]["intent"] == "draft"


def test_platform_specific_text_overrides_the_limit(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_primary", "y" * 400)
    page.click("summary:has-text('different text')")
    page.fill('[data-ver="bluesky"]', "short bluesky text")
    assert "Ready to add" in page.inner_text("#problems")


def test_dst_gap_time_is_refused(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_primary", "hi"); page.select_option("#f_tz", "Europe/Paris"); page.fill("#f_local", "2027-03-28T02:30")
    assert "does not exist in Europe/Paris" in page.inner_text("#problems")
    page.fill("#f_local", "2027-10-31T02:30")
    assert "happens twice" in page.inner_text("#problems")


def test_upload_photo_converts_to_jpeg_and_attaches_it(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.set_input_files("#file", files=[{"name": "My Photo.png", "mimeType": "image/png", "buffer": PNG}])
    page.wait_for_selector("[data-mrow]")
    media = [k for k in gh.files if k.startswith("MEDIA:media/insta/")]
    assert len(media) == 1 and media[0].endswith("-my-photo.jpg") and gh.files[media[0]][0][:3] == b"\xff\xd8\xff"
    url = page.input_value("[data-mu]")
    assert url == f"https://raw.githubusercontent.com/me/post-queue-dashboard/HEAD/{media[0][6:]}"
    page.fill("[data-ma]", "a tiny test image"); page.fill("#f_primary", "with photo")
    page.click('button[type=submit]'); page.wait_for_selector("text=Added to the calendar")
    p = [p for p in gh.posts().values() if p["primary"] == "with photo"][0]
    assert p["media"] == [{"type": "image", "url": url, "alt": "a tiny test image"}]


def test_instagram_needs_media_message(page, gh):
    gh.put("config/accounts.json", {"accounts": [{"id": "insta-ig", "platform": "instagram", "workspace": "insta", "ig_user_id": "1", "secrets": {}}]})
    connect(page)
    page.click('[data-go="compose"]'); page.fill("#f_primary", "hello")
    assert "needs an image or a video" in page.inner_text("#problems")


def test_post_now_saves_commands_and_dispatches_with_both_keys(page, gh):
    connect(page)
    page.click('[data-go="compose"]'); page.fill("#f_primary", "Right now please")
    page.click('[data-act="postnow"]'); page.click("#dlg button[value=cancel]")
    assert gh.dispatches == []
    page.click('[data-act="postnow"]'); page.click("#dlgOk")
    page.wait_for_selector("text=Saved and started")
    pid = [i for i, p in gh.posts().items() if p["primary"] == "Right now please"][0]
    assert {"action": "publish_now", "post_id": pid} in gh.commands()
    assert gh.dispatches == [{"ref": "main", "inputs": {"mode": "publish-due", "allow_publish": "true"}}]


def test_publish_due_needs_confirmation_and_check_never_publishes(page, gh):
    connect(page)
    page.click('[data-act="publishdue"]'); page.click("#dlg button[value=cancel]")
    assert gh.dispatches == []
    page.click('[data-act="publishdue"]'); page.click("#dlgOk"); page.wait_for_selector("text=Started")
    assert gh.dispatches[0]["inputs"] == {"mode": "publish-due", "allow_publish": "true"}
    page.click('[data-go="accounts"]'); page.click('[data-act="check"] >> nth=0'); page.wait_for_selector("text=check-setup")
    assert gh.dispatches[1]["inputs"] == {"mode": "check-setup", "allow_publish": "false"}


def test_edit_existing_draft_updates_in_place_and_conflicts_are_reported(page, gh):
    connect(page)
    page.click('[data-go="posts"]'); page.click('[data-act="edit"] >> nth=0')
    page.fill("#f_primary", "edited caption"); page.click('[data-act="savedraft"]'); page.wait_for_selector("text=Draft saved")
    assert gh.posts()["draft-one"]["primary"] == "edited caption" and len(gh.posts()) == 4
    page.click('[data-act="edit"] >> nth=0')
    gh.put("queue/posts/draft-one.json", {"id": "draft-one", "title": "changed elsewhere", "primary": "other", "intent": "draft", "targets": [{"account": "insta-bsky"}]})
    page.fill("#f_primary", "mine"); page.click('[data-act="savedraft"]'); page.wait_for_selector("#msg .err")
    assert "changed since it was loaded" in page.inner_text("#msg") and gh.posts()["draft-one"]["primary"] == "other"


def test_delete_draft_asks_first(page, gh):
    connect(page)
    page.click('[data-go="posts"]'); page.click('[data-act="delete"]'); page.click("#dlg button[value=cancel]")
    assert "draft-one" in gh.posts()
    page.click('[data-act="delete"]'); page.click("#dlgOk"); page.wait_for_selector("text=Deleted")
    assert "draft-one" not in gh.posts()


def test_cancel_and_retry_write_commands(page, gh):
    gh.put("queue/posts/soon-one.json", {"id": "soon-one", "title": "Soon", "primary": "s", "intent": "scheduled", "scheduled": {"local": "2030-01-01T10:00", "tz": "UTC"}, "targets": [{"account": "insta-bsky"}]})
    connect(page)
    page.click('[data-go="posts"]')
    page.click('li:has-text("Soon") [data-act="cancel"]'); page.click("#dlgOk"); page.wait_for_selector("text=Cancel requested")
    page.click('li:has-text("<img") [data-act="retry"]'); page.click("#dlgOk"); page.wait_for_selector("text=Retry started")
    assert {"action": "cancel", "post_id": "soon-one"} in gh.commands() and {"action": "retry", "post_id": "bad-one"} in gh.commands()


def test_accounts_page_shows_bluesky_connected_and_others_ready(page, gh):
    connect(page)
    page.click('[data-go="accounts"]'); t = page.inner_text("#view")
    assert "insta.bsky.social" in t and "connected" in t and "ready to connect" in t and "not available yet" in t
    assert page.locator('[data-act="connect"][data-pl="x"]').count() == 0


def test_connect_instagram_writes_names_only_and_rejects_secret_values(page, gh):
    connect(page)
    page.click('[data-go="accounts"]'); page.click('[data-act="connect"][data-pl="instagram"]')
    assert "instagram_content_publish" in page.inner_text("#connectCard")
    page.fill("#c_id", "insta-ig"); page.fill('[data-cf="ig_user_id"]', "17841400000000000")
    page.locator("#connForm summary").click()
    page.fill('[data-cs="access_token"]', "EAAGm0PX4ZCpsBAKZB7sVeryLongSecretTokenValue1234567890")
    page.click("#connForm button[type=submit]")
    assert "Never type the secret value" in page.inner_text("#connErr") and "insta-ig" not in gh.files["config/accounts.json"][0]
    page.fill('[data-cs="access_token"]', "IG_TOKEN_INSTA")
    page.click("#connForm button[type=submit]"); page.wait_for_selector("text=Last step")
    ig = [a for a in json.loads(gh.files["config/accounts.json"][0])["accounts"] if a["id"] == "insta-ig"][0]
    assert ig == {"id": "insta-ig", "platform": "instagram", "workspace": "insta", "ig_user_id": "17841400000000000", "secrets": {"access_token": "IG_TOKEN_INSTA"}}


def test_connect_validates_numbers_and_duplicates(page, gh):
    connect(page)
    page.click('[data-go="accounts"]'); page.click('[data-act="connect"][data-pl="instagram"]')
    page.fill("#c_id", "insta-ig"); page.fill('[data-cf="ig_user_id"]', "not-a-number"); page.click("#connForm button[type=submit]")
    assert "numbers only" in page.inner_text("#connErr")
    page.click('[data-act="connect"][data-pl="bluesky"]'); page.fill("#c_id", "insta-bsky"); page.click("#connForm button[type=submit]")
    page.wait_for_selector("#connErr .err"); assert "already exists" in page.inner_text("#connErr")


def test_remove_account_blocked_while_posts_are_scheduled(page, gh):
    gh.put("queue/posts/live-one.json", {"id": "live-one", "title": "L", "primary": "x", "intent": "scheduled", "scheduled": {"local": "2030-01-01T10:00", "tz": "UTC"}, "targets": [{"account": "insta-bsky"}]})
    connect(page)
    page.click('[data-go="accounts"]'); page.click('[data-act="rmacct"]'); page.wait_for_selector("#msg .err")
    assert "Cannot remove" in page.inner_text("#msg") and "insta-bsky" in gh.files["config/accounts.json"][0]


def test_calendar_navigates_months(page, gh):
    connect(page)
    page.evaluate("calY = 2026; calM = 9; render()")
    assert "Sent post" in page.locator(".cal").inner_html()
    page.click('[data-act="calnext"]'); assert "Sent post" not in page.locator(".cal").inner_html()


def test_mobile_width_has_no_horizontal_scroll(page, gh):
    page.set_viewport_size({"width": 390, "height": 800})
    connect(page)
    for r in ("calendar", "compose", "posts", "accounts", "setup"):
        page.click(f'[data-go="{r}"]')
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), r
