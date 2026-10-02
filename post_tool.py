#!/usr/bin/env python3
"""
FashionBattle Instagram Publisher
=================================
Helper script executed daily by a Claude Code Routine. Claude writes the caption
itself, so no Anthropic API key is required.

Required secret (routine environment variable):
  IG_ACCESS_TOKEN   Meta access token with Instagram publishing permission

Commands:
  python post_tool.py check                  Verify the token and the product API
  python post_tool.py next                   Select the next product and download its photos
  python post_tool.py design "Short Title"   Render branded photos (frame, logo, price badge)
  python post_tool.py post caption.txt       Publish the post to Instagram
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

# "IGAA..." tokens use Instagram Login (graph.instagram.com);
# "EAA..." tokens use Facebook Login (graph.facebook.com).
IG_LOGIN = IG_ACCESS_TOKEN.startswith("IG")
GRAPH = "https://graph.instagram.com/v21.0" if IG_LOGIN else "https://graph.facebook.com/v21.0"

ROOT = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(ROOT, "public_images")   # Branded photos, pushed to GitHub
RAW_DIR = os.path.join(ROOT, "raw_images")      # Original photos, never committed
POSTED_FILE = os.path.join(ROOT, "posted.json")
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

def mark_posted(pid, name):
    posted = load_posted()
    posted[pid] = {"name": name, "posted_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
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


# ---------------- Commands ----------------
def cmd_check():
    need_token()
    refresh_token()
    ig_id, username = get_ig_user_id()
    products = fetch_products()
    ok = [p for p in products if is_postable(p)]
    left = [p for p in ok if p["_id"] not in load_posted()]
    print(f"OK: Instagram @{username} (id {ig_id})")
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

    mark_posted(data["id"], data["info"]["name"])
    os.remove(NEXT_FILE)
    print(f"POSTED: {data['info']['name']} | {len(urls)} photos | Instagram media id {r['id']}")


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
