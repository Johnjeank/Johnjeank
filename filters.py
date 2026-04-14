"""
filters.py – Advanced content-based filters for the Vinted iPhone monitor.

Two rule sets applied on top of the basic config.py filters:

1. EXCLUDED_KEYWORDS  – reject any listing whose title or description
                        contains one of these strings (FR + EN).
2. MODEL_PRICE_CAPS   – per-iPhone-model maximum price in EUR.
                        First matching rule wins (most-specific first).
"""

from __future__ import annotations

import re

# ── 1. Exclusion keywords ─────────────────────────────────────────────────────
#
# Checked against: title + description (case-insensitive, accent-insensitive).
# Add/remove terms here to tune sensitivity.

EXCLUDED_KEYWORDS: list[str] = [
    # ── Cracked / broken screen ───────────────────────────────────────────────
    "crack", "cracked", "craquelé", "craquele", "fissuré", "fissure",
    "fissuration", "écran cassé", "ecran casse", "vitre cassée",
    "vitre cassee", "vitre fissurée", "vitre fissuree", "écran fissuré",
    "ecran fissure", "broken screen", "screen crack", "screen damage",
    "screen broken", "verre cassé", "verre casse",

    # ── Not functional ────────────────────────────────────────────────────────
    "ne fonctionne pas", "ne marche pas", "ne s'allume pas",
    "ne s allume pas", "ne s'allume plus", "ne s allume plus",
    "bloqué au démarrage", "bloque au demarrage",
    "hors service", "hors d'usage", "hors d usage",
    "pour pièces", "pour pieces", "pour la pièce", "pour la piece",
    "pièces détachées", "pieces detachees", "pour piece detachee",
    "broken", "not working", "doesn't work", "does not work",
    "défectueux", "defectueux", "défaut", "dysfonctionnement",
    "ne s'allume", "ne demarre pas", "ne redémarre pas",
    "mort", "dead", "hs ",                    # trailing space avoids "chassis"

    # ── iCloud / activation lock ──────────────────────────────────────────────
    "icloud bloqué", "icloud bloque", "icloud locked", "icloud lock",
    "activation lock", "verrou activation", "verrou d'activation",
    "compte icloud", "locked to icloud", "bloqué icloud",
    "bloque icloud", "locked icloud", "activation icloud",
    "find my", "find my iphone",               # often listed with lock issues

    # ── Off-platform payment requests (scam indicator) ────────────────────────
    "paypal", "pay pal",
    "iban", "virement", "virement bancaire", "virement sepa",
    "western union", "wire transfer", "bank transfer",
    "paiement par virement", "paiement hors", "pay outside vinted",
    "contact me", "contactez-moi", "whatsapp", "telegram",
]

# Pre-compile a single regex for speed (alternation of all terms)
_EXCLUDED_RE = re.compile(
    "|".join(re.escape(k) for k in EXCLUDED_KEYWORDS),
    flags=re.IGNORECASE,
)


def has_excluded_keyword(text: str) -> bool:
    """Return True if *text* matches any exclusion keyword."""
    return bool(_EXCLUDED_RE.search(text))


# ── 2. Per-model price caps (EUR) ─────────────────────────────────────────────
#
# List of (patterns, max_price_eur) tuples.
# The FIRST tuple whose ANY pattern appears in the listing title wins.
# Order matters: put more-specific model names BEFORE generic ones.

MODEL_PRICE_CAPS: list[tuple[list[str], float]] = [
    # ── iPhone 16 ─────────────────────────────────────────────────────────────
    (["iphone 16 pro max", "iphone16 pro max"],     100),
    (["iphone 16 pro"],                             100),
    (["iphone 16 plus"],                            100),
    (["iphone 16"],                                 100),
    # ── iPhone 15 ─────────────────────────────────────────────────────────────
    (["iphone 15 pro max", "iphone15 pro max"],     100),
    (["iphone 15 pro"],                             100),
    (["iphone 15 plus"],                            100),
    (["iphone 15"],                                 100),
    # ── iPhone 14 ─────────────────────────────────────────────────────────────
    (["iphone 14 pro max", "iphone14 pro max"],     100),
    (["iphone 14 pro"],                             100),
    (["iphone 14 plus"],                            100),
    (["iphone 14"],                                 100),
    # ── iPhone 13 ─────────────────────────────────────────────────────────────
    (["iphone 13 pro max", "iphone13 pro max"],     100),
    (["iphone 13 pro"],                             100),
    (["iphone 13 mini"],                            100),
    (["iphone 13"],                                 100),
    # ── iPhone 12 ─────────────────────────────────────────────────────────────
    (["iphone 12 pro max", "iphone12 pro max"],      70),
    (["iphone 12 pro"],                              70),
    (["iphone 12 mini"],                             70),
    (["iphone 12"],                                  70),
    # ── iPhone 11 ─────────────────────────────────────────────────────────────
    (["iphone 11 pro max", "iphone11 pro max"],      70),
    (["iphone 11 pro"],                              70),
    (["iphone 11"],                                  70),
    # ── iPhone XS / XR / X  (must come before bare "iphone x") ───────────────
    (["iphone xs max", "iphone xsmax"],              50),
    (["iphone xs"],                                  50),
    (["iphone xr"],                                  50),
    (["iphone x "],                                  50),   # trailing space avoids xs/xr
    # ── iPhone SE ─────────────────────────────────────────────────────────────
    (["iphone se 3", "iphone se (3", "iphone se3"],  70),   # SE 3rd gen ≈ 11-class
    (["iphone se 2", "iphone se (2", "iphone se2"],  50),   # SE 2nd gen ≈ X-class
    (["iphone se"],                                  35),   # 1st gen (2016)
    # ── iPhone 8 / 7 / 6 ─────────────────────────────────────────────────────
    (["iphone 8 plus", "iphone8 plus", "iphone 8+"], 40),
    (["iphone 8"],                                   35),
    (["iphone 7 plus", "iphone7 plus", "iphone 7+"], 35),
    (["iphone 7"],                                   35),
    (["iphone 6s plus", "iphone6s plus"],            25),
    (["iphone 6s"],                                  25),
    (["iphone 6 plus", "iphone6 plus"],              20),
    (["iphone 6"],                                   20),
]


def model_price_cap(title: str) -> float | None:
    """
    Return the per-model max price for the iPhone mentioned in *title*,
    or None if no known model is found.
    """
    title_lower = title.lower()
    for patterns, cap in MODEL_PRICE_CAPS:
        if any(p in title_lower for p in patterns):
            return cap
    return None
