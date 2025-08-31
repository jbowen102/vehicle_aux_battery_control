#!/bin/sh

sudo kill $(pgrep -f "python event_loop.py") > /dev/null 2>&1
# https://stackoverflow.com/a/40652908
# https://stackoverflow.com/questions/43724467/what-is-the-difference-between-kill-and-kill-9
# https://stackoverflow.com/a/617184
