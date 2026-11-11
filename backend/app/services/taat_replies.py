"""
app/services/taat_replies.py — deterministic replies for TAAT actions.

Anything that changes state (or was asked to and didn't) is reported from
the real result of the action, never narrated by the LLM: a model asked to
"confirm" an action will happily describe one that never happened.
"""
from typing import Optional

WRITE_INTENTS = {"DEVICE_CONTROL", "ALARM", "RULE", "USER", "SCHEDULE", "REMEMBER"}


def _fmt_params(params) -> str:
    if not isinstance(params, dict) or not params:
        return "command"
    return ", ".join(f"{k}={v}" for k, v in params.items())


def _fmt_rule(r: dict) -> str:
    return f"{r.get('key')} {r.get('condition')} {r.get('threshold')} ({r.get('severity')})"


def build_action_reply(
    intent: str,
    action: Optional[dict],
    action_result: Optional[dict],
    verification: Optional[dict],
    ctx: dict,
    confirm_required: Optional[dict] = None,
    plan_summary: str = "",
) -> Optional[str]:
    """Exact reply text for a write intent, or None to let the LLM answer."""
    if intent not in WRITE_INTENTS:
        return None

    if confirm_required:
        what = plan_summary or f"{intent.replace('_', ' ').lower()} action"
        return (f"⚠️ High-risk action: {what}. Nothing has been changed yet — "
                f"reply 'proceed' to confirm.")

    r = action_result if isinstance(action_result, dict) else None

    if intent == "ALARM":
        if not r:
            return "⚠️ The alarm action failed — nothing was changed."
        if not r.get("success", True):
            return f"⚠️ Could not update alarms — {r.get('reason', 'unknown error')}."
        count = r.get("count", 0)
        if count == 0:
            return "No matching active alarms found for this request."
        return f"✅ {count} alarm(s) {r.get('action', 'updated')}."

    if intent == "RULE":
        if not r:
            rules = ctx.get("existing_rules") or []
            listing = ("Current rules: " + "; ".join(_fmt_rule(x) for x in rules[:10]) + "."
                       if rules else "There are no threshold rules yet.")
            return ("I couldn't work out which rule to create, change or delete, so nothing was changed. "
                    f"{listing} Example: \"alarm when temperature above 50 on <device>\".")
        if not r.get("success", True):
            return f"⚠️ No rule was changed — {r.get('reason', 'unknown error')}."
        if "deleted" in r:
            return f"✅ Deleted {r['deleted']} rule(s)."
        if r.get("rule_id") and "device" in r:
            return f"✅ Created rule — {_fmt_rule(r)} on {r.get('device')}."
        if r.get("rule_id"):
            n = r.get("updated", 1)
            return f"✅ Updated {n} rules — {_fmt_rule(r)}." if n > 1 else f"✅ Updated rule — {_fmt_rule(r)}."
        return "⚠️ The rule action did not complete — nothing was changed."

    if intent == "USER":
        if not r:
            return ("No user was changed — I couldn't identify a valid user or action. "
                    "Example: \"invite name@example.com as user\".")
        if r.get("error"):
            return f"⚠️ No user was changed — {r['error']}."
        act = r.get("action", "")
        if act == "list":
            users = r.get("users", [])
            if not users:
                return "No users found."
            return f"{len(users)} user(s): " + ", ".join(f"{u['email']} ({u['role']})" for u in users[:20]) + "."
        if act == "invited":
            return (f"✅ Invited {r.get('email')} as {r.get('role')}. Temporary password: {r.get('password')}. "
                    f"Share it securely and have them change it on first login.")
        if act == "deleted":
            return f"✅ Removed user {r.get('email')}."
        if act == "role_changed":
            return f"✅ Changed {r.get('email')}'s role from {r.get('old_role')} to {r.get('new_role')}."
        return "⚠️ The user action did not complete — nothing was changed."

    if intent == "SCHEDULE":
        if not r:
            if action and action.get("action") in (None, "schedule"):
                return "⚠️ Nothing was scheduled — the device or the time could not be resolved."
            return ("I couldn't work out what to schedule, so nothing was scheduled. "
                    "Example: \"schedule turn on led2 on <device> in 2 hours\".")
        if r.get("is_scheduled"):
            when = r.get("scheduled_display") or r.get("human_label") or r.get("scheduled_for", "")
            return f"⏰ Scheduled → {r.get('device_name', 'device')}: {_fmt_params(r.get('params'))} at {when}."
        if r.get("is_schedule_list"):
            rows = r.get("scheduled", [])
            if not rows:
                return "No scheduled commands."
            return f"{len(rows)} scheduled command(s): " + "; ".join(
                f"{x.get('device_name')}: {_fmt_params(x.get('params'))} — {x.get('human_label') or x.get('scheduled_for')}"
                for x in rows[:10]) + "."
        if r.get("is_schedule_cancel"):
            n = r.get("cancelled", 0)
            return f"✅ Cancelled {n} scheduled command(s)." if n else "No scheduled commands to cancel."
        return "⚠️ The schedule action did not complete — nothing was changed."

    if intent == "REMEMBER":
        if r and r.get("success"):
            return f"✅ Saved permanently: \"{r.get('remembered', '')}\". I will use this in all future conversations."
        return "⚠️ I could not save that — nothing was stored."

    # DEVICE_CONTROL
    if not r:
        if action:
            return "⚠️ The command was not sent — it failed before reaching the device."
        return ("I couldn't turn that into a device command, so nothing was sent. "
                "Example: \"turn on led2 on <device>\".")
    if not r.get("success", True):
        return f"⚠️ Command not sent — {r.get('reason', 'unknown error')}."
    dev, prm = r.get("device_name", "device"), _fmt_params(r.get("params"))
    ver = verification if isinstance(verification, dict) else {}
    overall, msg = ver.get("overall", "skipped"), ver.get("message", "")
    if overall == "success" and msg:
        return f"✅ Command executed on {dev}: {prm}. {msg}. Device confirmed the change."
    if overall == "failed" and msg:
        return (f"⚠️ Command was sent to {dev}: {prm}, but {msg}. "
                f"The device may be offline or its firmware did not respond.")
    return f"Command sent to {dev}: {prm}. Waiting for the device to confirm."


def build_chips(intent: str, action_result) -> dict:
    """
    Payloads for the chat UI's action chips. A chip is a green "done" badge,
    so one is only returned for an action that actually succeeded, and in the
    shape the UI expects — never the raw tool result.
    """
    chips = {"rpc_executed": None, "alarm_actioned": None, "rule_actioned": None, "user_actioned": None}
    r = action_result
    if not isinstance(r, dict) or r.get("success") is False or r.get("error"):
        return chips

    if intent in ("DEVICE_CONTROL", "SCHEDULE"):
        chips["rpc_executed"] = r
    elif intent == "ALARM":
        if r.get("count"):
            chips["alarm_actioned"] = r
    elif intent == "RULE":
        if "deleted" in r:
            if not r["deleted"]:
                return chips
            if r.get("scope") == "all":
                chips["rule_actioned"] = {"action": "deleted_all", "count": r["deleted"]}
            else:
                n = r["deleted"]
                chips["rule_actioned"] = {"action": "deleted",
                                          "key": f"{r.get('key')} ({n} rules)" if n > 1 else r.get("key")}
        elif r.get("rule_id") and "device" in r:
            chips["rule_actioned"] = {**r, "action": "created"}
        elif r.get("rule_id"):
            chips["rule_actioned"] = {**r, "action": "updated"}
    elif intent == "USER":
        chips["user_actioned"] = r
    return chips
