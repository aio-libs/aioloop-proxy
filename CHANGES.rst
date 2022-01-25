..
    You should *NOT* be adding new change log entries to this file, this
    file is managed by towncrier. You *may* edit previous change logs to
    fix problems like typo corrections or such.
    To add a new change log entry, please see
    https://pip.pypa.io/en/latest/development/#adding-a-news-entry
    we named the news folder "CHANGES".

    WARNING: Don't drop the next directive!

.. towncrier release notes start

0.0.3 (2022-01-26)
==================

Bugfixes
--------

- Fix subprocess transport when highlevel api (``asyncio.create_subprocess_exec()``) is used. (:issue:`7`)
