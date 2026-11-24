#!/bin/sh
# Quality da própria skill: o self-check (os e2e de Go e Rails são locais, fora do repo).
set -e
cd "$(dirname "$0")/.."
sh self-check.sh
