"""TikTok Content Posting API (video only).

Until TikTok audits the app, only two things are possible, and both are private:
  mode "draft"  (default): the video is sent to the creator's TikTok inbox; the person opens TikTok
                           and taps Post. Needs scope video.upload.
  mode "direct":           posted straight away, visibility SELF_ONLY ("only me"). Needs scope
                           video.publish, and the TikTok account must be set to private.
Access tokens last 24 hours, so each run exchanges the stored refresh token for a fresh access token.
TikTok may answer with a different refresh token; this adapter never prints it. If that happens the
run records a warning and the secret must be replaced (see docs/PLATFORMS.md).
The video is downloaded from its public https address and uploaded as one chunk (up to 64 MB).
"""
from __future__ import annotations

import sys

from ..errors import PublishError
from ..http import call
from .base import Adapter, PublishRequest, PublishResult

API = "https://open.tiktokapis.com"
MAX_BYTES = 64 * 1024 * 1024


class TikTokAdapter(Adapter):
    platform = "tiktok"
    needs = ("client_key", "client_secret", "refresh_token")

    def _token(self) -> str:
        r = call(self.http, "POST", f"{API}/v2/oauth/token/", what="TikTok token refresh",
                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                 data={"client_key": self.secret("client_key"), "client_secret": self.secret("client_secret"),
                       "grant_type": "refresh_token", "refresh_token": self.secret("refresh_token")}).json()
        if not r.get("access_token"):
            raise PublishError("permanent", f"TikTok token refresh failed: {r.get('error') or ''} {r.get('error_description') or ''}".strip())
        if r.get("refresh_token") and r["refresh_token"] != self.secret("refresh_token"):
            print("WARNING: TikTok issued a new refresh token. Replace the refresh token secret before it stops working.",
                  file=sys.stderr)
        return r["access_token"]

    def verify(self) -> dict:
        tok = self._token()
        ident = ""
        try:
            u = call(self.http, "GET", f"{API}/v2/user/info/", what="TikTok identity", params={"fields": "open_id,display_name"},
                     headers={"Authorization": f"Bearer {tok}"}).json()
            ident = ((u.get("data") or {}).get("user") or {}).get("display_name", "")
        except PublishError:
            pass   # user.info.basic scope may not be granted; the token itself is valid
        return {"ok": True, "identity": ident or "token valid"}

    def _json(self, r, what):
        d = r.json()
        err = d.get("error") or {}
        if err.get("code") not in (None, "ok"):
            raise PublishError("permanent", f"{what}: {err.get('code')}: {err.get('message', '')}"[:400])
        return d.get("data") or {}

    def publish(self, req: PublishRequest) -> PublishResult:
        vids = [m for m in req.media if m.get("type") == "video"]
        if not vids:
            raise PublishError("permanent", "TikTok needs a video")
        mode = self.account.get("mode", "draft")
        if mode not in ("draft", "direct"):
            raise PublishError("permanent", "TikTok account mode must be 'draft' or 'direct'")
        tok = self._token()
        h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"}
        v = call(self.http, "GET", vids[0]["url"], what="Download video")
        data = v.content
        size = len(data)
        if not size or size > MAX_BYTES:
            raise PublishError("permanent", f"TikTok: the video is {size // (1024 * 1024)} MB; this tool uploads up to 64 MB")
        body = {"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}
        if mode == "direct":
            body["post_info"] = {"title": (req.text or "")[:2200], "privacy_level": "SELF_ONLY",
                                 "disable_duet": False, "disable_comment": False, "disable_stitch": False}
            path = "/v2/post/publish/video/init/"
        else:
            path = "/v2/post/publish/inbox/video/init/"
        d = self._json(call(self.http, "POST", API + path, what="TikTok init", headers=h, json=body), "TikTok init")
        pid, up = d.get("publish_id"), d.get("upload_url")
        if not pid or not up:
            raise PublishError("permanent", "TikTok init returned no publish_id or upload_url")
        call(self.http, "PUT", up, create=True, what="TikTok upload",
             headers={"Content-Type": vids[0].get("mime", "video/mp4"), "Content-Length": str(size),
                      "Content-Range": f"bytes 0-{size - 1}/{size}"}, data=data)
        want = "PUBLISH_COMPLETE" if mode == "direct" else "SEND_TO_USER_INBOX"
        for _ in range(24):
            st = self._json(call(self.http, "POST", f"{API}/v2/post/publish/status/fetch/", what="TikTok status",
                                 headers=h, json={"publish_id": pid}), "TikTok status")
            s = st.get("status")
            if s == want or s == "PUBLISH_COMPLETE":
                return PublishResult(pid, "")
            if s == "FAILED":
                raise PublishError("permanent", f"TikTok could not process the video: {st.get('fail_reason', 'unknown')}")
            self.sleep(5)
        raise PublishError("uncertain", "TikTok: still processing after 2 minutes; check the TikTok app before retrying")
