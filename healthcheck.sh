#!/bin/sh
# Liveness check for the whisper-opencti connector. Returns 0 when the
# python entrypoint (`python -m src.main`) is alive, 1 otherwise.
#
# Internal-enrichment connectors have no HTTP listener — they're RabbitMQ
# consumers — so a process-existence check is the simplest meaningful
# liveness signal. The `[p]ython` character-class trick prevents grep from
# matching its own argv when scanned by `ps`.
ps | grep -q '[p]ython -m src.main' || exit 1