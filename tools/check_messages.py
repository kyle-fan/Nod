"""Used by ./doctor.sh. Reads the sidecar's view of Messages (nothing is opened or typed)
and says whether the people in hub/profile.json have a chat on screen."""
import json
import sys
import urllib.request
from pathlib import Path

GREEN, YELLOW, END = "\033[32m", "\033[33m", "\033[0m"


def main() -> int:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8790/screen?app=Messages", timeout=8) as r:
            screen = json.load(r)
        people = list(json.loads((Path(__file__).resolve().parent.parent / "hub/profile.json").read_text()).get("people", {}))
    except Exception as e:   # noqa: BLE001
        print(f"  {YELLOW}CHECK{END} could not read Messages: {e}")
        return 1
    elements = screen.get("elements", [])
    names = [e["label"] for e in elements
             if e["role"] != "textfield" and e["label"] not in ("Send", "Back to conversations")]
    box = [e["label"] for e in elements if e["role"] == "textfield"]
    if box:
        print(f"  {GREEN}OK{END}    Messages: a chat is open ({box[0].split(':')[0]})")
        return 0
    if not names:
        print(f"  {YELLOW}CHECK{END} Messages is open but no chats were found: is its window on screen?")
        return 1
    more = ", ..." if len(names) > 5 else ""
    print(f"  {GREEN}OK{END}    Messages: {len(names)} chats visible ({', '.join(names[:5])}{more})")
    warnings = 0
    for person in people:
        hit = [n for n in names if n.lower() == person.lower() or n.lower().split()[0] == person.lower().split()[0]]
        if hit:
            print(f'  {GREEN}OK{END}    "{person}" from the profile has a chat ({hit[0]})')
        else:
            warnings += 1
            print(f'  {YELLOW}CHECK{END} no chat called "{person}" in the chat list: "tell {person} ..." will say it '
                  "can't see the chat. Rename a contact, or bring that chat to the top")
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
