# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Intent-specific tool handlers extracted from Assistant._handle_tool.

Each function handles one intent and returns a response string.
"""

import logging
import re
import webbrowser
from datetime import datetime

logger = logging.getLogger(__name__)


def _handle_tool(assistant, intent, text):
    """Route intent to the appropriate handler. Returns response string."""
    handler = _HANDLERS.get(intent)
    if handler:
        return handler(assistant, text)
    return ""


# ── Individual intent handlers ──────────────────────────────────────────


def _handle_computer(assistant, text):

    result = assistant.computer_tool.handle_natural(text)
    if isinstance(result, dict):
        if result.get("success"):
            data = result.get("stdout", result.get("data", ""))
            if isinstance(data, dict):
                data = str(data)
            return str(data)[:1000]
        return f"Error: {result.get('error', 'unknown')}"
    return str(result)[:1000]


def _handle_news(assistant, text):
    return assistant.news_tool.handle_natural(text)


def _handle_security(assistant, text):
    return assistant.security_tool.handle_natural(text)


def _handle_cai(assistant, text):
    from .tools.cai_security import run_analysis as cai_analyze
    from .tools.cai_security import run_audit as cai_audit
    from .tools.cai_security import run_threat_analysis as cai_threat

    text_lower = text.lower()
    if "audit" in text_lower:
        return cai_audit()
    if "threat" in text_lower:
        return cai_threat()
    return cai_analyze(text)


def _handle_n8n(assistant, text):
    return assistant.n8n_tool.handle_natural(text)


def _handle_worldmonitor(assistant, text):
    webbrowser.open("https://worldmonitor.app")
    return "🌍 **World Monitor** browser mein khol diya!"


def _handle_memory(assistant, text):
    from . import memory as memory_module

    text_lower = text.lower()
    if "clear all" in text_lower or "sab clear" in text_lower:
        memory_module.clear()
        with assistant._history_lock:
            assistant.history = []
        return f"🧹 Saari memories clear, {assistant._get_prefix()}!"
    if "clear old" in text_lower or "purani clear" in text_lower or "cleanup" in text_lower:
        n = memory_module.apply_cleanup()
        if n > 0:
            return f"🧹 {n} purani memories delete kar di! Ab memory fresh hai."
        return "🧹 Koi purani memory nahi thi delete karne ko."
    if "clear" in text_lower or "forget" in text_lower:
        memory_module.clear()
        with assistant._history_lock:
            assistant.history = []
        return f"🧹 Memory cleared, {assistant._get_prefix()}!"
    if "recall" in text_lower or "previous" in text_lower or "history" in text_lower:
        recent = memory_module.get_recent(5)
        if not recent:
            return "🧠 Koi purani baat nahi mili."
        return "🧠 Purani baatein:\n" + "\n".join([f"- {m['user']} → {m['assistant'][:80]}" for m in recent])
    if "remember" in text_lower:
        match = re.search(r"remember\s+(?:that\s+)?(.+)", text, re.IGNORECASE)
        if match:
            fact = match.group(1)
            memory_module.save_conversation("remember", fact, context="explicit_memory")
            return f"📝 Noted: {fact}"
    return "🧠 Commands: remember, recall, clear, clear old, cleanup"


def _handle_notes(assistant, text):
    from . import notes as notes_module

    text_lower = text.lower()
    if "search" in text_lower or "dhund" in text_lower or "find" in text_lower:
        q = text_lower
        for w in ["search", "dhund", "find", "notes", "note", "mein", "my", "meri", "dikha", "show", "kar", "karo"]:
            q = q.replace(w, "")
        q = q.strip().strip('"').strip("'").strip()
        if not q:
            return "Kya search karun notes mein? 'search notes budget' bolo."
        results = notes_module.search(q)
        if not results:
            return f"🔍 '{q}' se koi note nahi mila."
        return "🔍 **Notes search results:**\n" + notes_module.format_list(results)
    if (
        "show" in text_lower
        or "dikha" in text_lower
        or "list" in text_lower
        or "meri" in text_lower
        or "my" in text_lower
    ):
        all_notes = notes_module.get_all()
        if not all_notes:
            return "📋 Koi notes nahi hai. 'note banao' ya 'remember this' bolo."
        return "📋 **Your Notes:**\n" + notes_module.format_list(all_notes)
    if "delete" in text_lower or "remove" in text_lower or "hat" in text_lower or "clear" in text_lower:
        m = re.search(r"(?:note|#)?\s*(\d+)", text)
        if m:
            nid = int(m.group(1))
            if notes_module.delete(nid):
                return f"🗑 Note #{nid} delete kar diya!"
            return f"❌ Note #{nid} nahi mila."
        return "Kaunsa note delete karun? Note # batao."
    if "pin" in text_lower or "unpin" in text_lower:
        m = re.search(r"(?:note|#)?\s*(\d+)", text)
        if m:
            nid = int(m.group(1))
            n = notes_module.toggle_pin(nid)
            if n:
                return f"{'📌' if n['pinned'] else '📍'} Note #{nid} {'pin' if n['pinned'] else 'unpin'} kar diya!"
            return f"❌ Note #{nid} nahi mila."
        return "Kaunsa note pin/unpin karun? Note # batao."
    title = (
        text.replace("note", "", 1)
        .replace("remember this", "", 1)
        .replace("note down", "", 1)
        .replace("save this", "", 1)
        .replace("write this down", "", 1)
        .strip()
        .strip(",:;")
    )
    if not title:
        return "Kya note karun? Kuch text batao."
    note = notes_module.add(title[:80], title[80:] if len(title) > 80 else "")
    return f"📝 Note #{note['id']} saved: **{note['title']}**\n'notes dikhao' se dekh sakte ho."


def _handle_focus(assistant, text):
    from . import focus_timer as focus_module

    text_lower = text.lower()
    st = focus_module.status()
    if "stop" in text_lower or "band" in text_lower:
        focus_module.stop()
        return f"⏱ Focus session stopped. You did great, {assistant._get_prefix()}!"
    if (
        "time" in text_lower
        or "kitna" in text_lower
        or "baki" in text_lower
        or "left" in text_lower
        or "status" in text_lower
    ):
        return focus_module.format_status(st)
    if "stats" in text_lower or "today" in text_lower or "aaj" in text_lower:
        s = focus_module.today_stats()
        return f"📊 **Today's Focus:** {s['focus_minutes']} min | {s['sessions']} sessions | {s['breaks']} breaks"
    if "week" in text_lower or "hafte" in text_lower:
        w = focus_module.week_stats()
        total = sum(d["focus_minutes"] for d in w.values())
        return f"📊 **Week Focus:** {total} min across {len(w)} days"
    if st.get("running"):
        return focus_module.format_status(st)
    m = re.search(r"(\d+)\s*(min|minute|minut|hours|hour|hr)?", text_lower)
    mins = int(m.group(1)) if m else 25
    focus_module.set_callbacks()
    focus_module.start(mins)
    return f"🎯 Focus mode ON! {mins} minutes. Stay sharp, {assistant._get_prefix()}! Type 'focus' for status."


def _handle_clipboard(assistant, text):
    from . import smart_actions

    text_lower = text.lower()
    if "history" in text_lower or "dikha" in text_lower or "show" in text_lower:
        items = smart_actions.get_history(10)
        if not items:
            return "📋 Clipboard history khali hai."
        return smart_actions.format_history(items)
    if "clear" in text_lower or "clean" in text_lower or "saaf" in text_lower:
        smart_actions.clear_history()
        return "📋 Clipboard history clear kar di."
    if "last" in text_lower or "latest" in text_lower:
        items = smart_actions.get_history(1)
        if items:
            preview = items[0]["text"][:200]
            actions = smart_actions.analyze(items[0]["text"])
            result = f"📋 **Last copy:**\n{preview}\n"
            if actions["has_actions"]:
                result += "\n**Smart Actions:**\n" + "\n".join(f"- {a['label']}" for a in actions["actions"])
            return result
        return "📋 Clipboard khali hai."
    items = smart_actions.get_history(5)
    if items:
        return smart_actions.format_history(items)
    return "📋 Clipboard khali hai. Kuch copy karo, main track karunga!"


def _handle_bookmark(assistant, text):
    from . import bookmarks as bookmarks_module

    text_lower = text.lower()
    if (
        "show" in text_lower
        or "dikha" in text_lower
        or "list" in text_lower
        or "meri" in text_lower
        or "my" in text_lower
    ):
        all_bm = bookmarks_module.get_all()
        if not all_bm:
            return "🔖 Koi bookmarks nahi hai. 'bookmark this' bolo kuch save karne ke liye."
        return bookmarks_module.format_list(all_bm)
    if "search" in text_lower or "dhund" in text_lower or "find" in text_lower:
        q = text_lower.replace("search", "").replace("dhund", "").replace("find", "").replace("bookmark", "").strip()
        if q:
            results = bookmarks_module.search(q)
            return bookmarks_module.format_search(results) if results else f"🔍 '{q}' se koi bookmark nahi mila."
        return "Kya search karun? 'search bookmark react' bolo."
    if "delete" in text_lower or "remove" in text_lower or "hat" in text_lower:
        m = re.search(r"(?:bookmark|#)?\s*(\d+)", text)
        if m:
            bid = int(m.group(1))
            if bookmarks_module.delete(bid):
                return f"🗑 Bookmark #{bid} delete kar diya!"
            return f"❌ Bookmark #{bid} nahi mila."
        return "Kaunsa bookmark delete karun? ID batao."
    urls = bookmarks_module.URL_RE.findall(text)
    if urls:
        tag_str = text_lower.replace(urls[0].lower(), "").strip().strip(",").strip()
        tags = [w.strip() for w in tag_str.split() if len(w.strip()) > 2][:5] if tag_str else []
        bm = bookmarks_module.add(urls[0], title=text[:60], tags=tags)
        return f"🔖 Bookmark #{bm['id']} saved: **{bm['title'][:40]}**\n{bm['url'][:50]}"
    try:
        import pyperclip

        cb = pyperclip.paste()
        if cb and bookmarks_module.URL_RE.match(cb):
            bm = bookmarks_module.add(cb, title=text[:60])
            return f"🔖 Bookmark #{bm['id']} saved from clipboard!"
    except Exception:
        logger.exception("Failed to save bookmark from clipboard")
    return "Kya bookmark karun? URL batao ya 'show bookmarks' bolo."


def _handle_calculate(assistant, text):
    from . import calculator as calc_module

    result = calc_module.calculate(text)
    if result["type"] == "error":
        return f"🧮 {result['display']}"
    return f"🧮 **{result['display']}**"


def _handle_search(assistant, text):
    from . import conv_search as conv_module

    text_lower = text.lower()
    for prefix in ["search chat", "search conversation", "search history", "find in chat", "dhund", "search karo"]:
        text_lower = text_lower.replace(prefix, "")
    q = text_lower.strip().strip(":").strip()
    m = re.search(r'"(.+?)"', text, re.DOTALL)
    if m:
        q = m.group(1)
    if not q or len(q) < 2:
        s = conv_module.stats()
        return f"🔍 Kya dhundhna hai? Total {s['total']} messages indexed. Example: 'search python code'"
    results = conv_module.search(q)
    return conv_module.format_results(results, q)


def _handle_habit(assistant, text):
    from . import habits as habits_module

    text_lower = text.lower()
    if (
        "show" in text_lower
        or "dikha" in text_lower
        or "list" in text_lower
        or "meri" in text_lower
        or "my" in text_lower
        or "all" in text_lower
    ):
        if "today" in text_lower or "aaj" in text_lower:
            return habits_module.format_today()
        return habits_module.format_list()
    if "add" in text_lower or "new" in text_lower or "naya" in text_lower:
        name = (
            text_lower.replace("add habit", "")
            .replace("new habit", "")
            .replace("naya habit", "")
            .replace("add", "")
            .strip()
            .strip(".:;,")
            .title()
        )
        if not name or len(name) < 2:
            return "Habit ka naam batao. Jaise: 'add habit exercise daily'"
        freq = "daily"
        if "weekly" in text_lower:
            freq = "weekly"
        elif "weekend" in text_lower:
            freq = "weekend"
        h = habits_module.add(name, freq)
        return (
            f"✅ Habit **#{h['id']} {h['name']}** add kar diya! [{freq}]\n'log habit {h['id']}' se mark kar sakte ho."
        )
    if "delete" in text_lower or "remove" in text_lower or "hat" in text_lower:
        m = re.search(r"(?:habit|#)?\s*(\d+)", text)
        if m:
            hid = int(m.group(1))
            if habits_module.delete(hid):
                return f"🗑 Habit #{hid} delete kar diya!"
            return f"❌ Habit #{hid} nahi mila."
        return "Kaunsa habit delete karun? ID batao."
    if "log" in text_lower or "mark" in text_lower or "done" in text_lower or "karo" in text_lower:
        m = re.search(r"(?:habit|#)?\s*(\d+)", text)
        if m:
            hid = int(m.group(1))
            if habits_module.log(hid):
                h = next((x for x in habits_module.get_all() if x["id"] == hid), None)
                streak = h.get("streak", 0) if h else 0
                return f"✅ Habit #{hid} done! 🔥{streak}d streak"
            return f"⚠️ Habit #{hid} already marked today!"
        return "Kaunsa habit log karun? ID batao. Jaise: 'log habit 3'"
    if "streak" in text_lower or "kitne" in text_lower:
        items = habits_module.today_status()
        if not items:
            return "Koi habits nahi hai."
        lines = ["🔥 **Habit Streaks:**"]
        for i in items:
            fire = "🔥" * min(i["streak"], 5) if i["streak"] > 0 else "⬜"
            lines.append(f"  #{i['id']} {i['name']}: {i['streak']}d streak {fire} (best: {i['best_streak']}d)")
        return "\n".join(lines)
    m = re.search(r"#?\s*(\d+)", text)
    if m:
        hid = int(m.group(1))
        if habits_module.log(hid):
            return f"✅ Habit #{hid} done!"
    return habits_module.format_today()


def _handle_random(assistant, text):
    from . import random_utils as random_mod

    result = random_mod.parse_text(text)
    return f"🎲 {random_mod.format_result(result)}"


def _handle_text(assistant, text):
    from . import text_utils as text_mod

    result = text_mod.parse_and_handle(text)
    return result["result"]


def _handle_stopwatch(assistant, text):
    from . import stopwatch as sw_mod

    tl = text.lower()
    if "start" in tl:
        sw_mod.start()
        return "⏱ Stopwatch start!"
    if "lap" in tl:
        t = sw_mod.lap()
        return f"⏱ Lap #{sw_mod.status()['laps']}: **{sw_mod.format_time(t)}**"
    if "stop" in tl or "band" in tl:
        t = sw_mod.stop()
        return f"⏱ Stopwatch stopped: **{sw_mod.format_time(t)}**"
    if "reset" in tl:
        sw_mod.reset()
        return "⏱ Stopwatch reset."
    return sw_mod.format_status()


def _handle_password(assistant, text):
    from . import password_gen as pwd_mod

    result = pwd_mod.parse_text(text)
    return f"🔑 {pwd_mod.format_result(result)}"


def _handle_sysinfo(assistant, text):
    from . import sysinfo as sys_mod

    tl = text.lower()
    if "battery" in tl or "batt" in tl:
        if "charge" in tl:
            return None
        return sys_mod.format_battery()
    if "cpu" in tl:
        return sys_mod.format_cpu()
    return sys_mod.format_info()


def _handle_list(assistant, text):
    from . import lists as lists_mod

    tl = text.lower()

    # Content-generation asks are NOT list-management ops. "Give me a list of
    # 3 programming languages" must reach the LLM, not become a shopping-list
    # add (E4 instruction_following root cause). Return None = fall through to LLM.
    _GEN = ("give me", "list of", "tell me", "what are", "which are",
            "examples of", "name some", "how many", "top ")
    _OPS = ("add", "append", "show", "display", "create", "new", "naya",
            "remove", "delete", "check", "mark", "done", "dikha", "clear",
            "daal", "dal", "hat")
    if any(g in tl for g in _GEN) and not any(re.search(r"\b" + re.escape(op) + r"\b", tl) for op in _OPS):
        return None

    list_name = "shopping"
    for possible in ["shopping", "grocery", "sabji", "samaan", "todo", "tasks", "general", "work", "personal"]:
        if possible in tl:
            list_name = possible
            break
    if "create" in tl or "new" in tl or "naya" in tl:
        lists_mod.add_item(list_name, "(list created)")
        return f"📋 List **{list_name}** create kar di! 'add milk to {list_name}' se items add karo."
    if "show" in tl or "dikha" in tl or "display" in tl or "all" in tl:
        if "all" in tl and "list" in tl:
            return lists_mod.format_all_lists()
        return lists_mod.format_list(list_name)
    if "check" in tl or "mark" in tl or "done" in tl:
        m = re.search(r"#?\s*(\d+)", text)
        if m:
            iid = int(m.group(1))
            if lists_mod.check_item(list_name, iid):
                return f"✅ Item #{iid} checked off {list_name} list!"
            return f"⚠️ Item #{iid} nahi mila."
        return "Kaunsa item? ID batao. Jaise 'check item 3'"
    if "uncheck" in tl or "undone" in tl:
        m = re.search(r"#?\s*(\d+)", text)
        if m:
            iid = int(m.group(1))
            lists_mod.check_item(list_name, iid, False)
            return f"⬜ Item #{iid} unchecked."
    if "remove" in tl or "delete" in tl or "hat" in tl:
        if "list" in tl:
            lists_mod.delete_list(list_name)
            return f"🗑 List **{list_name}** delete kar di."
        m = re.search(r"#?\s*(\d+)", text)
        if m:
            iid = int(m.group(1))
            if lists_mod.remove_item(list_name, iid):
                return f"🗑 Item #{iid} removed from {list_name}."
            return f"⚠️ Item #{iid} nahi mila."
        return "Kaunsa item remove karun? ID batao."
    item_text = text
    for p in ["add", "list", list_name, "mein", "mai", "me", "dal do", "dal de", "daal", "rakh"]:
        item_text = item_text.replace(p, "")
    item_text = item_text.strip().strip(".,:;").strip()
    if item_text and len(item_text) > 1:
        items = lists_mod.add_items_from_text(list_name, item_text)
        return f"📋 {len(items)} item(s) **{list_name}** mein add! '{' '.join(i['text'] for i in items[:3])}'"
    return f"📋 **{lists_mod.format_list(list_name)}**\nKya add karun? Baat batao."


def _handle_countdown(assistant, text):
    from . import countdowns as cd_mod

    tl = text.lower()
    if "show" in tl or "dikha" in tl or "list" in tl or "all" in tl or "upcoming" in tl or "events" in tl:
        return cd_mod.format_events()
    if "delete" in tl or "remove" in tl or "hat" in tl:
        m = re.search(r"#?\s*(\d+)", text)
        if m:
            eid = int(m.group(1))
            if cd_mod.delete(eid):
                return f"🗑 Event #{eid} delete kar diya!"
            return f"❌ Event #{eid} nahi mila."
        return "Kaunsa event delete karun? ID batao."
    if "search" in tl or "find" in tl:
        q = tl.replace("search", "").replace("find", "").replace("event", "").strip()
        if q:
            results = cd_mod.search(q)
            if results:
                return cd_mod.format_events(results)
            return f"🔍 '{q}' se koi event nahi mila."
    return cd_mod.parse_and_add(text)


def _handle_json(assistant, text):
    from . import json_tools as json_mod

    result = json_mod.parse_and_handle(text)
    return result["result"]


def _handle_journal(assistant, text):
    from . import journal as journal_mod

    tl = text.lower()
    if "show" in tl or "dikha" in tl or "display" in tl or "all" in tl:
        entries = journal_mod.get_all()
        return journal_mod.format_entries(entries, "All Entries")
    if "today" in tl or "aaj" in tl:
        entries = journal_mod.get_today()
        return journal_mod.format_entries(entries, "Today")
    if "search" in tl or "find" in tl or "dhund" in tl:
        q = (
            tl.replace("search", "")
            .replace("find", "")
            .replace("dhund", "")
            .replace("journal", "")
            .replace("diary", "")
            .replace("entry", "")
            .strip()
        )
        if q:
            entries = journal_mod.search(q)
            return journal_mod.format_entries(entries, f"'{q}' results")
        return "Kya search karun? 'search journal meeting' bolo."
    if "stats" in tl or "statistics" in tl:
        s = journal_mod.stats()
        return f"📓 **Journal Stats:**\n  Total entries: {s['total']}\n  Active days: {s['days']}\n  Current streak: {s['streak']}d\n  Avg words/entry: {s['avg_words']}"
    if "delete" in tl or "remove" in tl:
        m = re.search(r"#?\s*(\d+)", text)
        if m:
            eid = int(m.group(1))
            if journal_mod.delete(eid):
                return f"🗑 Journal entry #{eid} delete kar di!"
            return f"❌ Entry #{eid} nahi mila."
        return "Kaunsa entry delete karun? ID batao."
    entry_text = text
    for p in ["journal", "diary", "dairy", "entry", "note", "likh", "likho", "add", "new"]:
        entry_text = entry_text.replace(p, "")
    entry_text = entry_text.strip().strip(".,:;").strip()
    if entry_text and len(entry_text) > 2:
        entry = journal_mod.add_entry(entry_text)
        return f'📓 Journal entry #{entry["id"]} add! "{entry["text"][:80]}"'
    return "📓 Kya likhna hai? 'journal aaj bahut kaam kiya' bolo."


def _handle_baseconv(assistant, text):
    from . import baseconv as baseconv_mod

    result = baseconv_mod.parse_and_handle(text)
    return result["result"]


def _handle_emoji(assistant, text):
    from . import emoji as emoji_mod

    result = emoji_mod.parse_text(text)
    return result["result"]


def _handle_flashcard(assistant, text):
    from . import flashcards as card_mod

    tl = text.lower()
    if "add" in tl or "new" in tl or "create" in tl:
        card = card_mod.parse_add_text(text)
        if card:
            return f"🃏 Card #{card['id']} added! [{card['deck']}] {card['front'][:30]} — {card['back'][:30]}"
        return "Format: 'add card Question / Answer' ya 'card What is Python / A language python'"
    if "show" in tl or "dikha" in tl or "all" in tl or "list" in tl:
        deck = "general"
        for d in card_mod._KNOWN_DECKS:
            if d in tl:
                deck = d
                break
        cards = card_mod.get_all(deck)
        return card_mod.format_cards(cards, f"Deck: {deck}")
    if "due" in tl or "study" in tl or "review" in tl or "practice" in tl:
        deck = ""
        for d in card_mod._KNOWN_DECKS:
            if d in tl:
                deck = d
                break
        due = card_mod.get_due(deck)
        return card_mod.format_cards(due, f"Due cards {deck}")
    if "stat" in tl or "stats" in tl:
        s = card_mod.get_stats()
        return f"🃏 **Flashcard Stats:** {s['total']} cards, {s['due']} due, {s['mastered']} mastered, avg {s['avg_correct']} correct"
    if "delete" in tl or "remove" in tl:
        m = re.search(r"#?\s*(\d+)", text)
        if m:
            cid = int(m.group(1))
            if card_mod.delete(cid):
                return f"🗑 Card #{cid} delete!"
            return f"❌ Card #{cid} nahi mila."
        return "Kaunsa card delete karun? ID batao."
    deck = ""
    for d in card_mod._KNOWN_DECKS:
        if d in tl:
            deck = d
    due = card_mod.study_session(deck)
    if not due:
        return "🃏 Sarre cards revise ho gaye! Kal vapas aana."
    return card_mod.format_cards(due, "Study Session")


def _handle_expense(assistant, text):
    from . import expenses as exp_mod

    tl = text.lower()
    if "today" in tl or "aaj" in tl or "dikha" in tl:
        return exp_mod.format_today()
    if "month" in tl or "mahina" in tl or "this month" in tl or "iss mahine" in tl:
        from datetime import date

        return exp_mod.format_month(date.today().year, date.today().month)
    if "delete" in tl or "remove" in tl:
        m = re.search(r"#?\s*(\d+)", text)
        if m:
            eid = int(m.group(1))
            if exp_mod.delete(eid):
                return f"🗑 Expense #{eid} delete kar di!"
            return f"❌ Expense #{eid} nahi mila."
        return "Kaunsa expense delete? ID batao."
    return exp_mod.parse_and_add(text)


def _handle_color(assistant, text):
    from . import colorconv as color_mod

    result = color_mod.parse_and_handle(text)
    return result["result"]


def _handle_music(assistant, text):
    from . import music_ctrl as music_mod

    result = music_mod.parse_text(text)
    return result["result"]


def _handle_network(assistant, text):
    from . import network as net_mod

    return net_mod.format_info()


def _handle_files(assistant, text):
    from . import file_tools as ft_mod

    result = ft_mod.parse_text(text)
    return result["result"]


def _handle_briefing(assistant, text):
    from . import briefing as brief_mod

    return brief_mod.format_briefing()


def _handle_timer(assistant, text):
    from . import timer as timer_mod

    result = timer_mod.parse_text(text)
    return result["result"]


def _handle_decision(assistant, text):
    from . import decisions as decide_mod

    result = decide_mod.parse_text(text)
    return result["result"]


def _handle_websearch(assistant, text):
    from . import websearch as web_mod

    result = web_mod.parse_text(text)
    return result["result"]


def _handle_share(assistant, text):
    from . import share as share_mod

    result = share_mod.parse_text(text)
    return result["result"]


def _handle_quote(assistant, text):
    from . import quotes as quote_mod

    result = quote_mod.parse_text(text)
    return result["result"]


def _handle_screen(assistant, text):
    from . import screen as screen_module

    assistant._emit_status("thinking", "Capturing screen...")
    screenshot_path = screen_module.capture_screen()
    if not screenshot_path:
        return "❌ Screenshot nahi le paya."
    analysis = screen_module.analyze_screenshot(screenshot_path, text)
    screen_module.cleanup(screenshot_path)
    if isinstance(analysis, dict):
        return analysis.get("text", "")
    return analysis


def _handle_screen_share(assistant, text):
    from . import screen_sharing as screen_sharing_module

    text_lower = text.lower()
    if "stop" in text_lower or "band" in text_lower:
        screen_sharing_module.stop()
        return f"🖥 Screen sharing band kar diya, {assistant._get_prefix()}!"
    if screen_sharing_module.is_active():
        return "🖥 Screen sharing already active hai. 'stop screen sharing' bolo band karne ke liye."
    screen_sharing_module.start(quality=60, fps=2)
    return "🖥 Screen sharing start kar diya! EDITH app pe dekho.\nBand karne ke liye 'stop screen sharing' bolo."


def _handle_dictation(assistant, text):
    return "🎤 **Dictation:** Use `Ctrl+Alt+D` hotkey. Hold → speak → release → auto-paste."


def _handle_schedule(assistant, text):
    from . import scheduler

    text_lower = text.lower()
    if "clear all" in text_lower or "sab delete" in text_lower:
        for t in scheduler.list_tasks():
            scheduler.delete_task(t["id"])
        return "🗑 Saare tasks delete! Schedule fresh hai."
    if (
        "clear" in text_lower
        or "delete" in text_lower
        or "remove" in text_lower
        or "hat" in text_lower
        or "cancel" in text_lower
    ):
        m = re.search(r"(?:task|#)?\s*(\d+)", text)
        if m:
            tid = int(m.group(1))
            if scheduler.delete_task(tid):
                return f"🗑 Task #{tid} delete kar diya!"
            return f"❌ Task #{tid} nahi mila."
        tasks = scheduler.list_tasks(status="pending")
        if not tasks:
            return "📭 Koi task nahi hai delete karne ko."
        return "Kaunsa task delete karun? Task number batao:\n" + "\n".join(
            [f"  #{t['id']} - {t['title']}" for t in tasks[:10]]
        )
    if (
        "done" in text_lower
        or "complete" in text_lower
        or "finish" in text_lower
        or "ho gaya" in text_lower
        or "mark" in text_lower
    ):
        m = re.search(r"(?:task|#)?\s*(\d+)", text)
        if m:
            tid = int(m.group(1))
            t = scheduler.update_task(tid, status="done")
            if t:
                return f"✅ Task #{tid} '{t['title']}' done! 🎉"
            return f"❌ Task #{tid} nahi mila."
        return "Kaunsa task complete hua? Task # batao."
    if (
        "list" in text_lower
        or "show" in text_lower
        or "all" in text_lower
        or "dikha" in text_lower
        or "pending" in text_lower
        or "summary" in text_lower
        or "overdue" in text_lower
        or "status" in text_lower
    ):
        return scheduler.get_summary()
    parsed = scheduler.parse_natural(text)
    if parsed:
        task = scheduler.create_task(**parsed)
        due_str = ""
        if task.get("due"):
            try:
                dt = datetime.fromisoformat(task["due"])
                due_str = f" ({dt.strftime('%d %b %I:%M %p').lstrip('0')})"
            except Exception:
                due_str = f" ({task['due']})"
        return f"📌 Task #{task['id']} create: **{task['title']}**{due_str}"
    return "📋 **Schedule commands:**\n- 'remind me to X at Y'\n- 'show tasks'\n- 'task #5 done'\n- 'delete task #3'\n- 'schedule Z for tomorrow'"


def _handle_pipeline(assistant, text):
    try:
        from .agents.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator(assistant=assistant)
        result = orch.run(text)
        return result.get("result", "Pipeline completed but no result.")
    except Exception as e:
        return f"[Pipeline error: {e}]"


def _handle_meeting(assistant, text):
    from . import meeting as meeting_module

    text_lower = text.lower()
    if "join" in text_lower:
        m = re.search(r"(https?://\S+)", text)
        url = m.group(1) if m else ""
        if not url:
            return f"Pehle URL share karo, {assistant._get_prefix()}!"
        ok = meeting_module.join_meeting(url)
        if not ok:
            return "❌ Link open nahi ho payi."
        if "record" in text_lower or "and record" in text_lower:
            res = meeting_module.start_meeting()
            if res.get("success"):
                return f"🤝 Link open kiya + 🎙️ recording start: {url}"
            return f"🤝 Link open kiya par recording start nahi ho payi: {res.get('error')}"
        return f"🤝 Meeting link open kiya: {url}"
    if "start" in text_lower or "listen" in text_lower or "record" in text_lower:
        title = None
        if "start meeting" in text_lower:
            t = text_lower.split("start meeting", 1)[1].strip().strip('"').strip("'")
            if t:
                title = t
        elif "record" in text_lower:
            t = text_lower.split("record", 1)[1].replace("meeting", "").strip().strip('"').strip("'")
            if t:
                title = t
        res = meeting_module.start_meeting(title=title)
        if res.get("success"):
            return f"🎙️ Meeting recording start kar di! Title: {res.get('title')}"
        return f"⚠️ Status: {res.get('error')}"
    if "stop" in text_lower or "end" in text_lower or "summarize" in text_lower:
        res = meeting_module.stop_meeting()
        if not res.get("success"):
            return f"⚠️ Error: {res.get('error')}"
        if "meeting" in res:
            m = res["meeting"]
            return f"📝 **Meeting Logged!**\n**Title**: {m['title']}\n**Summary**: {m['summary']}"
        return "🎙️ Meeting stopped but transcript was empty."
    if "simulate" in text_lower:
        topic = (
            text_lower.replace("simulate meeting on", "")
            .replace("simulate meeting", "")
            .replace("simulate", "")
            .strip()
        )
        if not topic:
            topic = "General Sync"
        res = meeting_module.simulate_meeting(topic)
        if res.get("success"):
            m = res["meeting"]
            return f"🤝 **Simulated Meeting on {topic}**\n**Summary**: {m['summary']}"
        return f"❌ Simulation error: {res.get('error')}"

    if "brief" in text_lower:
        topic = (
            text_lower.replace("meeting", "")
            .replace("brief", "")
            .replace("generate", "")
            .replace("make", "")
            .replace("for", "")
            .strip()
        )
        if not topic:
            topic = "General Meeting"
        res = meeting_module.generate_meeting_brief(topic)
        return f"🗒️ **Meeting Brief: {topic}**\n\n{res['brief']}"

    if "status" in text_lower or "chal raha" in text_lower or "recording" in text_lower:
        st = meeting_module.get_status()
        if st.get("active"):
            return (
                f"🎙️ **Recording active:** {st.get('title')}\n"
                f"⏱ Lines captured: {st.get('transcript_count')}\n"
                f"'stop meeting' bolo summary banwane ke liye."
            )
        return "🎙️ Koi active meeting recording nahi hai. 'start meeting' se shuru karo."

    if "search" in text_lower or "dhund" in text_lower or "find meeting" in text_lower:
        q = (
            text_lower.replace("search meeting", "")
            .replace("search meetings", "")
            .replace("search", "")
            .replace("meeting", "")
            .replace("dhund", "")
            .replace("find", "")
            .strip()
        )
        results = meeting_module.search_meetings(q)
        if not q:
            return "Kya dhundhna hai meetings mein? 'search meeting budget' jaisa bolo."
        if results:
            return f"🔍 **{len(results)} match** for '{q}':\n" + meeting_module.format_meetings(results)
        return f"🔍 '{q}' se koi meeting nahi mila."

    if "export" in text_lower:
        to_file = "file" in text_lower or "save to file" in text_lower or "download" in text_lower
        if "all" in text_lower or "everything" in text_lower:
            if to_file:
                res = meeting_module.export_all_meetings_to_file()
                if res["success"]:
                    return f"📤 Saari meetings file mein save: {res['path']}"
                return "⚠️ Export fail ho gaya."
            md = meeting_module.export_all_meetings()
            return f"📤 **All Meetings export (Markdown):**\n\n```markdown\n{md}\n```"
        m = re.search(r"#\s*(\d+)", text)
        if m:
            if to_file:
                res = meeting_module.export_meeting_to_file(int(m.group(1)))
                if res["success"]:
                    return f"📤 Meeting #{m.group(1)} file mein save: {res['path']}"
                return f"⚠️ {res.get('error')}"
            md = meeting_module.export_meeting(int(m.group(1)))
            if md:
                return f"📤 **Meeting #{m.group(1)} export (Markdown):**\n\n```markdown\n{md}\n```"
            return f"❌ Meeting #{m.group(1)} nahi mila."
        return "Kaunsa meeting export karun? 'export meeting #3' ya 'export all meetings' bolo."

    if ("save" in text_lower or "add" in text_lower) and ("note" in text_lower or "notes" in text_lower):
        m = re.search(r"#\s*(\d+)", text)
        if m:
            res = meeting_module.save_meeting_to_notes(int(m.group(1)))
            if res.get("success"):
                return (
                    f"📝 Meeting #{m.group(1)} ko note #{res['note_id']} mein save kiya! 'show notes' se dekh sakte ho."
                )
            return f"⚠️ {res.get('error')}"
        return "Kaunsa meeting notes mein save karun? 'meeting #3 save to notes' bolo."

    if (
        "action item" in text_lower
        or ("task" in text_lower and "action" in text_lower)
        or ("action" in text_lower and "task" in text_lower)
    ):
        m = re.search(r"#\s*(\d+)", text)
        if m:
            res = meeting_module.add_action_items_to_tasks(int(m.group(1)))
            if not res.get("success"):
                return f"⚠️ {res.get('error')}"
            if not res["created"]:
                return res["message"]
            lines = [res["message"]]
            for c in res["created"]:
                tag = f" ({c['assignee']})" if c["assignee"] else ""
                lines.append(f"  ✅ Task #{c['id']}: {c['title']}{tag}")
            return "\n".join(lines)
        return "Kaunsi meeting ke action items tasks mein add karun? 'meeting #3 action items to tasks' bolo."

    if "stats" in text_lower or "statistic" in text_lower:
        s = meeting_module.get_stats()
        return (
            f"📊 **Meeting Stats:**\n"
            f"  Total meetings: {s['total']}\n"
            f"  Total action items: {s['total_action_items']}\n"
            f"  Last meeting: {s['last_meeting'] or 'None'}"
        )

    if "schedule" in text_lower and "meeting" in text_lower:
        mm = re.search(r"meeting\s+(.+?)\s+(?:at|for|on|@)\s+(.+)", text_lower)
        if mm:
            title = mm.group(1).strip()
            when = mm.group(2).strip()
            res = meeting_module.schedule_meeting(title, when)
            if res.get("success"):
                when_str = datetime.fromisoformat(res["meeting"]["when"]).strftime("%d %b %Y %I:%M %p")
                return f"📅 Meeting scheduled: **{title}** @ {when_str}"
            return f"⚠️ {res.get('error')}"
        return "Format: 'schedule meeting Standup at 5pm' ya 'schedule meeting Sync for tomorrow 10am'."

    if "scheduled" in text_lower or "upcoming meetings" in text_lower or "my scheduled" in text_lower:
        if "delete" in text_lower or "remove" in text_lower or "cancel" in text_lower or "hat" in text_lower:
            m = re.search(r"(?:meeting|#)?\s*(\d+)", text)
            if m:
                mid = int(m.group(1))
                if meeting_module.delete_scheduled_meeting(mid):
                    return f"🗑 Scheduled meeting #{mid} cancel kar diya!"
                return f"❌ Scheduled meeting #{mid} nahi mila."
            return "Kaunsa scheduled meeting cancel karun? ID batao."
        return meeting_module.format_scheduled_meetings()

    if "delete" in text_lower or "remove" in text_lower or "hat" in text_lower:
        m = re.search(r"(?:meeting|#)?\s*(\d+)", text)
        if m:
            mid = int(m.group(1))
            if meeting_module.delete_meeting(mid):
                return f"🗑 Meeting #{mid} delete kar diya!"
            return f"❌ Meeting #{mid} nahi mila."
        return "Kaunsa meeting delete karun? Meeting # batao. 'show meetings' se list dekh sakte ho."

    if (
        "show" in text_lower
        or "list" in text_lower
        or "history" in text_lower
        or "dikha" in text_lower
        or "my meetings" in text_lower
        or "past meetings" in text_lower
        or "all meetings" in text_lower
        or ("meeting" in text_lower and "#" in text_lower)
    ):
        if "#" in text_lower:
            m = re.search(r"#\s*(\d+)", text)
            if m:
                return meeting_module.format_meeting_detail(int(m.group(1)))
        return meeting_module.format_meetings(meeting_module.list_meetings())

    meetings = meeting_module.list_meetings()
    if meetings:
        return meeting_module.format_meetings(meetings)
    return "🤝 Meeting helper. Zoom/Meet join karne, 'start meeting' record karne, ya 'simulate meeting on X' try karo."


# ── Dispatch table ──────────────────────────────────────────────────────

_HANDLERS = {
    "computer": _handle_computer,
    "news": _handle_news,
    "security": _handle_security,
    "cai": _handle_cai,
    "n8n": _handle_n8n,
    "worldmonitor": _handle_worldmonitor,
    "memory": _handle_memory,
    "notes": _handle_notes,
    "focus": _handle_focus,
    "clipboard": _handle_clipboard,
    "bookmark": _handle_bookmark,
    "calculate": _handle_calculate,
    "search": _handle_search,
    "habit": _handle_habit,
    "random": _handle_random,
    "text": _handle_text,
    "stopwatch": _handle_stopwatch,
    "password": _handle_password,
    "sysinfo": _handle_sysinfo,
    "list": _handle_list,
    "countdown": _handle_countdown,
    "json": _handle_json,
    "journal": _handle_journal,
    "baseconv": _handle_baseconv,
    "emoji": _handle_emoji,
    "flashcard": _handle_flashcard,
    "expense": _handle_expense,
    "color": _handle_color,
    "music": _handle_music,
    "network": _handle_network,
    "files": _handle_files,
    "briefing": _handle_briefing,
    "timer": _handle_timer,
    "decision": _handle_decision,
    "websearch": _handle_websearch,
    "share": _handle_share,
    "quote": _handle_quote,
    "screen": _handle_screen,
    "screen_share": _handle_screen_share,
    "dictation": _handle_dictation,
    "schedule": _handle_schedule,
    "pipeline": _handle_pipeline,
    "meeting": _handle_meeting,
}
