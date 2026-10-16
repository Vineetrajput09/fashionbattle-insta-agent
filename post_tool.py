#!/usr/bin/env python3
"""
FashionBattle Social Publisher (Instagram + Facebook)
=====================================================
Helper script executed daily by a Claude Code Routine. Claude writes the captions
itself, so no Anthropic API key is required.

Secrets (routine environment variables):
  IG_ACCESS_TOKEN         Required. Meta token with Instagram publishing permission
  FB_PAGE_ACCESS_TOKEN    Optional. Facebook Page token (pages_manage_posts).
                          When set, every post is also published to the Facebook Page.

Commands:
  python post_tool.py check                  Verify the tokens and the product API
  python post_tool.py next                   Select the next product and download its photos
  python post_tool.py design "Short Title"   Render branded photos (frame, logo, price badge)
  python post_tool.py post caption.txt       Publish to Instagram (and Facebook if configured)
"""

import os, io, re, sys, json, time, subprocess
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from PIL import Image, ImageOps

# ---------------- Configuration ----------------
PRODUCT_API_URL = os.environ.get("PRODUCT_API_URL", "https://new-fashion-battle.onrender.com/api/products")
PRODUCT_LINK_TEMPLATE = os.environ.get("PRODUCT_LINK_TEMPLATE", "https://fashionbattle.in/product?id={id}")
MAX_PHOTOS = int(os.environ.get("MAX_PHOTOS", "4"))
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_USER_ID = os.environ.get("IG_USER_ID", "")             # Optional; detected from the token if empty
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")   # Optional; enables Facebook posting
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")             # Optional; detected from the token if empty
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v25.0")

# "IGAA..." tokens use Instagram Login (graph.instagram.com);
# "EAA..." tokens use Facebook Login (graph.facebook.com).
IG_LOGIN = IG_ACCESS_TOKEN.startswith("IG")
GRAPH = f"https://graph.instagram.com/{GRAPH_VERSION}" if IG_LOGIN else f"https://graph.facebook.com/{GRAPH_VERSION}"
FB_GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

ROOT = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(ROOT, "public_images")   # Branded photos, pushed to GitHub
RAW_DIR = os.path.join(ROOT, "raw_images")      # Original photos, never committed
POSTED_FILE = os.path.join(ROOT, "posted.json")
FB_CAPTION_FILE = os.path.join(ROOT, "caption_facebook.txt")   # Optional Facebook-specific caption
NEXT_FILE = os.path.join(ROOT, "next_post.json")


