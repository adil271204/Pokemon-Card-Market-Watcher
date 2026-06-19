"""Idempotent migration: create set-scanner tables if they don't exist."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import engine
from src.models import Base, PokemonSet, PokemonCard, SetScan, SetScanResult  # noqa: F401

print("Running migrate_set_scanner.py …")

# create_all is idempotent (checkfirst=True by default with SA)
Base.metadata.create_all(bind=engine)

print("Done – set-scanner tables ready.")
