"""Compact, native Telegram-expandable reel SEO preview.

Telegram's <blockquote expandable> lets the owner tap the quote to open/close
the detailed SEO without a new bot button, callback or separate message.
"""
from html import escape


def format_reel_details(clip, reel_meta, source_facts, source_lines):
    number = clip.get("serial", clip["id"])
    title = reel_meta["title"]
    description = reel_meta["description"]
    keywords = reel_meta["tags_string"]
    details = (
        "الوصف والهاشتاغات:\n" + description + "\n\n"
        + f"كلمات يوتيوب ({len(keywords)}/500):\n" + keywords
        + "\n\n" + source_lines(source_facts)
    )
    # Native Telegram expandable quotation: tap to expand or collapse.
    # Never send a user-supplied string as unescaped HTML.
    markup = (
        f"ريلز {number} — بيانات النشر\n"
        f"العنوان ({len(title)}/100): {escape(title)}\n"
        f"<blockquote expandable>{escape(details)}</blockquote>"
    )
    if len(markup) > 3900:
        raise RuntimeError("TELEGRAM_REEL_METADATA_TOO_LONG_FOR_COLLAPSIBLE_MESSAGE")
    return markup
