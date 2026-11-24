#!/bin/sh
# Quality da própria skill: o self-check (os e2e de Go e Rails rodam no CI).
set -e
cd "$(dirname "$0")/.."
sh self-check.sh
