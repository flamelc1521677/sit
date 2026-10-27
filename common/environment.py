"""Read machine-local user configuration without storing it in the repository."""
import os


def user_value(name):
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except FileNotFoundError:
        return None


def configured_value(name):
    return os.getenv(name) or user_value(name)
