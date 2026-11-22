"""
identity.py  -  Checks on the IDs a ship broadcasts.

  valid_imo(imo)        7 digits and the check digit adds up (IMO numbers are self-checking)
  mmsi_country(mmsi)    country from the MMSI's first three digits (Maritime Identification Digits)
  mmsi_problems(mmsi)   list of reasons an MMSI looks fake or wrong

A ship that broadcasts a Russian flag but an MMSI from Gabon or Cameroon, or a made-up
MMSI like 123456789, is a classic sign of identity manipulation.
"""
import re

# MID -> country. Not the full ITU table: the countries that matter for Arctic / shadow-fleet work,
# plus common flags of convenience. Add rows as needed (ITU MARS list).
MID = {
    "201": "Albania", "209": "Cyprus", "210": "Cyprus", "212": "Cyprus", "215": "Malta", "229": "Malta",
    "248": "Malta", "249": "Malta", "256": "Malta", "211": "Germany", "218": "Germany", "219": "Denmark",
    "220": "Denmark", "224": "Spain", "225": "Spain", "226": "France", "227": "France", "228": "France",
    "230": "Finland", "231": "Faroe Islands", "232": "United Kingdom", "233": "United Kingdom",
    "234": "United Kingdom", "235": "United Kingdom", "236": "Gibraltar", "237": "Greece", "239": "Greece",
    "240": "Greece", "241": "Greece", "244": "Netherlands", "245": "Netherlands", "246": "Netherlands",
    "251": "Iceland", "255": "Portugal (Madeira)", "257": "Norway", "258": "Norway", "259": "Norway",
    "261": "Poland", "263": "Portugal", "265": "Sweden", "266": "Sweden", "271": "Turkey", "272": "Ukraine",
    "273": "Russia", "275": "Latvia", "276": "Estonia", "277": "Lithuania", "304": "Antigua and Barbuda", "306": "Curacao",
    "305": "Antigua and Barbuda", "308": "Bahamas", "309": "Bahamas", "311": "Bahamas", "312": "Belize",
    "314": "Barbados", "316": "Canada", "319": "Cayman Islands", "331": "Greenland", "338": "United States",
    "341": "St Kitts and Nevis", "351": "Panama", "352": "Panama", "353": "Panama", "354": "Panama",
    "355": "Panama", "356": "Panama", "357": "Panama", "370": "Panama", "371": "Panama", "372": "Panama",
    "373": "Panama", "374": "Panama", "366": "United States", "367": "United States", "368": "United States",
    "369": "United States", "375": "St Vincent and the Grenadines", "376": "St Vincent and the Grenadines",
    "377": "St Vincent and the Grenadines", "403": "Saudi Arabia", "412": "China", "413": "China",
    "414": "China", "416": "Taiwan", "419": "India", "422": "Iran", "431": "Japan", "432": "Japan",
    "440": "South Korea", "441": "South Korea", "445": "North Korea", "457": "Mongolia", "470": "UAE",
    "471": "UAE", "477": "Hong Kong", "511": "Palau", "518": "Cook Islands", "538": "Marshall Islands",
    "548": "Philippines", "563": "Singapore", "564": "Singapore", "565": "Singapore", "566": "Singapore",
    "572": "Tuvalu", "576": "Vanuatu", "577": "Vanuatu", "613": "Cameroon", "620": "Comoros",
    "626": "Gabon", "636": "Liberia", "637": "Liberia", "667": "Sierra Leone", "671": "Togo",
}
SUSPICIOUS_MMSIS = {"000000000", "111111111", "123456789", "987654321", "999999999"}


def same_country(a: str, b: str) -> bool:
    """'Russian' = 'Russia', 'Pananma' (typo) = 'Panama'. Spelling slips must not look like flag fraud."""
    import difflib
    a, b = a.strip().lower(), b.strip().lower()
    return a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def valid_imo(imo) -> bool:
    s = str(imo or "").strip().upper().removeprefix("IMO").strip()
    if not re.fullmatch(r"\d{7}", s):
        return False
    total = sum(int(d) * w for d, w in zip(s[:6], range(7, 1, -1)))
    return total % 10 == int(s[6])


def mmsi_country(mmsi) -> str | None:
    s = str(mmsi or "")
    return MID.get(s[:3]) if len(s) == 9 else None


def mmsi_problems(mmsi, reported_flag: str = None) -> list[str]:
    s = str(mmsi or "").strip()
    out = []
    if not re.fullmatch(r"\d{9}", s):
        return [f"MMSI '{s}' is not 9 digits"]
    if s in SUSPICIOUS_MMSIS or len(set(s)) == 1:
        out.append(f"MMSI {s} is a placeholder / made-up number")
    first = s[0]
    if first not in "234567":
        # 0 = group/coast, 8 = handheld, 9 = SAR aircraft / AIS-SART / MOB - never a merchant ship
        out.append(f"MMSI {s} starts with {first}: not a ship-station MMSI")
    elif s[:3] not in MID and not ("201" <= s[:3] <= "775"):
        out.append(f"MMSI {s}: MID {s[:3]} is not allocated to any country")
    country = mmsi_country(s)
    if reported_flag and country and not same_country(reported_flag, country):
        out.append(f"MMSI {s} is allocated to {country}, but the registry flag is {reported_flag}: "
                   "re-flagged, or the registry is out of date")
    return out
