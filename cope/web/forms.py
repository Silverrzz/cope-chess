from __future__ import annotations


FormValues = dict[str, list[str]]


def form_value(form: FormValues, key: str, default: str = "") -> str:
    values = form.get(key)
    if not values:
        return default
    return values[-1].strip()
