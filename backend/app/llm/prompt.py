import json

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading workstation.
- Analyze portfolio composition, risk concentration, and P&L.
- Suggest trades with reasoning.
- Execute trades when the user asks or agrees, using actions of type buy or sell (ticker, quantity).
- Manage the watchlist proactively with watch_add / watch_remove (ticker).
- Be concise and data-driven.
- Always respond with valid JSON: {"message": str, "actions": [{"type": "buy|sell|watch_add|watch_remove", "ticker": str, "quantity": number}]}.
Use an empty actions list when no action is needed. Trades are market orders filled at current price."""


def build_messages(context: dict, history: list[dict], user_message: str) -> list[dict]:
    system = f"{SYSTEM_PROMPT}\n\nCurrent portfolio context:\n{json.dumps(context, default=str)}"
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history]
    msgs.append({"role": "user", "content": user_message})
    return msgs
