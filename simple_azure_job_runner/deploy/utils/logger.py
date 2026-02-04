import logging

import coloredlogs

PREFIX = "smart_replay"

has_console_handler = False


class Logger:
    """Logger class to be used by all modules in the project.

    The available logging levels are given below in decreasing order of severity:
        CRITICAL
        ERROR
        WARNING
        INFO
        DEBUG

    When a log_level is set to a logger, logging messages which are less severe than it will be ignored.
    For example, if the log_level is set to INFO, then DEBUG messages will be ignored.
    """

    def __init__(self):
        self.root_logger = logging.getLogger(PREFIX)
        self.log_level = "INFO"
        self.file_handler: logging.FileHandler | None = None

    def close(self):
        """Close the file handler if it exists."""
        if self.file_handler:
            self.file_handler.close()
            self.file_handler = None

    def get_root_logger(self, log_level="INFO", log_file=None):
        self._setup(log_level, log_file)
        return self.root_logger

    def set_log_level(self, log_level):
        self.log_level = log_level
        self.root_logger.setLevel(log_level)
        for handler in self.root_logger.handlers:
            handler.setLevel(log_level)

    def set_log_file(self, log_file):
        self._add_file_handler(log_file)

    def _setup(self, log_level, log_file):
        self.log_level = log_level
        self.root_logger.setLevel(log_level)
        self._add_console_handler()
        coloredlogs.install(
            level=self.log_level, logger=self.root_logger, fmt="%(asctime)s %(hostname)s %(levelname)s %(message)s"
        )
        if log_file:
            self._add_file_handler(log_file)

        # To avoid having duplicate logs in the console
        self.root_logger.propagate = False

    def _add_console_handler(self):
        global has_console_handler
        if not has_console_handler:
            console_handler_formatter = logging.Formatter("%(filename)s [%(levelname)s]: %(message)s")
            console_handler = logging.StreamHandler()
            console_handler.setLevel(self.log_level)
            console_handler.setFormatter(console_handler_formatter)
            self.root_logger.addHandler(console_handler)
            has_console_handler = True

    def _add_file_handler(self, log_file):
        file_handler_formatter = logging.Formatter(
            "%(asctime)s %(filename)s [%(levelname)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(self.log_level)
        file_handler.setFormatter(file_handler_formatter)
        self.root_logger.addHandler(file_handler)
        self.file_handler = file_handler

    @staticmethod
    def get_logger(name):
        # We enforce the creation of a child logger (PREFIX.name) to keep the root logger setup
        if name.startswith(PREFIX + "."):
            return logging.getLogger(name)
        return logging.getLogger(PREFIX + "." + name)


def _mirror_format_message(parent, record):
    msg = record.message
    parts = msg.split(" ")
    if len(parts) > 4:
        record.asctime = parts[0] + " " + parts[1]
        record.hostname = parts[2]
        record.levelname = parts[3]
        record.message = " ".join(parts[4:])
    return parent.oldFormatMessage(record)


class MirrorLog:
    """Wraps the log formatters so that we can re-log some log output we got from a child process without
    getting redundant log header information.  For example, blind re-logging of child process output would
    give you something like this:

    > 2024-11-01 22:19:38 sr-trainer10 INFO 2024-11-01 22:19:36 sr-trainer10 WARNING Creating job foobar

    But this class will ensure the line is logged with the correct timestamp and warning level so you get this instead:

    > 2024-11-01 22:19:36 sr-trainer10 WARNING Creating job foobar

    """

    def __init__(self, log):
        self.log = log

    def __enter__(self):
        for handler in self.log.handlers:
            handler.formatter.oldFormatMessage = handler.formatter.formatMessage
            handler.formatter.formatMessage = lambda record: _mirror_format_message(handler.formatter, record)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        for handler in self.log.handlers:
            handler.formatter.formatMessage = handler.formatter.oldFormatMessage
            del handler.formatter.oldFormatMessage
