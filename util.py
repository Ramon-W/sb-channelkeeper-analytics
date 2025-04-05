def parse_date(date: str):
    month, day, year = map(int, date.split("/"))
    if year < 100:
        year += 2000
    if month not in range(1, 12) or day not in range(1, 31) or year not in range(2000, 2100):
        raise ValueError(f"Date values are not valid: {month}/{day}/{year}.")
    return month, day, year

def is_number(s):
    if s == '':
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False