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
import time, logging, re, html as _html
from telegram import MessageEntity
from telegram.error import BadRequest

log = logging.getLogger("PremiumEmoji")

# ── emoji char → premium (custom) emoji id ────────────────────────────
# From the owner's committed id list. Any emoji the bot uses that is NOT
# here simply stays a normal emoji (perfectly fine). Add more ids over time.
CHAR2ID = {
    "🪙":"5382164415019768638", "💰":"5417924076503062111",
    "🔓":"5429405838345265327", "🏆":"5188344996356448758",
    "📊":"5203993413346680064", "🚀":"5188481279963715781",
    "💳":"5472250091332993630", "💎":"5462902520215002477",
    "🎯":"5461009483314517035", "🔑":"5307843983102204243",
    "🔋":"5307905813451397794", "⚡":"5373066076558996568",
    "🔥":"5373310043586310463", "⭐":"5408977655330517200",
    "👑":"5433758796289685818", "🎉":"5193018401810822951",
    "🔗":"5440410042773824003", "📈":"5298614648138919107",
    "💬":"5235570365094188078", "🛒":"5400090058030075645",
    "📞":"5213179235996294999", "🧩":"5213306719215577669",
    "🏦":"5238132025323444613", "📩":"5472239203590888751",
}
# Longest first so multi-codepoint emoji match before their parts.
_EMOJI_RE = re.compile("|".join(re.escape(e) for e in
                       sorted(CHAR2ID, key=len, reverse=True))) if CHAR2ID else None


def has_premium(text: str) -> bool:
    """True if the text contains at least one emoji we have a premium id for."""
    return bool(_EMOJI_RE and _EMOJI_RE.search(text or ""))


def _inject(escaped: str) -> str:
    if not _EMOJI_RE:
        return escaped
    return _EMOJI_RE.sub(
        lambda m: f'<tg-emoji emoji-id="{CHAR2ID[m.group(0)]}">{m.group(0)}</tg-emoji>',
        escaped)


def md_to_html(text: str) -> str:
    """
    Convert the small subset of legacy Markdown this bot uses into Telegram
    HTML, injecting premium custom emoji (<tg-emoji>) into the visible text
    (never inside code). HTML is actually more robust than legacy Markdown:
    a lone '*' or '_' is just literal text, so messages that used to fail to
    parse now render fine.
    """
    if not text:
        return text
    S = "\x00"
    blocks, inls = [], []
    # 1. stash fenced code ```...``` (optional language line)
    def _sb(m): blocks.append(m.group(1)); return f"{S}B{len(blocks)-1}{S}"
    t = re.sub(r"```(?:[a-zA-Z0-9_+-]*\n)?(.*?)```", _sb, text, flags=re.S)
    # 2. stash inline code `...`
    def _si(m): inls.append(m.group(1)); return f"{S}I{len(inls)-1}{S}"
    t = re.sub(r"`([^`\n]+)`", _si, t)
    # 3. escape everything else
    t = _html.escape(t, quote=False)
    # 4. *bold* (balanced, single line)
    t = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", t)
    # 5. _italic_ only at word boundaries (won't touch name_with_underscores)
    t = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", t)
    # 6. [text](url)
    t = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+|tg://[^)\s]+)\)",
               r'<a href="\2">\1</a>', t)
    # 7. premium emoji in the visible text
    t = _inject(t)
    # 8. restore code (escaped, no emoji injection)
    t = re.sub(rf"{S}I(\d+){S}",
               lambda m: f"<code>{_html.escape(inls[int(m.group(1))], quote=False)}</code>", t)
    t = re.sub(rf"{S}B(\d+){S}",
               lambda m: f"<pre>{_html.escape(blocks[int(m.group(1))], quote=False)}</pre>", t)
    return t


def strip_html(text: str) -> str:
    """Last-resort plain text: remove tags, unescape entities."""
    t = re.sub(r"<[^>]+>", "", text or "")
    return _html.unescape(t)


def strip_md(text: str) -> str:
    """Plain text from legacy Markdown: drop *, _, ` markers, keep content
    and emoji. Used for the ultimate no-parse-mode fallback."""
    if not text:
        return text
    t = re.sub(r"```(?:[a-zA-Z0-9_+-]*\n)?(.*?)```", r"\1", text, flags=re.S)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    t = re.sub(r"\*([^*\n]+)\*", r"\1", t)
    t = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"\1", t)
    t = re.sub(r"\[([^\]\n]+)\]\([^)\s]+\)", r"\1", t)
    return t

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
