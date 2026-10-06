import traceback


def assert_no_leak(exc: BaseException, *secrets: str) -> None:
    """The error must expose none of the secrets anywhere, including via chaining."""
    assert exc.__cause__ is None
    assert exc.__context__ is None
    exposed = [
        str(exc),
        repr(exc),
        repr(exc.args),
        "".join(traceback.format_exception(exc)),
    ]
    for secret in secrets:
        for text in exposed:
            assert secret not in text
