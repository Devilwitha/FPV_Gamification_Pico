"""Laender fuer Konto, Profil und Leaderboard (ISO 3166-1 Alpha-2, deutsche Namen).

Gespeichert wird im Konto nur der Code (z. B. "CH"); angezeigt der Name.
normalize() ordnet frueher frei eingetippte Werte ("Schweiz", "germany",
"CH") einem Code zu.
"""

import unicodedata

# Oben in der Auswahlliste
PRIORITY = ("CH", "DE", "AT", "LI")

COUNTRIES = {
    "AD": "Andorra", "AE": "Vereinigte Arabische Emirate", "AF": "Afghanistan", "AG": "Antigua und Barbuda",
    "AL": "Albanien", "AM": "Armenien", "AO": "Angola", "AR": "Argentinien", "AT": "Österreich",
    "AU": "Australien", "AZ": "Aserbaidschan", "BA": "Bosnien und Herzegowina", "BB": "Barbados",
    "BD": "Bangladesch", "BE": "Belgien", "BF": "Burkina Faso", "BG": "Bulgarien", "BH": "Bahrain",
    "BI": "Burundi", "BJ": "Benin", "BN": "Brunei", "BO": "Bolivien", "BR": "Brasilien", "BS": "Bahamas",
    "BT": "Bhutan", "BW": "Botswana", "BY": "Belarus", "BZ": "Belize", "CA": "Kanada",
    "CD": "Demokratische Republik Kongo", "CF": "Zentralafrikanische Republik", "CG": "Republik Kongo",
    "CH": "Schweiz", "CI": "Elfenbeinküste", "CL": "Chile", "CM": "Kamerun", "CN": "China", "CO": "Kolumbien",
    "CR": "Costa Rica", "CU": "Kuba", "CV": "Kap Verde", "CY": "Zypern", "CZ": "Tschechien", "DE": "Deutschland",
    "DJ": "Dschibuti", "DK": "Dänemark", "DM": "Dominica", "DO": "Dominikanische Republik", "DZ": "Algerien",
    "EC": "Ecuador", "EE": "Estland", "EG": "Ägypten", "ER": "Eritrea", "ES": "Spanien", "ET": "Äthiopien",
    "FI": "Finnland", "FJ": "Fidschi", "FR": "Frankreich", "GA": "Gabun", "GB": "Vereinigtes Königreich",
    "GD": "Grenada", "GE": "Georgien", "GH": "Ghana", "GM": "Gambia", "GN": "Guinea", "GQ": "Äquatorialguinea",
    "GR": "Griechenland", "GT": "Guatemala", "GW": "Guinea-Bissau", "GY": "Guyana", "HK": "Hongkong",
    "HN": "Honduras", "HR": "Kroatien", "HT": "Haiti", "HU": "Ungarn", "ID": "Indonesien", "IE": "Irland",
    "IL": "Israel", "IN": "Indien", "IQ": "Irak", "IR": "Iran", "IS": "Island", "IT": "Italien", "JM": "Jamaika",
    "JO": "Jordanien", "JP": "Japan", "KE": "Kenia", "KG": "Kirgisistan", "KH": "Kambodscha", "KM": "Komoren",
    "KN": "St. Kitts und Nevis", "KR": "Südkorea", "KW": "Kuwait", "KZ": "Kasachstan", "LA": "Laos",
    "LB": "Libanon", "LC": "St. Lucia", "LI": "Liechtenstein", "LK": "Sri Lanka", "LR": "Liberia",
    "LS": "Lesotho", "LT": "Litauen", "LU": "Luxemburg", "LV": "Lettland", "LY": "Libyen", "MA": "Marokko",
    "MC": "Monaco", "MD": "Moldau", "ME": "Montenegro", "MG": "Madagaskar", "MK": "Nordmazedonien",
    "ML": "Mali", "MM": "Myanmar", "MN": "Mongolei", "MO": "Macau", "MR": "Mauretanien", "MT": "Malta",
    "MU": "Mauritius", "MV": "Malediven", "MW": "Malawi", "MX": "Mexiko", "MY": "Malaysia", "MZ": "Mosambik",
    "NA": "Namibia", "NE": "Niger", "NG": "Nigeria", "NI": "Nicaragua", "NL": "Niederlande", "NO": "Norwegen",
    "NP": "Nepal", "NZ": "Neuseeland", "OM": "Oman", "PA": "Panama", "PE": "Peru", "PG": "Papua-Neuguinea",
    "PH": "Philippinen", "PK": "Pakistan", "PL": "Polen", "PR": "Puerto Rico", "PS": "Palästina",
    "PT": "Portugal", "PY": "Paraguay", "QA": "Katar", "RO": "Rumänien", "RS": "Serbien", "RU": "Russland",
    "RW": "Ruanda", "SA": "Saudi-Arabien", "SC": "Seychellen", "SD": "Sudan", "SE": "Schweden",
    "SG": "Singapur", "SI": "Slowenien", "SK": "Slowakei", "SL": "Sierra Leone", "SM": "San Marino",
    "SN": "Senegal", "SO": "Somalia", "SR": "Suriname", "SV": "El Salvador", "SY": "Syrien", "SZ": "Eswatini",
    "TD": "Tschad", "TG": "Togo", "TH": "Thailand", "TJ": "Tadschikistan", "TM": "Turkmenistan",
    "TN": "Tunesien", "TR": "Türkei", "TT": "Trinidad und Tobago", "TW": "Taiwan", "TZ": "Tansania",
    "UA": "Ukraine", "UG": "Uganda", "US": "Vereinigte Staaten", "UY": "Uruguay", "UZ": "Usbekistan",
    "VA": "Vatikanstadt", "VE": "Venezuela", "VN": "Vietnam", "XK": "Kosovo", "YE": "Jemen",
    "ZA": "Südafrika", "ZM": "Sambia", "ZW": "Simbabwe",
}

