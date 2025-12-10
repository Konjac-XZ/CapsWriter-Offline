import re


def clearly_type(obj):
    result = type(obj).__name__
    match = re.search(r"'(.*?)'", result)
    if match:
        return match.group(1)
    else:
        return result
