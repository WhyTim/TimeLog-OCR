from app.startup import ENTRY_NAME, RUN_KEY, set_startup_enabled


class Key:
    def __init__(self, registry):
        self.registry = registry
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 1
    REG_SZ = 1
    def __init__(self):
        self.values = {}
    def OpenKey(self, root, path, reserved, access):
        assert path == RUN_KEY
        return Key(self)
    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value
    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_startup_enable_disable_with_mock():
    fake = FakeWinreg()
    ok, error = set_startup_enabled(True, winreg_module=fake, command='"app.exe" --minimized')
    assert ok, error
    assert fake.values[ENTRY_NAME] == '"app.exe" --minimized'
    ok, error = set_startup_enabled(False, winreg_module=fake)
    assert ok, error
    assert ENTRY_NAME not in fake.values
