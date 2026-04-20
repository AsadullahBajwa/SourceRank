"""
save_session.py — Extracts X cookies directly from Chrome Profile 5.
Run once, then python scheduler.py works forever.
"""

import os
import json

SESSION_PATH = os.path.join(os.path.dirname(__file__), "data", "session.json")


def main():
    print("\n=== SourceRank Session Setup ===")
    print("Make sure Chrome is fully closed (check Task Manager too).")
    input("Press Enter to continue...")

    print("Reading cookies from Chrome Profile 5...")

    try:
        import rookiepy
        cookies = rookiepy.chrome(["x.com", ".x.com", "twitter.com", ".twitter.com"])
    except Exception as e:
        print(f"Error: {e}")
        return

    if not cookies:
        print("No X cookies found.")
        return

    cookie_list = []
    for c in cookies:
        cookie_list.append({
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("host", ".x.com"),
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1) or -1,
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", True),
            "sameSite": "None",
        })

    session = {"cookies": cookie_list, "origins": []}
    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    with open(SESSION_PATH, "w") as f:
        json.dump(session, f, indent=2)

    print(f"Saved {len(cookie_list)} cookies to {SESSION_PATH}")
    print("You can now run: python scheduler.py\n")


if __name__ == "__main__":
    main()