# Weitere Schreibweisen fuer normalize() (englisch/umgangssprachlich)
_ALIASES = {
    "switzerland": "CH", "suisse": "CH", "svizzera": "CH", "germany": "DE", "austria": "AT",
    "england": "GB", "uk": "GB", "grossbritannien": "GB", "united kingdom": "GB", "usa": "US",
    "united states": "US", "amerika": "US", "holland": "NL", "netherlands": "NL", "france": "FR",
    "italy": "IT", "italia": "IT", "spain": "ES", "espana": "ES", "czech republic": "CZ", "poland": "PL",
}


def _fold(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(text.casefold().replace("-", " ").split())


_BY_NAME = {_fold(name): code for code, name in COUNTRIES.items()}
# Umlaut-Umschreibungen ("Oesterreich", "Daenemark", "Tuerkei")
_BY_NAME.update({
    _fold(name.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")): code
    for code, name in COUNTRIES.items()
})
_BY_NAME.update(_ALIASES)


def name(code):
    """Anzeigename zu einem Code; unbekannte Werte werden unveraendert angezeigt."""
    return COUNTRIES.get((code or "").upper(), code or "")


def is_valid(code):
    return (code or "").upper() in COUNTRIES


def normalize(value):
    """Code zu einem frei eingetippten Wert ("CH", "Schweiz", "germany") oder None."""
    value = (value or "").strip()
    if value.upper() in COUNTRIES:
        return value.upper()
    return _BY_NAME.get(_fold(value))


def choices():
    """[(code, name)] fuer Auswahllisten: Prioritaet oben, danach alphabetisch."""
    rest = sorted((c for c in COUNTRIES if c not in PRIORITY), key=lambda c: _fold(COUNTRIES[c]))
    return [(c, COUNTRIES[c]) for c in PRIORITY], [(c, COUNTRIES[c]) for c in rest]
