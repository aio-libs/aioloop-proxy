..
    You should *NOT* be adding new change log entries to this file, this
    file is managed by towncrier. You *may* edit previous change logs to
    fix problems like typo corrections or such.
    To add a new change log entry, please see
    https://pip.pypa.io/en/latest/development/#adding-a-news-entry
    we named the news folder "CHANGES".

    WARNING: Don't drop the next directive!

.. towncrier release notes start

0.0.13 (2022-01-28)
===================

No significant changes.


0.0.5 (2022-01-26)
==================

Features
--------

- Add type hints. (:issue:`11`)


0.0.4 (2022-01-26)
==================

Features
--------

- Add ``proxy_loop.get_parent_loop()`` public method. (:issue:`9`)

Bugfixes
--------

- Don't finalize already closed loop. (:issue:`8`)


0.0.3 (2022-01-26)
==================

Bugfixes
--------

- Fix subprocess transport when highlevel api (``asyncio.create_subprocess_exec()``) is used. (:issue:`7`)
