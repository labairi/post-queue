"""LinkedIn (personal profile) through the Posts API.

Needs a LinkedIn developer app with the products "Share on LinkedIn" and "Sign In with LinkedIn using
OpenID Connect" (scopes openid, profile, w_member_social) and a member access token. These tokens last
about 60 days and LinkedIn does not hand out refresh tokens for this product, so the person logs in
again every two months. Text with at most one image. Company Pages need a different approval and are
not built. Not yet tested against the live API.
"""
from __future__ import annotations

import re

from ..errors import PublishError
from ..http import call
from .base import Adapter, PublishRequest, PublishResult

API = "https://api.linkedin.com"
DEFAULT_VERSION = "202609"        # LinkedIn-Version header (YYYYMM); set account.api_version to change
RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")


def escape(text: str) -> str:
    """The commentary field treats these characters as markup; escape them so the text posts as typed."""
    return RESERVED.sub(r"\\\1", text)


class LinkedInAdapter(Adapter):
    platform = "linkedin"
    needs = ("access_token",)

    def _h(self, extra=None):
        h = {"Authorization": f"Bearer {self.secret('access_token')}", "X-Restli-Protocol-Version": "2.0.0",
             "LinkedIn-Version": str(self.account.get("api_version", DEFAULT_VERSION))}
        h.update(extra or {})
        return h

    def _me(self) -> dict:
        return call(self.http, "GET", f"{API}/v2/userinfo", what="LinkedIn identity",
                    headers={"Authorization": f"Bearer {self.secret('access_token')}"}).json()

    def verify(self) -> dict:
        me = self._me()
        return {"ok": True, "identity": me.get("name") or me.get("sub"), "id": me.get("sub")}

    def publish(self, req: PublishRequest) -> PublishResult:
        sub = self._me().get("sub")
        if not sub:
            raise PublishError("permanent", "LinkedIn: could not read the member id (token needs the openid scope)")
        owner = f"urn:li:person:{sub}"
        body = {"author": owner, "commentary": escape(req.text or ""), "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        imgs = [m for m in req.media if m.get("type") == "image"]
        if any(m.get("type") == "video" for m in req.media):
            raise PublishError("permanent", "LinkedIn video is not built yet")
        if imgs:
            init = call(self.http, "POST", f"{API}/rest/images?action=initializeUpload", what="LinkedIn image init",
                        headers=self._h({"Content-Type": "application/json"}),
                        json={"initializeUploadRequest": {"owner": owner}}).json().get("value") or {}
            if not init.get("uploadUrl") or not init.get("image"):
                raise PublishError("permanent", "LinkedIn image init returned no upload address")
            data = call(self.http, "GET", imgs[0]["url"], what="Download image").content
            call(self.http, "PUT", init["uploadUrl"], what="LinkedIn image upload", data=data,
                 headers={"Authorization": f"Bearer {self.secret('access_token')}"})
            body["content"] = {"media": {"id": init["image"], "altText": (imgs[0].get("alt") or "")[:4086]}}
        r = call(self.http, "POST", f"{API}/rest/posts", create=True, what="LinkedIn publish",
                 headers=self._h({"Content-Type": "application/json"}), json=body)
        urn = r.headers.get("x-restli-id") or r.headers.get("X-RestLi-Id") or ""
        if not urn:
            raise PublishError("uncertain", "LinkedIn accepted the post but returned no id; check LinkedIn before retrying")
        return PublishResult(urn, f"https://www.linkedin.com/feed/update/{urn}/")
