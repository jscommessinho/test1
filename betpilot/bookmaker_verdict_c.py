#!/usr/bin/env python3
"""Verdict C, non-invasive adapter for the untouched RC15/Step5 engines.

C-01: map exact API-Football BIG5 league+season labels to frozen RC15 labels.
C-02: fail closed on RC12 reliability bins computed for a *different*
       calibration policy than the actual operational probabilities.

Historical frequencies cannot be rebuilt without their original match-level
checkpoint; do not fabricate frequencies, sample sizes or new calibrators.
This module is intended for single-process native prematch execution.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import threading
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "BETPILOT_STEP5"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import integrate_native as native  # noqa: E402

_FROZEN = native.RC
_MODEL = _FROZEN / "RC12_Fitted_Calibrators.json"
_LOCK = threading.RLock()

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


def _reference_mismatches(report: dict, model: dict) -> list[str]:
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


def _mask_historical_text(out_dir: Path, events: list[str]) -> None:
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


def _rc15_fixed(row, odds, out, delegate, model):
    original = row["competition"]
    canonical = canonical_competition(original)
    report, manifest, input_csv = delegate(
        {**row, "competition": canonical}, odds, out
    )
    report["data_quality"]["verdict_c_competition_mapping"] = {
        "source": original,
        "engine": canonical,
        "applied": original != canonical,
        "preserved_external_event_identity": True,
    }
    mismatch = _reference_mismatches(report, model)
    path = Path(out) / "rc15_native"
    analysis_file = path / "RC15_Analysis.json"
    analysis = json.loads(analysis_file.read_text("utf8"))
    if len(analysis["reports"]) != 1:
        raise RuntimeError("Single-match RC15 report expected")
    analysis["reports"][0] = report
    analysis_file.write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        "utf8",
    )
    _mask_historical_text(path, mismatch)
    return report, manifest, input_csv


def run_full_fixed(payload: dict, output_dir) -> dict:
    """Call original Step5 orchestration with a scoped, restored adapter hook.

    All frozen engines, exact price computations and their original manifests
    remain unchanged. This hook is serialized to avoid concurrent patch races.
    """
    model = json.loads(_MODEL.read_text("utf8"))
    with _LOCK:
        delegate = native.native_rc15

        def corrected(row, odds, out):
            return _rc15_fixed(row, odds, out, delegate, model)

        native.native_rc15 = corrected
        try:
            result = native.run_full(copy.deepcopy(payload), output_dir)
        finally:
            native.native_rc15 = delegate
    # Record a separate provenance audit, hashed alongside the generated files.
    out = Path(output_dir)
    analysis = json.loads((out / "rc15_native" / "RC15_Analysis.json").read_text("utf8"))
    quality = analysis["reports"][0]["data_quality"]
    audit = {
        "schema": "BETPILOT_BOOKMAKER_VERDICT_C_V1",
        "source": "unmodified Step5 RC15, RC12 and BISE",
        "competition_mapping": quality["verdict_c_competition_mapping"],
        "unavailable_reference_events": quality["verdict_c_reference_mismatches"],
        "coefficients_changed": False,
        "historical_recalibration_claimed": False,
        "prospective_prediction_validated": False,
    }
    audit_file = out / "BOOKMAKER_VERDICT_C_AUDIT.json"
    audit_file.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        "utf8",
    )
    manifest_file = out / "RUN_MANIFEST.json"
    manifest = json.loads(manifest_file.read_text("utf8"))
    manifest["sha256"][audit_file.name] = native.sha256(audit_file)
    manifest_file.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        "utf8",
    )
    return result


def main(argv=None):
    import argparse
    a = argparse.ArgumentParser(description="RC15/MA2 native Verdict-C corrected run")
    a.add_argument("input", type=Path, help="Existing verified RC15_EVENT_INPUT.json")
    a.add_argument("--output-dir", required=True, type=Path)
    args = a.parse_args(argv)
    result = run_full_fixed(json.loads(args.input.read_text("utf8")), args.output_dir)
    print(json.dumps({"status": result["status"], "verdict_c": "applied"}))


if __name__ == "__main__":
    main()
