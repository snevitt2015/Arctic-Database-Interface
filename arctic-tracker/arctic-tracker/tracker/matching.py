"""
matching.py  -  Pair OSINT records with the vessels in our Excel watch list.

Order of trust:
  1. IMO  - permanent hull number, the best match
  2. MMSI - radio ID; dark/shadow ships sometimes change it, so second best
  3. Name - last resort (names get changed too, and are often duplicated)

Every row gets a `vessel` column with the Excel name, and `match_on`
telling you HOW it was matched (so you can judge how much to trust it).
Rows that match nothing get vessel = "" and are kept - they could be
a ship you don't know about yet.
"""
import pandas as pd


def _norm_name(name) -> str:
    return "".join(ch for ch in str(name).upper() if ch.isalnum())


def match_to_watchlist(positions: pd.DataFrame, vessels: pd.DataFrame) -> pd.DataFrame:
    df = positions.copy()
    by_imo = {v["imo"]: v["name"] for _, v in vessels.iterrows() if v["imo"]}
    by_mmsi = {v["mmsi"]: v["name"] for _, v in vessels.iterrows() if v["mmsi"]}
    by_name = {_norm_name(v["name"]): v["name"] for _, v in vessels.iterrows()}
    imo_of = {v["name"]: v["imo"] for _, v in vessels.iterrows()}

    matched, how = [], []
    for _, row in df.iterrows():
        if row["imo"] in by_imo:
            matched.append(by_imo[row["imo"]]); how.append("IMO")
        elif row["mmsi"] in by_mmsi:
            matched.append(by_mmsi[row["mmsi"]]); how.append("MMSI")
        elif _norm_name(row["name"]) in by_name and row["name"]:
            matched.append(by_name[_norm_name(row["name"])]); how.append("name")
        else:
            matched.append(""); how.append("unmatched")
    df["vessel"] = matched
    df["match_on"] = how
    # Fill in the IMO for rows that only had an MMSI or name
    df.loc[df["imo"] == "", "imo"] = df["vessel"].map(imo_of).fillna("")
    return df


def identity_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Ships that used more than one MMSI or name over time - a common
    shadow-fleet tactic. Returns one row per vessel with the list of identities."""
    out = []
    for vessel, g in df[df["vessel"] != ""].groupby("vessel"):
        mmsis = sorted(set(m for m in g["mmsi"] if m))
        names = sorted(set(n.upper() for n in g["name"] if n))
        if len(mmsis) > 1 or len(names) > 1:
            out.append({"vessel": vessel, "mmsis_seen": ", ".join(mmsis), "names_seen": ", ".join(names)})
    return pd.DataFrame(out, columns=["vessel", "mmsis_seen", "names_seen"])