def fail(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


# ---------------- Posting history ----------------
def load_posted():
    try:
        with open(POSTED_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def mark_posted(pid, name, **extra):
    posted = load_posted()
    posted[pid] = {"name": name, "posted_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    posted[pid].update(extra)
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(posted, f, ensure_ascii=False, indent=1)


# ---------------- Product API ----------------
def fetch_products():
    """Fetch every page of products. Retries allow the API server to wake from sleep."""
    products, page = [], 1
    while page <= 50:
        for attempt in range(4):
            try:
                r = requests.get(PRODUCT_API_URL, params={"page": page}, timeout=90)
                r.raise_for_status()
                body = r.json()
                break
            except Exception as e:
                if attempt == 3:
                    fail(f"Product API is unreachable: {e}")
                time.sleep(20)
        batch = body.get("data") or []
        products += batch
        total = (body.get("pagination") or {}).get("total", len(products))
        if not batch or len(products) >= total:
            break
        page += 1
    return products

def is_postable(p):
    """Only active, approved, in-stock products with at least one image."""
    return (p.get("status") == "active" and not p.get("is_deleted")
            and p.get("approval_status") == 1 and (p.get("stock_quantity") or 0) > 0
            and (p.get("images") or p.get("primary_image")))

def product_info(p):
    """Product details for the caption. Seller contact data is deliberately excluded."""
    variants = [v for v in (p.get("variants") or []) if (v.get("stock_quantity") or 0) > 0]
    first = variants[0] if variants else {}
    return {
        "name": p.get("product_name"), "brand": p.get("brand"),
        "category": (p.get("category_id") or {}).get("breadcrumb"),
        # Price and discount are passed through exactly as the API returns them (no calculation)
        "price": p.get("price"), "discount_percent": p.get("discount") or 0,
        "sizes_available": [v.get("size") for v in variants if v.get("size")],
        "colors": sorted({v.get("color") or v.get("shade") for v in variants
                          if v.get("color") or v.get("shade")}),
        "fabric": first.get("fabric"), "fit": first.get("fit_shape"), "length": first.get("length"),
        "return_policy": p.get("return_policy"),
        "description": (p.get("description") or "")[:1500],
        "link": PRODUCT_LINK_TEMPLATE.format(id=p.get("_id", ""), slug=p.get("url_slug", "")),
    }


# ---------------- Images ----------------
def safe_url(u):
    """Encode spaces and special characters in image URLs."""
    s = urlsplit(u)
    return urlunsplit((s.scheme, s.netloc, quote(s.path, safe="/%"), s.query, s.fragment))

def download_image(url, name):
    """Download an original product photo into raw_images/ (not committed)."""
    r = requests.get(safe_url(url), timeout=60)
    r.raise_for_status()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
    path = os.path.join(RAW_DIR, name)
    img.save(path, "JPEG", quality=95)
    return path

def cleanup_old_images(keep_days=3):
    cutoff = time.time() - keep_days * 86400
    for f in os.listdir(IMG_DIR):
        fp = os.path.join(IMG_DIR, f)
        if f.endswith(".jpg") and os.path.getmtime(fp) < cutoff:
            os.remove(fp)

def repo_slug():
    """Return 'owner/repo' from GITHUB_REPO or the git remote."""
    if os.environ.get("GITHUB_REPO"):
        return os.environ["GITHUB_REPO"].strip("/")
    url = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
    m = re.search(r"([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        fail("Could not detect the GitHub repository. Set GITHUB_REPO to 'owner/repo'.")
    return m.group(1)

def public_url(rel_path):
    return f"https://raw.githubusercontent.com/{repo_slug()}/{GITHUB_BRANCH}/{quote(rel_path)}"

def wait_until_public(url, timeout=300):
    """Instagram downloads images by URL, so wait until GitHub serves them."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.head(url, timeout=20, allow_redirects=True)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(10)
    fail(f"Image is not publicly reachable: {url} (Is the repository public? Was the push successful?)")


# ---------------- Instagram (Meta Graph API) ----------------
def need_token():
    if not IG_ACCESS_TOKEN:
        fail("IG_ACCESS_TOKEN environment variable is missing.")

def refresh_token():
    """Instagram Login tokens last ~60 days; refreshing on every run extends them."""
    if not IG_LOGIN:
        return
    try:
        r = requests.get("https://graph.instagram.com/refresh_access_token", params={
            "grant_type": "ig_refresh_token", "access_token": IG_ACCESS_TOKEN}, timeout=30).json()
        if r.get("expires_in"):
            print(f"INFO: Token refreshed, valid for about {r['expires_in'] // 86400} days")
    except Exception:
        pass

def get_ig_user_id():
    """Return (instagram_user_id, username) for the account behind the token."""
    if IG_USER_ID:
        return IG_USER_ID, "?"
    if IG_LOGIN:
        r = requests.get(f"{GRAPH}/me", params={"fields": "user_id,username",
                         "access_token": IG_ACCESS_TOKEN}, timeout=30).json()
        if "error" in r:
            fail(f"Instagram token is invalid or expired: {r['error'].get('message')}")
        return str(r.get("user_id") or r.get("id")), r.get("username")
    fields = "instagram_business_account{id,username}"
    # Page token (never expires): /me is the Facebook Page itself
    r = requests.get(f"{GRAPH}/me", params={"fields": fields,
                     "access_token": IG_ACCESS_TOKEN}, timeout=30).json()
    ig = r.get("instagram_business_account")
    if ig:
        return ig["id"], ig.get("username")
    # User token: search the user's Pages
    r = requests.get(f"{GRAPH}/me/accounts", params={"fields": "name," + fields,
                     "access_token": IG_ACCESS_TOKEN}, timeout=30).json()
    if "error" in r:
        fail(f"Meta token is invalid or expired: {r['error'].get('message')}")
    for page in r.get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            return ig["id"], ig.get("username")
    fail("No Instagram Business account found for this token. Is it linked to a Facebook Page?")

def ig_create(ig_id, **data):
    data["access_token"] = IG_ACCESS_TOKEN
    r = requests.post(f"{GRAPH}/{ig_id}/media", data=data, timeout=90).json()
    if "id" not in r:
        fail(f"Instagram error: {r.get('error', r)}")
    return r["id"]

def ig_wait(cid):
    """Wait until Instagram has finished processing a media container."""
    for _ in range(24):
        s = requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code",
                         "access_token": IG_ACCESS_TOKEN}, timeout=30).json()
        if s.get("status_code") == "FINISHED":
            return
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            fail(f"Instagram media error: {s}")
        time.sleep(5)
    fail("Instagram took too long to process the image.")


# ---------------- Facebook Page (Meta Graph API) ----------------
def fb_enabled():
    return bool(FB_PAGE_ACCESS_TOKEN)

def fb_get_page():
    """Return (page_id, page_name, page_token). Accepts a Page token or a User token."""
    r = requests.get(f"{FB_GRAPH}/me", params={"fields": "id,name,fan_count",
                     "access_token": FB_PAGE_ACCESS_TOKEN}, timeout=30).json()
    if "error" not in r:   # fan_count exists only on Pages, so this is a Page token
        return FB_PAGE_ID or r["id"], r.get("name"), FB_PAGE_ACCESS_TOKEN
    acc = requests.get(f"{FB_GRAPH}/me/accounts", params={"fields": "id,name,access_token",
                       "access_token": FB_PAGE_ACCESS_TOKEN}, timeout=30).json()
    if "error" in acc:
        raise RuntimeError(f"Facebook token is invalid or expired: {acc['error'].get('message')}")
    pages = [p for p in acc.get("data", []) if not FB_PAGE_ID or p["id"] == FB_PAGE_ID]
    if not pages:
        raise RuntimeError("No Facebook Page found for this token (missing pages_show_list permission?).")
    return pages[0]["id"], pages[0]["name"], pages[0]["access_token"]

def fb_post(urls, message):
    """Publish a photo post (single photo or multi-photo) to the Facebook Page."""
    page_id, name, token = fb_get_page()
    media = []
    for u in urls:   # Step 1: upload every photo unpublished
        r = requests.post(f"{FB_GRAPH}/{page_id}/photos", data={
            "url": u, "published": "false", "access_token": token}, timeout=120).json()
        if "id" not in r:
            raise RuntimeError(f"Facebook photo upload error: {r.get('error', r)}")
        media.append(r["id"])
    data = {"message": message, "access_token": token}   # Step 2: one post with all photos attached
    for i, mid in enumerate(media):
        data[f"attached_media[{i}]"] = json.dumps({"media_fbid": mid})
    r = requests.post(f"{FB_GRAPH}/{page_id}/feed", data=data, timeout=120).json()
    if "id" not in r:
        raise RuntimeError(f"Facebook publish error: {r.get('error', r)}")
    return r["id"], name


# ---------------- Commands ----------------
def cmd_check():
    need_token()
    refresh_token()
    ig_id, username = get_ig_user_id()
    products = fetch_products()
    ok = [p for p in products if is_postable(p)]
    left = [p for p in ok if p["_id"] not in load_posted()]
    print(f"OK: Instagram @{username} (id {ig_id})")
    if fb_enabled():
        try:
            _, page_name, _ = fb_get_page()
            print(f"OK: Facebook Page '{page_name}'")
        except Exception as e:
            print(f"WARNING: Facebook is not working: {e}")
    else:
        print("INFO: Facebook posting is off (FB_PAGE_ACCESS_TOKEN is not set)")
    print(f"OK: {len(products)} products in API, {len(ok)} postable, {len(left)} remaining")

def cmd_next():
    os.makedirs(IMG_DIR, exist_ok=True)
    cleanup_old_images()
    posted = load_posted()
    candidates = [p for p in fetch_products() if is_postable(p) and p["_id"] not in posted]
    if not candidates:
        print("NO_PRODUCTS: All products have already been posted.")
        return
    candidates.sort(key=lambda p: p.get("createdAt", ""), reverse=True)   # Newest first

    os.makedirs(RAW_DIR, exist_ok=True)
    for p in candidates[:3]:
        raw = []
        for i, u in enumerate((p.get("images") or [p["primary_image"]])[:MAX_PHOTOS]):
            try:
                raw.append(download_image(u, f"{p['_id']}_{i}.jpg"))
            except Exception:
                continue
        if raw:
            data = {"id": p["_id"], "info": product_info(p), "raw_images": raw, "images": [],
                    "remaining_after_this": len(candidates) - 1}
            with open(NEXT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            print(json.dumps(data, ensure_ascii=False, indent=1))
            return
    fail("Could not download photos for any of the next 3 products.")

def cmd_design(title):
    """Apply frame and logo to every photo; add the price badge to the first one."""
    from design import make_post_image
    try:
        with open(NEXT_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        fail("next_post.json not found. Run 'python post_tool.py next' first.")
    info = data["info"]
    title = (title or "").strip()[:40] or None
    price = f"\u20b9{info['price']}" if info.get("price") else None
    os.makedirs(IMG_DIR, exist_ok=True)
    images = []
    for i, raw in enumerate(data["raw_images"]):
        name = f"{data['id']}_{i}.jpg"
        if i == 0:
            make_post_image(raw, os.path.join(IMG_DIR, name), title, price, info.get("discount_percent"))
        else:
            make_post_image(raw, os.path.join(IMG_DIR, name))
        images.append(f"public_images/{name}")
    data["images"] = images
    with open(NEXT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"DESIGNED: {len(images)} photos -> " + ", ".join(images))

def cmd_post(caption_file):
    need_token()
    refresh_token()
    try:
        with open(NEXT_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        fail("next_post.json not found. Run 'python post_tool.py next' first.")
    with open(caption_file, encoding="utf-8") as f:
        caption = f.read().strip()[:2200]
    if len(caption) < 20:
        fail("Caption is empty or too short.")
    if data["id"] in load_posted():
        fail("This product has already been posted.")
    if not data.get("images"):
        fail("Photos have not been designed. Run 'python post_tool.py design \"Title\"' first.")

    ig_id, _ = get_ig_user_id()
    urls = [public_url(p) for p in data["images"]]
    for u in urls:
        wait_until_public(u)

    if len(urls) == 1:
        cid = ig_create(ig_id, image_url=urls[0], caption=caption)
    else:
        children = []
        for u in urls:
            c = ig_create(ig_id, image_url=u, is_carousel_item="true")
            ig_wait(c)
            children.append(c)
        cid = ig_create(ig_id, media_type="CAROUSEL", children=",".join(children), caption=caption)
    ig_wait(cid)
    r = requests.post(f"{GRAPH}/{ig_id}/media_publish", data={
        "creation_id": cid, "access_token": IG_ACCESS_TOKEN}, timeout=90).json()
    if "id" not in r:
        fail(f"Publish error: {r.get('error', r)}")

    # Instagram is live. Now publish the same post to Facebook (a failure here never blocks the record).
    fb_status, fb_extra = "Facebook: skipped (FB_PAGE_ACCESS_TOKEN is not set)", {}
    if fb_enabled():
        fb_caption = caption
        if os.path.exists(FB_CAPTION_FILE):
            with open(FB_CAPTION_FILE, encoding="utf-8") as f:
                fb_caption = f.read().strip() or caption
        try:
            fb_id, page_name = fb_post(urls, fb_caption)
            fb_status, fb_extra = f"Facebook post id {fb_id} (Page: {page_name})", {"facebook_post_id": fb_id}
        except Exception as e:
            fb_status, fb_extra = f"Facebook: FAILED ({e})", {"facebook_error": str(e)[:300]}

    mark_posted(data["id"], data["info"]["name"], instagram_media_id=r["id"], **fb_extra)
    os.remove(NEXT_FILE)
    print(f"POSTED: {data['info']['name']} | {len(urls)} photos | Instagram media id {r['id']} | {fb_status}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("check", "next", "design", "post"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "check":
        cmd_check()
    elif sys.argv[1] == "next":
        cmd_next()
    elif sys.argv[1] == "design":
        cmd_design(" ".join(sys.argv[2:]))
    else:
        cmd_post(sys.argv[2] if len(sys.argv) > 2 else "caption.txt")
