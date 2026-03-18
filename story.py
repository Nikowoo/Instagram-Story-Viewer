#!/usr/bin/env python3
import argparse
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

APP_ID    = "936619743392459"
BLOKS_VER = "9c0aa96c08c5b24220ee33094940e011645902f00d10e21e03b027ede1dc2735"
DOC_ID    = "26214862078151455"

CREDS_FILE = os.path.expanduser("~/.ig_story_creds")


#  CREDENTIAL CACHE 

def save_creds(session_id: str, csrf_token: str):
    data = json.dumps({"session_id": session_id, "csrf_token": csrf_token})
    with open(CREDS_FILE, "w") as f:
        f.write(data)
    try:
        os.chmod(CREDS_FILE, stat.S_IRUSR | stat.S_IWUSR)  # chmod 600
    except Exception:
        pass  # linux only
    print(f"[*] Credentials saved to {CREDS_FILE}")


def load_creds():
    if not os.path.exists(CREDS_FILE):
        return None
    try:
        with open(CREDS_FILE) as f:
            data = json.load(f)
        return data["session_id"], data["csrf_token"]
    except Exception:
        return None


def clear_creds():
    if os.path.exists(CREDS_FILE):
        os.remove(CREDS_FILE)
        print(f"[*] Credentials cleared ({CREDS_FILE} deleted).")
    else:
        print("[*] No saved credentials found.")


def user_id_from_session(session_id: str) -> str:
    """
    Extract the numeric user ID directly from the sessionid cookie.
    sessionid format: <user_id>%3A<token>%3A... or <user_id>:<token>:...
    This is always the first segment before the first colon.
    """
    decoded = urllib.parse.unquote(session_id)
    return decoded.split(":")[0]


#  SESSION BOOTSTRAP 

