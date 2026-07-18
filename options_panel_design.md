# Design: Persistent Options Panel (Option A)

Add a **persistent reply keyboard** — a small grid of buttons pinned above the text
input — so users tap options instead of typing commands. This is the Telegram feature that
literally sits "instead of commands": tapping a button sends its label as an ordinary text
message, which the bot routes to a handler.

> Scope: `src/chatcheck_bot/bot.py` only. No database, infra, or deployment changes.
> The check-in flow, callback data, and cron are untouched.

> **Status — implemented & verified (2026-07-18).** Built against `python-telegram-bot`
> **21.11.1**; `ruff check` and `ruff format --check` clean, full `pytest` suite green
> including the two new tests in §9. Findings from the audit are below.

---

## Audit & review findings

Verified against the installed library and the codebase before/while implementing.

**API assumptions — all hold on PTB 21.11.1:**

- `ReplyKeyboardMarkup(is_persistent=True, resize_keyboard=True)` — both kwargs accepted;
  `.is_persistent` / `.resize_keyboard` / `.keyboard` attributes present.
- `filters.Text([label]).filter(msg)` matches exactly on `msg.text`; a contact message
  (`text=None`) does not match.
- `MessageHandler` exposes `.callback` and `.filters`; `app.handlers[0]` is the group-0 list.
- `filters.CONTACT` and `filters.Text([…])` are disjoint, so handler order is not load-bearing.

**Correction folded into the implementation:**

- Dropping `ReplyKeyboardRemove()` orphaned its import — removed it too, else `ruff` fails
  with F401 and the leftover call would `NameError` at runtime.

**Out of scope — flagged, not changed:**

- **Pre-existing latent bug.** Invoking `/frequency` *before* `/start` upserts a user row
  with `next_check_at` but no `chat_id`; `get_due_users` then returns it and `_tick` runs
  `int(user["chat_id"])` — a `KeyError` raised *outside* its `try/except`, which crashes the
  whole cron run (and skips every remaining due user). The options panel can't reach this
  (its button appears only after registration), but the `/frequency` **command** can. Fix
  candidates: filter on `chat_id` in `get_due_users`, or skip chat-less users in `_tick`.
  Left untouched pending a decision.

---

## 1. Goal & rationale

