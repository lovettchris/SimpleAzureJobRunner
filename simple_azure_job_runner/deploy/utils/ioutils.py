import sys


def get_exception_info(exception: BaseException | None = None) -> str:
    errorType, value, stack = sys.exc_info()
    if exception is not None:
        value = exception
    err_msg = f"### Exception: {errorType}: {value}"
    import traceback

    for line in traceback.format_tb(stack):
        err_msg += line.strip("\r\n")
    return err_msg