def get_tokens(session_id: str, csrf_token: str, user_id: str):
    """
    Fetch the IG homepage and scrape fb_dtsg and lsd.
    These rotate per-session so we grab them fresh each run.

    We send a full cookie string including ds_user_id (derived from sessionid)
    which is required for web_profile_info and other endpoints to not 302.
    """
    # tell ig who we are, it's a curtesy
    cookie = (
        f"sessionid={session_id}; "
        f"csrftoken={csrf_token}; "
        f"ds_user_id={user_id}"
    )

    req = urllib.request.Request(
        "https://www.instagram.com/",
        headers={
            "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language":           "en-US,en;q=0.9",
            "Cookie":                    cookie,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest":            "document",
            "Sec-Fetch-Mode":            "navigate",
            "Sec-Fetch-Site":            "none",
            "Sec-Fetch-User":            "?1",
        },
    )

    # don't follow redirects 302 means the tokens are expired
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    def find(pattern):
        m = re.search(pattern, html)
        return m.group(1) if m else ""

    fb_dtsg = find(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"')
    lsd     = find(r'"LSD",\[\],\{"token":"([^"]+)"')

    if not fb_dtsg or not lsd:
        raise RuntimeError(
            "Could not scrape tokens from instagram.com. "
            "Your sessionid is likely expired.\n"
            "  Run: python3 story.py <username> <new_sessionid> <new_csrftoken>"
        )

    return fb_dtsg, lsd


#  API CALLS 

def make_cookie(session_id: str, csrf_token: str, user_id: str) -> str:
    return (
        f"sessionid={session_id}; "
        f"csrftoken={csrf_token}; "
        f"ds_user_id={user_id}"
    )


def resolve_user_id(username: str, session_id: str, csrf_token: str,
                    user_id: str, lsd: str) -> str:
    """Resolve a username to a numeric Instagram user ID."""
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={urllib.parse.quote(username)}"
    req = urllib.request.Request(url, headers={
        "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
        "Accept":            "*/*",
        "Accept-Language":   "en-US,en;q=0.9",
        "X-IG-App-ID":       APP_ID,
        "X-CSRFToken":       csrf_token,
        "X-FB-LSD":          lsd,
        "X-BLOKS-VERSION-ID": BLOKS_VER,
        "Origin":            "https://www.instagram.com",
        "Referer":           "https://www.instagram.com/",
        "Sec-Fetch-Dest":    "empty",
        "Sec-Fetch-Mode":    "cors",
        "Sec-Fetch-Site":    "same-origin",
        "Cookie":            make_cookie(session_id, csrf_token, user_id),
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    user = data.get("data", {}).get("user")
    if not user:
        raise ValueError(f"@{username} not found, is private, or has no account.")
    return str(user["id"])


def fetch_stories(reel_ids: list, session_id: str, csrf_token: str,
                  user_id: str, fb_dtsg: str, lsd: str) -> dict:
    variables = {
        "initial_reel_id": reel_ids[0],
        "reel_ids":        reel_ids,
        "first":           len(reel_ids),
        "last":            0,
    }
    body = urllib.parse.urlencode({
        "av":                       user_id,
        "__d":                      "www",
        "__user":                   "0",
        "__a":                      "1",
        "__req":                    "x",
        "dpr":                      "1",
        "__ccg":                    "EXCELLENT",
        "lsd":                      lsd,
        "fb_dtsg":                  fb_dtsg,
        "jazoest":                  "26333",
        "fb_api_caller_class":      "RelayModern",
        "fb_api_req_friendly_name": "PolarisStoriesV3ReelPageGalleryQuery",
        "server_timestamps":        "true",
        "variables":                json.dumps(variables),
        "doc_id":                   DOC_ID,
    }).encode()

    req = urllib.request.Request(
        "https://www.instagram.com/graphql/query",
        data=body,
        method="POST",
        headers={
            "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept":            "*/*",
            "Accept-Language":   "en-US,en;q=0.9",
            "Content-Type":      "application/x-www-form-urlencoded",
            "X-IG-App-ID":       APP_ID,
            "X-CSRFToken":       csrf_token,
            "X-FB-LSD":          lsd,
            "X-FB-Friendly-Name": "PolarisStoriesV3ReelPageGalleryQuery",
            "X-BLOKS-VERSION-ID": BLOKS_VER,
            "X-Root-Field-Name": "xdt_viewer",
            "X-ASBD-ID":         "359341",
            "Origin":            "https://www.instagram.com",
            "Referer":           "https://www.instagram.com/",
            "Sec-Fetch-Dest":    "empty",
            "Sec-Fetch-Mode":    "cors",
            "Sec-Fetch-Site":    "same-origin",
            "Cookie":            make_cookie(session_id, csrf_token, user_id),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


#  parse and download 

def parse_items(data: dict) -> list:
    results = []
    try:
        edges = data["data"]["xdt_api__v1__feed__reels_media__connection"]["edges"]
    except KeyError:
        return results

    for edge in edges:
        node     = edge["node"]
        username = node.get("user", {}).get("username", "unknown")
        for item in node.get("items", []):
            media_type  = item.get("media_type", 0)  # 1 = photo, 2 = video
            taken_at    = item.get("taken_at", 0)
            expiring_at = item.get("expiring_at", 0)

            # Photo thumbnail
            candidates = item.get("image_versions2", {}).get("candidates", [])
            thumb_url  = candidates[0]["url"] if candidates else None

            # video type 101 is the mp4
            # 101/102/103 are the same
            video_url = None
            video_versions = item.get("video_versions") or []
            for v in video_versions:
                if v.get("type") == 101:
                    video_url = v["url"]
                    break
            if not video_url and video_versions:
                video_url = video_versions[0]["url"]

            results.append({
                "username":    username,
                "pk":          item["pk"],
                "media_type":  "video" if media_type == 2 else "photo",
                "taken_at":    datetime.utcfromtimestamp(taken_at).strftime("%Y-%m-%d %H:%M UTC") if taken_at else "",
                "expiring_at": datetime.utcfromtimestamp(expiring_at).strftime("%Y-%m-%d %H:%M UTC") if expiring_at else "",
                "thumb_url":   thumb_url,
                "video_url":   video_url,
            })
    return results


def download_item(entry: dict, out_dir: str):
    """
    Download the story item. Videos saved as .mp4, photos as .jpg.
    """
    os.makedirs(out_dir, exist_ok=True)

    if entry["media_type"] == "video" and entry.get("video_url"):
        url   = entry["video_url"]
        fname = os.path.join(out_dir, f"{entry['username']}__{entry['pk']}.mp4")
    elif entry.get("thumb_url"):
        url   = entry["thumb_url"]
        fname = os.path.join(out_dir, f"{entry['username']}__{entry['pk']}.jpg")
    else:
        return None

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(fname, "wb") as f:
            f.write(resp.read())
    return fname


#  MAIN 

def main():
    parser = argparse.ArgumentParser(
        description="Fetch Instagram stories (videos + photos) by username.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  First run (saves credentials locally):
    python3 story.py [username] [sessionid] [csrftoken]

  Subsequent runs (credentials loaded automatically):
    python3 story.py [username]
    python3 story.py username --download
    python3 story.py user1 user2 --download --out-dir ./out
    python3 story.py [username] --json

  Forget saved credentials:
    python3 story.py --clear-creds
        """,
    )
    parser.add_argument("usernames", nargs="*", metavar="USERNAME",
                        help="Instagram username(s) — @ prefix optional")
    parser.add_argument("-D", "--download", action="store_true",
                    help="Download stories — videos as .mp4, photos as .jpg")
    parser.add_argument("--out-dir", default="stories", metavar="DIR",
                        help="Output directory for downloads (default: ./thumbs)")
    parser.add_argument("-J", "--json", action="store_true",
                    help="Print raw API JSON response and exit")
    parser.add_argument("--clear-creds", action="store_true",
                        help="Delete saved credentials and exit")
    args = parser.parse_args()

    if args.clear_creds:
        clear_creds()
        sys.exit(0)

    #  parse args, usernames + optional sessionid + csrftoken 
    # sessionid always contains %3A (URL-encoded colon)
    # csrftoken is exactly 32 alphanumeric characters
    # everything else is a username
    session_id = None
    csrf_token = None
    usernames  = []

    for arg in args.usernames:
        stripped = arg.lstrip("@")
        if session_id is None and "%3A" in stripped and len(stripped) > 20:
            session_id = stripped
        elif csrf_token is None and session_id is not None and len(stripped) == 32 and stripped.isalnum():
            csrf_token = stripped
        else:
            usernames.append(stripped)

    if not usernames:
        parser.print_help()
        sys.exit(1)

    #  load or save credentials 
    if session_id and csrf_token:
        save_creds(session_id, csrf_token)
    else:
        cached = load_creds()
        if cached:
            session_id, csrf_token = cached
            print(f"[*] Using saved credentials from {CREDS_FILE}")
        else:
            print("[!] No credentials found.")
            print("    Pass them on the first run:")
            print("      python3 story.py <username> <sessionid> <csrftoken>")
            print()
            print("    Get them from your browser:")
            print("      DevTools -> Application/Storage -> Cookies -> instagram.com")
            print("      Copy the values for 'sessionid' and 'csrftoken'")
            sys.exit(1)

    #  extract the user_id from sessionid 
    user_id = user_id_from_session(session_id)
    print(f"[*] Account user_id : {user_id}")

    #  Scrape fb_dtsg and lsd from homepage 
    print("[*] Fetching session tokens...")
    try:
        fb_dtsg, lsd = get_tokens(session_id, csrf_token, user_id)
        print(f"    lsd    : {lsd}")
        print(f"    fb_dtsg: {fb_dtsg[:40]}...")
    except urllib.error.HTTPError as e:
        if e.code == 302:
            print("[!] Session expired — Instagram redirected to login.")
            print(f"    Delete {CREDS_FILE} and re-run with fresh cookies:")
            print("      python3 story.py <username> <new_sessionid> <new_csrftoken>")
        else:
            print(f"[!] HTTP {e.code} while fetching instagram.com")
        sys.exit(1)
    except RuntimeError as e:
        print(f"[!] {e}")
        sys.exit(1)

    # resolve usernames to userid
    reel_ids = []
    print()
    for username in usernames:
        print(f"[*] Resolving @{username}...")
        try:
            uid = resolve_user_id(username, session_id, csrf_token, user_id, lsd)
            reel_ids.append(uid)
            print(f"    -> user_id: {uid}")
        except urllib.error.HTTPError as e:
            print(f"    [!] HTTP {e.code} for @{username}.")
            sys.exit(1)
        except ValueError as e:
            print(f"    [!] {e}")
            sys.exit(1)

    #  get story thumbnails 
    print(f"\n[*] Fetching stories for {len(reel_ids)} user(s)...")
    try:
        data = fetch_stories(reel_ids, session_id, csrf_token, user_id, fb_dtsg, lsd)
    except urllib.error.HTTPError as e:
        print(f"[!] HTTP {e.code}: {e.reason}")
        sys.exit(1)

    if args.json:
        print(json.dumps(data, indent=2))
        return

    if data.get("status") != "ok":
        print(f"[!] API returned status: {data.get('status')}")
        print(json.dumps(data, indent=2))
        sys.exit(1)

    #  Display results 
    thumbnails = parse_items(data)

    if not thumbnails:
        print("\n[!] No active stories found.")
        return

    col = "{:<25} {:<6} {:<20} {:<20}"
    bar = "" * 80
    print(f"\n{bar}")
    print(col.format("USERNAME", "TYPE", "TAKEN AT", "EXPIRES"))
    print(bar)

    for t in thumbnails:
        print(col.format(t["username"], t["media_type"], t["taken_at"], t["expiring_at"]))
        display_url = (t.get("video_url") if t["media_type"] == "video" else t.get("thumb_url")) or "(none)"
        print(f"  URL: {display_url[:100]}{'...' if len(display_url) > 100 else ''}")

        if args.download and (t.get("thumb_url") or t.get("video_url")):
            try:
                fname = download_item(t, args.out_dir)
                print(f"  -> saved: {fname}")
            except Exception as e:
                print(f"  -> download failed: {e}")

    print(bar)
    accounts = len(set(t["username"] for t in thumbnails))
    print(f"\n[+] {len(thumbnails)} story item(s) from {accounts} account(s).")
    if args.download:
        print(f"[+] Stories saved to ./{args.out_dir}/")


if __name__ == "__main__":
    main()