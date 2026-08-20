"""
╔══════════════════════════════════════════════════════════════════════╗
║   PREMIUM EMOJI  —  animated custom emoji with automatic fallback    ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  Telegram custom (premium/animated) emoji are sent as message        ║
║  ENTITIES, not text. Each entity points at a custom_emoji_id and     ║
║  sits on top of a normal fallback emoji character.                   ║
║                                                                      ║
║  • Viewers WITH Telegram Premium see the animated version.           ║
║  • Everyone else sees the plain fallback emoji.                      ║
║                                                                      ║
║  AUTO-FALLBACK (no restart needed):                                  ║
║  If Telegram ever refuses the custom-emoji entities (e.g. the        ║
║  owner's Premium lapses, or an id becomes invalid), send_premium()   ║
║  catches the error and instantly re-sends the SAME message as plain  ║
║  text with the fallback emoji. It also remembers the failure for a   ║
║  short while so it doesn't keep retrying, and silently re-checks      ║
║  later — all at runtime, nothing to restart.                         ║
╚══════════════════════════════════════════════════════════════════════╝
"""
import time, logging
from telegram import MessageEntity
from telegram.error import BadRequest

log = logging.getLogger("PremiumEmoji")

# ── Logical name → (fallback emoji, custom_emoji_id) ──────────────────
# Curated from the owner's "Premium id" list. The fallback char is what
# non-premium users (and the auto-fallback path) show.
EMOJI = {
    "coin":   ("🪙", "5382164415019768638"),
    "money":  ("💰", "5417924076503062111"),
    "unlock": ("🔓", "5429405838345265327"),
    "trophy": ("🏆", "5188344996356448758"),
    "chart":  ("📊", "5203993413346680064"),
    "rocket": ("🚀", "5188481279963715781"),
    "card":   ("💳", "5472250091332993630"),
    "gem":    ("💎", "5462902520215002477"),
    "target": ("🎯", "5461009483314517035"),
    "key":    ("🔑", "5307843983102204243"),
    "battery":("🔋", "5307905813451397794"),
    "bolt":   ("⚡", "5373066076558996568"),
    "fire":   ("🔥", "5373310043586310463"),
    "star":   ("⭐", "5408977655330517200"),
    "crown":  ("👑", "5433758796289685818"),
    "party":  ("🎉", "5193018401810822951"),
}

# ── runtime fallback state ────────────────────────────────────────────
_premium_ok      = True     # optimistic; flips off on first refusal
_disabled_until  = 0.0      # re-try premium again after this timestamp
_RETRY_AFTER_SEC = 1800     # re-check premium every 30 min


def _u16(s: str) -> int:
    """Length of a string in UTF-16 code units (Telegram entity units)."""
    return len(s.encode("utf-16-le")) // 2


def compose(*parts):
    """
    Build (text, entities) from a sequence of parts:
      • "plain string"          → literal text
      • ("em", "key")           → premium emoji by logical name
      • ("b", "bold text")      → bold
      • ("code", "mono")        → monospace

    Entities carry correct UTF-16 offsets so custom emoji line up exactly.
    """
    text, ents, off = "", [], 0
    for p in parts:
        if isinstance(p, tuple):
            kind = p[0]
            if kind == "em":
                fallback, eid = EMOJI.get(p[1], ("•", ""))
                ln = _u16(fallback)
                if eid:
                    ents.append(MessageEntity(
                        type="custom_emoji", offset=off, length=ln,
                        custom_emoji_id=eid))
                text += fallback; off += ln
            elif kind in ("b", "code"):
                seg = p[1]; ln = _u16(seg)
                ents.append(MessageEntity(
                    type=("bold" if kind == "b" else "code"),
                    offset=off, length=ln))
                text += seg; off += ln
            else:
                seg = str(p[1]); text += seg; off += _u16(seg)
        else:
            text += p; off += _u16(p)
    return text, ents


def _plain(parts) -> str:
    """Same parts, but flattened to plain text (fallback emoji, no styling)."""
    out = []
    for p in parts:
        if isinstance(p, tuple):
            if p[0] == "em":
                out.append(EMOJI.get(p[1], ("•", ""))[0])
            else:
                out.append(str(p[1]))
        else:
            out.append(p)
    return "".join(out)


async def send_premium(bot, chat_id, parts, reply_markup=None, **kw):
    """
    Send a message built from `parts` (see compose()). Tries animated
    premium emoji; on ANY refusal from Telegram, instantly re-sends the
    same message as plain text. Never raises for the emoji reason alone.
    """
    global _premium_ok, _disabled_until
    now = time.time()
    use_premium = _premium_ok or now >= _disabled_until

    if use_premium:
        text, ents = compose(*parts)
        try:
            msg = await bot.send_message(
                chat_id, text, entities=ents,
                reply_markup=reply_markup, **kw)
            _premium_ok = True
            return msg
        except BadRequest as e:
            # Only the custom-emoji path should fall back; re-raise anything
            # unrelated so real bugs stay visible.
            if "custom emoji" not in str(e).lower() and "emoji" not in str(e).lower():
                raise
            _premium_ok = False
            _disabled_until = now + _RETRY_AFTER_SEC
            log.warning("Premium emoji refused (%s) — falling back to plain "
                        "for %ds. No restart needed.", e, _RETRY_AFTER_SEC)

    # Fallback: plain text, no entities.
    return await bot.send_message(
        chat_id, _plain(parts), reply_markup=reply_markup, **kw)
