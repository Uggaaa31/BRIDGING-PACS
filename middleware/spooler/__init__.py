"""
spooler - Store-and-Forward Local Spooler (Offline Buffer)
==========================================================
Paket antrean lokal berbasis SQLite untuk menahan payload hasil bacaan
radiologi saat API SIMGOS tidak merespons (timeout / HTTP 5xx).

Modul:
  queue_manager.py : akses database antrean (enqueue, batch, retry, sent)
  spool_worker.py  : background thread yang menguras antrean secara berkala
"""

from __future__ import annotations
