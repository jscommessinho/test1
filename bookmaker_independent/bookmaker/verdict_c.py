"""Verdetto C C01/C02, standalone RC15. Original algorithms unchanged."""
from __future__ import annotations
import re
from pathlib import Path

# Strict exact labels: never classify Brazilian Serie A as Italian Serie A,
# other national Premier Leagues as England, or arbitrary cups as BIG5.
_BIG5 = (
    "Italy Serie A",
    "England Premier League",
    "Spain La Liga",
    "France Ligue 1",
    "Germany Bundesliga I",
)
# API-Football's German competition label differs from frozen LivePick's.
# PitchAPI's Spanish competition is pinned separately by league ID in V3.
# This exact alias changes only the external mapping, never RC15 calibration.
_EXACT_PROVIDER_ALIASES = {"Germany Bundesliga": "Germany Bundesliga I",
                           "LaLiga": "Spain La Liga"}
_YEAR_SUFFIX = re.compile(r"^20[0-9]{2}$")
_1X2 = ("1", "X", "2", "1X", "X2", "12")
_OU = ("Over_2_5", "Under_2_5")


def canonical_competition(label: str) -> str:
    """Return the frozen RC15 BIG5 key, strictly for an exact name [+ year]."""
    if not isinstance(label, str):
        raise ValueError("competition must be a string")
    name = " ".join(label.split())
    for provider, canonical in (
        *((base, base) for base in _BIG5),
        *_EXACT_PROVIDER_ALIASES.items(),
    ):
        if name == provider:
            return canonical
        prefix = provider + " "
        if name.startswith(prefix) and _YEAR_SUFFIX.fullmatch(name[len(prefix):]):
            return canonical
    return name


def reference_mismatches(report: dict, model: dict) -> list[str]:
    """Mark only reference bins whose fitted method differs from runtime."""
    scope = report["data_quality"]["scope"]
    mismatches = []
    for market, names in (("1X2", _1X2), ("OU2_5", _OU)):
        calibration = model["markets"][scope][market]
        operational = report["single_market_probabilities_RC12_experimental"][market]["method"]
        if operational != calibration["operational_method"]:
            raise RuntimeError("RC12 model and operational report disagree for " + scope + "/" + market)
        if calibration["selected_on_2024"] == operational:
            continue
        for event in names:
            s = report["historical_support"][event]
            # Reference was built using the 2024-selected probabilities; the
            # operational policy later switched to direct. The old bin's n,
            # observed rate and bias must NOT be attached to a direct quote p.
            for key in ("n", "mean_prediction", "observed_frequency", "bias_pp"):
                s[key] = None
            s["support_level"] = "UNAVAILABLE_REFERENCE_MISMATCH"
            s["reference"] = (
                "Historical bins were built with the 2024-selected method; "
                "operational method differs. Match-level source missing: "
                "cannot produce valid bin counts or calibration."
            )
            s["operational_reference_aligned"] = False
            mismatches.append(event)
    report["data_quality"]["verdict_c_reference_mismatches"] = mismatches
    report["data_quality"]["verdict_c_reference_policy"] = (
        "FAIL_CLOSED: no unsupported history-based confidence"
    )
    return mismatches


def mask_historical_text(out_dir: Path, events: list[str]) -> None:
    """Mask every affected count in RC15 rendered markdown and HTML; fail closed.

    RC15 calls the human-readable goal events "Over 2.5" and "Under 2.5"
    in Markdown, but stores them as "Over_2_5" and "Under_2_5" in JSON.
    Replacing JSON keys verbatim would silently leave the published MD
    counts stale, even if the HTML and JSON were correctly masked.
    """
    if not events:
        return
    warning = (
        "Verdict C: the historical support for selected 1X2/O-U events is "
        "UNAVAILABLE: its bins were fitted to different probabilities. "
        "Operational probabilities are unchanged; no new empirical claim."
    )
    labels = {
        event: {
            "Over_2_5": "Over 2.5",
            "Under_2_5": "Under 2.5",
        }.get(event, event)
        for event in events
    }
    md = out_dir / "RC15_Intelligence_Report.md"
    lines = md.read_text("utf8").splitlines()
    found = set()
    in_support_table = False
    for i, line in enumerate(lines):
        if line.startswith("| Mercato | Probabilità | Indicatore 0–10 | Supporto storico |"):
            in_support_table = True
        if in_support_table and not line.strip():
            in_support_table = False
        if not in_support_table or not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) != 6:
            continue
        label = cells[1].strip()
        if label in labels.values():
            cells[4] = " STORICO NON ALLINEATO (n non disponibile) "
            lines[i] = "|".join(cells)
            found.add(label)
    if found != set(labels.values()):
        raise RuntimeError("Failed to mask all affected RC15 Markdown reference rows")
    md.write_text("**" + warning + "**\n\n" + "\n".join(lines) + "\n", "utf8")

    html = out_dir / "RC15_Intelligence_Report.html"
    html_text = html.read_text("utf8")
    for event in events:
        html_label = event.replace("_", " ")
        pattern = (
            r"(<tr><td>" + re.escape(html_label) +
            r"</td><td>[^<]+</td><td>)[^<]+(</td></tr>)"
        )
        html_text, n = re.subn(pattern, r"\1NOT ALIGNED\2", html_text)
        if n != 1:
            raise RuntimeError("Failed to mask RC15 HTML reference count: " + event)
    html_text = html_text.replace(
        '<div class="eyebrow">BETPILOT / PREMATCH / RC15</div>',
        '<p class="warn">VERDICT C: Historical reference bins for some markets '
        'are not aligned with the operational model; counts withheld.</p>'
        '<div class="eyebrow">BETPILOT / PREMATCH / RC15</div>',
        1,
    )
    html.write_text(html_text, "utf8")