The bot's only real post-registration option today is **changing the check-in cadence**,
reachable via the `/frequency` command ([bot.py:101-104](src/chatcheck_bot/bot.py#L101-L104)).
Typing a command is discoverable only if you know it exists. A pinned panel makes that
option a one-tap affordance.

Why a reply keyboard (Option A) rather than the command menu (`setMyCommands`, Option B):

| | Reply keyboard (A) | Command menu (B) |
|---|---|---|
| What the user sees | Buttons pinned above the input | Slide-up list behind the "≡ Menu" button |
| What a tap does | Sends the button's **text** | Runs a **command** (`/frequency`) |
| "Instead of commands"? | **Yes** — no slash typing at all | No — it *is* the command list |
| Setup | Per-chat, sent with a message | One global `setMyCommands` call |

Option B is a cheap complement (see [§8](#8-out-of-scope--future)), but A is the panel the
request asks for.

---

## 2. Current onboarding surface

```
/start ─▶ contact-request reply keyboard ─▶ share contact
        └▶ handle_contact: "Реєстрацію завершено ✅" (ReplyKeyboardRemove)
                          └▶ "Як часто нагадувати?" (inline frequency keyboard)
                                                     └▶ handle_frequency_choice (callback)
```

After onboarding the chat has **no** reply keyboard — the input area is bare. That empty
slot is exactly where the options panel goes.

---

## 3. Design overview

Three small edits to [bot.py](src/chatcheck_bot/bot.py):

1. **Define the panel** — one label constant + one `ReplyKeyboardMarkup`, marked
   `is_persistent=True` so Telegram keeps it pinned.
2. **Show it once, at the end of onboarding** — swap the `ReplyKeyboardRemove()` in
   `handle_contact` for the new menu. Sending any reply keyboard replaces the previous
   one, so the contact keyboard is cleared *and* the panel appears in a single message.
3. **Route the button** — a `MessageHandler` matching the button's exact label text,
   pointed at the existing `frequency_command`. The label is the routing key, so it lives
   in **one** constant referenced by both the markup and the handler.

No new imports are needed — `ReplyKeyboardMarkup`, `KeyboardButton`, `MessageHandler`, and
`filters` are already imported ([bot.py:10-26](src/chatcheck_bot/bot.py#L10-L26)).

---

## 4. Changes to `bot.py`

### 4.1 New constants (after `PROMPT_TEXT`, ~line 55)

```python
# Persistent options panel shown once onboarding completes. Tapping a button
# sends its label as a normal text message, which the router in §4.3 maps to a
# handler. The label doubles as the routing key, so it lives in one constant
# referenced by both the markup and the MessageHandler — rename it in one place.
BTN_FREQUENCY = "⏰ Змінити частоту"

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_FREQUENCY)]],
    resize_keyboard=True,  # compact keys, not full-height
    is_persistent=True,    # stay pinned above the input, not one-shot
)
```

### 4.2 Show the panel at the end of onboarding

In `handle_contact` ([bot.py:106](src/chatcheck_bot/bot.py#L106)):

```diff
-    await update.message.reply_text("Реєстрацію завершено! ✅", reply_markup=ReplyKeyboardRemove())
+    await update.message.reply_text("Реєстрацію завершено! ✅", reply_markup=MAIN_MENU)
```

> **⚠️ Ruff gotcha:** this removes the only use of `ReplyKeyboardRemove`, so drop it from
> the import block ([bot.py:15](src/chatcheck_bot/bot.py#L15)) or CI's `ruff check` fails
> on an unused import (F401).

### 4.3 Route the button to the existing frequency flow

Alongside the other handler registrations ([bot.py:135-139](src/chatcheck_bot/bot.py#L135-L139)),
after the `CONTACT` handler:

```python
app.add_handler(MessageHandler(filters.Text([BTN_FREQUENCY]), frequency_command))
```

`frequency_command` is reused unchanged — it already re-sends the inline frequency
keyboard, so both `/frequency` and the panel button land on the same code path.

---

## 5. Behaviour & interaction notes

- **Persistence.** A reply keyboard sticks per-chat until another reply keyboard is sent or
  `ReplyKeyboardRemove` clears it. We send `MAIN_MENU` once at onboarding; nothing after
  that disturbs it, so the panel stays for the life of the chat.
- **Inline keyboards coexist.** The frequency chooser and the daily Yes/No prompt are
  *inline* keyboards (attached to a message body); they don't touch the reply keyboard.
  The panel stays pinned while those appear inline in the chat.
- **Why the panel is its own message.** Reply keyboards can only be attached via
  `sendMessage`, never via `editMessageText`. `handle_frequency_choice` edits its message
  ([bot.py:116](src/chatcheck_bot/bot.py#L116)), so it can't carry the panel — which is
  fine, because the panel is already pinned and doesn't need re-sending.
- **Filters are disjoint.** `filters.Text([BTN_FREQUENCY])` matches only that exact string.
  It can't collide with `filters.CONTACT` or the `CommandHandler`s (`/start`, `/frequency`
  arrive as bot-command entities, not plain text), so handler order is not load-bearing.

---

## 6. Edge cases & gotchas

- **Re-running `/start`.** `start` sends the one-time contact keyboard, which temporarily
  replaces the panel; finishing registration re-sends `MAIN_MENU`, restoring it. Acceptable.
- **Label is the contract.** If the emoji or wording of `BTN_FREQUENCY` changes, the router
  matches automatically because both sides read the same constant — but any *hard-coded*
  duplicate of that string (e.g. in a test) must be updated too.
- **Manual typing.** A user who types the exact label text triggers `frequency_command`.
  That's the intended behaviour, not a bug.
- **`is_persistent` support.** Requires Bot API 6.1 / PTB ≥ 20. This repo runs PTB 21.x, so
  it's available.

---

## 7. Migration for existing users

New users get the panel during onboarding. Users who registered **before** this ships won't
have it until a message carrying a reply keyboard reaches them. Options, cheapest first:

1. **Do nothing** — they get the panel the next time they re-run `/start`. Fine for a
   low-population personal bot.
2. **One-off nudge** — send a single message with `reply_markup=MAIN_MENU` to active users
   (a throwaway script, or fold it into the next cron tick as a separate message — *not* the
   Yes/No prompt itself, since that message needs its inline keyboard).

Recommendation: option 1. Revisit only if the user base grows.

---

## 8. Out of scope / future

- **More buttons.** The panel grows by adding rows. Natural next options: pause/resume
  reminders, or a quick stats readout. Layout example:

  ```python
  MAIN_MENU = ReplyKeyboardMarkup(
      [
          [KeyboardButton(BTN_FREQUENCY)],
          [KeyboardButton(BTN_PAUSE), KeyboardButton(BTN_STATS)],
      ],
      resize_keyboard=True,
      is_persistent=True,
  )
  ```

  Each new button needs its own `MessageHandler` (or one handler that switches on the
  label). Pause/resume and stats would also need new `WaterBotDB` methods — out of scope
  here.
- **Option B (`setMyCommands`).** A one-time global call registering `/start` and
  `/frequency` with descriptions, so they show under the "≡ Menu" button. Complements this
  panel; push it once from the webhook-setup bootstrap rather than per request.

---

## 9. Testing

AWS is stubbed by `conftest` before import, so these are pure, offline unit tests in the
style of [tests/test_bot.py](tests/test_bot.py). `MessageHandler` and `filters` are reached
through the `bot` module namespace (already imported there).

```python
def test_main_menu_panel_is_persistent_with_frequency_button():
    menu = bot.MAIN_MENU
    assert menu.is_persistent is True
    assert menu.resize_keyboard is True
    labels = [btn.text for row in menu.keyboard for btn in row]
    assert labels == [bot.BTN_FREQUENCY]


def test_menu_button_routes_to_frequency_command():
    routed = [
        h
        for h in bot.app.handlers[0]
        if isinstance(h, bot.MessageHandler) and h.callback is bot.frequency_command
    ]
    assert len(routed) == 1
    match = routed[0].filters
    assert match.filter(mock.Mock(text=bot.BTN_FREQUENCY))
    assert not match.filter(mock.Mock(text="будь-який інший текст"))
```

The first test locks the panel's shape (persistent, resized, one frequency button); the
second proves the button is actually wired to `frequency_command` and its filter accepts
the label while rejecting other text — catching a label/handler drift regression.

Both are implemented in [tests/test_bot.py](tests/test_bot.py) and pass (`2 passed`).

---

## 10. Change summary

| File | Change |
|---|---|
| `src/chatcheck_bot/bot.py` | Add `BTN_FREQUENCY` + `MAIN_MENU`; show `MAIN_MENU` in `handle_contact`; register the label→`frequency_command` handler; drop the now-unused `ReplyKeyboardRemove` import |
| `tests/test_bot.py` | Add the two tests in §9 |

Net: four small edits to `bot.py` (one of them removing the now-unused `ReplyKeyboardRemove`
import) and two tests added. No infra, no DB, no deployment impact.
